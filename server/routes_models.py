"""
server/routes_models.py - Hardware-Constrained Model Management, SciSpaCy Linkers & System Diagnostics

Exposes FastAPI endpoints for:
1. CPU Embedding and FlashRank model inspection and background offline downloads.
2. SciSpaCy biomedical NER model discovery, download, and offline packaging.
3. Offline ontology linkers (UMLS, MeSH, RxNorm, HPO, GO) detection and SQLite index compiler.
4. Comprehensive offline system diagnostics (LM Studio, SQLite WAL, SumatraPDF, Tantivy).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import threading
import time
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel, Field

import config
from core.database import get_db_connection

logger = logging.getLogger("medisearch.routes_models")

router = APIRouter(prefix="/api/models", tags=["models"])

# -----------------------------------------------------------------------------
# Directory Resolution & Directory Safeguards
# -----------------------------------------------------------------------------
DATA_DIR: Path = Path(getattr(config, "DATA_DIR", config.BASE_DIR / "data"))
MODELS_DIR: Path = Path(getattr(config, "MODELS_DIR", DATA_DIR / "models"))
LINKERS_DIR: Path = Path(getattr(config, "LINKERS_DIR", DATA_DIR / "linkers"))
SCISPACY_MODELS_DIR: Path = Path(getattr(config, "SCISPACY_MODELS_DIR", MODELS_DIR / "scispacy"))
ONTOLOGY_DB_PATH: Path = LINKERS_DIR / "ontology_kb.db"

# Ensure runtime directories exist
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
        "id": "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
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

_index_lock = threading.Lock()
_indexing_state: Dict[str, Any] = {
    "status": "idle",  # "idle" | "running" | "completed" | "failed"
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
    model_type: str = Field(..., description="'embedding', 'flashrank', or 'scispacy'")
    model_name: str = Field(..., description="Target model ID or package name")


class BuildIndexRequest(BaseModel):
    kbs: Optional[List[str]] = Field(
        default=None,
        description="List of ontologies to index: ['umls', 'mesh', 'rxnorm', 'hpo', 'go']. If empty, indexes all detected.",
    )
    force_rebuild: bool = Field(default=False, description="Whether to rebuild ontology_kb.db from scratch")


# -----------------------------------------------------------------------------
# Helper Functions: Inspection & Diagnostics
# -----------------------------------------------------------------------------
def _is_embedding_downloaded(model_id: str) -> bool:
    try:
        from parsers.embedding_engine import is_embedding_model_downloaded
        return bool(is_embedding_model_downloaded(model_id))
    except Exception:
        # Fallback inspection of huggingface hub directory
        hf_dir = Path(getattr(config, "HF_CACHE_DIR", DATA_DIR / "models" / "huggingface"))
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
        fr_dir = Path(getattr(config, "FLASHRANK_CACHE_DIR", DATA_DIR / "models" / "flashrank"))
        target = fr_dir / model_name
        return target.exists() and any(target.iterdir())


def _is_scispacy_installed(model_name: str) -> bool:
    """Checks whether the SciSpaCy pipeline is accessible as a package or local directory."""
    # 1. Python package spec check
    if importlib.util.find_spec(model_name) is not None:
        return True
    # 2. Local extracted folder check
    extracted_path = SCISPACY_MODELS_DIR / model_name
    if extracted_path.exists() and (extracted_path / "config.cfg").exists():
        return True
    # 3. Versioned nested folder check inside SCISPACY_MODELS_DIR
    for nested in SCISPACY_MODELS_DIR.glob(f"{model_name}*"):
        if nested.is_dir() and (nested / "config.cfg").exists():
            return True
        inner = nested / model_name
        if inner.is_dir() and (inner / "config.cfg").exists():
            return True
    return False


def _find_sumatra_executable() -> Optional[Path]:
    """Scans Windows standard installation folders and PATH for SumatraPDF.exe."""
    paths_to_check = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "SumatraPDF" / "SumatraPDF.exe",
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "SumatraPDF" / "SumatraPDF.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "SumatraPDF" / "SumatraPDF.exe",
    ]
    for p in paths_to_check:
        if p.exists() and p.is_file():
            return p
    which_path = shutil.which("SumatraPDF.exe") or shutil.which("sumatrapdf")
    return Path(which_path) if which_path else None


def _inspect_ontology_sources() -> Dict[str, Any]:
    """Inspects DATA_DIR / linkers for UMLS, MeSH, RxNorm, HPO, and GO folders and .jsonl files."""
    results: Dict[str, Any] = {}
    kbs = ["umls", "mesh", "rxnorm", "hpo", "go"]
    kbs_dir = LINKERS_DIR / "kbs"

    for kb in kbs:
        # Check standard and date-stamped folder variants
        candidates = [
            LINKERS_DIR / kb,
            LINKERS_DIR / "2023-04-23" / kb,
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
                folder_size_bytes = sum(f.stat().st_size for f in found_dir.glob("**/*") if f.is_file())
            except Exception:
                folder_size_bytes = 0

        # Check for matching JSONL definition file
        jsonl_candidates = [
            kbs_dir / f"umls_{kb}_2022.jsonl",
            kbs_dir / f"{kb}.jsonl",
            kbs_dir / f"umls_2022_ab_cat0129.jsonl" if kb == "umls" else None,
            LINKERS_DIR / f"{kb}.jsonl",
        ]
        found_jsonl: Optional[Path] = None
        for jc in jsonl_candidates:
            if jc and jc.exists() and jc.is_file():
                found_jsonl = jc
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

    # Semantic type tree check
    tui_path = kbs_dir / "umls_semantic_type_tree.tsv"
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
        with sqlite3.connect(f"file:{ONTOLOGY_DB_PATH}?mode=ro", uri=True, timeout=5.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='kb_concepts'")
            if cursor.fetchone():
                cursor.execute("SELECT count(*) FROM kb_concepts")
                concepts_count = cursor.fetchone()[0]

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='kb_aliases'")
            if cursor.fetchone():
                cursor.execute("SELECT count(*) FROM kb_aliases")
                aliases_count = cursor.fetchone()[0]
    except Exception as exc:
        logger.warning(f"Failed to read ontology_kb.db stats: {exc}")

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
    url = model_meta["url"]
    tar_path = SCISPACY_MODELS_DIR / f"{model_id}.tar.gz"
    extract_target = SCISPACY_MODELS_DIR / model_id

    try:
        with _download_lock:
            _active_downloads[model_id] = {
                "status": "downloading",
                "progress": 0.0,
                "message": f"Downloading {model_id} archive...",
                "bytes_read": 0,
                "total_bytes": 0,
            }

        logger.info(f"Starting SciSpaCy model download from {url} to {tar_path}")

        # Stream download with progress tracking
        req = urllib.request.Request(url, headers={"User-Agent": "MedisearchAI-Downloader/1.0"})
        with urllib.request.urlopen(req, timeout=300) as response, open(tar_path, "wb") as out_file:
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

        logger.info(f"Extracting {tar_path} into {SCISPACY_MODELS_DIR}")
        extract_target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path, "r:gz") as tar:
            # Safe member extraction on Python 3.11+
            if hasattr(tarfile, "data_filter"):
                tar.extractall(SCISPACY_MODELS_DIR, filter="data")
            else:
                tar.extractall(SCISPACY_MODELS_DIR)

        # Locate extracted config.cfg and ensure normalized folder path exists
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

        # Clean archive to conserve Windows disk budget
        if tar_path.exists():
            try:
                tar_path.unlink()
            except Exception:
                pass

        with _download_lock:
            _active_downloads[model_id].update({
                "status": "completed",
                "progress": 100.0,
                "message": f"{model_id} successfully installed and ready for offline inference.",
            })
        logger.info(f"SciSpaCy model {model_id} successfully installed")

    except Exception as exc:
        logger.error(f"Failed to download/install SciSpaCy model {model_id}: {exc}", exc_info=True)
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
def _build_ontology_database_worker(target_kbs: Optional[List[str]], force_rebuild: bool) -> None:
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

    try:
        # Delegate to specialized engine if available
        try:
            from parsers.scispacy_linker_engine import build_offline_ontology_index as engine_builder
            logger.info("Delegating ontology indexing to parsers.scispacy_linker_engine.build_offline_ontology_index")

            def _progress_cb(pct: float, stage: str, msg: str):
                with _index_lock:
                    _indexing_state.update({
                        "progress": round(pct, 1),
                        "current_stage": stage,
                        "message": msg,
                    })

            res = engine_builder(target_kbs=target_kbs, force_rebuild=force_rebuild, progress_callback=_progress_cb)
            with _index_lock:
                _indexing_state.update({
                    "status": "completed",
                    "progress": 100.0,
                    "current_stage": "Complete",
                    "message": "Ontology database index built successfully.",
                    "details": res if isinstance(res, dict) else {},
                    "finished_at": time.time(),
                })
            return
        except (ImportError, AttributeError):
            logger.info("Using embedded streaming SQLite compiler for ontology linking")

        # Native Streaming Implementation
        if force_rebuild and ONTOLOGY_DB_PATH.exists():
            try:
                ONTOLOGY_DB_PATH.unlink()
            except Exception:
                pass

        conn = sqlite3.connect(ONTOLOGY_DB_PATH, timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA cache_size = -64000;")  # 64 MB RAM cache ceiling

        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS kb_concepts (
                cui TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                types TEXT DEFAULT '[]',
                definition TEXT DEFAULT '',
                kb_source TEXT NOT NULL
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS kb_aliases (
                alias_norm TEXT NOT NULL,
                cui TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                kb_source TEXT NOT NULL,
                PRIMARY KEY (alias_norm, cui)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_kb_aliases_lookup ON kb_aliases(alias_norm);")
        conn.commit()

        sources = _inspect_ontology_sources()
        kbs_to_process = target_kbs or ["umls", "mesh", "rxnorm", "hpo", "go"]
        active_kbs = [kb for kb in kbs_to_process if sources.get(kb, {}).get("detected")]

        if not active_kbs:
            raise RuntimeError("No offline ontology files (UMLS, MeSH, RxNorm, HPO, GO) detected in data/linkers/")

        total_kbs = len(active_kbs)
        total_concepts_inserted = 0
        total_aliases_inserted = 0

        for idx, kb in enumerate(active_kbs):
            kb_info = sources[kb]
            pct_base = (idx / total_kbs) * 100
            pct_span = 100 / total_kbs

            with _index_lock:
                _indexing_state.update({
                    "progress": round(pct_base + 2.0, 1),
                    "current_stage": f"Processing {kb.upper()} Definitions",
                    "message": f"Reading knowledge base entries for {kb.upper()}...",
                })

            # Stage A: Load .jsonl definitions if available
            jsonl_file = kb_info.get("jsonl_path")
            concept_defs: Dict[str, Dict[str, Any]] = {}
            if jsonl_file and Path(jsonl_file).exists():
                batch_c = []
                with open(jsonl_file, "r", encoding="utf-8", errors="replace") as jf:
                    for line in jf:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            cui = record.get("concept_id") or record.get("cui")
                            canonical = record.get("canonical_name") or record.get("name") or cui
                            types = json.dumps(record.get("types") or [])
                            definition = record.get("definition") or ""
                            if cui and canonical:
                                batch_c.append((cui, canonical, types, definition, kb))
                                if len(batch_c) >= 5000:
                                    cursor.executemany("""
                                        INSERT OR REPLACE INTO kb_concepts (cui, canonical_name, types, definition, kb_source)
                                        VALUES (?, ?, ?, ?, ?)
                                    """, batch_c)
                                    conn.commit()
                                    total_concepts_inserted += len(batch_c)
                                    batch_c.clear()
                        except Exception:
                            continue
                if batch_c:
                    cursor.executemany("""
                        INSERT OR REPLACE INTO kb_concepts (cui, canonical_name, types, definition, kb_source)
                        VALUES (?, ?, ?, ?, ?)
                    """, batch_c)
                    conn.commit()
                    total_concepts_inserted += len(batch_c)

            # Stage B: Stream concept_aliases.json
            folder = kb_info.get("folder_path")
            aliases_file = Path(folder) / "concept_aliases.json" if folder else None

            if aliases_file and aliases_file.exists():
                with _index_lock:
                    _indexing_state.update({
                        "progress": round(pct_base + (pct_span * 0.4), 1),
                        "current_stage": f"Indexing {kb.upper()} Aliases",
                        "message": f"Streaming synonym aliases for {kb.upper()} into SQLite...",
                    })

                # Stream-read JSON in memory-conscious blocks
                with open(aliases_file, "r", encoding="utf-8", errors="replace") as af:
                    aliases_map = json.load(af)

                batch_a = []
                for cui, alias_list in aliases_map.items():
                    # Resolve canonical name from DB or use first alias
                    canonical_name = cui
                    cursor.execute("SELECT canonical_name FROM kb_concepts WHERE cui = ? LIMIT 1", (cui,))
                    row = cursor.fetchone()
                    if row and row[0]:
                        canonical_name = row[0]
                    elif alias_list:
                        canonical_name = alias_list[0]
                        # Seed minimal concept row
                        cursor.execute("""
                            INSERT OR IGNORE INTO kb_concepts (cui, canonical_name, types, definition, kb_source)
                            VALUES (?, ?, '[]', '', ?)
                        """, (cui, canonical_name, kb))

                    for alias in alias_list:
                        clean_alias = alias.strip().lower()
                        if 2 <= len(clean_alias) <= 150:
                            batch_a.append((clean_alias, cui, canonical_name, kb))
                            if len(batch_a) >= 10000:
                                cursor.executemany("""
                                    INSERT OR IGNORE INTO kb_aliases (alias_norm, cui, canonical_name, kb_source)
                                    VALUES (?, ?, ?, ?)
                                """, batch_a)
                                conn.commit()
                                total_aliases_inserted += len(batch_a)
                                batch_a.clear()

                if batch_a:
                    cursor.executemany("""
                        INSERT OR IGNORE INTO kb_aliases (alias_norm, cui, canonical_name, kb_source)
                        VALUES (?, ?, ?, ?)
                    """, batch_a)
                    conn.commit()
                    total_aliases_inserted += len(batch_a)

        # Optimize SQLite B-Trees
        with _index_lock:
            _indexing_state.update({
                "progress": 98.0,
                "current_stage": "Optimizing B-Trees",
                "message": "Finalizing indexes and compacting database...",
            })

        cursor.execute("PRAGMA optimize;")
        conn.commit()
        conn.close()

        elapsed = round(time.time() - start_time, 2)
        logger.info(f"Ontology index built successfully: {total_concepts_inserted} concepts, {total_aliases_inserted} aliases in {elapsed}s")

        with _index_lock:
            _indexing_state = {
                "status": "completed",
                "progress": 100.0,
                "current_stage": "Ready",
                "message": f"Successfully compiled {total_concepts_inserted:,} concepts and {total_aliases_inserted:,} aliases in {elapsed}s.",
                "details": {
                    "concepts_count": total_concepts_inserted,
                    "aliases_count": total_aliases_inserted,
                    "elapsed_seconds": elapsed,
                    "db_size_mb": round(ONTOLOGY_DB_PATH.stat().st_size / (1024 * 1024), 2),
                },
                "error": None,
                "started_at": start_time,
                "finished_at": time.time(),
            }

    except Exception as exc:
        logger.error(f"Ontology index build failed: {exc}", exc_info=True)
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

@router.get("/cpu_embedding_models")
async def get_cpu_embedding_models() -> Dict[str, Any]:
    """Returns available CPU ONNX embedding models and their offline download state."""
    active_model = getattr(config, "DEFAULT_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    models = []
    for m in SUPPORTED_EMBEDDING_MODELS:
        models.append({
            "id": m["id"],
            "name": m["name"],
            "dimension": m["dimension"],
            "size_mb": m["size_mb"],
            "downloaded": _is_embedding_downloaded(m["id"]),
            "default": m["default"],
        })
    return {"models": models, "active": active_model}


@router.get("/flashrank_models")
async def get_flashrank_models() -> Dict[str, Any]:
    """Returns available FlashRank cross-encoder reranker models and download states."""
    active_model = "ms-marco-TinyBERT-L-2-v2"
    models = []
    for m in SUPPORTED_FLASHRANK_MODELS:
        models.append({
            "id": m["id"],
            "name": m["name"],
            "size_mb": m["size_mb"],
            "downloaded": _is_flashrank_downloaded(m["id"]),
            "default": m["default"],
        })
    return {"models": models, "active": active_model}


@router.get("/scispacy_models")
async def get_scispacy_models() -> Dict[str, Any]:
    """Returns available AllenAI SciSpaCy biomedical NER pipelines and local installation status."""
    active_pipeline = "en_core_sci_md"
    results = []

    for m in SUPPORTED_SCISPACY_MODELS:
        model_id = m["id"]
        installed = _is_scispacy_installed(model_id)

        # Check in-progress download status
        with _download_lock:
            dl_info = _active_downloads.get(model_id, {})

        results.append({
            "id": model_id,
            "name": m["name"],
            "version": m["version"],
            "size_mb": m["size_mb"],
            "ram_mb": m["ram_mb"],
            "description": m["description"],
            "recommended": m["recommended"],
            "installed": installed,
            "download_status": dl_info.get("status", "installed" if installed else "available"),
            "progress": dl_info.get("progress", 100.0 if installed else 0.0),
            "message": dl_info.get("message", "Ready for offline extraction" if installed else "Available to download"),
        })

    return {"models": results, "active": active_pipeline}


@router.post("/download")
async def download_model(request: ModelDownloadRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Initiates an explicit offline download for an embedding, FlashRank, or SciSpaCy model."""
    m_type = request.model_type.strip().lower()
    m_name = request.model_name.strip()

    if m_type == "embedding":
        try:
            from parsers.embedding_engine import download_embedding_model
            # Schedule in threadpool to avoid blocking FastAPI event loop
            res = await asyncio.to_thread(download_embedding_model, m_name)
            return {"status": "success", "message": f"Embedding model {m_name} downloaded successfully.", "result": res}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed downloading embedding model: {exc}")

    elif m_type == "flashrank":
        try:
            from parsers.reranker import download_flashrank_model
            res = await asyncio.to_thread(download_flashrank_model, m_name)
            return {"status": "success", "message": f"FlashRank model {m_name} downloaded successfully.", "result": res}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed downloading FlashRank model: {exc}")

    elif m_type == "scispacy":
        meta = next((m for m in SUPPORTED_SCISPACY_MODELS if m["id"] == m_name), None)
        if not meta:
            raise HTTPException(status_code=400, detail=f"Unknown SciSpaCy model '{m_name}'")

        with _download_lock:
            current = _active_downloads.get(m_name, {})
            if current.get("status") in ("downloading", "extracting"):
                return {"status": "in_progress", "message": f"{m_name} download is already underway.", "info": current}

        background_tasks.add_task(_download_and_extract_scispacy_worker, meta)
        return {"status": "started", "message": f"Background installation started for {m_name}."}

    else:
        raise HTTPException(status_code=400, detail=f"Invalid model_type '{m_type}'. Must be 'embedding', 'flashrank', or 'scispacy'.")


@router.post("/scispacy/download")
async def download_scispacy_model_direct(request: ModelDownloadRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Dedicated convenience route to download and install a SciSpaCy pipeline."""
    request.model_type = "scispacy"
    return await download_model(request, background_tasks)


@router.get("/scispacy/download_status")
async def get_scispacy_download_status(model_name: str = Query(..., description="SciSpaCy model ID")) -> Dict[str, Any]:
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
    """Inspects local S3 linker folders, KB definition files, and compiled SQLite index."""
    sources = _inspect_ontology_sources()
    db_info = _inspect_ontology_db()

    total_detected = sum(1 for k in ["umls", "mesh", "rxnorm", "hpo", "go"] if sources.get(k, {}).get("detected"))
    has_compiled_index = db_info["exists"] and db_info["concepts_count"] > 0

    return {
        "status": "success",
        "linkers_dir": str(LINKERS_DIR),
        "total_ontologies_detected": total_detected,
        "ontologies": sources,
        "database": db_info,
        "index_ready": has_compiled_index,
    }


@router.post("/linkers/build_index")
async def build_linkers_index(request: BuildIndexRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Triggers the low-RAM streaming SQLite compiler across detected ontology folders."""
    with _index_lock:
        if _indexing_state.get("status") == "running":
            return {
                "status": "in_progress",
                "message": "Ontology indexing is already running.",
                "state": _indexing_state,
            }

    background_tasks.add_task(_build_ontology_database_worker, request.kbs, request.force_rebuild)
    return {
        "status": "started",
        "message": "Offline ontology compilation started in background.",
    }


@router.get("/linkers/build_progress")
async def get_linkers_build_progress() -> Dict[str, Any]:
    """Returns current indexing progress, stage percentage, and compiled record statistics."""
    with _index_lock:
        return dict(_indexing_state)


# -----------------------------------------------------------------------------
# System Diagnostics Endpoints
# -----------------------------------------------------------------------------
@router.get("/diagnostics/lm_studio")
async def diagnostic_lm_studio() -> Dict[str, Any]:
    """Inspects connectivity and loaded models on local LM Studio instance."""
    lm_url = getattr(config, "LM_STUDIO_URL", "http://127.0.0.1:1234/v1")
    models_url = f"{lm_url.rstrip('/')}/models"

    start_t = time.time()
    try:
        req = urllib.request.Request(models_url, headers={"User-Agent": "MedisearchAI-Diagnostic/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed_ms = round((time.time() - start_t) * 1000, 1)
            loaded_models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
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


@router.get("/diagnostics/db")
async def diagnostic_database() -> Dict[str, Any]:
    """Validates the SQLite database connection, table row counts, and WAL status."""
    db_path = Path(getattr(config, "DB_PATH", DATA_DIR / "mcq_hub.db"))
    if not db_path.exists():
        return {
            "status": "error",
            "service": "db",
            "path": str(db_path),
            "exists": False,
            "message": "Database file does not exist. Launch run.py to initialize schema.",
        }

    counts = {}
    wal_size_kb = 0.0
    db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)
    wal_file = Path(f"{db_path}-wal")
    if wal_file.exists():
        wal_size_kb = round(wal_file.stat().st_size / 1024, 2)

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for tbl in ["csv_questions", "quiz_sessions", "session_questions", "cached_syntheses", "knowledge_sources", "ignored_topics"]:
                try:
                    cursor.execute(f"SELECT count(*) FROM {tbl}")
                    counts[tbl] = cursor.fetchone()[0]
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
    except Exception as exc:
        return {
            "status": "error",
            "service": "db",
            "path": str(db_path),
            "error": str(exc),
        }


@router.get("/diagnostics/sumatrapdf")
async def diagnostic_sumatrapdf() -> Dict[str, Any]:
    """Verifies whether SumatraPDF is installed on Windows for fast native page navigation."""
    exe_path = _find_sumatra_executable()
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


@router.get("/diagnostics/tantivy")
async def diagnostic_tantivy() -> Dict[str, Any]:
    """Inspects Tantivy inverted index directory and document count."""
    idx_path = Path(getattr(config, "INDEX_PATH", DATA_DIR / "tantivy_index"))
    if not idx_path.exists():
        return {
            "status": "empty",
            "service": "tantivy",
            "path": str(idx_path),
            "exists": False,
            "doc_count": 0,
        }

    meta_file = idx_path / "meta.json"
    doc_count = 0
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
                for seg in meta.get("segments", []):
                    doc_count += seg.get("max_doc", 0)
        except Exception:
            pass

    size_mb = round(sum(f.stat().st_size for f in idx_path.glob("**/*") if f.is_file()) / (1024 * 1024), 2)
    return {
        "status": "ok" if doc_count > 0 else "empty",
        "service": "tantivy",
        "path": str(idx_path),
        "exists": True,
        "doc_count": doc_count,
        "size_mb": size_mb,
    }


@router.get("/diagnostics/all")
async def diagnostic_all() -> Dict[str, Any]:
    """Aggregates all system diagnostic indicators for the Settings Diagnostic Tab."""
    lm = await diagnostic_lm_studio()
    db = await diagnostic_database()
    sumatra = await diagnostic_sumatrapdf()
    tantivy = await diagnostic_tantivy()
    linkers = await get_linkers_status()

    return {
        "timestamp": time.time(),
        "lm_studio": lm,
        "database": db,
        "sumatrapdf": sumatra,
        "tantivy": tantivy,
        "linkers": linkers,
    }
