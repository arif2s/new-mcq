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
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

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
_LOCAL = threading.local()
_ONTOLOGY_DB_EPOCH: int = 0
_CONFIGURED_SCISPACY_MODEL_CACHE: Optional[str] = None

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

def _get_ontology_db_conn() -> sqlite3.Connection:
    """
    Returns a thread-local cached SQLite connection configured with WAL and 256 MB MMAP
    to satisfy Invariant 3 and prevent connection thrashing.
    Automatically reconnects if _ONTOLOGY_DB_EPOCH advances after a rebuild.
    """
    conn = getattr(_LOCAL, "ontology_conn", None)
    conn_epoch = getattr(_LOCAL, "ontology_conn_epoch", -1)

    if conn is not None and conn_epoch == _ONTOLOGY_DB_EPOCH:
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            _LOCAL.ontology_conn = None
    elif conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _LOCAL.ontology_conn = None

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
    _LOCAL.ontology_conn = conn
    _LOCAL.ontology_conn_epoch = _ONTOLOGY_DB_EPOCH
    return conn


@contextmanager
def _get_db():
    """Context manager for thread-local connection reuse without explicit close."""
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

@functools.lru_cache(maxsize=8192)
def normalize_alias(text: str) -> str:
    """Canonical normalization for ontology alias matching (handles inverted MeSH descriptors)."""
    if not text:
        return ""
    raw = str(text).strip()
    if "," in raw and raw.count(",") == 1:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) == 2 and len(parts[0].split()) <= 4 and len(parts[1].split()) <= 4:
            raw = f"{parts[1]} {parts[0]}"
    cleaned = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


@functools.lru_cache(maxsize=4096)
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
                # 1. Look up CUI via alias_norm or direct CUI match
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


# ============================================================================
# 4. FILE DISCOVERY FOR DOWNLOADED S3 LINKERS & .JSONL FILES
# ============================================================================

def _find_linker_subfolder(kb_key: str) -> Optional[Path]:
    """Locates the folder containing concept_aliases.json / tfidf_vectorizer.joblib."""
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
                return folder

    if LINKERS_DIR.exists():
        for p in LINKERS_DIR.rglob("concept_aliases.json"):
            parent_name = p.parent.name.lower()
            if parent_name == kb_key or (kb_key == "go" and "gene" in parent_name):
                return p.parent
    return None


def _find_kb_jsonl_file(kb_key: str) -> Optional[Path]:
    """Locates the .jsonl Knowledge Base definition file for a given ontology."""
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
                return candidate

    if LINKERS_DIR.exists():
        for fname in filenames:
            for found in LINKERS_DIR.rglob(fname):
                if found.is_file():
                    return found
    return None


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

        ontologies_status[kb_key] = {
            "id": kb_key,
            "name": meta["name"],
            "ready_to_index": has_jsonl or has_aliases,
            "has_jsonl": has_jsonl,
            "jsonl_path": str(jsonl_path) if jsonl_path else None,
            "has_aliases_json": has_aliases,
            "has_tfidf": has_tfidf,
            "has_nmslib": has_nmslib,
            "folder_path": str(folder) if folder else None,
        }

    tui_tree_exists = any(
        p.exists()
        for p in (
            KBS_DIR / "umls_semantic_type_tree.tsv",
            LINKERS_DIR / "umls_semantic_type_tree.tsv",
        )
    )

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

        if force_rebuild:
            conn.executescript("""
                DROP INDEX IF EXISTS idx_kb_aliases_norm;
                DROP INDEX IF EXISTS idx_kb_aliases_cui;
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

            def _flush_batches() -> None:
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
                conn.commit()

            # Path A: Stream .jsonl line-by-line (Constant ~5 MB RAM ceiling)
            if jsonl_path and jsonl_path.exists():
                logger.info("Streaming ontology JSONL: %s", jsonl_path)
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

                        if len(concept_batch) >= 10000 or len(alias_batch) >= 25000:
                            _flush_batches()

                _flush_batches()

            # Path B: Handle concept_aliases.json if present without crashing on lists
            if aliases_json_path and aliases_json_path.exists() and not seen_cuis_in_kb:
                logger.info("Checking concept_aliases.json from %s", aliases_json_path)
                try:
                    with open(aliases_json_path, "r", encoding="utf-8", errors="ignore") as af:
                        aliases_data = json.load(af)

                    if isinstance(aliases_data, dict):
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

                            if len(concept_batch) >= 10000 or len(alias_batch) >= 25000:
                                _flush_batches()
                        _flush_batches()
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
            "Optimizing B-Tree lookup indices on ontology_kb.db...",
        )

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_kb_aliases_norm ON kb_aliases(alias_norm, is_canonical DESC, priority ASC);
            CREATE INDEX IF NOT EXISTS idx_kb_aliases_cui ON kb_aliases(cui);
            PRAGMA synchronous = NORMAL;
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
# 6. FAST CUI & SYNONYM RESOLVER (SQLITE B-TREE + CHAR 3-GRAM TF-IDF FALLBACK)
# ============================================================================

_TFIDF_CACHE: Dict[str, Dict[str, Any]] = {}


def _load_tfidf_matcher(kb_key: str) -> Optional[Dict[str, Any]]:
    """Lazy thread-safe loader for offline character 3-gram TF-IDF matrices."""
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


@functools.lru_cache(maxsize=4096)
def resolve_ontology_concept(
    term: str,
    enable_fuzzy_tfidf: bool = True,
    min_tfidf_score: float = 0.84,
) -> Dict[str, Any]:
    """
    Resolves a clinical phrase against offline ontologies using exact B-Tree lookup + TF-IDF fallback.
    Returns dictionary conforming strictly to TopicExtractorOutputDict['topic_details'] contract.
    """
    norm = normalize_alias(term)
    if not norm or len(norm) < 2:
        return {
            "matched": False,
            "cui": "",
            "canonical_name": term,
            "semantic_category": "unknown",
            "score": 0.0,
        }

    # 1. Fast SQLite B-Tree Exact & Canonical Alias Lookup (< 0.2ms, Zero RAM allocation)
    if ONTOLOGY_DB_PATH.exists():
        try:
            with _get_db() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT c.cui, c.canonical_name, c.kb_source, c.semantic_category, c.tui_list, c.definition
                    FROM kb_aliases a
                    JOIN kb_concepts c ON a.cui = c.cui
                    WHERE a.alias_norm = ?
                    ORDER BY a.is_canonical DESC, a.priority ASC
                    LIMIT 1
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

    # 2. Sparse Character 3-Gram TF-IDF Lookup across MeSH, RxNorm, HPO (Zero dense buffer allocation)
    if enable_fuzzy_tfidf and len(norm) >= 5:
        import numpy as np

        best_candidate: Optional[Dict[str, Any]] = None
        highest_score = float(min_tfidf_score)

        for kb_key in ("mesh", "rxnorm", "hpo"):
            matcher = _load_tfidf_matcher(kb_key)
            if not matcher:
                continue
            try:
                q_vec = matcher["vectorizer"].transform([term])
                if q_vec.nnz == 0:
                    continue

                sparse_scores = matcher["matrix"].dot(q_vec.T).tocoo()
                if sparse_scores.nnz == 0:
                    continue

                max_data_pos = int(np.argmax(sparse_scores.data))
                score = float(sparse_scores.data[max_data_pos])
                best_idx = int(sparse_scores.row[max_data_pos])

                if score >= highest_score and best_idx < len(matcher["aliases"]):
                    matched_alias = matcher["aliases"][best_idx]
                    matched_norm = normalize_alias(matched_alias)
                    if ONTOLOGY_DB_PATH.exists():
                        with _get_db() as conn:
                            cur = conn.cursor()
                            cur.execute(
                                """
                                SELECT c.cui, c.canonical_name, c.kb_source, c.semantic_category, c.tui_list, c.definition
                                FROM kb_aliases a
                                JOIN kb_concepts c ON a.cui = c.cui
                                WHERE a.alias_norm = ?
                                ORDER BY a.is_canonical DESC, a.priority ASC
                                LIMIT 1
                                """,
                                (matched_norm,),
                            )
                            row = cur.fetchone()
                            if row:
                                highest_score = score
                                best_candidate = {
                                    "matched": True,
                                    "cui": row["cui"],
                                    "canonical_name": row["canonical_name"],
                                    "kb_source": row["kb_source"],
                                    "semantic_category": row["semantic_category"],
                                    "tui_list": row["tui_list"] or "",
                                    "definition": row["definition"] or "",
                                    "score": round(score, 4),
                                }
                                if score >= 0.98:
                                    break
            except Exception as exc:
                logger.debug("TF-IDF evaluation error for %s: %s", kb_key, exc)
                continue

        if best_candidate:
            return best_candidate

    return {
        "matched": False,
        "cui": "",
        "canonical_name": term,
        "semantic_category": "unknown",
        "score": 0.0,
    }


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


def load_scispacy_pipeline(model_id: Optional[str] = None) -> List[Any]:
    """
    Loads and caches the configured SciSpaCy pipeline(s) strictly on CPU with thread safety.
    Guarantees zero VRAM allocation and evicts stale pipelines to respect the <500 MB RAM budget.
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

        def _load_single(m_name: str):
            local_dir = _find_extracted_spacy_model_dir(m_name)
            if local_dir:
                return spacy.load(str(local_dir))
            return spacy.load(m_name)

        pipelines: List[Any] = []
        if target_id == "en_core_sci_md+bionlp+bc5cdr":
            for m in ("en_core_sci_md", "en_ner_bionlp13cg_md", "en_ner_bc5cdr_md"):
                try:
                    pipelines.append(_load_single(m))
                except Exception as exc:
                    logger.warning("Could not load sub-model %s: %s", m, exc)
        else:
            try:
                pipelines.append(_load_single(target_id))
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
                    pipelines.append(_load_single(fallback_name))
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
