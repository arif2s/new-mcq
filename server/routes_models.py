"""
Hardware-constrained model manager, diagnostic endpoints, and ontology index builder.
Enforces zero VRAM usage, cached engine singletons, thread boundaries, and blueprint contract adherence.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import importlib.util
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Enforce Invariant 1: Zero VRAM for backend processes before any model imports
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from pydantic import ConfigDict

    _MODEL_CONFIG = ConfigDict(extra="allow")
except ImportError:
    _MODEL_CONFIG = None

try:
    from config import (
        BASE_DIR,
        DATA_DIR,
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_FLASHRANK_MODEL,
        FLASHRANK_CACHE_DIR,
        HF_CACHE_DIR,
        INDEX_PATH,
        KBS_DIR,
        LINKERS_DIR,
        LM_STUDIO_TIMEOUT,
        LM_STUDIO_URL,
        MODELS_DIR,
        ONTOLOGY_DB_PATH,
        SCISPACY_MODEL_CATALOG,
        SCISPACY_MODELS_DIR,
        DB_PATH,
        ensure_directories,
    )
except ImportError:
    BASE_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = BASE_DIR / "data"
    MODELS_DIR = DATA_DIR / "models"
    HF_CACHE_DIR = MODELS_DIR / "huggingface"
    FLASHRANK_CACHE_DIR = MODELS_DIR / "flashrank"
    SCISPACY_MODELS_DIR = MODELS_DIR / "scispacy"
    LINKERS_DIR = DATA_DIR / "linkers"
    KBS_DIR = LINKERS_DIR / "kbs"
    ONTOLOGY_DB_PATH = LINKERS_DIR / "ontology_kb.db"
    DB_PATH = DATA_DIR / "mcq_hub.db"
    INDEX_PATH = DATA_DIR / "tantivy_index"
    LM_STUDIO_URL = "http://127.0.0.1:1234/v1"
    LM_STUDIO_TIMEOUT = 12.0
    DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
    DEFAULT_FLASHRANK_MODEL = "ms-marco-TinyBERT-L-2-v2"
    SCISPACY_MODEL_CATALOG = {}

from core.database import get_db_connection

logger = logging.getLogger("medisearch.routes_models")

router = APIRouter(prefix="/api/models", tags=["models"])

# Ensure directory structures exist
LINKERS_DIR.mkdir(parents=True, exist_ok=True)
SCISPACY_MODELS_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------------------------------------------------------------
# Catalog Definitions
# -----------------------------------------------------------------------------
SUPPORTED_EMBEDDING_MODELS: List[Dict[str, Any]] = [
    {
        "id": "sentence-transformers/all-MiniLM-L6-v2",
        "name": "all-MiniLM-L6-v2 (384-d, Fast & Lightweight)",
        "dimension": 384,
        "size_mb": 90,
        "default": True,
    },
    {
        "id": "BAAI/bge-small-en-v1.5",
        "name": "BGE-Small-EN-v1.5 (384-d, High Quality)",
        "dimension": 384,
        "size_mb": 130,
        "default": False,
    },
    {
        "id": "pritamdeka/S-PubMedBert-MS-MARCO",
        "name": "PubMedBERT MS-MARCO (768-d, Specialized Clinical)",
        "dimension": 768,
        "size_mb": 420,
        "default": False,
    },
    {
        "id": "MaunikG/SapBERT-from-PubMedBERT-fulltext-ONNX",
        "name": "SapBERT Clinical Fulltext (768-d, UMLS Synonym Alignment)",
        "dimension": 768,
        "size_mb": 440,
        "default": False,
    },
]

SUPPORTED_FLASHRANK_MODELS: List[Dict[str, Any]] = [
    {
        "id": "ms-marco-TinyBERT-L-2-v2",
        "name": "TinyBERT (Ultra-Fast CPU Cross-Encoder)",
        "size_mb": 17,
        "default": True,
    },
    {
        "id": "ms-marco-MiniLM-L-12-v2",
        "name": "MiniLM-L-12 (Accurate Deep Reranker)",
        "size_mb": 130,
        "default": False,
    },
]

SUPPORTED_SCISPACY_MODELS: List[Dict[str, Any]] = [
    {
        "id": "en_core_sci_md",
        "name": "en_core_sci_md (Recommended Default)",
        "version": "0.5.4",
        "url": "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_md-0.5.4.tar.gz",
        "size_mb": 50,
        "ram_mb": 180,
        "description": "360k vocabulary + 50k PubMed vectors + GENIA parser. Extracts Anatomy, Microorganisms, Subtypes, and Procedures.",
        "recommended": True,
    },
    {
        "id": "en_core_sci_sm",
        "name": "en_core_sci_sm (Fast Lightweight)",
        "version": "0.5.4",
        "url": "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz",
        "size_mb": 15,
        "ram_mb": 90,
        "description": "Fastest CPU extraction pipeline. 100k biomedical vocabulary + MedMentions dependency parser.",
        "recommended": False,
    },
    {
        "id": "en_ner_bionlp13cg_md",
        "name": "en_ner_bionlp13cg_md (Organism & Organ Specialist)",
        "version": "0.5.4",
        "url": "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_ner_bionlp13cg_md-0.5.4.tar.gz",
        "size_mb": 115,
        "ram_mb": 150,
        "description": "BioNLP corpus: Dedicated tags for ORGAN, ORGANISM, CELL, TISSUE, and CANCER.",
        "recommended": False,
    },
    {
        "id": "en_ner_bc5cdr_md",
        "name": "en_ner_bc5cdr_md (Disease & Chemical Specialist)",
        "version": "0.5.4",
        "url": "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_ner_bc5cdr_md-0.5.4.tar.gz",
        "size_mb": 115,
        "ram_mb": 150,
        "description": "BioCreative V corpus: Explicit DISEASE and CHEMICAL/Drug entity recognition.",
        "recommended": False,
    },
    {
        "id": "en_core_sci_lg",
        "name": "en_core_sci_lg (Comprehensive Large Vocabulary)",
        "version": "0.5.4",
        "url": "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_lg-0.5.4.tar.gz",
        "size_mb": 550,
        "ram_mb": 750,
        "description": "785k vocabulary + 600k vectors. Broadest medical coverage (requires ~750 MB RAM).",
        "recommended": False,
    },
]

# -----------------------------------------------------------------------------
# Global Async State Trackers
# -----------------------------------------------------------------------------
_download_lock = threading.Lock()
_active_downloads: Dict[str, Dict[str, Any]] = {}

_download_all_state: Dict[str, Any] = {
    "status": "idle",
    "progress": 0.0,
    "message": "",
    "details": {},
    "error": None,
}


def _download_all_defaults_worker() -> None:
    """Sequential worker downloading and warming up all default offline models and vendor assets."""
    global _download_all_state
    with _download_lock:
        _download_all_state = {
            "status": "running",
            "progress": 5.0,
            "message": "Starting download of all default offline models and assets...",
            "details": {},
            "error": None,
        }

    try:
        # Step 1: Default Embedding Model
        with _download_lock:
            _download_all_state.update({
                "progress": 15.0,
                "message": f"Downloading default embedding model ({DEFAULT_EMBEDDING_MODEL})...",
            })
        from parsers.embedding_engine import download_embedding_model
        download_embedding_model(DEFAULT_EMBEDDING_MODEL)

        # Step 2: Default FlashRank Model
        with _download_lock:
            _download_all_state.update({
                "progress": 35.0,
                "message": f"Downloading default FlashRank reranker ({DEFAULT_FLASHRANK_MODEL})...",
            })
        from parsers.reranker import download_flashrank_model
        download_flashrank_model(DEFAULT_FLASHRANK_MODEL)

        # Step 3: Default SciSpaCy Model
        with _download_lock:
            _download_all_state.update({
                "progress": 55.0,
                "message": "Downloading default SciSpaCy model (en_core_sci_md)...",
            })
        from parsers.scispacy_linker_engine import download_scispacy_model
        download_scispacy_model("en_core_sci_md")

        # Step 4: Default Docling v2 Layout Models
        with _download_lock:
            _download_all_state.update({
                "progress": 75.0,
                "message": "Downloading Docling v2 layout & table models...",
            })
        from parsers.docling_v2_parser import download_docling_models
        download_docling_models()

        # Step 5: UI Vendor Assets
        with _download_lock:
            _download_all_state.update({
                "progress": 90.0,
                "message": "Caching offline UI vendor libraries (Tailwind, Marked, KaTeX)...",
            })
        from config import ensure_offline_vendor_assets
        ensure_offline_vendor_assets()

        with _download_lock:
            _download_all_state = {
                "status": "completed",
                "progress": 100.0,
                "message": "All required offline models and vendor assets are downloaded and ready!",
                "details": {"completed": True},
                "error": None,
            }
        logger.info("Download all defaults worker completed successfully.")

    except Exception as exc:
        logger.error("Download all defaults worker failed: %s", exc)
        with _download_lock:
            _download_all_state = {
                "status": "failed",
                "progress": 0.0,
                "message": f"Download failed: {exc}",
                "details": {},
                "error": str(exc),
            }


@router.post("/download_all")
@router.post("/download_all_defaults")
async def download_all_defaults(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Triggers background download worker for all default offline models and vendor assets."""
    with _download_lock:
        if _download_all_state.get("status") == "running":
            return {
                "status": "in_progress",
                "message": "Download process is already running.",
                "info": dict(_download_all_state),
            }

    background_tasks.add_task(_download_all_defaults_worker)
    return {
        "status": "started",
        "message": "Background installation started for all required offline models and assets.",
    }


@router.get("/download_all_status")
async def get_download_all_status() -> Dict[str, Any]:
    """Polls progress of the complete offline model download worker."""
    with _download_lock:
        return dict(_download_all_state)

_index_lock = threading.Lock()
_indexing_state: Dict[str, Any] = {
    "status": "idle",
    "progress": 0.0,
    "current_stage": "",
    "message": "",
    "details": {},
    "error": None,
    "started_at": 0.0,
    "finished_at": 0.0,
}


# -----------------------------------------------------------------------------
# Pydantic Schemas
# -----------------------------------------------------------------------------
class ModelDownloadRequest(BaseModel):
    if _MODEL_CONFIG is not None:
        model_config = _MODEL_CONFIG
    else:

        class Config:
            extra = "allow"

    model_type: Optional[str] = "embedding"
    model_name: Optional[str] = None
    model_id: Optional[str] = None

    def get_model_type(self) -> str:
        return (self.model_type or "embedding").strip().lower()

    def get_model_name(self) -> str:
        return (self.model_name or self.model_id or "").strip()


class SetActiveModelRequest(BaseModel):
    if _MODEL_CONFIG is not None:
        model_config = _MODEL_CONFIG
    else:

        class Config:
            extra = "allow"

    model_type: str = Field(..., description="'embedding', 'flashrank', or 'scispacy'")
    model_id: Optional[str] = None
    model_name: Optional[str] = None

    def get_model_id(self) -> str:
        return (self.model_id or self.model_name or "").strip()


class BuildIndexRequest(BaseModel):
    if _MODEL_CONFIG is not None:
        model_config = _MODEL_CONFIG
    else:

        class Config:
            extra = "allow"

    kbs: Optional[List[str]] = None
    target_kbs: Optional[List[str]] = None
    selected_ontologies: Optional[List[str]] = None
    ontologies: Optional[List[str]] = None
    force_rebuild: Optional[bool] = False
    force: Optional[bool] = False

    def get_kbs(self) -> Optional[List[str]]:
        return (
            self.kbs
            or self.target_kbs
            or self.selected_ontologies
            or self.ontologies
        )

    def get_force_rebuild(self) -> bool:
        return bool(self.force_rebuild or self.force)


class TestEmbedPayload(BaseModel):
    text: str = Field(..., description="Text input to embed")


class TestRerankPayload(BaseModel):
    query: str = Field(..., description="Search query")
    passages: List[str] = Field(..., description="List of passages to rerank")


# -----------------------------------------------------------------------------
# Helper Functions: Inspection & Diagnostics
# -----------------------------------------------------------------------------
def _is_embedding_downloaded(model_id: str) -> bool:
    try:
        from parsers.embedding_engine import is_embedding_model_downloaded

        return bool(is_embedding_model_downloaded(model_id))
    except Exception:
        hf_dir = Path(HF_CACHE_DIR)
        if not hf_dir.exists():
            return False
        clean_name = "--".join(["models"] + model_id.split("/"))
        target_dir = hf_dir / clean_name
        return target_dir.exists() and any(target_dir.iterdir())


def _is_flashrank_downloaded(model_name: str) -> bool:
    try:
        from parsers.reranker import is_flashrank_model_downloaded

        return bool(is_flashrank_model_downloaded(model_name))
    except Exception:
        fr_dir = Path(FLASHRANK_CACHE_DIR)
        target = fr_dir / model_name
        return target.exists() and any(target.iterdir())


def _is_scispacy_installed(model_name: str) -> bool:
    """Checks whether the SciSpaCy pipeline is accessible as a package or local directory."""
    try:
        from parsers.scispacy_linker_engine import is_scispacy_model_downloaded

        return bool(is_scispacy_model_downloaded(model_name))
    except Exception:
        pass

    if importlib.util.find_spec(model_name) is not None:
        return True

    extracted_path = SCISPACY_MODELS_DIR / model_name
    if extracted_path.exists() and (extracted_path / "config.cfg").exists():
        return True

    for nested in SCISPACY_MODELS_DIR.glob(f"{model_name}*"):
        if nested.is_dir() and (nested / "config.cfg").exists():
            return True
        inner = nested / model_name
        if inner.is_dir() and (inner / "config.cfg").exists():
            return True

    return False


def _find_sumatra_executable() -> Optional[Path]:
    """Scans Windows standard installation folders and PATH for SumatraPDF binary (Invariant 6)."""
    paths_to_check = [
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "SumatraPDF"
        / "SumatraPDF.exe",
        Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
        / "SumatraPDF"
        / "SumatraPDF.exe",
        Path(
            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        )
        / "SumatraPDF"
        / "SumatraPDF.exe",
    ]
    for p in paths_to_check:
        if p.exists() and p.is_file():
            return p

    which_path = (
        shutil.which("SumatraPDF.exe")
        or shutil.which("SumatraPDF")
        or shutil.which("sumatrapdf")
    )
    return Path(which_path) if which_path else None


def _inspect_ontology_sources() -> Dict[str, Any]:
    """Inspects LINKERS_DIR and KBS_DIR for UMLS, MeSH, RxNorm, HPO, and GO definitions."""
    try:
        from parsers.scispacy_linker_engine import get_ontology_files_status

        status_data = get_ontology_files_status()
        if isinstance(status_data, dict) and "ontologies" in status_data:
            return status_data["ontologies"]
    except Exception:
        pass

    results: Dict[str, Any] = {}
    kbs = ["umls", "mesh", "rxnorm", "hpo", "go"]
    kbs_dir = KBS_DIR if KBS_DIR.exists() else LINKERS_DIR / "kbs"

    for kb in kbs:
        candidates = [
            LINKERS_DIR / kb,
            LINKERS_DIR / "2023-04-23" / kb,
            LINKERS_DIR / "data" / "linkers" / "2023-04-23" / kb,
        ]
        found_dir: Optional[Path] = None
        for cand in candidates:
            if cand.exists() and cand.is_dir():
                found_dir = cand
                break

        has_aliases = False
        has_tfidf_joblib = False
        has_tfidf_npz = False
        has_nmslib = False
        folder_size_bytes = 0

        if found_dir:
            has_aliases = (found_dir / "concept_aliases.json").exists()
            has_tfidf_joblib = (found_dir / "tfidf_vectorizer.joblib").exists()
            has_tfidf_npz = (found_dir / "tfidf_vectors_sparse.npz").exists()
            has_nmslib = (found_dir / "nmslib_index.bin").exists()
            try:
                folder_size_bytes = sum(
                    f.stat().st_size
                    for f in found_dir.glob("**/*")
                    if f.is_file()
                )
            except Exception:
                folder_size_bytes = 0

        jsonl_candidates = [
            kbs_dir / f"umls_{kb}_2022.jsonl",
            kbs_dir / f"{kb}.jsonl",
            kbs_dir / "umls_2022_ab_cat0129.jsonl" if kb == "umls" else None,
            LINKERS_DIR / f"{kb}.jsonl",
        ]
        found_jsonl: Optional[Path] = None
        for jc in jsonl_candidates:
            if jc and jc.exists() and jc.is_file():
                found_jsonl = jc
                try:
                    folder_size_bytes += jc.stat().st_size
                except Exception:
                    pass
                break

        results[kb] = {
            "name": kb.upper(),
            "detected": bool(found_dir is not None or found_jsonl is not None),
            "folder_path": str(found_dir) if found_dir else None,
            "has_concept_aliases": has_aliases,
            "has_tfidf_vectorizer": has_tfidf_joblib,
            "has_sparse_matrix": has_tfidf_npz,
            "has_nmslib_index": has_nmslib,
            "jsonl_path": str(found_jsonl) if found_jsonl else None,
            "size_mb": round(folder_size_bytes / (1024 * 1024), 2),
        }

    tui_path = kbs_dir / "umls_semantic_type_tree.tsv"
    if not tui_path.exists():
        tui_path = LINKERS_DIR / "umls_semantic_type_tree.tsv"
    results["semantic_type_tree"] = {
        "detected": tui_path.exists(),
        "path": str(tui_path) if tui_path.exists() else None,
    }

    return results


def _inspect_ontology_db() -> Dict[str, Any]:
    """Inspects the local SQLite database backing compiled ontology aliases."""
    if not ONTOLOGY_DB_PATH.exists():
        return {
            "exists": False,
            "path": str(ONTOLOGY_DB_PATH),
            "concepts_count": 0,
            "aliases_count": 0,
            "size_mb": 0.0,
        }

    size_mb = round(ONTOLOGY_DB_PATH.stat().st_size / (1024 * 1024), 2)
    concepts_count = 0
    aliases_count = 0

    try:
        with closing(
            sqlite3.connect(str(ONTOLOGY_DB_PATH), timeout=5.0)
        ) as conn:
            conn.execute("PRAGMA query_only = ON;")
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='kb_concepts'"
            )
            if cursor.fetchone():
                cursor.execute("SELECT count(*) FROM kb_concepts")
                concepts_count = int(cursor.fetchone()[0])

            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='kb_aliases'"
            )
            if cursor.fetchone():
                cursor.execute("SELECT count(*) FROM kb_aliases")
                aliases_count = int(cursor.fetchone()[0])
    except Exception as exc:
        logger.warning("Failed to read ontology_kb.db stats: %s", exc)

    return {
        "exists": True,
        "path": str(ONTOLOGY_DB_PATH),
        "concepts_count": concepts_count,
        "aliases_count": aliases_count,
        "size_mb": size_mb,
    }


# -----------------------------------------------------------------------------
# Background Worker: SciSpaCy Downloader & Installer
# -----------------------------------------------------------------------------
def _download_and_extract_scispacy_worker(model_meta: Dict[str, Any]) -> None:
    model_id = model_meta["id"]

    with _download_lock:
        _active_downloads[model_id] = {
            "status": "downloading",
            "progress": 10.0,
            "message": f"Downloading {model_id} archive...",
        }

    try:
        from parsers.scispacy_linker_engine import download_scispacy_model

        res = download_scispacy_model(model_id)

        if res.get("status") == "success" or res.get("downloaded"):
            with _download_lock:
                _active_downloads[model_id] = {
                    "status": "completed",
                    "progress": 100.0,
                    "message": f"{model_id} successfully installed and ready for offline inference.",
                }
            logger.info(
                "SciSpaCy model %s installed successfully via scispacy_linker_engine",
                model_id,
            )
            return
        else:
            raise RuntimeError(
                res.get("message", "Model download returned non-success status.")
            )

    except Exception as eng_exc:
        logger.warning(
            "Engine download failed for %s, attempting direct fallback: %s",
            model_id,
            eng_exc,
        )

    url = model_meta.get("url")
    if not url:
        with _download_lock:
            _active_downloads[model_id] = {
                "status": "failed",
                "progress": 0.0,
                "message": f"No download URL available for {model_id}.",
                "error": "Missing URL",
            }
        return

    tar_path = SCISPACY_MODELS_DIR / f"{model_id}.tar.gz"
    extract_target = SCISPACY_MODELS_DIR / model_id

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "MedisearchAI-Downloader/1.0"}
        )
        with urllib.request.urlopen(req, timeout=300) as response, open(
            tar_path, "wb"
        ) as out_file:
            total_bytes = int(response.headers.get("Content-Length", 0))
            bytes_read = 0
            block_size = 128 * 1024

            while True:
                chunk = response.read(block_size)
                if not chunk:
                    break
                out_file.write(chunk)
                bytes_read += len(chunk)
                if total_bytes > 0:
                    pct = round((bytes_read / total_bytes) * 100, 1)
                    with _download_lock:
                        _active_downloads[model_id].update({
                            "progress": min(95.0, pct),
                            "bytes_read": bytes_read,
                            "total_bytes": total_bytes,
                            "message": f"Downloading {model_id} ({pct}%)...",
                        })

        with _download_lock:
            _active_downloads[model_id].update({
                "status": "extracting",
                "progress": 96.0,
                "message": f"Extracting {model_id} archive...",
            })

        extract_target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path, "r:gz") as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(SCISPACY_MODELS_DIR, filter="data")
            else:
                tar.extractall(SCISPACY_MODELS_DIR)

        found_config: Optional[Path] = None
        for cfg in SCISPACY_MODELS_DIR.glob(f"{model_id}*/config.cfg"):
            found_config = cfg.parent
            break
        if not found_config:
            for cfg in SCISPACY_MODELS_DIR.glob(f"**/{model_id}*/config.cfg"):
                found_config = cfg.parent
                break

        if found_config and found_config != extract_target:
            if extract_target.exists():
                shutil.rmtree(extract_target)
            shutil.copytree(found_config, extract_target)

        if tar_path.exists():
            try:
                tar_path.unlink()
            except Exception:
                pass

        with _download_lock:
            _active_downloads[model_id] = {
                "status": "completed",
                "progress": 100.0,
                "message": f"{model_id} successfully installed and ready for offline inference.",
            }

    except Exception as exc:
        logger.error(
            "Failed to install SciSpaCy model %s: %s", model_id, exc, exc_info=True
        )
        if tar_path.exists():
            try:
                tar_path.unlink()
            except Exception:
                pass
        with _download_lock:
            _active_downloads[model_id] = {
                "status": "failed",
                "progress": 0.0,
                "message": f"Installation failed: {str(exc)}",
                "error": str(exc),
            }


# -----------------------------------------------------------------------------
# Background Worker: Streaming SQLite Ontology Index Compiler
# -----------------------------------------------------------------------------
def _build_ontology_database_worker(
    target_kbs: Optional[List[str]], force_rebuild: bool
) -> None:
    """Streams concept_aliases.json and .jsonl into ontology_kb.db in low-RAM batches."""
    global _indexing_state
    start_time = time.time()

    with _index_lock:
        _indexing_state = {
            "status": "running",
            "progress": 0.0,
            "current_stage": "Initializing SQLite Schema",
            "message": "Preparing ontology database tables...",
            "details": {},
            "error": None,
            "started_at": start_time,
            "finished_at": 0.0,
        }

    def _progress_cb(*args: Any, **kwargs: Any) -> None:
        pct = 0.0
        stage = "Indexing"
        msg = "Processing ontologies..."

        if len(args) == 1:
            if isinstance(args[0], (int, float)):
                pct = float(args[0])
            elif isinstance(args[0], str):
                msg = str(args[0])
        elif len(args) == 2:
            pct = float(args[0]) if isinstance(args[0], (int, float)) else 0.0
            msg = str(args[1])
        elif len(args) >= 3:
            pct = float(args[0]) if isinstance(args[0], (int, float)) else 0.0
            stage = str(args[1])
            msg = str(args[2])

        pct = kwargs.get("pct", kwargs.get("progress", pct))
        stage = kwargs.get("stage", stage)
        msg = kwargs.get("msg", kwargs.get("message", msg))

        with _index_lock:
            _indexing_state.update({
                "progress": round(min(100.0, max(0.0, float(pct))), 1),
                "current_stage": str(stage),
                "message": str(msg),
            })

    try:
        import parsers.scispacy_linker_engine as linker_module

        engine_builder = getattr(
            linker_module, "build_offline_ontology_index", None
        ) or getattr(linker_module, "build_ontology_database", None)

        if engine_builder is not None:
            logger.info(
                "Delegating ontology indexing to parsers.scispacy_linker_engine"
            )
            res = engine_builder(
                selected_ontologies=target_kbs,
                target_kbs=target_kbs,
                force_rebuild=force_rebuild,
                progress_callback=_progress_cb,
            )

            with _index_lock:
                _indexing_state = {
                    "status": "completed",
                    "progress": 100.0,
                    "current_stage": "Complete",
                    "message": "Ontology database index built successfully.",
                    "details": res
                    if isinstance(res, dict)
                    else ({"result": res} if res is not None else {}),
                    "error": None,
                    "started_at": start_time,
                    "finished_at": time.time(),
                }
            return

    except Exception as exc:
        logger.error("Ontology index build failed: %s", exc, exc_info=True)
        with _index_lock:
            _indexing_state = {
                "status": "failed",
                "progress": 0.0,
                "current_stage": "Error",
                "message": f"Index build failed: {str(exc)}",
                "details": {},
                "error": str(exc),
                "started_at": start_time,
                "finished_at": time.time(),
            }


# =============================================================================
# Router Endpoints
# =============================================================================

@router.get("/cpu_embeddings")
@router.post("/cpu_embeddings")
@router.get("/cpu_embedding_models")
@router.post("/cpu_embedding_models")
async def get_cpu_embedding_models() -> Dict[str, Any]:
    """Lists available CPU embedding models, disk caching status, and active model selection."""
    try:
        from parsers.embedding_engine import get_configured_embedding_model

        active_model = str(get_configured_embedding_model(force_refresh=True) or DEFAULT_EMBEDDING_MODEL)
    except Exception:
        active_model = str(DEFAULT_EMBEDDING_MODEL)

    models = []
    for m in SUPPORTED_EMBEDDING_MODELS:
        m_id = str(m["id"])
        is_dl = _is_embedding_downloaded(m_id)
        is_active = m_id.lower() == active_model.lower()
        models.append({
            "id": m_id,
            "name": str(m["name"]),
            "dimension": int(m["dimension"]),
            "size_mb": int(m["size_mb"]),
            "downloaded": is_dl,
            "default": bool(m.get("default")),
            "active": is_active,
        })

    return {"models": models, "active": active_model}


@router.get("/flashrank_models")
@router.post("/flashrank_models")
async def get_flashrank_models() -> Dict[str, Any]:
    """Lists available FlashRank cross-encoder reranking models and download status."""
    try:
        from parsers.reranker import get_configured_flashrank_model

        active_model = str(get_configured_flashrank_model(force_refresh=True) or DEFAULT_FLASHRANK_MODEL)
    except Exception:
        active_model = str(DEFAULT_FLASHRANK_MODEL)

    models = []
    for m in SUPPORTED_FLASHRANK_MODELS:
        m_id = str(m["id"])
        is_dl = _is_flashrank_downloaded(m_id)
        is_active = m_id.lower() == active_model.lower()
        models.append({
            "id": m_id,
            "name": str(m["name"]),
            "size_mb": int(m["size_mb"]),
            "downloaded": is_dl,
            "default": bool(m.get("default")),
            "active": is_active,
        })

    return {"models": models, "active": active_model}


@router.get("/scispacy_models")
@router.post("/scispacy_models")
async def get_scispacy_models() -> Dict[str, Any]:
    """Lists SciSpaCy biomedical NER models, RAM footprints, and installation status."""
    try:
        from parsers.scispacy_linker_engine import get_configured_scispacy_model

        active_pipeline = str(get_configured_scispacy_model(force_refresh=True) or "en_core_sci_md")
    except Exception:
        active_pipeline = "en_core_sci_md"

    results = []
    for m in SUPPORTED_SCISPACY_MODELS:
        model_id = str(m["id"])
        installed = _is_scispacy_installed(model_id)

        with _download_lock:
            dl_info = _active_downloads.get(model_id, {})

        results.append({
            "id": model_id,
            "name": str(m["name"]),
            "version": str(m["version"]),
            "size_mb": int(m["size_mb"]),
            "ram_mb": int(m["ram_mb"]),
            "description": str(m["description"]),
            "recommended": bool(m["recommended"]),
            "installed": installed,
            "download_status": str(
                dl_info.get(
                    "status", "installed" if installed else "available"
                )
            ),
            "progress": float(dl_info.get("progress", 100.0 if installed else 0.0)),
            "message": str(
                dl_info.get(
                    "message",
                    "Ready for offline extraction"
                    if installed
                    else "Available to download",
                )
            ),
        })

    return {"models": results, "active": active_pipeline}


@router.post("/set_active")
@router.post("/select")
async def set_active_model(payload: SetActiveModelRequest) -> Dict[str, Any]:
    """Activates an offline downloaded embedding, FlashRank, or SciSpaCy model."""
    m_type = (payload.model_type or "").strip().lower()
    m_id = payload.get_model_id()

    if not m_id:
        raise HTTPException(
            status_code=400, detail="Missing required 'model_id' or 'model_name'."
        )

    if m_type == "embedding":
        from parsers.embedding_engine import set_configured_embedding_model
        active = await asyncio.to_thread(set_configured_embedding_model, m_id)
        return {"status": "success", "model_type": m_type, "active": active}
    elif m_type == "flashrank":
        from parsers.reranker import set_configured_flashrank_model
        active = await asyncio.to_thread(set_configured_flashrank_model, m_id)
        return {"status": "success", "model_type": m_type, "active": active}
    elif m_type == "scispacy":
        from parsers.scispacy_linker_engine import set_configured_scispacy_model
        active = await asyncio.to_thread(set_configured_scispacy_model, m_id)
        return {"status": "success", "model_type": m_type, "active": active}
    else:
        raise HTTPException(
            status_code=400, detail=f"Invalid model_type '{m_type}'"
        )


@router.post("/download")
async def download_model(
    request: ModelDownloadRequest, background_tasks: BackgroundTasks
) -> Dict[str, Any]:
    """Downloads embedding, FlashRank, or SciSpaCy models with zero VRAM and thread-safe file handling."""
    m_type = request.get_model_type()
    m_name = request.get_model_name()

    if not m_name:
        raise HTTPException(
            status_code=400,
            detail="Missing required 'model_name' or 'model_id' parameter.",
        )

    if m_type == "embedding":
        try:
            from parsers.embedding_engine import download_embedding_model

            res = await asyncio.to_thread(download_embedding_model, m_name)
            return {
                "status": "success",
                "message": f"Embedding model {m_name} downloaded successfully.",
                "result": res,
            }
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed downloading embedding model: {exc}",
            )

    elif m_type == "flashrank":
        try:
            from parsers.reranker import download_flashrank_model

            res = await asyncio.to_thread(download_flashrank_model, m_name)
            return {
                "status": "success",
                "message": f"FlashRank model {m_name} downloaded successfully.",
                "result": res,
            }
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed downloading FlashRank model: {exc}",
            )

    elif m_type == "scispacy":
        meta = next(
            (m for m in SUPPORTED_SCISPACY_MODELS if m["id"] == m_name), None
        )
        if not meta and m_name in SCISPACY_MODEL_CATALOG:
            cat = SCISPACY_MODEL_CATALOG[m_name]
            meta = {
                "id": m_name,
                "name": m_name,
                "version": cat.get("version", "0.5.4"),
                "url": cat.get("url"),
                "size_mb": 100,
                "ram_mb": 150,
                "description": "Biomedical NER pipeline",
                "recommended": False,
            }

        if not meta:
            raise HTTPException(
                status_code=400, detail=f"Unknown SciSpaCy model '{m_name}'"
            )

        with _download_lock:
            current = _active_downloads.get(m_name, {})
            if current.get("status") in ("downloading", "extracting"):
                return {
                    "status": "in_progress",
                    "message": f"{m_name} download is already underway.",
                    "info": current,
                }

        background_tasks.add_task(_download_and_extract_scispacy_worker, meta)
        return {
            "status": "started",
            "message": f"Background installation started for {m_name}.",
        }

    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model_type '{m_type}'. Must be 'embedding', 'flashrank', or 'scispacy'.",
        )


@router.post("/scispacy/download")
async def download_scispacy_model_direct(
    request: ModelDownloadRequest, background_tasks: BackgroundTasks
) -> Dict[str, Any]:
    """Dedicated alias endpoint for SciSpaCy biomedical model downloads."""
    request.model_type = "scispacy"
    return await download_model(request, background_tasks)


@router.get("/scispacy/download_status")
async def get_scispacy_download_status(
    model_name: str = Query(..., description="SciSpaCy model ID")
) -> Dict[str, Any]:
    """Polls real-time download and extraction progress for a SciSpaCy model."""
    with _download_lock:
        info = _active_downloads.get(model_name)
        if not info:
            installed = _is_scispacy_installed(model_name)
            return {
                "model_name": model_name,
                "status": "installed" if installed else "idle",
                "progress": 100.0 if installed else 0.0,
                "message": "Ready" if installed else "Not downloaded",
            }
        return {"model_name": model_name, **info}


@router.get("/linkers/status")
async def get_linkers_status() -> Dict[str, Any]:
    """Reports status of raw offline ontology files and compiled SQLite B-Tree index."""
    sources = await asyncio.to_thread(_inspect_ontology_sources)
    db_info = await asyncio.to_thread(_inspect_ontology_db)

    total_detected = sum(
        1
        for k in ["umls", "mesh", "rxnorm", "hpo", "go"]
        if sources.get(k, {}).get("detected")
    )
    has_compiled_index = bool(
        db_info.get("exists") and db_info.get("concepts_count", 0) > 0
    )

    return {
        "status": "success",
        "linkers_dir": str(LINKERS_DIR),
        "total_ontologies_detected": total_detected,
        "ontologies": sources,
        "database": db_info,
        "index_ready": has_compiled_index,
    }


@router.post("/linkers/build")
@router.post("/linkers/build_index")
async def build_linkers_index(
    request: BuildIndexRequest, background_tasks: BackgroundTasks
) -> Dict[str, Any]:
    """Compiles offline ontology definitions and aliases into ontology_kb.db in background."""
    with _index_lock:
        if _indexing_state.get("status") == "running":
            return {
                "status": "in_progress",
                "message": "Ontology indexing is already running.",
                "state": dict(_indexing_state),
            }

    target_kbs = request.get_kbs()
    force_rebuild = request.get_force_rebuild()

    background_tasks.add_task(
        _build_ontology_database_worker, target_kbs, force_rebuild
    )
    return {
        "status": "started",
        "message": "Offline ontology compilation started in background.",
    }


@router.get("/linkers/build_progress")
async def get_linkers_build_progress() -> Dict[str, Any]:
    """Returns real-time progress percentage and current compilation stage for ontology_kb.db."""
    with _index_lock:
        return dict(_indexing_state)


# -----------------------------------------------------------------------------
# System Diagnostics Endpoints
# -----------------------------------------------------------------------------
@router.get("/diagnostic/lm_studio")
@router.get("/diagnostics/lm_studio")
async def diagnostic_lm_studio() -> Dict[str, Any]:
    """Probes local LM Studio HTTP server endpoint without blocking the async event loop."""
    lm_url = str(LM_STUDIO_URL or "http://127.0.0.1:1234/v1")
    models_url = f"{lm_url.rstrip('/')}/models" if lm_url.endswith("/v1") else f"{lm_url.rstrip('/')}/v1/models"

    def _probe_sync() -> Dict[str, Any]:
        start_t = time.perf_counter()
        req = urllib.request.Request(
            models_url,
            headers={"User-Agent": "MedisearchAI-Diagnostic/1.0"},
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed_ms = round((time.perf_counter() - start_t) * 1000, 1)
            loaded_models = [
                str(m.get("id"))
                for m in data.get("data", [])
                if isinstance(m, dict) and m.get("id")
            ]
            return {
                "status": "ok",
                "service": "lm_studio",
                "endpoint": models_url,
                "latency_ms": elapsed_ms,
                "models_count": len(loaded_models),
                "loaded_models": loaded_models,
                "active_model": loaded_models[0] if loaded_models else None,
                "online": True,
            }

    try:
        return await asyncio.to_thread(_probe_sync)
    except Exception as exc:
        return {
            "status": "offline",
            "service": "lm_studio",
            "endpoint": models_url,
            "latency_ms": None,
            "error": str(exc),
            "online": False,
            "recommendation": "Start LM Studio, load a GGUF model, and start the Local Server on port 1234.",
        }


@router.get("/diagnostic/db")
@router.get("/diagnostics/db")
async def diagnostic_database() -> Dict[str, Any]:
    """Inspects SQLite database existence, WAL journal file, and core table row counts."""
    db_path = Path(DB_PATH)
    if not db_path.exists():
        return {
            "status": "error",
            "service": "db",
            "path": str(db_path),
            "exists": False,
            "message": "Database file does not exist. Launch run.py to initialize schema.",
        }

    def _query_db_sync() -> Dict[str, Any]:
        counts = {}
        db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)
        wal_file = Path(f"{db_path}-wal")
        wal_size_kb = (
            round(wal_file.stat().st_size / 1024, 2)
            if wal_file.exists()
            else 0.0
        )

        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            tables = [
                "mcq_bank",
                "csv_questions",
                "task_queue",
                "quiz_sessions",
                "session_questions",
                "extracted_topics",
                "cached_syntheses",
                "knowledge_sources",
                "ignored_topics",
            ]
            for tbl in tables:
                try:
                    cursor.execute(f"SELECT count(*) FROM {tbl}")
                    row = cursor.fetchone()
                    counts[tbl] = int(row[0] if row else 0)
                except Exception:
                    counts[tbl] = -1

        return {
            "status": "ok",
            "service": "db",
            "path": str(db_path),
            "exists": True,
            "size_mb": db_size_mb,
            "wal_size_kb": wal_size_kb,
            "row_counts": counts,
        }

    try:
        return await asyncio.to_thread(_query_db_sync)
    except Exception as exc:
        return {
            "status": "error",
            "service": "db",
            "path": str(db_path),
            "error": str(exc),
        }


@router.get("/diagnostic/sumatrapdf")
@router.get("/diagnostics/sumatrapdf")
async def diagnostic_sumatrapdf() -> Dict[str, Any]:
    """Verifies SumatraPDF desktop executable discovery on Windows."""
    exe_path = await asyncio.to_thread(_find_sumatra_executable)
    if exe_path and exe_path.exists():
        return {
            "status": "ok",
            "service": "sumatrapdf",
            "installed": True,
            "path": str(exe_path),
        }
    return {
        "status": "missing",
        "service": "sumatrapdf",
        "installed": False,
        "path": None,
        "recommendation": "Install SumatraPDF to enable sub-second page jumping from clinical citations.",
    }


@router.get("/diagnostic/tantivy")
@router.get("/diagnostics/tantivy")
async def diagnostic_tantivy() -> Dict[str, Any]:
    """Inspects Tantivy inverted index segment files and indexed document count."""
    idx_path = Path(INDEX_PATH)
    if not idx_path.exists():
        return {
            "status": "empty",
            "service": "tantivy",
            "path": str(idx_path),
            "exists": False,
            "doc_count": 0,
        }

    def _inspect_tantivy_sync() -> Dict[str, Any]:
        meta_file = idx_path / "meta.json"
        doc_count = 0
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as mf:
                    meta = json.load(mf)
                    for seg in meta.get("segments", []):
                        doc_count += int(seg.get("max_doc", 0) or 0)
            except Exception:
                pass

        size_mb = round(
            sum(f.stat().st_size for f in idx_path.glob("**/*") if f.is_file())
            / (1024 * 1024),
            2,
        )
        return {
            "status": "ok" if doc_count > 0 else "empty",
            "service": "tantivy",
            "path": str(idx_path),
            "exists": True,
            "doc_count": doc_count,
            "size_mb": size_mb,
        }

    return await asyncio.to_thread(_inspect_tantivy_sync)


@router.get("/diagnostic/all")
@router.get("/diagnostics/all")
async def diagnostic_all() -> Dict[str, Any]:
    """Runs parallel offline health checks across LM Studio, SQLite, SumatraPDF, Tantivy, and Ontologies."""
    lm_task = diagnostic_lm_studio()
    db_task = diagnostic_database()
    sumatra_task = diagnostic_sumatrapdf()
    tantivy_task = diagnostic_tantivy()
    linkers_task = get_linkers_status()

    lm, db, sumatra, tantivy, linkers = await asyncio.gather(
        lm_task, db_task, sumatra_task, tantivy_task, linkers_task
    )

    return {
        "timestamp": time.time(),
        "lm_studio": lm,
        "database": db,
        "sumatrapdf": sumatra,
        "tantivy": tantivy,
        "linkers": linkers,
    }


# -----------------------------------------------------------------------------
# Latency & Feature Test Bench
# -----------------------------------------------------------------------------
@router.post("/test_embed")
async def test_embed_model(payload: TestEmbedPayload) -> Dict[str, Any]:
    """Generates CPU MiniLM embedding for input text using cached singleton and measures latency."""
    try:
        from parsers.embedding_engine import get_embedding_engine

        def _embed_worker() -> tuple[list[float], float]:
            embedder = get_embedding_engine()
            t0 = time.perf_counter()
            vec = embedder.embed_single(payload.text)
            t1 = time.perf_counter()
            return vec, round((t1 - t0) * 1000, 2)

        vector, elapsed_ms = await asyncio.to_thread(_embed_worker)

        return {
            "status": "success",
            "dimension": len(vector) if vector else 0,
            "sample_vector": vector[:5] if vector else [],
            "latency_ms": elapsed_ms,
            "vram_usage_mb": 0.0,
        }
    except Exception as exc:
        logger.error("test_embed_model failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/test_rerank")
async def test_rerank_model(payload: TestRerankPayload) -> Dict[str, Any]:
    """Reranks input passages against query adhering to Invariant 9 (text key contract)."""
    try:
        from parsers.reranker import rerank_chunks

        # Invariant 9: Explicit 'text' key is mandatory
        chunks = [
            {"text": p, "child_content": p, "id": str(i)}
            for i, p in enumerate(payload.passages)
        ]

        def _rerank_worker() -> tuple[list[dict], float]:
            t0 = time.perf_counter()
            reranked = rerank_chunks(payload.query, chunks, top_k=len(chunks))
            t1 = time.perf_counter()
            return reranked, round((t1 - t0) * 1000, 2)

        results, elapsed_ms = await asyncio.to_thread(_rerank_worker)

        return {
            "status": "success",
            "results": results,
            "latency_ms": elapsed_ms,
            "vram_usage_mb": 0.0,
        }
    except Exception as exc:
        logger.error("test_rerank_model failed: %s", exc)
        return {"status": "error", "message": str(exc)}