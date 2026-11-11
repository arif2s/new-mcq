from __future__ import annotations
import gc
import os
import sys
import threading
import time
import logging
from typing import Any, Callable, Optional

import httpx

from config import DEFAULT_MODEL, LM_STUDIO_URL, MAX_CONTEXT_TOKENS

logger = logging.getLogger("medisearch.model_lifecycle")


def get_gpu_enabled_subprocess_env(
    base_env: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Returns a subprocess environment dict with backend CPU-only CUDA masks removed."""
    env = dict(base_env if base_env is not None else os.environ)
    if env.get("CUDA_VISIBLE_DEVICES", "").strip() in ("", "-1"):
        env.pop("CUDA_VISIBLE_DEVICES", None)
    return env


class LazyModelHolder:
    """Thread-safe lazy loader and memory releaser for heavy NLP/Transformer models."""

    def __init__(self, name: str, loader_fn: Callable[[], Any], idle_ttl_seconds: int = 600) -> None:
        self.name = name
        self._loader_fn = loader_fn
        self.idle_ttl_seconds = idle_ttl_seconds
        self._instance: Any = None
        self._last_used: float = 0.0
        self._lock = threading.RLock()

    @property
    def is_loaded(self) -> bool:
        return self._instance is not None

    def get(self) -> Any:
        with self._lock:
            if self._instance is None:
                self._instance = self._loader_fn()
            self._last_used = time.monotonic()
            return self._instance

    def release_memory(self) -> bool:
        """Explicitly unload the model from RAM/VRAM and trigger garbage collection."""
        with self._lock:
            if self._instance is None:
                return False
            self._instance = None
            self._last_used = 0.0
        gc.collect()
        torch_mod = sys.modules.get("torch")
        if torch_mod is not None:
            try:
                if torch_mod.cuda.is_available():
                    torch_mod.cuda.empty_cache()
            except Exception:
                pass
        return True

    def unload_if_idle(self, ttl_override: float | None = None) -> bool:
        ttl = ttl_override if ttl_override is not None else self.idle_ttl_seconds
        with self._lock:
            if self._instance is None:
                return False
            if (time.monotonic() - self._last_used) >= ttl:
                return self.release_memory()
            return False


class ContextLengthExceededError(ValueError):
    """Raised when prompt/input tokens exceed the loaded model context window."""

    def __init__(
        self,
        message: str,
        required_tokens: int = 0,
        model_limit: int = 0,
        excess_tokens: int = 0,
    ) -> None:
        super().__init__(message)
        self.required_tokens = required_tokens
        self.model_limit = model_limit
        self.excess_tokens = excess_tokens


class ModelLifecycleManager:
    """Queries LM Studio native REST API (/api/v0/models) for loaded_context_length
    and dynamically scales input/output word and token budgets for 4k–60k+ models."""

    def __init__(self, lm_studio_url: str = LM_STUDIO_URL) -> None:
        raw = str(lm_studio_url or "http://localhost:1234/v1").rstrip("/")
        self.base_url = raw[:-3] if raw.endswith("/v1") else raw
        self._cached_context_tokens: int = int(MAX_CONTEXT_TOKENS or 4096)
        self._cached_max_tokens: int = 32768
        self._active_model_id: str = str(DEFAULT_MODEL or "local-model")
        self._last_probe_ts: float = 0.0
        self._user_max_words_override: Optional[int] = None
        self._user_max_input_tokens: Optional[int] = None
        self._user_max_output_tokens: Optional[int] = None
        self._strict_overflow_check: bool = False

    @property
    def active_model_id(self) -> str:
        """Public getter for currently active model ID."""
        return self._active_model_id

    @property
    def cached_context_tokens(self) -> int:
        """Public getter for live or cached context window tokens."""
        return self._cached_context_tokens

    @property
    def user_max_words_override(self) -> Optional[int]:
        return self._user_max_words_override

    @user_max_words_override.setter
    def user_max_words_override(self, val: Optional[int]) -> None:
        self._user_max_words_override = val

    @property
    def user_max_input_tokens(self) -> Optional[int]:
        return self._user_max_input_tokens

    @user_max_input_tokens.setter
    def user_max_input_tokens(self, val: Optional[int]) -> None:
        self._user_max_input_tokens = val

    @property
    def user_max_output_tokens(self) -> Optional[int]:
        return self._user_max_output_tokens

    @user_max_output_tokens.setter
    def user_max_output_tokens(self, val: Optional[int]) -> None:
        self._user_max_output_tokens = val

    @property
    def strict_overflow_check(self) -> bool:
        return self._strict_overflow_check

    @strict_overflow_check.setter
    def strict_overflow_check(self, val: bool) -> None:
        self._strict_overflow_check = bool(val)

    def calculate_budgets_from_tokens(
        self,
        loaded_ctx: int,
        max_ctx: int = 32768,
        model_id: Optional[str] = None,
        user_max_words_override: Optional[int] = None,
        user_max_input_tokens: Optional[int] = None,
        user_max_output_tokens: Optional[int] = None,
        strict_overflow_check: Optional[bool] = None,
    ) -> dict[str, Any]:
        loaded_ctx = max(2048, int(loaded_ctx or self._cached_context_tokens or MAX_CONTEXT_TOKENS or 4096))
        max_ctx = max(loaded_ctx, int(max_ctx or 32768))

        # Scale factor relative to legacy 4096-token baseline
        scale_factor = max(1.0, loaded_ctx / 4096.0)

        # Output completion tokens: user override or dynamically proportional (22% of context, capped between 800 and 12,000)
        eff_user_out = user_max_output_tokens or self._user_max_output_tokens
        if eff_user_out is not None and int(eff_user_out) > 0:
            max_output_tokens = max(16, min(int(eff_user_out), loaded_ctx - 100))
        else:
            max_output_tokens = max(800, min(12000, int(loaded_ctx * 0.22)))

        # Reserve output completion and chat/system overhead (minimum 600 tokens or safe_max_out + 64)
        overhead = max(600, max_output_tokens + 64)
        eff_user_in = user_max_input_tokens or self._user_max_input_tokens
        if eff_user_in is not None and int(eff_user_in) > 0:
            usable_input_tokens = max(16, min(int(eff_user_in), max(16, loaded_ctx - overhead)))
        else:
            usable_input_tokens = max(1024, int((loaded_ctx - 600) * 0.78))

        # Convert token capacity to words (1 clinical word ~= 1.35 tokens)
        auto_summary_word_limit = max(650, int(usable_input_tokens / 1.35))

        override_val = int(user_max_words_override or self._user_max_words_override or 0)
        if override_val > 100:
            effective_summary_words = min(override_val, auto_summary_word_limit)
        else:
            effective_summary_words = auto_summary_word_limit

        # Scale per-stage word caps proportionally for 30k-60k context windows
        primary_source_input_words = max(650, min(25000, int(effective_summary_words * 0.70)))
        secondary_source_input_words = max(450, min(18000, int(effective_summary_words * 0.50)))

        # Microedit word limit: stays compact (150-280) on standard context (<=8192) or scales dynamically
        # with loaded context up to 6000 words on high-context (16k-64k+) models.
        if override_val > 0:
            microedit_word_limit = max(150, min(override_val, override_val // 6))
        elif loaded_ctx <= 8192:
            microedit_word_limit = 280
        else:
            microedit_word_limit = max(280, min(6000, int(280 * scale_factor)))

        primary_max_words = max(500, min(8000, int(500 * scale_factor)))
        combined_max_words = max(1000, min(15000, int(1000 * scale_factor)))
        subtopic_block_char_limit = max(600, min(18000, int(600 * scale_factor)))

        strict_check = self._strict_overflow_check if strict_overflow_check is None else bool(strict_overflow_check)

        return {
            "model_id": model_id or self._active_model_id,
            "loaded_context_length_tokens": loaded_ctx,
            "max_context_length_tokens": max_ctx,
            "usable_input_tokens": usable_input_tokens,
            "max_output_tokens": max_output_tokens,
            "auto_summary_word_limit": auto_summary_word_limit,
            "effective_summary_word_limit": effective_summary_words,
            "primary_source_input_words": primary_source_input_words,
            "secondary_source_input_words": secondary_source_input_words,
            "microedit_word_limit": microedit_word_limit,
            "primary_max_words": primary_max_words,
            "combined_max_words": combined_max_words,
            "subtopic_block_char_limit": subtopic_block_char_limit,
            "user_max_words_override": override_val if override_val > 0 else None,
            "user_max_input_tokens": eff_user_in if eff_user_in is not None and int(eff_user_in) > 0 else None,
            "user_max_output_tokens": eff_user_out if eff_user_out is not None and int(eff_user_out) > 0 else None,
            "strict_overflow_check": strict_check,
        }

    async def get_active_model_context_info(
        self,
        user_max_words_override: Optional[int] = None,
        user_max_input_tokens: Optional[int] = None,
        user_max_output_tokens: Optional[int] = None,
        strict_overflow_check: Optional[bool] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> dict[str, Any]:
        loaded_ctx = self._cached_context_tokens
        max_ctx = self._cached_max_tokens
        model_id = self._active_model_id

        owns_client = client is None
        http_client = client or httpx.AsyncClient(timeout=2.5)
        try:
            resp = await http_client.get(f"{self.base_url}/api/v0/models")
            if resp.status_code == 200:
                payload = resp.json()
                models = payload.get("data") or payload.get("models") or []
                if isinstance(models, list) and models:
                    # Strictly prioritize models with state == 'loaded' or explicit loaded_context_length
                    loaded_models = [
                        m for m in models
                        if isinstance(m, dict)
                        and (m.get("state") == "loaded" or m.get("loaded_context_length") is not None)
                        and m.get("type") in ("llm", "vlm", None)
                    ]
                    target = loaded_models[0] if loaded_models else models[0]
                    if isinstance(target, dict):
                        model_id = str(target.get("id") or target.get("key") or model_id)
                        max_ctx = int(target.get("max_context_length") or 32768)
                        loaded_ctx = int(
                            target.get("loaded_context_length")
                            or target.get("context_length")
                            or max_ctx
                        )
                        self._cached_context_tokens = loaded_ctx
                        self._cached_max_tokens = max_ctx
                        self._active_model_id = model_id
                        self._last_probe_ts = time.monotonic()
        except Exception as exc:
            logger.debug("Using cached LM Studio context length (%d): %s", loaded_ctx, exc)
        finally:
            if owns_client and http_client is not None:
                try:
                    await http_client.aclose()
                except Exception:
                    pass

        return self.calculate_budgets_from_tokens(
            loaded_ctx=loaded_ctx,
            max_ctx=max_ctx,
            model_id=model_id,
            user_max_words_override=user_max_words_override,
            user_max_input_tokens=user_max_input_tokens,
            user_max_output_tokens=user_max_output_tokens,
            strict_overflow_check=strict_overflow_check,
        )


model_lifecycle = ModelLifecycleManager()
