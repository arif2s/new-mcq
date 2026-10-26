"""
SciSpaCy NLP pipeline loader and TF-IDF offline UMLS/MeSH/RxNorm ontology linker.
Enforces zero VRAM usage, sub-millisecond SQLite B-Tree lookups, thread safety, and offline safety.
"""

from __future__ import annotations

import functools
import gc
import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import threading
import urllib.request
import zipfile
from collections import OrderedDict
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from config import (
    KBS_DIR,
    LINKERS_DIR,
    ONTOLOGY_DB_PATH,
    SCISPACY_MODEL_CATALOG,
    SCISPACY_MODELS_DIR,
)
from core.database import get_db_connection

logger = logging.getLogger(__name__)

# Thread synchronization locks
_PIPELINE_LOCK = threading.RLock()
_TFIDF_LOCK = threading.RLock()
_DB_CONN_LOCK = threading.RLock()
_CACHE_LOCK = threading.RLock()
_LOCAL = threading.local()
_ONTOLOGY_DB_EPOCH: int = 0
_CONFIGURED_SCISPACY_MODEL_CACHE: Optional[str] = None

# Pre-compiled regex for single-pass alias normalization
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# SQLite parameter batch limit (safe margin below SQLite's 999 / 32766 host parameter cap)
_SQLITE_BATCH_CHUNK_SIZE = 450

# Bounded thread-safe LRU cache shared between single-term and batch concept resolvers
_CONCEPT_CACHE_MAXSIZE = 16384
_CONCEPT_RESOLUTION_CACHE: "OrderedDict[Tuple[str, bool, float], Dict[str, Any]]" = OrderedDict()

# File discovery & directory size caches to avoid repeated rglob walks
_SUBFOLDER_CACHE: Dict[str, Path] = {}
_JSONL_PATH_CACHE: Dict[str, Path] = {}
_DIR_SIZE_CACHE: Dict[str, Tuple[float, int]] = {}

# Components not needed for biomedical NER extraction (saving ~65-75% CPU time)
_EXCLUDED_SPACY_PIPE_COMPONENTS: List[str] = [
    "tagger",
    "parser",
    "attribute_ruler",
    "lemmatizer",
]

# ============================================================================
# 1. CLINICAL UMLS SEMANTIC TYPE (TUI) TAXONOMY & FILTERING
# ============================================================================

CLINICAL_TUI_CATEGORIES: Dict[str, str] = {
    # Anatomy & Tissue & Body Substances
    "T017": "anatomy", "T018": "anatomy", "T021": "anatomy", "T022": "anatomy",
    "T023": "anatomy", "T024": "anatomy", "T025": "anatomy", "T026": "anatomy",
    "T029": "anatomy", "T030": "anatomy", "T031": "anatomy",
    # Microorganisms & Pathogens (Bacteria, Viruses, Fungi, Parasites)
    "T001": "microorganism", "T002": "microorganism", "T004": "microorganism",
    "T005": "microorganism", "T007": "microorganism", "T008": "microorganism",
    "T099": "microorganism", "T194": "microorganism", "T204": "microorganism",
    # Diseases, Syndromes, Subtypes, Congenital/Acquired Pathology & Neoplasms
    "T019": "disease", "T020": "disease", "T037": "disease", "T046": "disease",
    "T047": "disease", "T048": "disease", "T049": "disease", "T050": "disease",
    "T190": "disease", "T191": "disease",
    # Clinical Signs, Symptoms & Lab/Diagnostic Findings
    "T033": "sign_symptom", "T034": "sign_symptom", "T184": "sign_symptom",
    # Drugs, Pharmacology, Biochemicals & Surgical Materials
    "T103": "drug_chemical", "T104": "drug_chemical", "T109": "drug_chemical",
    "T114": "drug_chemical", "T116": "drug_chemical", "T121": "drug_chemical",
    "T122": "drug_chemical", "T123": "drug_chemical", "T125": "drug_chemical",
    "T126": "drug_chemical", "T127": "drug_chemical", "T129": "drug_chemical",
    "T130": "drug_chemical", "T131": "drug_chemical", "T192": "drug_chemical",
    "T195": "drug_chemical", "T196": "drug_chemical", "T197": "drug_chemical",
    "T200": "drug_chemical",
    # Diagnostic & Surgical Procedures / Devices
    "T059": "procedure", "T060": "procedure", "T061": "procedure", "T074": "procedure",
    # Physiology, Genetics & Molecular Function
    "T028": "physiology", "T038": "physiology", "T039": "physiology",
    "T040": "physiology", "T042": "physiology", "T043": "physiology",
    "T044": "physiology", "T045": "physiology",
}

BLOCKED_NON_MEDICAL_TUIS: Set[str] = {
    "T051", "T052", "T053", "T054", "T055", "T056", "T057", "T058",
    "T062", "T063", "T064", "T065", "T066", "T067", "T068", "T069",
    "T070", "T071", "T072", "T073", "T075", "T077", "T078", "T079",
    "T080", "T081", "T082", "T083", "T085", "T086", "T087", "T088",
    "T089", "T090", "T091", "T092", "T093", "T094", "T095", "T096",
    "T097", "T098", "T100", "T101", "T102", "T167", "T168", "T169",
    "T170", "T171", "T185",
}

ONTOLOGY_DEFS: Dict[str, Dict[str, Any]] = {
    "umls": {
        "name": "UMLS (Unified Medical Language System)",
        "jsonl_candidates": ["umls_2022_ab_cat0129.jsonl", "umls.jsonl"],
        "folder_candidates": ["umls"],
        "default_category": "clinical",
        "priority": 1,
    },
    "mesh": {
        "name": "MeSH (Medical Subject Headings — Anatomy, Organisms, Diseases)",
        "jsonl_candidates": ["umls_mesh_2022.jsonl", "mesh.jsonl"],
        "folder_candidates": ["mesh"],
        "default_category": "mesh",
        "priority": 2,
    },
    "rxnorm": {
        "name": "RxNorm (Clinical Drugs & Pharmacological Classes)",
        "jsonl_candidates": ["umls_rxnorm_2022.jsonl", "rxnorm.jsonl"],
        "folder_candidates": ["rxnorm"],
        "default_category": "drug_chemical",
        "priority": 3,
    },
    "hpo": {
        "name": "HPO (Human Phenotype Ontology — Signs & Syndrome Subtypes)",
        "jsonl_candidates": ["umls_hpo_2022.jsonl", "hpo.jsonl"],
        "folder_candidates": ["hpo"],
        "default_category": "sign_symptom",
        "priority": 4,
    },
    "go": {
        "name": "GO (Gene Ontology — Molecular & Cellular Pathways)",
        "jsonl_candidates": ["umls_go_2022.jsonl", "go.jsonl"],
        "folder_candidates": ["go"],
        "default_category": "physiology",
        "priority": 5,
    },
}


# ============================================================================
# 2. THREAD-LOCAL DATABASE CONNECTION MANAGEMENT (256 MB MMAP CAP)
# ============================================================================

def _create_configured_sqlite_conn() -> sqlite3.Connection:
    """Opens a new SQLite connection configured with WAL and 256 MB MMAP."""
    os.makedirs(ONTOLOGY_DB_PATH.parent, exist_ok=True)
    conn = sqlite3.connect(str(ONTOLOGY_DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 20000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA mmap_size = 268435456;")  # Strict 256 MB MMAP cap (Invariant 3)
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = -32000;")  # 32 MB page cache
    return conn


def _get_ontology_db_conn() -> sqlite3.Connection:
    """
    Returns a thread-local cached SQLite connection configured with WAL and 256 MB MMAP
    to satisfy Invariant 3 and prevent connection thrashing.
    Automatically reconnects if _ONTOLOGY_DB_EPOCH advances after a rebuild.
    Avoids per-query 'SELECT 1' round-trips on the hot path.
    """
    conn = getattr(_LOCAL, "ontology_conn", None)
    conn_epoch = getattr(_LOCAL, "ontology_conn_epoch", -1)

    if conn is not None and conn_epoch == _ONTOLOGY_DB_EPOCH:
        return conn

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _LOCAL.ontology_conn = None

    conn = _create_configured_sqlite_conn()
    _LOCAL.ontology_conn = conn
    _LOCAL.ontology_conn_epoch = _ONTOLOGY_DB_EPOCH
    return conn


@contextmanager
def _get_db():
    """Context manager for thread-local connection reuse with automatic recovery if closed."""
    conn = _get_ontology_db_conn()
    try:
        yield conn
    except sqlite3.ProgrammingError:
        # Recover transparently if the cached connection was externally closed
        try:
            conn.close()
        except Exception:
            pass
        _LOCAL.ontology_conn = None
        conn = _get_ontology_db_conn()
        yield conn


def _close_thread_local_conn() -> None:
    """Closes current thread-local connection and increments epoch so other threads refresh."""
    global _ONTOLOGY_DB_EPOCH
    with _DB_CONN_LOCK:
        _ONTOLOGY_DB_EPOCH += 1
    conn = getattr(_LOCAL, "ontology_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _LOCAL.ontology_conn = None


# ============================================================================
# 3. NORMALIZATION & SYNONYM / ALIAS RETRIEVAL
# ============================================================================

@functools.lru_cache(maxsize=32768)
def normalize_alias(text: str) -> str:
    """
    Canonical normalization for ontology alias matching (handles inverted MeSH descriptors).
    Uses a single pre-compiled regex pass to strip non-alphanumerics and collapse whitespace.
    """
    if not text:
        return ""
    raw = text.strip() if isinstance(text, str) else str(text).strip()
    if not raw:
        return ""
    if "," in raw and raw.count(",") == 1:
        p0, p1 = raw.split(",", 1)
        p0_s, p1_s = p0.strip(), p1.strip()
        if len(p0_s.split()) <= 4 and len(p1_s.split()) <= 4:
            raw = f"{p1_s} {p0_s}"
    return _NON_ALNUM_RE.sub(" ", raw.lower()).strip()


@functools.lru_cache(maxsize=8192)
def get_concept_aliases(term: str) -> List[str]:
    """
    Looks up all aliases, synonyms, and canonical names for a concept in ontology_kb.db
    given a term or concept identifier. Cached in memory for sub-millisecond repeated queries.
    """
    if not term or not str(term).strip():
        return []

    norm = normalize_alias(term)
    if not norm:
        return []

    aliases: List[str] = []
    seen: Set[str] = set()

    def _add(a: str) -> None:
        if not a:
            return
        a_clean = str(a).strip()
        a_low = a_clean.lower()
        if a_clean and a_low not in seen:
            seen.add(a_low)
            aliases.append(a_clean)

    if ONTOLOGY_DB_PATH.exists():
        try:
            with _get_db() as conn:
                cur = conn.cursor()
                # 1. Look up CUI via alias_norm covering index or direct CUI match
                cur.execute(
                    "SELECT cui, canonical_name FROM kb_aliases "
                    "WHERE alias_norm = ? ORDER BY is_canonical DESC, priority ASC LIMIT 1",
                    (norm,),
                )
                row = cur.fetchone()
                cui = row["cui"] if row else None
                if row and row["canonical_name"]:
                    _add(row["canonical_name"])

                if not cui:
                    cur.execute(
                        "SELECT cui, canonical_name FROM kb_concepts WHERE cui = ? LIMIT 1",
                        (str(term).strip().upper(),),
                    )
                    c_direct = cur.fetchone()
                    if c_direct:
                        cui = c_direct["cui"]
                        if c_direct["canonical_name"]:
                            _add(c_direct["canonical_name"])

                # 2. If CUI found, fetch all aliases associated with that CUI
                if cui:
                    cur.execute(
                        "SELECT canonical_name, kb_source FROM kb_concepts WHERE cui = ?",
                        (cui,),
                    )
                    c_row = cur.fetchone()
                    if c_row and c_row["canonical_name"]:
                        _add(c_row["canonical_name"])

                    cur.execute(
                        "SELECT DISTINCT canonical_name, alias_norm FROM kb_aliases "
                        "WHERE cui = ? ORDER BY is_canonical DESC, priority ASC LIMIT 20",
                        (cui,),
                    )
                    for a_row in cur.fetchall():
                        if a_row["canonical_name"]:
                            _add(a_row["canonical_name"])
                        if a_row["alias_norm"]:
                            _add(a_row["alias_norm"])
        except Exception as exc:
            logger.debug("get_concept_aliases SQLite lookup error: %s", exc)

    return aliases


def get_concept_aliases_batch(terms: Sequence[str]) -> Dict[str, List[str]]:
    """
    Batched version of get_concept_aliases for resolving synonyms across multiple terms efficiently.
    """
    if not terms:
        return {}
    return {t: get_concept_aliases(t) for t in terms}


# ============================================================================
# 4. FILE DISCOVERY FOR DOWNLOADED S3 LINKERS & .JSONL FILES
# ============================================================================

def _find_linker_subfolder(kb_key: str) -> Optional[Path]:
    """Locates the folder containing concept_aliases.json / tfidf_vectorizer.joblib with caching."""
    cached = _SUBFOLDER_CACHE.get(kb_key)
    if cached is not None and cached.exists():
        return cached

    candidates = [
        LINKERS_DIR / kb_key,
        LINKERS_DIR / "2023-04-23" / kb_key,
        LINKERS_DIR / "2020-10-09" / kb_key,
        LINKERS_DIR / "data" / "linkers" / "2023-04-23" / kb_key,
    ]
    if kb_key == "go":
        candidates.extend([
            LINKERS_DIR / "gene_ontology",
            LINKERS_DIR / "2023-04-23" / "gene_ontology",
        ])

    for folder in candidates:
        if folder.exists() and folder.is_dir():
            if (folder / "concept_aliases.json").exists() or (folder / "tfidf_vectorizer.joblib").exists():
                _SUBFOLDER_CACHE[kb_key] = folder
                return folder

    if LINKERS_DIR.exists():
        for p in LINKERS_DIR.rglob("concept_aliases.json"):
            parent_name = p.parent.name.lower()
            if parent_name == kb_key or (kb_key == "go" and "gene" in parent_name):
                _SUBFOLDER_CACHE[kb_key] = p.parent
                return p.parent
    return None


def _find_kb_jsonl_file(kb_key: str) -> Optional[Path]:
    """Locates the .jsonl Knowledge Base definition file for a given ontology with caching."""
    cached = _JSONL_PATH_CACHE.get(kb_key)
    if cached is not None and cached.exists():
        return cached

    meta = ONTOLOGY_DEFS.get(kb_key, {})
    filenames = meta.get("jsonl_candidates", [f"{kb_key}.jsonl"])

    search_dirs = [
        KBS_DIR,
        LINKERS_DIR,
        LINKERS_DIR / kb_key,
        LINKERS_DIR / "kbs" / "2023-04-23",
        LINKERS_DIR / "data" / "kbs" / "2023-04-23",
    ]
    for d in search_dirs:
        if not d.exists():
            continue
        for fname in filenames:
            candidate = d / fname
            if candidate.exists() and candidate.is_file():
                _JSONL_PATH_CACHE[kb_key] = candidate
                return candidate

    if LINKERS_DIR.exists():
        for fname in filenames:
            for found in LINKERS_DIR.rglob(fname):
                if found.is_file():
                    _JSONL_PATH_CACHE[kb_key] = found
                    return found
    return None


def _get_cached_dir_size(folder: Path) -> int:
    """Computes directory size in bytes, cached by directory mtime to avoid repeated rglob walks."""
    try:
        key = str(folder)
        mtime = folder.stat().st_mtime
        cached = _DIR_SIZE_CACHE.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        total = sum(f.stat().st_size for f in folder.glob("**/*") if f.is_file())
        _DIR_SIZE_CACHE[key] = (mtime, total)
        return total
    except Exception:
        return 0


def get_ontology_files_status() -> Dict[str, Any]:
    """Reports status of offline linker folders, .jsonl definitions, and compiled database."""
    os.makedirs(LINKERS_DIR, exist_ok=True)
    os.makedirs(KBS_DIR, exist_ok=True)

    ontologies_status = {}
    for kb_key, meta in ONTOLOGY_DEFS.items():
        folder = _find_linker_subfolder(kb_key)
        jsonl_path = _find_kb_jsonl_file(kb_key)

        has_aliases = bool(folder and (folder / "concept_aliases.json").exists())
        has_tfidf = bool(
            folder
            and (folder / "tfidf_vectorizer.joblib").exists()
            and (folder / "tfidf_vectors_sparse.npz").exists()
        )
        has_nmslib = bool(folder and (folder / "nmslib_index.bin").exists())
        has_jsonl = bool(jsonl_path and jsonl_path.exists())

        total_bytes = 0
        if folder and folder.exists():
            total_bytes += _get_cached_dir_size(folder)
        if jsonl_path and jsonl_path.exists():
            try:
                if not (folder and folder.exists() and jsonl_path.is_relative_to(folder)):
                    total_bytes += jsonl_path.stat().st_size
            except AttributeError:
                # Fallback for Python < 3.9 if ever encountered
                try:
                    total_bytes += jsonl_path.stat().st_size
                except Exception:
                    pass
            except Exception:
                pass

        size_mb = round(total_bytes / (1024 * 1024), 2)

        ontologies_status[kb_key] = {
            "id": kb_key,
            "name": meta["name"],
            "detected": has_jsonl or has_aliases,
            "ready_to_index": has_jsonl or has_aliases,
            "has_jsonl": has_jsonl,
            "jsonl_path": str(jsonl_path) if jsonl_path else None,
            "has_concept_aliases": has_aliases,
            "has_aliases_json": has_aliases,
            "has_tfidf_vectorizer": has_tfidf,
            "has_sparse_matrix": has_tfidf,
            "has_tfidf": has_tfidf,
            "has_nmslib": has_nmslib,
            "has_nmslib_index": has_nmslib,
            "folder_path": str(folder) if folder else None,
            "size_mb": size_mb,
        }

    tui_path = KBS_DIR / "umls_semantic_type_tree.tsv"
    if not tui_path.exists():
        tui_path = LINKERS_DIR / "umls_semantic_type_tree.tsv"
    tui_tree_exists = tui_path.exists()
    tui_tree_dict = {
        "detected": tui_tree_exists,
        "path": str(tui_path) if tui_tree_exists else None,
    }
    ontologies_status["semantic_type_tree"] = tui_tree_dict

    indexed_concepts = 0
    indexed_aliases = 0
    by_source: Dict[str, int] = {}

    if ONTOLOGY_DB_PATH.exists():
        try:
            with _get_db() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM kb_concepts")
                row = cur.fetchone()
                indexed_concepts = row[0] if row else 0

                cur.execute("SELECT COUNT(*) FROM kb_aliases")
                row = cur.fetchone()
                indexed_aliases = row[0] if row else 0

                cur.execute("SELECT kb_source, COUNT(*) FROM kb_concepts GROUP BY kb_source")
                for r in cur.fetchall():
                    by_source[r[0]] = r[1]
        except Exception as exc:
            logger.debug("get_ontology_files_status query info: %s", exc)

    return {
        "status": "success",
        "linkers_dir": str(LINKERS_DIR),
        "kbs_dir": str(KBS_DIR),
        "tui_tree_present": tui_tree_exists,
        "indexed_concepts": indexed_concepts,
        "indexed_aliases": indexed_aliases,
        "indexed_by_source": by_source,
        "ontologies": ontologies_status,
    }


# ============================================================================
# 5. DISK-BACKED SQLITE ONTOLOGY COMPILER & ENGINE (< 50 MB RAM)
# ============================================================================

def _init_ontology_db_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kb_concepts (
            cui TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            kb_source TEXT NOT NULL,
            semantic_category TEXT NOT NULL DEFAULT 'clinical',
            tui_list TEXT DEFAULT '',
            types TEXT DEFAULT '[]',
            definition TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS kb_aliases (
            alias_norm TEXT NOT NULL,
            cui TEXT NOT NULL,
            canonical_name TEXT NOT NULL DEFAULT '',
            kb_source TEXT NOT NULL DEFAULT '',
            is_canonical INTEGER DEFAULT 0,
            priority INTEGER DEFAULT 3,
            PRIMARY KEY (alias_norm, cui)
        );
    """)

    # Safe idempotent migration to ensure all columns exist before index creation
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(kb_concepts);")
    concept_cols = {row["name"] for row in cursor.fetchall()}
    if "semantic_category" not in concept_cols:
        cursor.execute(
            "ALTER TABLE kb_concepts ADD COLUMN semantic_category TEXT NOT NULL DEFAULT 'clinical';"
        )
    if "tui_list" not in concept_cols:
        cursor.execute("ALTER TABLE kb_concepts ADD COLUMN tui_list TEXT DEFAULT '';")
    if "types" not in concept_cols:
        cursor.execute("ALTER TABLE kb_concepts ADD COLUMN types TEXT DEFAULT '[]';")

    cursor.execute("PRAGMA table_info(kb_aliases);")
    alias_cols = {row["name"] for row in cursor.fetchall()}
    if "canonical_name" not in alias_cols:
        cursor.execute(
            "ALTER TABLE kb_aliases ADD COLUMN canonical_name TEXT NOT NULL DEFAULT '';"
        )
    if "kb_source" not in alias_cols:
        cursor.execute("ALTER TABLE kb_aliases ADD COLUMN kb_source TEXT NOT NULL DEFAULT '';")
    if "is_canonical" not in alias_cols:
        cursor.execute("ALTER TABLE kb_aliases ADD COLUMN is_canonical INTEGER DEFAULT 0;")
    if "priority" not in alias_cols:
        cursor.execute("ALTER TABLE kb_aliases ADD COLUMN priority INTEGER DEFAULT 3;")
    conn.commit()


def _classify_tuis(tuis: List[str], default_category: str = "clinical") -> Optional[str]:
    """Maps UMLS TUIs to high-yield clinical categories or returns None if strictly non-medical."""
    if not tuis:
        return default_category

    matched_categories = [
        CLINICAL_TUI_CATEGORIES[tui] for tui in tuis if tui in CLINICAL_TUI_CATEGORIES
    ]
    if matched_categories:
        for pref in (
            "disease",
            "microorganism",
            "anatomy",
            "drug_chemical",
            "procedure",
            "sign_symptom",
            "physiology",
        ):
            if pref in matched_categories:
                return pref
        return matched_categories[0]

    if all(tui in BLOCKED_NON_MEDICAL_TUIS for tui in tuis):
        return None

    return default_category


def build_offline_ontology_index(
    selected_ontologies: Optional[List[str]] = None,
    target_kbs: Optional[List[str]] = None,
    kbs: Optional[List[str]] = None,
    ontologies: Optional[List[str]] = None,
    force_rebuild: bool = True,
    progress_callback: Optional[Callable] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Streams downloaded .jsonl and alias files into ontology_kb.db in low-RAM batches.
    Uses deferred covering index creation and per-ontology transactions for fast bulk ingestion.
    Maintains full compatibility with all route callers.
    """
    active_kbs = selected_ontologies or target_kbs or kbs or ontologies
    if not active_kbs:
        active_kbs = ["mesh", "rxnorm", "hpo", "go", "umls"]

    callback = progress_callback or kwargs.get("callback") or kwargs.get("progress")

    def _report_progress(pct: float, stage: str, msg: str = "") -> None:
        if not callback:
            return
        try:
            callback(pct, stage, msg)
        except TypeError:
            try:
                callback(pct, f"{stage}: {msg}" if msg else stage)
            except TypeError:
                try:
                    callback(pct)
                except Exception:
                    pass

    os.makedirs(LINKERS_DIR, exist_ok=True)
    _close_thread_local_conn()
    _SUBFOLDER_CACHE.clear()
    _JSONL_PATH_CACHE.clear()
    _DIR_SIZE_CACHE.clear()
    gc.collect()

    total_concepts = 0
    total_aliases = 0

    conn = sqlite3.connect(str(ONTOLOGY_DB_PATH), timeout=60.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = OFF;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA cache_size = -128000;")
        conn.execute("PRAGMA mmap_size = 268435456;")

        # Drop secondary indexes before bulk loading so B-Trees are built once at the end
        conn.executescript("""
            DROP INDEX IF EXISTS idx_kb_aliases_norm;
            DROP INDEX IF EXISTS idx_kb_aliases_cui;
            DROP INDEX IF EXISTS idx_kb_concepts_source;
        """)

        if force_rebuild:
            conn.executescript("""
                DROP TABLE IF EXISTS kb_concepts;
                DROP TABLE IF EXISTS kb_aliases;
            """)

        _init_ontology_db_schema(conn)
        cursor = conn.cursor()

        total_sources = len(active_kbs)
        for s_idx, kb_key in enumerate(active_kbs):
            if kb_key not in ONTOLOGY_DEFS:
                continue

            meta = ONTOLOGY_DEFS[kb_key]
            prio = meta["priority"]
            default_cat = meta["default_category"]
            jsonl_path = _find_kb_jsonl_file(kb_key)
            linker_folder = _find_linker_subfolder(kb_key)
            aliases_json_path = (linker_folder / "concept_aliases.json") if linker_folder else None

            pct_base = int(10 + 75 * (s_idx / max(total_sources, 1)))
            _report_progress(
                pct_base,
                f"Indexing {meta['name']}",
                f"Reading definitions for {kb_key.upper()}...",
            )

            concept_batch: List[tuple] = []
            alias_batch: List[tuple] = []
            seen_cuis_in_kb: Set[str] = set()

            def _flush_batches(commit_now: bool = False) -> None:
                nonlocal total_concepts, total_aliases
                if concept_batch:
                    cursor.executemany(
                        """
                        INSERT OR REPLACE INTO kb_concepts
                        (cui, canonical_name, kb_source, semantic_category, tui_list, types, definition)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        concept_batch,
                    )
                    total_concepts += len(concept_batch)
                    concept_batch.clear()
                if alias_batch:
                    cursor.executemany(
                        """
                        INSERT OR REPLACE INTO kb_aliases
                        (alias_norm, cui, canonical_name, kb_source, is_canonical, priority)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        alias_batch,
                    )
                    total_aliases += len(alias_batch)
                    alias_batch.clear()
                if commit_now:
                    conn.commit()

            # Path A: Stream .jsonl line-by-line within a single transaction per ontology
            if jsonl_path and jsonl_path.exists():
                logger.info("Streaming ontology JSONL: %s", jsonl_path)
                conn.execute("BEGIN;")
                try:
                    with open(jsonl_path, "r", encoding="utf-8", errors="ignore") as jf:
                        for line in jf:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                            except Exception:
                                continue

                            cui = str(obj.get("concept_id") or "").strip()
                            canonical = str(obj.get("canonical_name") or "").strip()
                            if not cui or not canonical:
                                continue

                            tuis = obj.get("types") or []
                            sem_cat = _classify_tuis(tuis, default_category=default_cat)
                            if sem_cat is None:
                                continue

                            defn = str(obj.get("definition") or "")[:450]
                            concept_batch.append((
                                cui,
                                canonical,
                                kb_key,
                                sem_cat,
                                ",".join(tuis),
                                json.dumps(tuis),
                                defn,
                            ))
                            seen_cuis_in_kb.add(cui)

                            canon_norm = normalize_alias(canonical)
                            if canon_norm and len(canon_norm) >= 2:
                                alias_batch.append((canon_norm, cui, canonical, kb_key, 1, prio))

                            seen_norms = {canon_norm}
                            for raw_alias in (obj.get("aliases") or [])[:25]:
                                a_norm = normalize_alias(raw_alias)
                                if a_norm and len(a_norm) >= 2 and a_norm not in seen_norms:
                                    seen_norms.add(a_norm)
                                    alias_batch.append((a_norm, cui, canonical, kb_key, 0, prio))

                            if len(concept_batch) >= 25000 or len(alias_batch) >= 60000:
                                _flush_batches(commit_now=False)

                    _flush_batches(commit_now=True)
                except Exception:
                    conn.rollback()
                    raise

            # Path B: Handle concept_aliases.json if present without crashing on lists
            if aliases_json_path and aliases_json_path.exists() and not seen_cuis_in_kb:
                logger.info("Checking concept_aliases.json from %s", aliases_json_path)
                try:
                    with open(aliases_json_path, "r", encoding="utf-8", errors="ignore") as af:
                        aliases_data = json.load(af)

                    if isinstance(aliases_data, dict):
                        conn.execute("BEGIN;")
                        try:
                            for cui, alias_list in aliases_data.items():
                                if not alias_list or not isinstance(alias_list, list):
                                    continue
                                canonical = str(alias_list[0]).strip()
                                if not canonical:
                                    continue
                                concept_batch.append((
                                    str(cui),
                                    canonical,
                                    kb_key,
                                    default_cat,
                                    "",
                                    "[]",
                                    "",
                                ))
                                seen_norms = set()
                                for idx, a_str in enumerate(alias_list[:25]):
                                    a_norm = normalize_alias(str(a_str))
                                    if a_norm and len(a_norm) >= 2 and a_norm not in seen_norms:
                                        seen_norms.add(a_norm)
                                        alias_batch.append((
                                            a_norm,
                                            str(cui),
                                            canonical,
                                            kb_key,
                                            1 if idx == 0 else 0,
                                            prio,
                                        ))

                                if len(concept_batch) >= 25000 or len(alias_batch) >= 60000:
                                    _flush_batches(commit_now=False)
                            _flush_batches(commit_now=True)
                        except Exception:
                            conn.rollback()
                            raise
                    elif isinstance(aliases_data, list):
                        logger.info(
                            "%s contains %d pre-indexed vector strings.",
                            aliases_json_path,
                            len(aliases_data),
                        )
                except Exception as exc:
                    logger.warning("Failed processing %s: %s", aliases_json_path, exc)

        _report_progress(
            90.0,
            "Building Indexes",
            "Optimizing covering B-Tree lookup indices on ontology_kb.db...",
        )

        # Covering index includes cui, canonical_name, kb_source so alias resolution is index-only
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_kb_aliases_norm
                ON kb_aliases(alias_norm, is_canonical DESC, priority ASC, cui, canonical_name, kb_source);
            CREATE INDEX IF NOT EXISTS idx_kb_aliases_cui
                ON kb_aliases(cui, is_canonical DESC, priority ASC);
            CREATE INDEX IF NOT EXISTS idx_kb_concepts_source
                ON kb_concepts(kb_source);
            PRAGMA synchronous = NORMAL;
            PRAGMA optimize;
        """)
        conn.commit()

    finally:
        conn.close()
        _close_thread_local_conn()

    # Clear all caches upon index update
    resolve_ontology_concept.cache_clear()
    get_concept_aliases.cache_clear()
    with _TFIDF_LOCK:
        _TFIDF_CACHE.clear()
    gc.collect()

    _report_progress(
        100.0,
        "Completed",
        f"Indexed {total_concepts:,} concepts and {total_aliases:,} clinical aliases.",
    )

    return {
        "status": "success",
        "indexed_concepts": total_concepts,
        "indexed_aliases": total_aliases,
    }


# Alias for cross-module compatibility
build_ontology_database = build_offline_ontology_index


# ============================================================================
# 6. FAST CUI & SYNONYM RESOLVER (SQLITE B-TREE + VECTORIZED INVERTED TF-IDF)
# ============================================================================

_TFIDF_CACHE: Dict[str, Dict[str, Any]] = {}


def _load_tfidf_matcher(kb_key: str) -> Optional[Dict[str, Any]]:
    """
    Lazy thread-safe loader for offline character 3-gram TF-IDF matrices.
    Precomputes the transposed CSR inverted index (matrix_t_csr) so sparse dot products
    only visit non-zero query 3-gram rows rather(O(nnz_query)) than scanning all N aliases.
    """
    if kb_key in _TFIDF_CACHE:
        return _TFIDF_CACHE[kb_key]

    if kb_key == "umls":
        return None  # Exclude giant UMLS matrix to protect 500 MB RAM budget

    with _TFIDF_LOCK:
        if kb_key in _TFIDF_CACHE:
            return _TFIDF_CACHE[kb_key]

        folder = _find_linker_subfolder(kb_key)
        if not folder:
            return None

        vec_path = folder / "tfidf_vectorizer.joblib"
        mat_path = folder / "tfidf_vectors_sparse.npz"
        aliases_path = folder / "concept_aliases.json"

        if not (vec_path.exists() and mat_path.exists() and aliases_path.exists()):
            return None

        try:
            import joblib
            import scipy.sparse

            vectorizer = joblib.load(vec_path)
            tfidf_matrix = scipy.sparse.load_npz(mat_path).tocsr()
            # Precompute transposed CSR matrix (V x N_aliases) for fast inverted-index multiplication
            matrix_t_csr = tfidf_matrix.T.tocsr()

            with open(aliases_path, "r", encoding="utf-8", errors="ignore") as f:
                raw_aliases = json.load(f)

            if isinstance(raw_aliases, list):
                aliases = [str(a) for a in raw_aliases]
            elif isinstance(raw_aliases, dict):
                aliases = []
                for _, a_list in raw_aliases.items():
                    if isinstance(a_list, list):
                        aliases.extend(str(a) for a in a_list)
                    else:
                        aliases.append(str(a_list))
            else:
                aliases = []

            matcher = {
                "vectorizer": vectorizer,
                "matrix": tfidf_matrix,
                "matrix_t_csr": matrix_t_csr,
                "aliases": aliases,
            }
            _TFIDF_CACHE[kb_key] = matcher
            logger.info("Loaded offline TF-IDF linker for '%s' (%d aliases).", kb_key, len(aliases))
            return matcher
        except Exception as exc:
            logger.debug("TF-IDF matcher for %s load skipped: %s", kb_key, exc)
            return None


def _lookup_cui_metadata(cui: str) -> Optional[Dict[str, Any]]:
    """Fetches concept metadata directly by CUI using thread-local connection."""
    if not ONTOLOGY_DB_PATH.exists():
        return None
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT cui, canonical_name, kb_source, semantic_category, tui_list, definition "
                "FROM kb_concepts WHERE cui = ?",
                (cui,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
    except Exception as exc:
        logger.debug("_lookup_cui_metadata error: %s", exc)
    return None


def _exact_sqlite_lookup_single(norm: str) -> Optional[Dict[str, Any]]:
    """
    Resolves a single normalized alias via the covering index idx_kb_aliases_norm first
    (LIMIT 1 index-only scan) before fetching concept metadata by primary key.
    """
    if not ONTOLOGY_DB_PATH.exists():
        return None
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.cui, c.canonical_name, c.kb_source, c.semantic_category, c.tui_list, c.definition
                FROM (
                    SELECT cui
                    FROM kb_aliases
                    WHERE alias_norm = ?
                    ORDER BY is_canonical DESC, priority ASC
                    LIMIT 1
                ) best
                JOIN kb_concepts c ON c.cui = best.cui
                """,
                (norm,),
            )
            row = cur.fetchone()
            if row:
                return {
                    "matched": True,
                    "cui": row["cui"],
                    "canonical_name": row["canonical_name"],
                    "kb_source": row["kb_source"],
                    "semantic_category": row["semantic_category"],
                    "tui_list": row["tui_list"] or "",
                    "definition": row["definition"] or "",
                    "score": 1.0,
                }
    except Exception as exc:
        logger.debug("Ontology SQLite lookup error: %s", exc)
    return None


def _batch_sqlite_exact_lookup(norms: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """
    Resolves an arbitrary collection of normalized alias strings in batched SQLite queries.
    Uses covering index partitioning so each alias_norm maps to its top-ranked CUI in one pass.
    """
    if not norms or not ONTOLOGY_DB_PATH.exists():
        return {}

    unique_norms = list(dict.fromkeys(n for n in norms if n and len(n) >= 2))
    if not unique_norms:
        return {}

    if len(unique_norms) == 1:
        single_hit = _exact_sqlite_lookup_single(unique_norms[0])
        return {unique_norms[0]: single_hit} if single_hit else {}

    results: Dict[str, Dict[str, Any]] = {}
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            for start in range(0, len(unique_norms), _SQLITE_BATCH_CHUNK_SIZE):
                chunk = unique_norms[start : start + _SQLITE_BATCH_CHUNK_SIZE]
                placeholders = ",".join("?" for _ in chunk)
                # Fetch candidate (alias_norm, cui) pairs in preference order from covering index
                cur.execute(
                    f"""
                    SELECT alias_norm, cui
                    FROM kb_aliases
                    WHERE alias_norm IN ({placeholders})
                    ORDER BY alias_norm, is_canonical DESC, priority ASC
                    """,
                    chunk,
                )
                winning_cui_by_norm: Dict[str, str] = {}
                for a_row in cur.fetchall():
                    a_norm = a_row["alias_norm"]
                    if a_norm not in winning_cui_by_norm:
                        winning_cui_by_norm[a_norm] = a_row["cui"]

                if not winning_cui_by_norm:
                    continue

                unique_cuis = list(dict.fromkeys(winning_cui_by_norm.values()))
                cui_placeholders = ",".join("?" for _ in unique_cuis)
                cur.execute(
                    f"""
                    SELECT cui, canonical_name, kb_source, semantic_category, tui_list, definition
                    FROM kb_concepts
                    WHERE cui IN ({cui_placeholders})
                    """,
                    unique_cuis,
                )
                cui_meta: Dict[str, sqlite3.Row] = {r["cui"]: r for r in cur.fetchall()}

                for a_norm, win_cui in winning_cui_by_norm.items():
                    c_row = cui_meta.get(win_cui)
                    if c_row is not None:
                        results[a_norm] = {
                            "matched": True,
                            "cui": c_row["cui"],
                            "canonical_name": c_row["canonical_name"],
                            "kb_source": c_row["kb_source"],
                            "semantic_category": c_row["semantic_category"],
                            "tui_list": c_row["tui_list"] or "",
                            "definition": c_row["definition"] or "",
                            "score": 1.0,
                        }
    except Exception as exc:
        logger.debug("Batch SQLite ontology lookup error: %s", exc)

    return results


def _vectorized_tfidf_lookup(
    terms: Sequence[str],
    min_tfidf_score: float = 0.84,
) -> Dict[str, Dict[str, Any]]:
    """
    Vectorized Character 3-Gram TF-IDF matcher across MeSH, RxNorm, and HPO.
    Transforms all candidate terms in a single batch per ontology and multiplies against
    the transposed CSR inverted index (Q @ M^T), then resolves winning aliases in one SQLite batch.
    """
    if not terms or not ONTOLOGY_DB_PATH.exists():
        return {}

    import numpy as np

    active_terms = list(dict.fromkeys(t for t in terms if t))
    if not active_terms:
        return {}

    # Track best (score, matched_norm) per term across ontologies
    best_match_by_term: Dict[str, Tuple[float, str]] = {}
    remaining_terms: List[str] = list(active_terms)

    for kb_key in ("mesh", "rxnorm", "hpo"):
        if not remaining_terms:
            break
        matcher = _load_tfidf_matcher(kb_key)
        if not matcher:
            continue

        aliases = matcher.get("aliases") or []
        if not aliases:
            continue

        try:
            q_mat = matcher["vectorizer"].transform(remaining_terms)
            if q_mat.nnz == 0:
                continue

            matrix_t_csr = matcher.get("matrix_t_csr")
            if matrix_t_csr is None and "matrix" in matcher:
                matrix_t_csr = matcher["matrix"].T.tocsr()
                matcher["matrix_t_csr"] = matrix_t_csr
            if matrix_t_csr is None:
                continue

            # CSR (K, V) @ CSR (V, N) -> CSR (K, N) visiting only non-zero query 3-grams
            scores_csr = q_mat.dot(matrix_t_csr)
            if scores_csr.nnz == 0:
                continue

            indptr = scores_csr.indptr
            indices = scores_csr.indices
            data = scores_csr.data
            num_aliases = len(aliases)

            next_remaining: List[str] = []
            for row_idx, term in enumerate(remaining_terms):
                start, end = int(indptr[row_idx]), int(indptr[row_idx + 1])
                current_best_score = best_match_by_term.get(term, (float(min_tfidf_score), ""))[0]

                if end > start:
                    row_data = data[start:end]
                    max_pos = int(np.argmax(row_data))
                    score = float(row_data[max_pos])
                    best_col = int(indices[start + max_pos])

                    if score >= current_best_score and best_col < num_aliases:
                        matched_norm = normalize_alias(aliases[best_col])
                        if matched_norm:
                            best_match_by_term[term] = (score, matched_norm)
                            current_best_score = score

                # Early-exit for terms that achieved >= 0.98 confidence
                if current_best_score < 0.98 or term not in best_match_by_term:
                    next_remaining.append(term)

            remaining_terms = next_remaining
        except Exception as exc:
            logger.debug("Vectorized TF-IDF evaluation error for %s: %s", kb_key, exc)
            continue

    if not best_match_by_term:
        return {}

    # Batch-resolve all matched normalized aliases in SQLite in a single pass
    matched_norms = [m_norm for _, m_norm in best_match_by_term.values()]
    resolved_by_norm = _batch_sqlite_exact_lookup(matched_norms)

    final_hits: Dict[str, Dict[str, Any]] = {}
    for term, (score, m_norm) in best_match_by_term.items():
        base_hit = resolved_by_norm.get(m_norm)
        if base_hit is not None:
            hit_copy = dict(base_hit)
            hit_copy["score"] = round(score, 4)
            final_hits[term] = hit_copy

    return final_hits


class _CacheInfo( Tuple[int, int, int, int]):
    """Lightweight namedtuple-compatible CacheInfo object for functools.lru_cache parity."""

    __slots__ = ()

    def __new__(cls, hits: int, misses: int, maxsize: int, currsize: int):
        return super().__new__(cls, (hits, misses, maxsize, currsize))

    @property
    def hits(self) -> int:
        return self[0]

    @property
    def misses(self) -> int:
        return self[1]

    @property
    def maxsize(self) -> int:
        return self[2]

    @property
    def currsize(self) -> int:
        return self[3]


_CACHE_HITS = 0
_CACHE_MISSES = 0


def _cache_get(key: Tuple[str, bool, float]) -> Optional[Dict[str, Any]]:
    global _CACHE_HITS
    with _CACHE_LOCK:
        val = _CONCEPT_RESOLUTION_CACHE.get(key)
        if val is not None:
            _CONCEPT_RESOLUTION_CACHE.move_to_end(key)
            _CACHE_HITS += 1
            return dict(val)
        return None


def _cache_put(key: Tuple[str, bool, float], value: Dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CONCEPT_RESOLUTION_CACHE[key] = dict(value)
        _CONCEPT_RESOLUTION_CACHE.move_to_end(key)
        while len(_CONCEPT_RESOLUTION_CACHE) > _CONCEPT_CACHE_MAXSIZE:
            _CONCEPT_RESOLUTION_CACHE.popitem(last=False)


def _clear_concept_resolution_cache() -> None:
    global _CACHE_HITS, _CACHE_MISSES
    with _CACHE_LOCK:
        _CONCEPT_RESOLUTION_CACHE.clear()
        _CACHE_HITS = 0
        _CACHE_MISSES = 0


def _get_concept_resolution_cache_info() -> _CacheInfo:
    with _CACHE_LOCK:
        return _CacheInfo(
            _CACHE_HITS,
            _CACHE_MISSES,
            _CONCEPT_CACHE_MAXSIZE,
            len(_CONCEPT_RESOLUTION_CACHE),
        )


def resolve_ontology_concept(
    term: str,
    enable_fuzzy_tfidf: bool = True,
    min_tfidf_score: float = 0.84,
) -> Dict[str, Any]:
    """
    Resolves a clinical phrase against offline ontologies using exact B-Tree lookup + TF-IDF fallback.
    Returns dictionary conforming strictly to TopicExtractorOutputDict['topic_details'] contract.
    Shares a bounded LRU cache with resolve_ontology_concepts_batch().
    """
    global _CACHE_MISSES
    cache_key = (term, bool(enable_fuzzy_tfidf), float(min_tfidf_score))
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    with _CACHE_LOCK:
        _CACHE_MISSES += 1

    norm = normalize_alias(term)
    if not norm or len(norm) < 2:
        miss_res = {
            "matched": False,
            "cui": "",
            "canonical_name": term,
            "semantic_category": "unknown",
            "score": 0.0,
        }
        _cache_put(cache_key, miss_res)
        return miss_res

    # 1. Fast SQLite Covering Index Lookup (< 0.1ms, Zero RAM allocation)
    exact_hit = _exact_sqlite_lookup_single(norm)
    if exact_hit is not None:
        _cache_put(cache_key, exact_hit)
        return exact_hit

    # 2. Vectorized Inverted-Index Character 3-Gram TF-IDF Lookup across MeSH, RxNorm, HPO
    if enable_fuzzy_tfidf and len(norm) >= 5:
        tfidf_hits = _vectorized_tfidf_lookup([term], min_tfidf_score=min_tfidf_score)
        if term in tfidf_hits:
            hit = tfidf_hits[term]
            _cache_put(cache_key, hit)
            return hit

    miss_res = {
        "matched": False,
        "cui": "",
        "canonical_name": term,
        "semantic_category": "unknown",
        "score": 0.0,
    }
    _cache_put(cache_key, miss_res)
    return miss_res


# Attach lru_cache-compatible methods (.cache_clear / .cache_info) for full API compatibility
resolve_ontology_concept.cache_clear = _clear_concept_resolution_cache  # type: ignore[attr-defined]
resolve_ontology_concept.cache_info = _get_concept_resolution_cache_info  # type: ignore[attr-defined]


def resolve_ontology_concepts_batch(
    terms: Sequence[str],
    enable_fuzzy_tfidf: bool = True,
    min_tfidf_score: float = 0.84,
) -> Dict[str, Dict[str, Any]]:
    """
    High-throughput batched resolver for a sequence of clinical terms.
    1. Checks the shared in-memory LRU cache.
    2. Batches all uncached normalized terms into chunked SQLite B-Tree IN(...) queries.
    3. Vectorizes all remaining unresolved terms (len >= 5) into a single sparse matrix
       multiplication per ontology (Q @ M^T) and resolves their CUIs in one SQLite batch.
    4. Populates the shared LRU cache so subsequent resolve_ontology_concept() calls hit cache.
    """
    global _CACHE_MISSES
    if not terms:
        return {}

    results: Dict[str, Dict[str, Any]] = {}
    uncached_terms: List[str] = []
    norm_by_term: Dict[str, str] = {}

    for term in dict.fromkeys(terms):
        cache_key = (term, bool(enable_fuzzy_tfidf), float(min_tfidf_score))
        cached = _cache_get(cache_key)
        if cached is not None:
            results[term] = cached
            continue

        with _CACHE_LOCK:
            _CACHE_MISSES += 1

        norm = normalize_alias(term)
        if not norm or len(norm) < 2:
            miss = {
                "matched": False,
                "cui": "",
                "canonical_name": term,
                "semantic_category": "unknown",
                "score": 0.0,
            }
            _cache_put(cache_key, miss)
            results[term] = miss
        else:
            uncached_terms.append(term)
            norm_by_term[term] = norm

    if not uncached_terms:
        return results

    # Step 1: Batched SQLite exact lookup across all unique normalized strings
    unique_norms = list(dict.fromkeys(norm_by_term.values()))
    exact_hits_by_norm = _batch_sqlite_exact_lookup(unique_norms)

    tfidf_candidates: List[str] = []
    for term in uncached_terms:
        norm = norm_by_term[term]
        exact_hit = exact_hits_by_norm.get(norm)
        if exact_hit is not None:
            hit_copy = dict(exact_hit)
            _cache_put((term, bool(enable_fuzzy_tfidf), float(min_tfidf_score)), hit_copy)
            results[term] = hit_copy
        elif enable_fuzzy_tfidf and len(norm) >= 5:
            tfidf_candidates.append(term)
        else:
            miss = {
                "matched": False,
                "cui": "",
                "canonical_name": term,
                "semantic_category": "unknown",
                "score": 0.0,
            }
            _cache_put((term, bool(enable_fuzzy_tfidf), float(min_tfidf_score)), miss)
            results[term] = miss

    # Step 2: Vectorized batch TF-IDF fallback for remaining candidates
    if tfidf_candidates:
        tfidf_hits = _vectorized_tfidf_lookup(tfidf_candidates, min_tfidf_score=min_tfidf_score)
        for term in tfidf_candidates:
            hit = tfidf_hits.get(term)
            if hit is not None:
                _cache_put((term, bool(enable_fuzzy_tfidf), float(min_tfidf_score)), hit)
                results[term] = hit
            else:
                miss = {
                    "matched": False,
                    "cui": "",
                    "canonical_name": term,
                    "semantic_category": "unknown",
                    "score": 0.0,
                }
                _cache_put((term, bool(enable_fuzzy_tfidf), float(min_tfidf_score)), miss)
                results[term] = miss

    return results


# ============================================================================
# 7. SCISPACY MODEL CATALOG, DOWNLOADER, LOCAL TAR.GZ EXTRACTOR & LOADER
# ============================================================================

_LOADED_SCISPACY_PIPELINES: Dict[str, List[Any]] = {}
_VERIFIED_MODEL_DIRS: Dict[str, Path] = {}


def _find_extracted_spacy_model_dir(model_name: str) -> Optional[Path]:
    """Searches data/models/scispacy/ for an extracted spaCy model directory with caching."""
    if model_name in _VERIFIED_MODEL_DIRS:
        verified_path = _VERIFIED_MODEL_DIRS[model_name]
        if verified_path.exists():
            return verified_path

    os.makedirs(SCISPACY_MODELS_DIR, exist_ok=True)
    target_root = SCISPACY_MODELS_DIR / model_name

    # Check already extracted directory
    if target_root.exists() and target_root.is_dir():
        if (target_root / "config.cfg").exists() and (target_root / "meta.json").exists():
            _VERIFIED_MODEL_DIRS[model_name] = target_root
            return target_root
        for cfg in target_root.rglob("config.cfg"):
            if (cfg.parent / "meta.json").exists():
                _VERIFIED_MODEL_DIRS[model_name] = cfg.parent
                return cfg.parent

    # If archive exists on disk but is not yet extracted, extract it automatically
    for archive_path in SCISPACY_MODELS_DIR.glob(f"{model_name}*"):
        if archive_path.is_file() and (
            archive_path.name.endswith(".tar.gz")
            or archive_path.name.endswith(".whl")
            or archive_path.name.endswith(".tgz")
        ):
            if _extract_model_archive(archive_path, target_root):
                break

    if target_root.exists():
        if (target_root / "config.cfg").exists() and (target_root / "meta.json").exists():
            _VERIFIED_MODEL_DIRS[model_name] = target_root
            return target_root
        for cfg in target_root.rglob("config.cfg"):
            if (cfg.parent / "meta.json").exists():
                _VERIFIED_MODEL_DIRS[model_name] = cfg.parent
                return cfg.parent

    return None


def _extract_model_archive(archive_path: Path, dest_dir: Path) -> bool:
    """Extracts a scispaCy .tar.gz or .whl archive directly into dest_dir safely."""
    try:
        os.makedirs(dest_dir, exist_ok=True)
        if archive_path.name.endswith(".tar.gz") or archive_path.name.endswith(".tgz"):
            with tarfile.open(archive_path, "r:gz") as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(path=dest_dir, filter="data")
                else:
                    tar.extractall(path=dest_dir)
            return True
        elif archive_path.name.endswith(".whl") or archive_path.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(path=dest_dir)
            return True
    except Exception as exc:
        logger.error("Failed to extract SciSpaCy model archive %s: %s", archive_path, exc)
    return False


def is_scispacy_model_downloaded(model_id: str) -> bool:
    """Checks if a SciSpaCy model is installed in Python or extracted in data/models/scispacy/."""
    if not model_id:
        return False

    if model_id == "en_core_sci_md+bionlp+bc5cdr":
        return all(
            is_scispacy_model_downloaded(m)
            for m in ("en_core_sci_md", "en_ner_bionlp13cg_md", "en_ner_bc5cdr_md")
        )

    if _find_extracted_spacy_model_dir(model_id) is not None:
        return True

    try:
        import spacy.util

        return bool(spacy.util.is_package(model_id))
    except Exception:
        return False


def download_scispacy_model(model_id: str) -> Dict[str, Any]:
    """Downloads SciSpaCy model archive from S3 into data/models/scispacy/ and extracts it atomically."""
    os.makedirs(SCISPACY_MODELS_DIR, exist_ok=True)

    if model_id == "en_core_sci_md+bionlp+bc5cdr":
        models_to_fetch = ["en_core_sci_md", "en_ner_bionlp13cg_md", "en_ner_bc5cdr_md"]
    elif model_id in SCISPACY_MODEL_CATALOG:
        models_to_fetch = [model_id]
    else:
        return {
            "status": "error",
            "model_id": model_id,
            "message": f"Unknown SciSpaCy model: {model_id}",
            "downloaded": False,
        }

    downloaded_paths: List[str] = []
    for m_name in models_to_fetch:
        if is_scispacy_model_downloaded(m_name):
            extracted = _find_extracted_spacy_model_dir(m_name)
            downloaded_paths.append(str(extracted or m_name))
            continue

        meta = SCISPACY_MODEL_CATALOG.get(m_name)
        if not meta or not meta.get("url"):
            continue

        url = meta["url"]
        archive_file = SCISPACY_MODELS_DIR / f"{m_name}-{meta.get('version', '0.5.4')}.tar.gz"
        dest_dir = SCISPACY_MODELS_DIR / m_name
        temp_file = SCISPACY_MODELS_DIR / f".{archive_file.name}.{os.getpid()}.{threading.get_ident()}.tmp"

        try:
            logger.info("Downloading SciSpaCy model %s from %s ...", m_name, url)
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                with open(temp_file, "wb") as out_f:
                    shutil.copyfileobj(resp, out_f, length=64 * 1024)
            os.replace(temp_file, archive_file)

            if _extract_model_archive(archive_file, dest_dir):
                # Remove archive after verified extraction to save disk space
                extracted = _find_extracted_spacy_model_dir(m_name)
                if extracted:
                    downloaded_paths.append(str(extracted))
                    try:
                        archive_file.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                return {
                    "status": "error",
                    "model_id": model_id,
                    "message": f"Extraction failed for {m_name}",
                    "downloaded": False,
                }

        except Exception as exc:
            if temp_file.exists():
                try:
                    temp_file.unlink(missing_ok=True)
                except OSError:
                    pass
            logger.error("Failed downloading SciSpaCy model %s: %s", m_name, exc)
            return {
                "status": "error",
                "model_id": model_id,
                "message": str(exc),
                "downloaded": False,
            }

    with _PIPELINE_LOCK:
        global _CONFIGURED_SCISPACY_MODEL_CACHE
        _CONFIGURED_SCISPACY_MODEL_CACHE = model_id
        _LOADED_SCISPACY_PIPELINES.clear()
        _VERIFIED_MODEL_DIRS.clear()
        gc.collect()

    is_ready = is_scispacy_model_downloaded(model_id)
    return {
        "status": "success" if is_ready else "error",
        "model_id": model_id,
        "paths": downloaded_paths,
        "downloaded": is_ready,
    }


def get_configured_scispacy_model(force_refresh: bool = False) -> str:
    """Fetches user-selected SciSpaCy model from SQLite user_state with in-memory caching."""
    global _CONFIGURED_SCISPACY_MODEL_CACHE
    with _PIPELINE_LOCK:
        if not force_refresh and _CONFIGURED_SCISPACY_MODEL_CACHE is not None:
            return _CONFIGURED_SCISPACY_MODEL_CACHE

        resolved: Optional[str] = None
        try:
            with closing(get_db_connection()) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT key, value FROM user_state "
                    "WHERE key IN ('scispacy_model', 'scispacyModel', 'app_persistence')"
                )
                state_map = {row["key"]: row["value"] for row in cur.fetchall()}

                for direct_key in ("scispacyModel", "scispacy_model"):
                    raw_val = state_map.get(direct_key)
                    if raw_val:
                        try:
                            parsed = json.loads(raw_val)
                            if isinstance(parsed, str) and parsed.strip():
                                resolved = parsed.strip()
                                break
                        except Exception:
                            cleaned = str(raw_val).strip().strip('"').strip("'")
                            if cleaned:
                                resolved = cleaned
                                break

                if not resolved and "app_persistence" in state_map:
                    raw_app = state_map["app_persistence"]
                    st = json.loads(raw_app) if isinstance(raw_app, str) else raw_app
                    if isinstance(st, dict) and st.get("scispacyModel"):
                        resolved = str(st["scispacyModel"]).strip()
        except Exception as exc:
            logger.debug("get_configured_scispacy_model error: %s", exc)

        _CONFIGURED_SCISPACY_MODEL_CACHE = resolved or "en_core_sci_md"
        return _CONFIGURED_SCISPACY_MODEL_CACHE


def set_configured_scispacy_model(model_id: str) -> str:
    """Explicitly updates the active SciSpaCy model in SQLite user_state and invalidates caches."""
    global _CONFIGURED_SCISPACY_MODEL_CACHE
    if not model_id or not str(model_id).strip():
        return get_configured_scispacy_model(force_refresh=True)

    clean_id = str(model_id).strip()
    with _PIPELINE_LOCK:
        _CONFIGURED_SCISPACY_MODEL_CACHE = clean_id
        _LOADED_SCISPACY_PIPELINES.clear()
        gc.collect()

        try:
            with closing(get_db_connection()) as conn:
                conn.execute(
                    """
                    INSERT INTO user_state (key, value, updated_at)
                    VALUES ('scispacyModel', ?, unixepoch('now'))
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
                    app_obj["scispacyModel"] = clean_id
                    app_obj["scispacy_model"] = clean_id
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
            logger.error("Failed to persist set_configured_scispacy_model to DB: %s", exc)

    return clean_id


def _load_optimized_spacy_model(spacy_mod: Any, m_name: str) -> Any:
    """
    Loads a single spaCy model excluding unused heavy pipeline stages (tagger, parser,
    attribute_ruler, lemmatizer) while adding a lightweight sentencizer if sentence
    boundaries (doc.sents) are needed by downstream callers.
    """
    local_dir = _find_extracted_spacy_model_dir(m_name)
    target = str(local_dir) if local_dir else m_name
    try:
        nlp = spacy_mod.load(target, exclude=_EXCLUDED_SPACY_PIPE_COMPONENTS)
    except TypeError:
        # Fallback if a mock or older loader does not accept 'exclude'
        nlp = spacy_mod.load(target)
        for comp in _EXCLUDED_SPACY_PIPE_COMPONENTS:
            if hasattr(nlp, "has_pipe") and nlp.has_pipe(comp):
                try:
                    nlp.remove_pipe(comp)
                except Exception:
                    pass

    # Ensure lightweight sentence boundary detection remains available when parser is excluded
    if hasattr(nlp, "has_pipe") and not nlp.has_pipe("parser") and not nlp.has_pipe("senter") and not nlp.has_pipe("sentencizer"):
        try:
            nlp.add_pipe("sentencizer", first=True)
        except Exception:
            pass
    return nlp


def _merge_ner_pipelines(base_nlp: Any, secondary_models: Sequence[Tuple[str, Any]]) -> List[Any]:
    """
    Merges secondary SciSpaCy NER models (e.g., en_ner_bc5cdr_md, en_ner_bionlp13cg_md)
    into the primary pipeline via replace_listeners("tok2vec", "ner", ["model.tok2vec"])
    so tokenization runs once and all NER heads execute in a single pass.
    Places specific NER models (BC5CDR, BioNLP13CG) before generic 'ENTITY' NER so specific
    biomedical labels take precedence on overlapping spans.
    Falls back cleanly to returning the list of stripped NER-only pipelines if listener
    replacement is unsupported by any sub-model.
    """
    if not secondary_models:
        return [base_nlp]

    # Check if spaCy v3 replace_listeners + add_pipe(..., source=...) is supported
    if not (hasattr(base_nlp, "add_pipe") and hasattr(base_nlp, "has_pipe")):
        return [base_nlp] + [nlp for _, nlp in secondary_models]

    unmerged_fallbacks: List[Any] = []
    # Insert specialized NER pipes BEFORE generic 'ner' so specific labels (DISEASE, CHEMICAL,
    # CANCER, ORGAN, etc.) claim overlapping spans before generic 'ENTITY'
    has_base_ner = base_nlp.has_pipe("ner")

    for short_name, sec_nlp in secondary_models:
        pipe_name = f"ner_{short_name}"
        if base_nlp.has_pipe(pipe_name):
            continue
        try:
            if hasattr(sec_nlp, "has_pipe") and sec_nlp.has_pipe("tok2vec") and sec_nlp.has_pipe("ner"):
                if hasattr(sec_nlp, "replace_listeners"):
                    sec_nlp.replace_listeners("tok2vec", "ner", ["model.tok2vec"])
            if has_base_ner:
                base_nlp.add_pipe("ner", name=pipe_name, source=sec_nlp, before="ner")
            else:
                base_nlp.add_pipe("ner", name=pipe_name, source=sec_nlp)
        except Exception as exc:
            logger.debug("Fallback to separate stripped pipeline for %s: %s", short_name, exc)
            unmerged_fallbacks.append(sec_nlp)

    return [base_nlp] + unmerged_fallbacks


def load_scispacy_pipeline(model_id: Optional[str] = None) -> List[Any]:
    """
    Loads and caches the configured SciSpaCy pipeline(s) strictly on CPU with thread safety.
    Excludes unused parser/tagger/lemmatizer stages and merges multi-model NER pipes into a
    single pass where supported. Guarantees zero VRAM allocation and evicts stale pipelines
    to respect the <500 MB RAM budget.
    """
    target_id = model_id or get_configured_scispacy_model()
    if target_id in _LOADED_SCISPACY_PIPELINES:
        return _LOADED_SCISPACY_PIPELINES[target_id]

    with _PIPELINE_LOCK:
        if target_id in _LOADED_SCISPACY_PIPELINES:
            return _LOADED_SCISPACY_PIPELINES[target_id]

        import spacy

        # Enforce CPU execution for spaCy/Thinc/PyTorch (Invariant 1)
        try:
            spacy.require_cpu()
        except Exception:
            pass

        pipelines: List[Any] = []
        if target_id == "en_core_sci_md+bionlp+bc5cdr":
            loaded_map: Dict[str, Any] = {}
            for m in ("en_core_sci_md", "en_ner_bionlp13cg_md", "en_ner_bc5cdr_md"):
                try:
                    loaded_map[m] = _load_optimized_spacy_model(spacy, m)
                except Exception as exc:
                    logger.warning("Could not load sub-model %s: %s", m, exc)

            if "en_core_sci_md" in loaded_map:
                secondary: List[Tuple[str, Any]] = []
                if "en_ner_bc5cdr_md" in loaded_map:
                    secondary.append(("bc5cdr", loaded_map["en_ner_bc5cdr_md"]))
                if "en_ner_bionlp13cg_md" in loaded_map:
                    secondary.append(("bionlp13cg", loaded_map["en_ner_bionlp13cg_md"]))
                pipelines = _merge_ner_pipelines(loaded_map["en_core_sci_md"], secondary)
            else:
                pipelines = list(loaded_map.values())
        else:
            try:
                pipelines.append(_load_optimized_spacy_model(spacy, target_id))
            except Exception as exc:
                logger.warning("Could not load configured model %s: %s", target_id, exc)

        if not pipelines:
            for fallback_name in (
                "en_core_sci_md",
                "en_core_sci_sm",
                "en_ner_bionlp13cg_md",
                "en_ner_bc5cdr_md",
                "en_core_web_sm",
            ):
                try:
                    pipelines.append(_load_optimized_spacy_model(spacy, fallback_name))
                    logger.info("Loaded fallback biomedical NLP pipeline: %s", fallback_name)
                    break
                except Exception:
                    continue

        if pipelines:
            # Evict any previously loaded pipelines so total SciSpaCy RAM stays < 500 MB
            if _LOADED_SCISPACY_PIPELINES:
                _LOADED_SCISPACY_PIPELINES.clear()
                gc.collect()
            _LOADED_SCISPACY_PIPELINES[target_id] = pipelines

        return pipelines


def extract_and_link_entities(
    texts: Union[str, Sequence[str]],
    model_id: Optional[str] = None,
    enable_fuzzy_tfidf: bool = True,
    min_tfidf_score: float = 0.84,
    batch_size: int = 32,
) -> Union[List[Dict[str, Any]], List[List[Dict[str, Any]]]]:
    """
    End-to-end batched biomedical entity extractor and ontology linker.
    Accepts either a single text string or a sequence of texts, runs nlp.pipe() across
    the active pipeline(s), deduplicates all unique entity strings across the batch,
    and resolves them in one batched SQLite + vectorized TF-IDF pass.
    """
    is_single = isinstance(texts, str)
    text_list: List[str] = [texts] if is_single else list(texts)
    if not text_list:
        return []

    pipelines = load_scispacy_pipeline(model_id=model_id)
    if not pipelines:
        return [] if is_single else [[] for _ in text_list]

    # 1. Extract raw entity spans per document across active pipeline(s) using nlp.pipe
    raw_spans_per_doc: List[List[Tuple[str, str, int, int]]] = [[] for _ in text_list]
    seen_spans_per_doc: List[Set[Tuple[int, int, str]]] = [set() for _ in text_list]
    unique_entity_texts: List[str] = []
    seen_entity_texts: Set[str] = set()

    for nlp in pipelines:
        if hasattr(nlp, "pipe"):
            docs_iter: Iterable[Any] = nlp.pipe(text_list, batch_size=batch_size)
        else:
            docs_iter = (nlp(t) for t in text_list)

        for doc_idx, doc in enumerate(docs_iter):
            for ent in getattr(doc, "ents", ()):
                ent_text = ent.text.strip()
                if not ent_text:
                    continue
                span_key = (int(ent.start_char), int(ent.end_char), ent_text.lower())
                if span_key in seen_spans_per_doc[doc_idx]:
                    continue
                seen_spans_per_doc[doc_idx].add(span_key)
                raw_spans_per_doc[doc_idx].append(
                    (ent_text, str(ent.label_), int(ent.start_char), int(ent.end_char))
                )
                if ent_text not in seen_entity_texts:
                    seen_entity_texts.add(ent_text)
                    unique_entity_texts.append(ent_text)

    # 2. Batch-resolve all unique entity strings across the entire corpus in one pass
    resolved_map = resolve_ontology_concepts_batch(
        unique_entity_texts,
        enable_fuzzy_tfidf=enable_fuzzy_tfidf,
        min_tfidf_score=min_tfidf_score,
    )

    # 3. Assemble linked entity dictionaries per document
    linked_docs: List[List[Dict[str, Any]]] = []
    for doc_spans in raw_spans_per_doc:
        doc_entities: List[Dict[str, Any]] = []
        for ent_text, ent_label, start_char, end_char in doc_spans:
            resolution = dict(resolved_map.get(ent_text) or resolve_ontology_concept(
                ent_text,
                enable_fuzzy_tfidf=enable_fuzzy_tfidf,
                min_tfidf_score=min_tfidf_score,
            ))
            resolution["text"] = ent_text
            resolution["label"] = ent_label
            resolution["start_char"] = start_char
            resolution["end_char"] = end_char
            doc_entities.append(resolution)
        linked_docs.append(doc_entities)

    return linked_docs[0] if is_single else linked_docs