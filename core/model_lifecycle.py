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


class ModelLifecycleManager:
    """Queries LM Studio native REST API (/api/v0/models) for loaded_context_length."""

    def __init__(self, lm_studio_url: str = LM_STUDIO_URL) -> None:
        raw = str(lm_studio_url or "http://localhost:1234/v1").rstrip("/")
        self.base_url = raw[:-3] if raw.endswith("/v1") else raw
        self._cached_context_tokens: int = int(MAX_CONTEXT_TOKENS or 4096)
        self._cached_max_tokens: int = 32768
        self._active_model_id: str = str(DEFAULT_MODEL or "local-model")

    def calculate_budgets_from_tokens(
        self,
        loaded_ctx: int,
        max_ctx: int = 32768,
        model_id: Optional[str] = None,
        user_max_words_override: Optional[int] = None,
    ) -> dict[str, Any]:
        loaded_ctx = max(1024, int(loaded_ctx or MAX_CONTEXT_TOKENS or 4096))
        max_ctx = max(loaded_ctx, int(max_ctx or 32768))
        usable_input_tokens = max(512, int((loaded_ctx - 800) * 0.75))
        auto_summary_word_limit = max(400, int(usable_input_tokens / 1.35))

        override_val = int(user_max_words_override or 0)
        if override_val > 100:
            effective_summary_words = min(override_val, auto_summary_word_limit)
        else:
            effective_summary_words = auto_summary_word_limit

        microedit_word_limit = min(280, max(150, effective_summary_words // 6))
        if override_val > 0:
            microedit_word_limit = min(microedit_word_limit, max(80, override_val))

        return {
            "model_id": model_id or self._active_model_id,
            "loaded_context_length_tokens": loaded_ctx,
            "max_context_length_tokens": max_ctx,
            "auto_summary_word_limit": auto_summary_word_limit,
            "effective_summary_word_limit": effective_summary_words,
            "microedit_word_limit": microedit_word_limit,
            "user_max_words_override": override_val if override_val > 0 else None,
        }

    async def get_active_model_context_info(
        self,
        user_max_words_override: Optional[int] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> dict[str, Any]:
        loaded_ctx = self._cached_context_tokens
        max_ctx = self._cached_max_tokens
        model_id = self._active_model_id

        owns_client = client is None
        try:
            http_client = client or httpx.AsyncClient(timeout=2.5)
            resp = await http_client.get(f"{self.base_url}/api/v0/models")
            if resp.status_code == 200:
                payload = resp.json()
                models = payload.get("data") or payload.get("models") or []
                if isinstance(models, list) and models:
                    loaded_models = [
                        m for m in models
                        if isinstance(m, dict)
                        and m.get("state") == "loaded"
                        and m.get("type") in ("llm", "vlm", None)
                    ]
                    target = loaded_models[0] if loaded_models else models[0]
                    if isinstance(target, dict):
                        model_id = str(target.get("id") or target.get("key") or model_id)
                        max_ctx = int(target.get("max_context_length") or 32768)
                        loaded_ctx = int(
                            target.get("loaded_context_length")
                            or target.get("context_length")
                            or min(max_ctx, 8192)
                        )
                        self._cached_context_tokens = loaded_ctx
                        self._cached_max_tokens = max_ctx
                        self._active_model_id = model_id
        except Exception as exc:
            logger.debug("Using cached LM Studio context length (%d): %s", loaded_ctx, exc)
        finally:
            if owns_client and client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

        return self.calculate_budgets_from_tokens(
            loaded_ctx=loaded_ctx,
            max_ctx=max_ctx,
            model_id=model_id,
            user_max_words_override=user_max_words_override,
        )


model_lifecycle = ModelLifecycleManager()
