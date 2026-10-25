import gc
import json
import logging
import os
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Enforce Invariant 1: Zero VRAM for Backend (strictly CPU-only ONNX execution)
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import numpy as np

from config import DEFAULT_EMBEDDING_MODEL, HF_CACHE_DIR
from core.database import get_db_connection

logger = logging.getLogger("medisearch.embedding")

# Known dimensions for supported clinical & general ONNX models
MODEL_DIMENSIONS: Dict[str, int] = {
    "Xenova/all-MiniLM-L6-v2": 384,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "Xenova/bge-small-en-v1.5": 384,
    "BAAI/bge-small-en-v1.5": 384,
    "tavakolih/all-MiniLM-L6-v2-pubmed-full": 384,
    "MaunikG/SapBERT-from-PubMedBERT-fulltext-ONNX": 768,
    "Xenova/SapBERT-from-PubMedBERT-fulltext": 768,
    "cambridgeltl/SapBERT-from-PubMedBERT-fulltext": 768,
    "onnx-community/Bio_ClinicalBERT-ONNX": 768,
}

_ONNX_CANDIDATE_FILENAMES: Tuple[str, ...] = (
    "onnx/model.onnx",
    "model.onnx",
    "onnx/model_quantized.onnx",
    "model_quantized.onnx",
)

_FALLBACK_MODELS: Tuple[str, ...] = (
    DEFAULT_EMBEDDING_MODEL,
    "Xenova/all-MiniLM-L6-v2",
    "sentence-transformers/all-MiniLM-L6-v2",
    "Xenova/bge-small-en-v1.5",
)

_LOCK = threading.RLock()
_CONFIGURED_MODEL_CACHE: Optional[str] = None
_ENGINE_CACHE: Dict[str, "CPUEmbeddingEngine"] = {}


def get_configured_embedding_model(force_refresh: bool = False) -> str:
    """Fetches the user-selected embedding model from SQLite user_state with memory caching."""
    global _CONFIGURED_MODEL_CACHE
    with _LOCK:
        if not force_refresh and _CONFIGURED_MODEL_CACHE is not None:
            return _CONFIGURED_MODEL_CACHE

        resolved_model: Optional[str] = None
        try:
            with closing(get_db_connection()) as conn:
                rows = conn.execute(
                    "SELECT key, value FROM user_state "
                    "WHERE key IN ('selectedEmbeddingModel', 'embeddingModel', 'app_persistence')"
                ).fetchall()
                state_map = {row["key"]: row["value"] for row in rows}

                # 1. Check direct user_state keys (AppPersistence contract in Section 4)
                for direct_key in ("selectedEmbeddingModel", "embeddingModel"):
                    raw_val = state_map.get(direct_key)
                    if raw_val:
                        try:
                            parsed = json.loads(raw_val)
                            if isinstance(parsed, str) and parsed.strip():
                                resolved_model = parsed.strip()
                                break
                        except Exception:
                            if isinstance(raw_val, str) and raw_val.strip():
                                resolved_model = raw_val.strip()
                                break

                # 2. Check nested keys inside 'app_persistence' JSON object
                if not resolved_model and "app_persistence" in state_map:
                    raw_app = state_map["app_persistence"]
                    state_obj = json.loads(raw_app) if isinstance(raw_app, str) else raw_app
                    if isinstance(state_obj, dict):
                        candidate = (
                            state_obj.get("selectedEmbeddingModel")
                            or state_obj.get("embeddingModel")
                        )
                        if isinstance(candidate, str) and candidate.strip():
                            resolved_model = candidate.strip()
        except Exception as exc:
            logger.debug("Failed to read configured embedding model from DB: %s", exc)

        _CONFIGURED_MODEL_CACHE = (
            resolved_model or DEFAULT_EMBEDDING_MODEL or "sentence-transformers/all-MiniLM-L6-v2"
        )
        return _CONFIGURED_MODEL_CACHE


def set_configured_embedding_model(model_id: str) -> str:
    """Explicitly updates the user-selected embedding model in SQLite user_state and clears in-memory caches."""
    global _CONFIGURED_MODEL_CACHE
    if not model_id or not str(model_id).strip():
        return get_configured_embedding_model(force_refresh=True)

    clean_id = str(model_id).strip()
    with _LOCK:
        _CONFIGURED_MODEL_CACHE = clean_id
        _ENGINE_CACHE.clear()
        gc.collect()

        try:
            with closing(get_db_connection()) as conn:
                conn.execute(
                    """
                    INSERT INTO user_state (key, value, updated_at)
                    VALUES ('selectedEmbeddingModel', ?, unixepoch('now'))
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (json.dumps(clean_id),),
                )
                conn.execute(
                    """
                    INSERT INTO user_state (key, value, updated_at)
                    VALUES ('embeddingModel', ?, unixepoch('now'))
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (json.dumps(clean_id),),
                )

                cursor = conn.cursor()
                cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
                row = cursor.fetchone()
                app_obj = {}
                if row and row[0]:
                    try:
                        raw_app = row[0]
                        app_obj = json.loads(raw_app) if isinstance(raw_app, str) else raw_app
                    except Exception:
                        pass
                if isinstance(app_obj, dict):
                    app_obj["embeddingModel"] = clean_id
                    app_obj["selectedEmbeddingModel"] = clean_id
                    conn.execute(
                        """
                        INSERT INTO user_state (key, value, updated_at)
                        VALUES ('app_persistence', ?, unixepoch('now'))
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = excluded.updated_at
                        """,
                        (json.dumps(app_obj),),
                    )
                conn.commit()
        except Exception as exc:
            logger.error("Failed to persist set_configured_embedding_model to DB: %s", exc)

    return clean_id


def _find_in_directory(base_dir: Path) -> Optional[Tuple[str, str]]:
    """Checks a concrete directory for tokenizer.json and any supported ONNX model file."""
    tok_file = base_dir / "tokenizer.json"
    if not tok_file.is_file() or tok_file.stat().st_size == 0:
        return None
    for onnx_rel in _ONNX_CANDIDATE_FILENAMES:
        mod_file = base_dir / onnx_rel
        if mod_file.is_file() and mod_file.stat().st_size > 0:
            return str(tok_file), str(mod_file)
    return None


def _resolve_local_model_files(model_id: str) -> Optional[Tuple[str, str]]:
    """Checks if tokenizer.json and model.onnx are already cached locally without network requests."""
    if not model_id:
        return None

    # 1. Fast filesystem scan in HF_CACHE_DIR (direct folder & HF hub snapshot structure)
    safe_repo_dir = f"models--{model_id.replace('/', '--')}"
    candidate_roots = [
        HF_CACHE_DIR / model_id,
        HF_CACHE_DIR / model_id.replace("/", "_"),
        HF_CACHE_DIR / safe_repo_dir,
        HF_CACHE_DIR / "hub" / safe_repo_dir,
    ]
    for root in candidate_roots:
        if not root.exists():
            continue
        direct_hit = _find_in_directory(root)
        if direct_hit:
            return direct_hit
        snapshots_dir = root / "snapshots"
        if snapshots_dir.is_dir():
            # Sort snapshots newest-first
            for snap in sorted(
                snapshots_dir.iterdir(),
                key=lambda p: p.stat().st_mtime if p.exists() else 0,
                reverse=True,
            ):
                if snap.is_dir():
                    snap_hit = _find_in_directory(snap)
                    if snap_hit:
                        return snap_hit

    # 2. Fallback to huggingface_hub local cache resolver
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return None

    for cache_dir in (str(HF_CACHE_DIR), None):
        try:
            tok_path = hf_hub_download(
                repo_id=model_id,
                filename="tokenizer.json",
                cache_dir=cache_dir,
                local_files_only=True,
            )
            if not tok_path or not os.path.exists(tok_path):
                continue
            for onnx_name in _ONNX_CANDIDATE_FILENAMES:
                try:
                    mod_path = hf_hub_download(
                        repo_id=model_id,
                        filename=onnx_name,
                        cache_dir=cache_dir,
                        local_files_only=True,
                    )
                    if mod_path and os.path.exists(mod_path):
                        return tok_path, mod_path
                except Exception:
                    continue
        except Exception:
            continue
    return None


def is_embedding_model_downloaded(model_id: str) -> bool:
    """Returns True if the ONNX model and tokenizer are present on disk for offline use."""
    return _resolve_local_model_files(model_id) is not None


def download_embedding_model(model_id: str) -> Dict[str, Any]:
    """Explicitly downloads the selected ONNX embedding model and tokenizer into HF_CACHE_DIR."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        return {
            "status": "error",
            "model_id": model_id,
            "message": str(exc),
            "downloaded": False,
        }

    os.makedirs(HF_CACHE_DIR, exist_ok=True)
    try:
        tok_path = hf_hub_download(
            repo_id=model_id,
            filename="tokenizer.json",
            cache_dir=str(HF_CACHE_DIR),
            local_files_only=False,
        )
        mod_path = None
        last_err = None
        for onnx_name in _ONNX_CANDIDATE_FILENAMES:
            try:
                mod_path = hf_hub_download(
                    repo_id=model_id,
                    filename=onnx_name,
                    cache_dir=str(HF_CACHE_DIR),
                    local_files_only=False,
                )
                if mod_path and os.path.exists(mod_path):
                    break
            except Exception as err:
                last_err = err

        if not mod_path:
            raise RuntimeError(f"Could not locate ONNX model file in {model_id}: {last_err}")

        with _LOCK:
            global _CONFIGURED_MODEL_CACHE
            _CONFIGURED_MODEL_CACHE = model_id
            _ENGINE_CACHE.pop(model_id, None)

        return {
            "status": "success",
            "model_id": model_id,
            "tokenizer_path": tok_path,
            "model_path": mod_path,
            "downloaded": True,
        }
    except Exception as exc:
        logger.error("Failed to download embedding model %s: %s", model_id, exc)
        return {
            "status": "error",
            "model_id": model_id,
            "message": str(exc),
            "downloaded": False,
        }


class CPUEmbeddingEngine:
    """Lazy CPU-only ONNX embedding engine enforcing <500MB RAM budget and thread safety."""

    _BATCH_SIZE: int = 32

    def __init__(self, model_id: Optional[str] = None):
        self.model_id: str = model_id or get_configured_embedding_model()
        self.tokenizer: Optional[Any] = None
        self.session: Optional[Any] = None
        self._lock = threading.RLock()
        self._dim: int = self._resolve_expected_dimension()

    def _resolve_expected_dimension(self) -> int:
        """Determines the vector dimension of the active or fallback local model without desync."""
        if _resolve_local_model_files(self.model_id):
            return MODEL_DIMENSIONS.get(self.model_id, 384)
        for fallback_id in _FALLBACK_MODELS:
            if fallback_id and _resolve_local_model_files(fallback_id):
                return MODEL_DIMENSIONS.get(fallback_id, 384)
        return MODEL_DIMENSIONS.get(self.model_id, 384)

    def get_dimension(self) -> int:
        with self._lock:
            if self.session is None:
                if self.model_id not in MODEL_DIMENSIONS or not _resolve_local_model_files(self.model_id):
                    # Attempt lazy load so fallback model selection & output tensor shape sync immediately
                    self._load_model()
            return self._dim

    def _load_model(self) -> bool:
        """Loads the ONNX model on CPU with bounded threading and zero offline network calls."""
        with self._lock:
            if self.session is not None and self.tokenizer is not None:
                return True

            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                local_files = _resolve_local_model_files(self.model_id)
                if not local_files:
                    for fallback_id in _FALLBACK_MODELS:
                        if not fallback_id or fallback_id == self.model_id:
                            continue
                        local_files = _resolve_local_model_files(fallback_id)
                        if local_files:
                            logger.info(
                                "Model '%s' not cached; using offline fallback embedding model: %s",
                                self.model_id,
                                fallback_id,
                            )
                            self.model_id = fallback_id
                            self._dim = MODEL_DIMENSIONS.get(self.model_id, 384)
                            break

                if not local_files:
                    logger.debug(
                        "Embedding model '%s' not present locally; offline CPU inference bypassed.",
                        self.model_id,
                    )
                    return False

                tok_path, mod_path = local_files
                tokenizer = Tokenizer.from_file(tok_path)
                tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
                tokenizer.enable_truncation(max_length=512)

                # Strictly configure thread & memory arena allocation to protect CPU & RAM budgets
                sess_options = ort.SessionOptions()
                cpu_threads = min(4, max(1, (os.cpu_count() or 1) // 2))
                sess_options.intra_op_num_threads = cpu_threads
                sess_options.inter_op_num_threads = 1
                sess_options.enable_cpu_mem_arena = True
                sess_options.graph_optimization_level = (
                    ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                )

                session = ort.InferenceSession(
                    mod_path,
                    sess_options=sess_options,
                    providers=["CPUExecutionProvider"],
                )

                try:
                    out_shape = session.get_outputs()[0].shape
                    if out_shape and isinstance(out_shape[-1], int) and out_shape[-1] > 0:
                        self._dim = int(out_shape[-1])
                    else:
                        self._dim = MODEL_DIMENSIONS.get(self.model_id, 384)
                except Exception:
                    self._dim = MODEL_DIMENSIONS.get(self.model_id, 384)

                self.tokenizer = tokenizer
                self.session = session
                logger.info(
                    "ONNX Runtime CPU session initialized for %s (dim=%d)",
                    self.model_id,
                    self._dim,
                )
                return True
            except Exception as exc:
                logger.warning("Embedding engine initialization skipped: %s", exc)
                self.session = None
                self.tokenizer = None
                return False

    def _embed_batch(self, batch_texts: List[str]) -> List[List[float]]:
        encodings = self.tokenizer.encode_batch(batch_texts)

        input_ids = np.array([enc.ids for enc in encodings], dtype=np.int64)
        attention_mask = np.array([enc.attention_mask for enc in encodings], dtype=np.int64)

        inputs: Dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }

        model_inputs = {i.name for i in self.session.get_inputs()}
        if "token_type_ids" in model_inputs:
            inputs["token_type_ids"] = np.array(
                [enc.type_ids for enc in encodings], dtype=np.int64
            )

        outputs = self.session.run(None, inputs)
        token_embeddings = outputs[0]

        if token_embeddings.ndim == 3:
            self._dim = int(token_embeddings.shape[-1])
            input_mask_expanded = np.expand_dims(attention_mask, -1)
            sum_embeddings = np.sum(token_embeddings * input_mask_expanded, axis=1)
            sum_mask = np.clip(
                np.sum(input_mask_expanded, axis=1), a_min=1e-9, a_max=None
            )
            mean_pooled = sum_embeddings / sum_mask
        else:
            if token_embeddings.ndim == 2 and token_embeddings.shape[-1] > 0:
                self._dim = int(token_embeddings.shape[-1])
            mean_pooled = token_embeddings

        norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
        normalized_embeddings = mean_pooled / np.clip(norms, a_min=1e-12, a_max=None)
        return normalized_embeddings.astype(np.float32).tolist()

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        with self._lock:
            if self.session is None or self.tokenizer is None:
                if not self._load_model():
                    return []

            try:
                clean_texts = [str(t).strip() or " " for t in texts]
                all_results: List[List[float]] = []
                for start_idx in range(0, len(clean_texts), self._BATCH_SIZE):
                    batch = clean_texts[start_idx : start_idx + self._BATCH_SIZE]
                    all_results.extend(self._embed_batch(batch))
                return all_results
            except Exception as exc:
                logger.error("Failed to compute embeddings: %s", exc)
                return []

    def embed_single(self, text: str) -> List[float]:
        results = self.embed([text])
        return results[0] if results else []


def get_embedding_engine(model_id: Optional[str] = None) -> CPUEmbeddingEngine:
    """Returns a process-wide cached CPUEmbeddingEngine singleton.

    Evicts previously cached models when switching models to strictly respect
    the <500 MB LanceDB + CPUEmbedding RAM budget (Section 1 & Invariant 8).
    """
    with _LOCK:
        target_id = model_id or get_configured_embedding_model()
        if target_id not in _ENGINE_CACHE:
            # Evict stale ONNX sessions to prevent multi-model RAM accumulation
            if _ENGINE_CACHE:
                _ENGINE_CACHE.clear()
                gc.collect()
            _ENGINE_CACHE[target_id] = CPUEmbeddingEngine(target_id)
        return _ENGINE_CACHE[target_id]
