import os
import json
import logging
from typing import List, Optional, Tuple
from contextlib import closing
import numpy as np

from config import HF_CACHE_DIR
from core.database import get_db_connection

logger = logging.getLogger(__name__)

# Known dimensions for supported clinical & general ONNX models
MODEL_DIMENSIONS = {
    "Xenova/all-MiniLM-L6-v2": 384,
    "Xenova/bge-small-en-v1.5": 384,
    "tavakolih/all-MiniLM-L6-v2-pubmed-full": 384,
    "MaunikG/SapBERT-from-PubMedBERT-fulltext-ONNX": 768,
    "Xenova/SapBERT-from-PubMedBERT-fulltext": 768,
    "onnx-community/Bio_ClinicalBERT-ONNX": 768,
}


def get_configured_embedding_model() -> str:
    """Fetches the user-selected embedding model from SQLite user_state."""
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            row = cursor.fetchone()
            if row:
                state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                model_id = state.get("embeddingModel")
                if model_id:
                    return model_id
    except Exception:
        pass
    return "Xenova/all-MiniLM-L6-v2"


def _resolve_local_model_files(model_id: str) -> Optional[Tuple[str, str]]:
    """
    Checks if tokenizer.json and model.onnx are already cached locally inside HF_CACHE_DIR
    or default HuggingFace cache without making any network requests.
    """
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
            for onnx_name in ("onnx/model.onnx", "model.onnx", "onnx/model_quantized.onnx"):
                try:
                    mod_path = hf_hub_download(
                        repo_id=model_id,
                        filename=onnx_name,
                        cache_dir=cache_dir,
                        local_files_only=True,
                    )
                    if tok_path and mod_path and os.path.exists(tok_path) and os.path.exists(mod_path):
                        return tok_path, mod_path
                except Exception:
                    continue
        except Exception:
            continue
    return None


def is_embedding_model_downloaded(model_id: str) -> bool:
    """Returns True if the ONNX model and tokenizer are present on disk for offline use."""
    return _resolve_local_model_files(model_id) is not None


def download_embedding_model(model_id: str) -> dict:
    """
    Explicitly downloads the selected ONNX embedding model and tokenizer into HF_CACHE_DIR.
    Triggered by the user pressing the 'Download for Offline Use' button in Settings.
    """
    from huggingface_hub import hf_hub_download

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
        for onnx_name in ("onnx/model.onnx", "model.onnx", "onnx/model_quantized.onnx"):
            try:
                mod_path = hf_hub_download(
                    repo_id=model_id,
                    filename=onnx_name,
                    cache_dir=str(HF_CACHE_DIR),
                    local_files_only=False,
                )
                if mod_path:
                    break
            except Exception as err:
                last_err = err

        if not mod_path:
            raise RuntimeError(f"Could not locate model.onnx in {model_id}: {last_err}")

        return {
            "status": "success",
            "model_id": model_id,
            "tokenizer_path": tok_path,
            "model_path": mod_path,
            "downloaded": True,
        }
    except Exception as e:
        logger.error(f"Failed to download embedding model {model_id}: {e}")
        return {"status": "error", "model_id": model_id, "message": str(e), "downloaded": False}


class CPUEmbeddingEngine:
    def __init__(self, model_id: Optional[str] = None):
        self.model_id = model_id or get_configured_embedding_model()
        self.tokenizer = None
        self.session = None
        self._dim = MODEL_DIMENSIONS.get(self.model_id, 384)

    def get_dimension(self) -> int:
        return self._dim

    def _load_model(self) -> bool:
        """
        Loads the ONNX model on CPU. Prioritizes local offline cache first so offline
        operation never hangs or throws unhandled network errors.
        """
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            local_files = _resolve_local_model_files(self.model_id)
            if not local_files:
                for fallback_id in ("Xenova/all-MiniLM-L6-v2", "Xenova/bge-small-en-v1.5"):
                    local_files = _resolve_local_model_files(fallback_id)
                    if local_files:
                        logger.info(f"Using cached offline fallback model: {fallback_id}")
                        self.model_id = fallback_id
                        break

            if not local_files:
                dl_res = download_embedding_model(self.model_id)
                if dl_res.get("status") == "success":
                    local_files = (dl_res["tokenizer_path"], dl_res["model_path"])
                else:
                    return False

            tok_path, mod_path = local_files
            self.tokenizer = Tokenizer.from_file(tok_path)
            self.session = ort.InferenceSession(mod_path, providers=["CPUExecutionProvider"])

            try:
                out_shape = self.session.get_outputs()[0].shape
                if out_shape and isinstance(out_shape[-1], int) and out_shape[-1] > 0:
                    self._dim = out_shape[-1]
            except Exception:
                self._dim = MODEL_DIMENSIONS.get(self.model_id, 384)

            logger.info(f"ONNX Runtime CPU session initialized for {self.model_id} (dim={self._dim}).")
            return True
        except Exception as e:
            logger.warning(f"Embedding engine unavailable (offline/not downloaded): {e}")
            self.session = None
            return False

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        if self.session is None:
            if not self._load_model():
                return []

        try:
            self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
            self.tokenizer.enable_truncation(max_length=512)

            encodings = self.tokenizer.encode_batch(texts)
            input_ids = np.array([enc.ids for enc in encodings], dtype=np.int64)
            attention_mask = np.array([enc.attention_mask for enc in encodings], dtype=np.int64)

            inputs = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }

            model_inputs = [i.name for i in self.session.get_inputs()]
            if "token_type_ids" in model_inputs:
                inputs["token_type_ids"] = np.array([enc.type_ids for enc in encodings], dtype=np.int64)

            outputs = self.session.run(None, inputs)
            token_embeddings = outputs[0]
            if token_embeddings.ndim == 3:
                self._dim = int(token_embeddings.shape[-1])
                input_mask_expanded = np.expand_dims(attention_mask, -1)
                sum_embeddings = np.sum(token_embeddings * input_mask_expanded, axis=1)
                sum_mask = np.clip(np.sum(input_mask_expanded, axis=1), a_min=1e-9, a_max=None)
                mean_pooled = sum_embeddings / sum_mask
            else:
                mean_pooled = token_embeddings

            norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
            normalized_embeddings = mean_pooled / np.clip(norms, a_min=1e-12, a_max=None)
            return normalized_embeddings.astype(np.float32).tolist()

        except Exception as e:
            logger.error(f"Failed to compute embeddings: {e}")
            return []

    def embed_single(self, text: str) -> List[float]:
        results = self.embed([text])
        return results[0] if results else []
