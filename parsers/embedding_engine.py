import os
import logging
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer
import onnxruntime as ort
import numpy as np

logger = logging.getLogger(__name__)

class CPUEmbeddingEngine:
    def __init__(self, model_id: str = "tavakolih/all-MiniLM-L6-v2-pubmed-full"):
        self.model_id = model_id
        self.tokenizer = None
        self.session = None
        self._load_model()

    def _load_model(self):
        try:
            logger.info(f"Loading tokenizer for {self.model_id}")
            # Ensure we have tokenizer.json
            tokenizer_path = hf_hub_download(repo_id=self.model_id, filename="tokenizer.json")
            self.tokenizer = Tokenizer.from_file(tokenizer_path)

            logger.info(f"Loading ONNX model for {self.model_id}")
            # Try to get model.onnx (sometimes called onnx/model.onnx)
            try:
                model_path = hf_hub_download(repo_id=self.model_id, filename="model.onnx")
            except Exception:
                try:
                    model_path = hf_hub_download(repo_id=self.model_id, filename="onnx/model.onnx")
                except Exception:
                    logger.warning(f"ONNX model not found in repo {self.model_id}, using default fallback.")
                    # Using a well-known ONNX repository for MiniLM
                    fallback_repo = "Xenova/all-MiniLM-L6-v2"
                    tokenizer_path = hf_hub_download(repo_id=fallback_repo, filename="tokenizer.json")
                    self.tokenizer = Tokenizer.from_file(tokenizer_path)
                    model_path = hf_hub_download(repo_id=fallback_repo, filename="onnx/model.onnx")

            # Force CPU execution provider to prevent CUDA initialization
            providers = ['CPUExecutionProvider']
            self.session = ort.InferenceSession(model_path, providers=providers)
            logger.info("ONNX Runtime session initialized on CPU.")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise e

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            # Tokenize texts
            # enable padding and truncation
            self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
            self.tokenizer.enable_truncation(max_length=512)

            encodings = self.tokenizer.encode_batch(texts)

            # Prepare inputs for ONNX model
            input_ids = np.array([enc.ids for enc in encodings], dtype=np.int64)
            attention_mask = np.array([enc.attention_mask for enc in encodings], dtype=np.int64)

            # Some ONNX models require token_type_ids
            inputs = {
                "input_ids": input_ids,
                "attention_mask": attention_mask
            }

            # Check model input names to handle token_type_ids if required
            model_inputs = [i.name for i in self.session.get_inputs()]
            if "token_type_ids" in model_inputs:
                token_type_ids = np.array([enc.type_ids for enc in encodings], dtype=np.int64)
                inputs["token_type_ids"] = token_type_ids

            # Run inference
            outputs = self.session.run(None, inputs)

            # Mean pooling based on attention mask
            token_embeddings = outputs[0] # Last hidden state: [batch_size, seq_length, hidden_size]
            input_mask_expanded = np.expand_dims(attention_mask, -1)

            sum_embeddings = np.sum(token_embeddings * input_mask_expanded, axis=1)
            sum_mask = np.clip(np.sum(input_mask_expanded, axis=1), a_min=1e-9, a_max=None)

            mean_pooled = sum_embeddings / sum_mask

            # L2 normalization
            norms = np.linalg.norm(mean_pooled, axis=1, keepdims=True)
            normalized_embeddings = mean_pooled / np.clip(norms, a_min=1e-12, a_max=None)

            return normalized_embeddings.tolist()

        except Exception as e:
            logger.error(f"Failed to compute embeddings: {e}")
            return []

    def embed_single(self, text: str) -> list[float]:
        results = self.embed([text])
        if results:
            return results[0]
        return []
