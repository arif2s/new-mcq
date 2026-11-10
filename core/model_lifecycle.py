from __future__ import annotations
import gc
import os
import sys
import threading
import time
from typing import Any, Callable, Optional


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
