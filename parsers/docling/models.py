"""
Docling layout and vision model management (`parsers/docling/models.py`).

Handles model weight checking, offline HuggingFace Hub environment configuration,
and background pre-downloading of layout and TableFormer models.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

# Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

try:
    from config import HF_CACHE_DIR
    if HF_CACHE_DIR:
        os.makedirs(HF_CACHE_DIR, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE_DIR))
except ImportError:
    HF_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "huggingface"
    os.makedirs(HF_CACHE_DIR, exist_ok=True)

logger = logging.getLogger(__name__)

DOCLING_REQUIRED_MODELS = (
    "docling-project/docling-layout-heron",
    "docling-project/docling-layout-heron-onnx",
    "docling-project/docling-models",
)


def is_docling_models_downloaded() -> bool:
    """Checks whether all required Docling layout and table model weights exist in HF cache."""
    cache_dirs = [HF_CACHE_DIR, Path(os.path.expanduser("~/.cache/huggingface/hub"))]
    for repo_id in DOCLING_REQUIRED_MODELS:
        folder_name = f"models--{repo_id.replace('/', '--')}"
        found = False
        for c_dir in cache_dirs:
            target = Path(c_dir) / folder_name
            if target.exists() and any(target.glob("snapshots/*")):
                found = True
                break
        if not found:
            return False
    return True


def ensure_offline_mode_if_downloaded() -> bool:
    """Sets HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE if all Docling models are downloaded locally."""
    if is_docling_models_downloaded():
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return True
    return False


def download_docling_models() -> Dict[str, Any]:
    """Pre-downloads and caches all required Docling Heron & table models for offline PDF parsing."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        return {
            "status": "error",
            "message": f"huggingface_hub not installed: {exc}",
            "downloaded": False,
        }

    target_dir = Path(HF_CACHE_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded_paths: List[str] = []

    try:
        prev_offline = os.environ.get("HF_HUB_OFFLINE")
        if "HF_HUB_OFFLINE" in os.environ:
            del os.environ["HF_HUB_OFFLINE"]

        for repo_id in DOCLING_REQUIRED_MODELS:
            logger.info("Downloading Docling model %s into %s...", repo_id, target_dir)
            p = snapshot_download(repo_id=repo_id, cache_dir=str(target_dir))
            downloaded_paths.append(p)

        if prev_offline is not None:
            os.environ["HF_HUB_OFFLINE"] = prev_offline

        return {
            "status": "success",
            "message": "All Docling layout models pre-downloaded for offline processing.",
            "downloaded_paths": downloaded_paths,
            "downloaded": True,
        }
    except Exception as exc:
        logger.error("Failed to pre-download Docling layout models: %s", exc)
        return {
            "status": "error",
            "message": str(exc),
            "downloaded": False,
        }
