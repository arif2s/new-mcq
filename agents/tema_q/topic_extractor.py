"""
agents/tema_q/topic_extractor.py

Production-grade clinical topic extraction and ontology normalization engine
for Medisearch AI / Clinical MCQ Hub.

Key Characteristics:
- Strictly CPU-only (zero VRAM overhead; preserves GPU memory for local LLMs).
- Clause & option boundary isolation (resolves merged/mangled multi-concept spans).
- Anatomical preposition & organ transposition mapping (e.g., "amyloidosis of the heart",
  "heart amyloidosis", and "amyloidosis heart" -> "Cardiac amyloidosis").
- Microorganism binomial & abbreviated nomenclature preservation (e.g., "S. aureus", "E. coli").
- Pharmacological stem recognition (-cillin, -olol, -statin, etc.) and standalone drug matching.
- Subtype protection guard: Prevents merging distinct disease subtypes, stages, or lateralities
  (e.g., Type 1 vs. Type 2 diabetes; Acute vs. Chronic pancreatitis).
- Multi-tier verification: Verified (high confidence / pre-selected) vs. Doubtful (low confidence).
- Thread-safe singleton models, zero-DDL cached ignore lists, and thread-boundary safety.
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import unicodedata

# ---------------------------------------------------------------------------
# Enforce Invariant 1: Zero VRAM for backend workers
# ---------------------------------------------------------------------------
os.environ["CUDA_VISIBLE_DEVICES"] = ""

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project Configuration & Directory Aliases
# ---------------------------------------------------------------------------
try:
    import config

    DATA_DIR: Path = Path(getattr(config, "DATA_DIR", "data"))
    LINKERS_DIR: Path = Path(getattr(config, "LINKERS_DIR", DATA_DIR / "linkers"))
    MODELS_DIR: Path = Path(getattr(config, "MODELS_DIR", DATA_DIR / "models"))
    SCISPACY_MODELS_DIR: Path = Path(
        getattr(config, "SCISPACY_MODELS_DIR", MODELS_DIR / "scispacy")
    )
    ONTOLOGY_DB_PATH: Path = Path(
        getattr(config, "ONTOLOGY_DB_PATH", LINKERS_DIR / "ontology_kb.db")
    )
    DB_PATH: Path = Path(getattr(config, "DB_PATH", DATA_DIR / "mcq_hub.db"))
except ImportError:
    DATA_DIR = Path("data")
    LINKERS_DIR = DATA_DIR / "linkers"
    MODELS_DIR = DATA_DIR / "models"
    SCISPACY_MODELS_DIR = MODELS_DIR / "scispacy"
    ONTOLOGY_DB_PATH = LINKERS_DIR / "ontology_kb.db"
    DB_PATH = DATA_DIR / "mcq_hub.db"

# ---------------------------------------------------------------------------
# Database Connection Isolation (Contract-Guarded)
# ---------------------------------------------------------------------------
try:
    from core.database import get_db_connection
except ImportError:

    def get_db_connection() -> sqlite3.Connection:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), timeout=20.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 20000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA mmap_size = 536870912;")  # 512 MB cap
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA cache_size = -64000;")
        return conn


try:
    from core.database import lookup_concept_by_cui, lookup_ontology_alias  # type: ignore
except ImportError:
    lookup_ontology_alias = None
    lookup_concept_by_cui = None

try:
    from parsers.scispacy_linker_engine import (
        load_scispacy_pipeline,
        normalize_alias,
        resolve_ontology_concept,
    )
except ImportError:

    def resolve_ontology_concept(
        term: str,
        enable_fuzzy_tfidf: bool = True,
        min_tfidf_score: float = 0.84,
    ) -> Dict[str, Any]:
        return {
            "matched": False,
            "cui": "",
            "canonical_name": term.strip().title(),
            "kb_source": "none",
            "semantic_category": "unknown",
            "tui_list": "",
            "definition": "",
            "score": 0.0,
        }

    def load_scispacy_pipeline(model_id: Optional[str] = None) -> List[Any]:
        return []

    def normalize_alias(text: str) -> str:
        return text.strip().lower()


try:
    from parsers.zim_reader import resolve_zim_topic as _raw_resolve_zim_topic  # type: ignore
except ImportError:
    _raw_resolve_zim_topic = None

try:
    from parsers.tantivy_engine import search_index
except ImportError:

    def search_index(query_str: str, limit_per_source: int = 1) -> Dict[str, list]:
        return {"obsidian": [], "pdf": [], "zim": []}


# ---------------------------------------------------------------------------
# Singleton MedSpaCy / SciSpaCy Model Loader (Thread-Safe, Invariant 8)
# ---------------------------------------------------------------------------
_SPACY_FALLBACK_NLP: Optional[Any] = None
_SPACY_INIT_LOCK = threading.RLock()
_SPACY_EXEC_LOCK = threading.RLock()


def _get_active_spacy_pipelines() -> List[Any]:
    """
    Retrieves the active SciSpaCy pipeline(s) from parsers.scispacy_linker_engine
    so model switches in Settings never leave duplicate pipelines in RAM.
    """
    global _SPACY_FALLBACK_NLP
    try:
        pipelines = load_scispacy_pipeline()
        if pipelines:
            return [p for p in pipelines if p is not None]
    except Exception as exc:
        logger.debug("load_scispacy_pipeline invocation note: %s", exc)

    if _SPACY_FALLBACK_NLP is not None:
        return [_SPACY_FALLBACK_NLP]

    with _SPACY_INIT_LOCK:
        if _SPACY_FALLBACK_NLP is not None:
            return [_SPACY_FALLBACK_NLP]
        try:
            import spacy

            if hasattr(spacy, "require_cpu"):
                spacy.require_cpu()
            blank_nlp = spacy.blank("en")
            if "sentencizer" not in blank_nlp.pipe_names:
                blank_nlp.add_pipe("sentencizer")
            _SPACY_FALLBACK_NLP = blank_nlp
            logger.info("Initialized fallback blank English spaCy pipeline.")
            return [_SPACY_FALLBACK_NLP]
        except Exception as exc:
            logger.warning("Could not initialize fallback spaCy pipeline: %s", exc)
            return []


def get_medspacy_model() -> Any:
    """
    Returns the primary loaded spaCy / SciSpaCy Language pipeline running strictly on CPU.
    Satisfies blueprint.md Section 3 signature (`get_medspacy_model() -> Any`).
    """
    pipelines = _get_active_spacy_pipelines()
    return pipelines[0] if pipelines else None


# ---------------------------------------------------------------------------
# Ignore List & DB Persistence (Zero DDL in Hot Loops + Fast In-Memory TTL)
# ---------------------------------------------------------------------------
_IGNORED_TOPICS_CACHE: Optional[Set[str]] = None
_IGNORED_TOPICS_CACHE_TIMESTAMP: float = 0.0
_IGNORED_TOPICS_CACHE_TTL: float = 5.0
_IGNORED_TOPICS_LOCK = threading.RLock()
_IGNORED_TABLE_VERIFIED: bool = False


def normalize_topic_key(text: str) -> str:
    """
    Normalizes a topic string into a consistent lookup key:
    - Unicode NFKC normalization
    - Strips leading articles ('the', 'a', 'an')
    - Strips punctuation wrappers
    - Collapses whitespace
    """
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", str(text)).strip().lower()
    norm = re.sub(r"^(?:the|a|an)\s+", "", norm)
    norm = re.sub(r"^[\W_]+|[\W_]+$", "", norm)
    norm = re.sub(r"[\s_]+", " ", norm)
    return norm.strip()


def _ensure_ignored_topics_table_once(conn: sqlite3.Connection) -> None:
    global _IGNORED_TABLE_VERIFIED
    if _IGNORED_TABLE_VERIFIED:
        return
    with _IGNORED_TOPICS_LOCK:
        if _IGNORED_TABLE_VERIFIED:
            return
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ignored_topics (
                    topic_norm TEXT PRIMARY KEY,
                    original_text TEXT NOT NULL,
                    created_at REAL DEFAULT (unixepoch('now'))
                )
                """
            )
            _IGNORED_TABLE_VERIFIED = True
        except Exception as exc:
            logger.debug("Failed checking ignored_topics table: %s", exc)


def get_ignored_topics_set(force_refresh: bool = False) -> Set[str]:
    """Retrieves all normalized topic keys marked as permanently ignored with memory TTL caching."""
    global _IGNORED_TOPICS_CACHE, _IGNORED_TOPICS_CACHE_TIMESTAMP

    now = time.time()
    if (
        not force_refresh
        and _IGNORED_TOPICS_CACHE is not None
        and (now - _IGNORED_TOPICS_CACHE_TIMESTAMP) < _IGNORED_TOPICS_CACHE_TTL
    ):
        return set(_IGNORED_TOPICS_CACHE)

    with _IGNORED_TOPICS_LOCK:
        if (
            not force_refresh
            and _IGNORED_TOPICS_CACHE is not None
            and (time.time() - _IGNORED_TOPICS_CACHE_TIMESTAMP) < _IGNORED_TOPICS_CACHE_TTL
        ):
            return set(_IGNORED_TOPICS_CACHE)

        try:
            with contextlib.closing(get_db_connection()) as conn:
                _ensure_ignored_topics_table_once(conn)
                cursor = conn.cursor()
                cursor.execute("SELECT topic_norm FROM ignored_topics")
                rows = cursor.fetchall()
                loaded = {str(r[0]) for r in rows if r and r[0]}
                _IGNORED_TOPICS_CACHE = loaded
                _IGNORED_TOPICS_CACHE_TIMESTAMP = time.time()
                return set(loaded)
        except Exception as exc:
            logger.error("Error loading ignored topics: %s", exc)
            return set(_IGNORED_TOPICS_CACHE) if _IGNORED_TOPICS_CACHE is not None else set()


def add_ignored_topic(term: str) -> bool:
    """Adds a term to the persistent ignored_topics SQLite table and invalidates cache."""
    key = normalize_topic_key(term)
    if not key:
        return False
    try:
        with contextlib.closing(get_db_connection()) as conn:
            _ensure_ignored_topics_table_once(conn)
            conn.execute(
                "INSERT OR REPLACE INTO ignored_topics (topic_norm, original_text, created_at) VALUES (?, ?, ?)",
                (key, str(term).strip(), time.time()),
            )
        with _IGNORED_TOPICS_LOCK:
            if _IGNORED_TOPICS_CACHE is not None:
                _IGNORED_TOPICS_CACHE.add(key)
        return True
    except Exception as exc:
        logger.error("Error adding ignored topic '%s': %s", term, exc)
        return False


def remove_ignored_topic(term: str) -> bool:
    """Removes a term from the persistent ignored_topics SQLite table and invalidates cache."""
    key = normalize_topic_key(term)
    if not key:
        return False
    try:
        with contextlib.closing(get_db_connection()) as conn:
            _ensure_ignored_topics_table_once(conn)
            conn.execute("DELETE FROM ignored_topics WHERE topic_norm = ?", (key,))
        with _IGNORED_TOPICS_LOCK:
            if _IGNORED_TOPICS_CACHE is not None:
                _IGNORED_TOPICS_CACHE.discard(key)
        return True
    except Exception as exc:
        logger.error("Error removing ignored topic '%s': %s", term, exc)
        return False


# ---------------------------------------------------------------------------
# Pre-Compiled Regexes & Static Lexicons (Zero Per-Call Recompilation)
# ---------------------------------------------------------------------------
CLINICAL_STOPWORDS: Set[str] = {
    "patient", "female", "male", "man", "woman", "boy", "girl", "infant", "child",
    "year-old", "years-old", "yr-old", "yo", "age", "history", "presentation",
    "examination", "physical exam", "exam", "finding", "findings", "symptom",
    "symptoms", "sign", "signs", "diagnosis", "management", "treatment", "therapy",
    "evaluation", "workup", "assessment", "complaint", "chief complaint",
    "laboratory", "lab", "labs", "study", "studies", "result", "results",
    "biopsy", "imaging", "x-ray", "ct", "ct scan", "mri", "ultrasound", "ecg",
    "ekg", "serum", "plasma", "blood", "urine", "normal", "abnormal", "elevated",
    "decreased", "increased", "reduced", "positive", "negative", "high", "low",
    "most likely", "best next step", "initial step", "definitive diagnosis",
    "gold standard", "mechanism of action", "adverse effect", "side effect",
    "risk factor", "risk factors", "contraindication", "indication", "following",
    "option", "correct answer", "explanation", "question", "case", "hospital",
    "clinic", "admission", "discharge", "day", "days", "week", "weeks", "month",
    "months", "year", "years", "hour", "hours", "minute", "minutes", "dose",
    "daily", "iv", "po", "subcutaneous", "oral", "intravenous", "mild", "moderate",
    "severe", "unremarkable", "reveals", "showed", "demonstrated", "presents with",
    "complains of", "history of", "consistent with", "associated with",
    "which of the following", "what is the", "most likely diagnosis", "next step in management",
}

ISOLATED_GENERIC_WORDS: Set[str] = {
    "heart", "liver", "lung", "lungs", "kidney", "kidneys", "brain", "bone",
    "bones", "skin", "eye", "eyes", "ear", "ears", "nose", "blood", "cell",
    "cells", "artery", "vein", "nerve", "patient", "doctor", "male", "female",
    "man", "woman", "age", "adult", "case", "disease", "disorder", "syndrome",
    "condition", "sign", "symptom", "treatment", "management", "therapy",
    "diagnosis", "finding", "result", "exam", "level", "levels", "test", "tests",
    "study", "studies", "unit", "score", "rate", "ratio", "value", "values",
    "type", "stage", "grade", "class", "group", "mild", "moderate", "severe",
    "acute", "chronic", "left", "right", "bilateral", "unilateral", "primary",
    "secondary", "tertiary", "normal", "abnormal", "high", "low", "positive", "negative",
    "type 1", "type 2", "stage 1", "stage 2", "stage 3", "stage 4", "stage i",
    "stage ii", "stage iii", "stage iv", "pain",
}

GENERIC_QUESTION_BOILERPLATE = re.compile(
    r"\b(?:"
    r"which\s+of\s+the\s+following\s+(?:is\s+the\s+)?(?:most\s+likely\s+)?(?:diagnosis|cause|mechanism|step|treatment|finding|management|complication|organism)\s*[\?:-]*|"
    r"what\s+is\s+the\s+(?:most\s+likely\s+)?(?:diagnosis|next\s+step|initial\s+step|management|cause|mechanism|treatment)\s*[\?:-]*|"
    r"most\s+likely\s+diagnosis\s+is|"
    r"best\s+next\s+step\s+in\s+management\s+is|"
    r"initial\s+management\s+is|"
    r"physical\s+examination\s+reveals?|"
    r"vital\s+signs\s+reveal|"
    r"laboratory\s+studies\s+show|"
    r"history\s+of\s+present\s+illness|"
    r"past\s+medical\s+history"
    r")\b",
    re.IGNORECASE,
)

PUNCT_SPLIT_REGEX = re.compile(r"[,;:|/()\[\]\n\r\t—–•]")

CLAUSE_CONJUNCTIONS_REGEX = re.compile(
    r"\b(?:and|or|vs\.?|versus|with|without|due to|caused by|secondary to|associated with|followed by)\b",
    re.IGNORECASE,
)

NEGATION_SCOPE_TRIGGER = re.compile(
    r"\b(?:no|denies|denied|without|negative for|absence of|rules out|ruled out|free of|unremarkable for|no evidence of|fails to reveal)\b",
    re.IGNORECASE,
)

CONTRAST_RESET_REGEX = re.compile(
    r"\b(?:but|however|although|yet|except|nevertheless)\b", re.IGNORECASE
)

LAB_VALUE_OR_NUMERIC_REGEX = re.compile(
    r"^(?:\d+(?:\.\d+)?|\d+/\d+)(?:\s*(?:mg/dl|g/dl|mmol/l|meq/l|ug/dl|mcg/dl|u/l|iu/l|mmhg|bpm|/min|%|cells/u[lL]|fl|pg|cm|mm|m|kg|g|mg|mcg|ml|l))?$",
    re.IGNORECASE,
)

AGE_TERM_REGEX = re.compile(
    r"^\d+[- ]?(?:year|yr|month|mo|week|wk|day|hr|hour)[- ]?(?:old)?$",
    re.IGNORECASE,
)

SENTENCE_SPLIT_REGEX = re.compile(
    r"(?<=[.!?])(?<!\b[A-Z]\.)(?<!\bvs\.)(?<!\bVs\.)(?<!\beg\.)(?<!\bie\.)(?<!\bdr\.)(?<!\bDr\.)(?<!\bpt\.)(?<!\bSt\.)\s+"
)

SPACY_EXCLUDED_NER_TYPES: Set[str] = {
    "DATE", "TIME", "PERCENT", "MONEY", "QUANTITY", "ORDINAL", "CARDINAL",
    "GPE", "LOC", "FAC", "LANGUAGE", "PERSON", "NORP",
}

ORGAN_TO_ADJECTIVE: Dict[str, str] = {
    "heart": "cardiac",
    "kidney": "renal",
    "kidneys": "renal",
    "liver": "hepatic",
    "lung": "pulmonary",
    "lungs": "pulmonary",
    "brain": "cerebral",
    "pancreas": "pancreatic",
    "stomach": "gastric",
    "spleen": "splenic",
    "intestine": "intestinal",
    "intestines": "intestinal",
    "bowel": "intestinal",
    "colon": "colonic",
    "rectum": "rectal",
    "thyroid": "thyroid",
    "adrenal": "adrenal",
    "bone": "osseous",
    "skin": "cutaneous",
    "eye": "ocular",
    "eyes": "ocular",
    "aorta": "aortic",
    "pericardium": "pericardial",
    "pleura": "pleural",
    "peritoneum": "peritoneal",
    "prostate": "prostatic",
    "ovary": "ovarian",
    "ovaries": "ovarian",
    "uterus": "uterine",
    "cervix": "cervical",
    "breast": "mammary",
    "bladder": "urinary bladder",
    "gallbladder": "biliary",
    "bile duct": "biliary",
    "appendix": "appendiceal",
}

ADJECTIVE_TO_ORGAN: Dict[str, str] = {
    "cardiac": "heart",
    "renal": "kidney",
    "hepatic": "liver",
    "pulmonary": "lung",
    "cerebral": "brain",
    "pancreatic": "pancreas",
    "gastric": "stomach",
    "splenic": "spleen",
    "intestinal": "intestine",
    "colonic": "colon",
    "rectal": "rectum",
    "thyroid": "thyroid",
    "adrenal": "adrenal",
    "osseous": "bone",
    "cutaneous": "skin",
    "ocular": "eye",
    "aortic": "aorta",
    "pericardial": "pericardium",
    "pleural": "pleura",
    "peritoneal": "peritoneum",
    "prostatic": "prostate",
    "ovarian": "ovary",
    "uterine": "uterus",
    "cervical": "cervix",
    "mammary": "breast",
    "biliary": "gallbladder",
    "appendiceal": "appendix",
}

PREPOSITIONAL_PATHOLOGY_REGEX = re.compile(
    r"\b([A-Za-z]{3,}(?:\s+[A-Za-z]{3,})?)\s+of\s+(?:the\s+)?([A-Za-z]{3,}(?:\s+[A-Za-z]{3,})?)\b",
    re.IGNORECASE,
)

KNOWN_GENERA: Set[str] = {
    "staphylococcus", "streptococcus", "escherichia", "pseudomonas", "plasmodium",
    "mycobacterium", "clostridioides", "clostridium", "listeria", "neisseria",
    "treponema", "borrelia", "legionella", "helicobacter", "campylobacter",
    "haemophilus", "klebsiella", "enterococcus", "proteus", "serratia",
    "acinetobacter", "bacteroides", "chlamydia", "mycoplasma", "rickettsia",
    "salmonella", "shigella", "vibrio", "yersinia", "bacillus",
    "corynebacterium", "bordetella", "brucella", "francisella", "candida",
    "aspergillus", "cryptococcus", "histoplasma", "coccidioides", "blastomyces",
    "pneumocystis", "toxoplasma", "giardia", "entamoeba", "trichomonas",
    "leishmania", "trypanosoma", "schistosoma", "taenia", "echinococcus",
    "moraxella", "nocardia", "actinomyces", "bartonella", "coxiella",
}

VIRAL_PARASITIC_ENTITIES: Set[str] = {
    "cytomegalovirus", "epstein-barr virus", "herpes simplex virus", "varicella-zoster virus",
    "human immunodeficiency virus", "hepatitis a virus", "hepatitis b virus",
    "hepatitis c virus", "hepatitis d virus", "hepatitis e virus", "respiratory syncytial virus",
    "human papillomavirus", "coxsackievirus", "norovirus", "rotavirus", "adenovirus",
    "parvovirus b19", "rabies virus", "dengue virus", "yellow fever virus", "chikungunya virus",
    "zika virus", "ebola virus", "influenza virus",
}

BINOMIAL_SPECIES_REGEX = re.compile(r"\b([A-Z][a-z]{2,})\s+([a-z]{3,}(?:-[a-z]{3,})?)\b")
ABBREV_SPECIES_REGEX = re.compile(r"\b([A-Z])\.\s*([a-z]{3,}(?:-[a-z]{3,})?)\b")

PHARMA_SUFFIXES: Tuple[str, ...] = (
    "cillin", "olol", "statin", "mab", "pril", "sartan", "prazole",
    "floxacin", "mycin", "micin", "cycline", "dipine", "terol", "zosin",
    "tidine", "gliptin", "gliflozin", "glutide", "parin", "xaban",
    "grel", "setron", "triptan", "afil", "nib", "tinib", "zumab",
    "ximab", "umab", "vir", "navir", "vudine", "caine", "semide",
    "pam", "lam", "pine", "dronate", "lukast", "coxib", "zole",
    "toin", "barbital", "sone", "olone", "onide",
)

KNOWN_DRUGS: Set[str] = {
    "warfarin", "heparin", "aspirin", "acetaminophen", "paracetamol",
    "morphine", "codeine", "fentanyl", "methadone", "buprenorphine",
    "naloxone", "insulin", "metformin", "digoxin", "atropine",
    "epinephrine", "norepinephrine", "dopamine", "amiodarone",
    "lidocaine", "phenytoin", "carbamazepine", "valproate", "lithium",
    "methotrexate", "azathioprine", "cyclosporine", "tacrolimus",
    "cisplatin", "carboplatin", "doxorubicin", "cyclophosphamide",
    "hydroxychloroquine", "allopurinol", "colchicine", "levothyroxine",
    "propylthiouracil", "methimazole", "spironolactone", "furosemide",
    "hydrochlorothiazide", "mannitol", "acetazolamide",
}

CLINICAL_HEAD_NOUNS: Set[str] = {
    "amyloidosis", "infarction", "lesion", "palsy", "artery", "nerve", "vein",
    "carcinoma", "acidosis", "alkalosis", "syndrome", "disease", "disorder",
    "failure", "insufficiency", "stenosis", "regurgitation", "tamponade",
    "embolism", "thrombosis", "aneurysm", "dissection", "hypertension",
    "hypotension", "shock", "sepsis", "infection", "abscess", "ulcer", "fistula",
    "obstruction", "perforation", "ischemia", "necrosis", "gangrene",
    "inflammation", "edema", "effusion", "pneumothorax", "hemothorax",
    "arrhythmia", "tachycardia", "bradycardia", "fibrillation", "flutter",
    "block", "hepatitis", "cirrhosis", "pancreatitis", "nephritis",
    "nephropathy", "glomerulonephritis", "encephalopathy", "meningitis",
    "encephalitis", "neuropathy", "myopathy", "dystrophy", "atrophy",
    "hypertrophy", "hyperplasia", "metaplasia", "dysplasia", "neoplasm",
    "tumor", "adenoma", "lymphoma", "leukemia", "sarcoma", "melanoma",
    "anemia", "leukopenia", "thrombocytopenia", "leukocytosis", "thrombocytosis",
    "diabetes", "thyroiditis", "arthritis", "arthropathy", "spondylitis",
    "osteomyelitis", "calculus", "lithiasis", "cholelithiasis", "nephrolithiasis",
    "hernia", "diverticulitis", "diverticulosis", "colitis", "gastritis", "esophagitis",
}

EPONYMIC_SYNDROME_REGEX = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:syndrome|disease|sign|phenomenon|node|triad|criteria|classification)\b"
)
MEDICAL_SUFFIX_REGEX = re.compile(
    r"\b(?:[A-Za-z-]{3,}\s+){0,2}[A-Za-z-]{3,}(?:emia|osis|itis|oma|pathy|trophy|plasia|uria|penia|cytosis|ectasis|sclerosis|megaly)\b",
    re.IGNORECASE,
)
_HEAD_NOUNS_PATTERN_STR = "|".join(
    re.escape(h) for h in sorted(CLINICAL_HEAD_NOUNS, key=len, reverse=True)
)
HEAD_NOUN_MATCH_REGEX = re.compile(
    rf"\b(?:[A-Za-z-]{{3,}}\s+){{0,2}}(?:{_HEAD_NOUNS_PATTERN_STR})\b",
    re.IGNORECASE,
)

LAYPERSON_MEDICAL_SYNONYMS: Dict[str, List[str]] = {
    "heart attack": ["myocardial infarction", "myocardial ischemia", "cardiac arrest", "acute coronary syndrome"],
    "stroke": ["cerebrovascular accident", "brain ischemia", "ischemic stroke"],
    "kidney failure": ["renal failure", "renal insufficiency", "acute kidney injury", "chronic kidney disease"],
    "liver disease": ["hepatic disease", "cirrhosis", "hepatitis"],
    "lung disease": ["pulmonary disease", "copd", "pneumonia"],
    "high blood pressure": ["hypertension", "essential hypertension"],
    "low blood pressure": ["hypotension"],
    "heart failure": ["congestive heart failure", "cardiac failure"],
    "blood clot": ["thrombosis", "embolism", "deep vein thrombosis", "pulmonary embolism"],
    "chest pain": ["angina", "angina pectoris"],
    "shortness of breath": ["dyspnea"],
    "fever": ["pyrexia", "febrile illness"],
    "fainting": ["syncope"],
    "kidney stone": ["nephrolithiasis", "renal calculus"],
    "gallstone": ["cholelithiasis"],
    "blood cancer": ["leukemia", "lymphoma", "multiple myeloma"],
    "skin cancer": ["melanoma", "basal cell carcinoma", "squamous cell carcinoma"],
    "joint pain": ["arthralgia", "arthritis"],
    "heart infection": ["endocarditis", "pericarditis", "myocarditis"],
}

CLINICAL_ACRONYMS: Dict[str, str] = {
    "MI": "Myocardial infarction",
    "AMI": "Acute myocardial infarction",
    "PE": "Pulmonary embolism",
    "DVT": "Deep vein thrombosis",
    "COPD": "Chronic obstructive pulmonary disease",
    "CHF": "Congestive heart failure",
    "HF": "Heart failure",
    "AKI": "Acute kidney injury",
    "CKD": "Chronic kidney disease",
    "ESRD": "End-stage renal disease",
    "GERD": "Gastroesophageal reflux disease",
    "SLE": "Systemic lupus erythematosus",
    "RA": "Rheumatoid arthritis",
    "OA": "Osteoarthritis",
    "MS": "Multiple sclerosis",
    "ALS": "Amyotrophic lateral sclerosis",
    "TB": "Tuberculosis",
    "DM": "Diabetes mellitus",
    "T1DM": "Type 1 diabetes mellitus",
    "T2DM": "Type 2 diabetes mellitus",
    "T1D": "Type 1 diabetes",
    "T2D": "Type 2 diabetes",
    "DKA": "Diabetic ketoacidosis",
    "HHS": "Hyperosmolar hyperglycemic state",
    "DIC": "Disseminated intravascular coagulation",
    "ITP": "Immune thrombocytopenic purpura",
    "TTP": "Thrombotic thrombocytopenic purpura",
    "HUS": "Hemolytic uremic syndrome",
    "ARDS": "Acute respiratory distress syndrome",
    "PUD": "Peptic ulcer disease",
    "IBD": "Inflammatory bowel disease",
    "UC": "Ulcerative colitis",
    "CD": "Crohn disease",
    "PSC": "Primary sclerosing cholangitis",
    "PBC": "Primary biliary cholangitis",
    "SBO": "Small bowel obstruction",
    "LBO": "Large bowel obstruction",
    "BPH": "Benign prostatic hyperplasia",
    "UTI": "Urinary tract infection",
    "PID": "Pelvic inflammatory disease",
    "PCOS": "Polycystic ovary syndrome",
    "OSA": "Obstructive sleep apnea",
    "HCM": "Hypertrophic cardiomyopathy",
    "DCM": "Dilated cardiomyopathy",
    "RCM": "Restrictive cardiomyopathy",
    "ASD": "Atrial septal defect",
    "VSD": "Ventricular septal defect",
    "PDA": "Patent ductus arteriosus",
    "TOF": "Tetralogy of Fallot",
}

SUBTYPE_MODIFIER_PATTERNS = {
    "subtype": re.compile(r"\btype\s*([1-4]|i{1,3}|iv|[a-d])\b", re.IGNORECASE),
    "stage": re.compile(r"\bstage\s*(0|i{1,3}[a-c]?|iv[a-c]?|[1-4][a-c]?)\b", re.IGNORECASE),
    "grade": re.compile(r"\bgrade\s*([1-4]|i{1,3}|iv|low|high)\b", re.IGNORECASE),
    "class": re.compile(r"\bclass\s*([1-4]|i{1,3}|iv|[a-d])\b", re.IGNORECASE),
    "chronicity": re.compile(r"\b(acute|chronic|subacute)\b", re.IGNORECASE),
    "laterality": re.compile(r"\b(left|right|bilateral|unilateral)\b", re.IGNORECASE),
    "etiology": re.compile(r"\b(primary|secondary|tertiary|congenital|acquired)\b", re.IGNORECASE),
    "nature": re.compile(r"\b(benign|malignant|invasive|in situ|non-invasive)\b", re.IGNORECASE),
}

ROMAN_NUMERAL_MAP = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4",
    "ia": "1a", "ib": "1b", "iia": "2a", "iib": "2b",
    "iiia": "3a", "iiib": "3b", "iiic": "3c", "iva": "4a", "ivb": "4b",
    "a": "a", "b": "b", "c": "c", "d": "d",
}

_ALLOWED_SEMANTIC_CATEGORIES: Set[str] = {
    "disease",
    "microorganism",
    "anatomy",
    "drug_chemical",
    "procedure",
    "unknown",
}


def _normalize_semantic_category(raw_cat: str) -> str:
    """Normalizes ontology categories to the strict TopicExtractorOutputDict contract (Section 4)."""
    c = str(raw_cat or "unknown").strip().lower()
    if c in _ALLOWED_SEMANTIC_CATEGORIES:
        return c
    if c in {"sign_symptom", "clinical", "mesh", "physiology", "pathology"}:
        return "disease"
    return "unknown"


# ---------------------------------------------------------------------------
# Concept Resolution & Fast LRU Caching
# ---------------------------------------------------------------------------
def _lookup_zim_title_cache(term: str) -> Optional[str]:
    """Fast indexed point-lookup in SQLite zim_titles cache (<0.1ms)."""
    try:
        with contextlib.closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT title FROM zim_titles WHERE title = ? COLLATE NOCASE LIMIT 1",
                (term,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return str(row[0])
    except Exception:
        pass
    return None


def resolve_zim_topic(term: str) -> Dict[str, Any]:
    """Unified offline ZIM resolver supporting B-Tree lookup and SQLite caching."""
    if _raw_resolve_zim_topic is not None:
        try:
            res = _raw_resolve_zim_topic(term)
            if res and res.get("matched"):
                return res
        except Exception:
            pass

    cached_title = _lookup_zim_title_cache(term)
    if cached_title:
        return {
            "matched": True,
            "canonical_title": cached_title,
            "zim_path": "",
            "entry_path": "",
        }

    try:
        from parsers.zim_reader import search_zim_articles  # type: ignore

        hits = search_zim_articles(term, limit=1)
        if hits and isinstance(hits, list):
            first = hits[0]
            hit_title = first.get("title", "")
            if hit_title and hit_title.lower() == term.lower():
                return {
                    "matched": True,
                    "canonical_title": hit_title,
                    "zim_path": first.get("file_path", ""),
                    "entry_path": first.get("entry_path", ""),
                }
    except Exception:
        pass

    return {"matched": False, "canonical_title": "", "zim_path": "", "entry_path": ""}


@functools.lru_cache(maxsize=4096)
def _cached_resolve_clinical_concept(clean_term: str) -> Dict[str, Any]:
    """Internal memoized resolver for clinical concepts."""
    # 1. SciSpaCy Linker Engine
    try:
        res = resolve_ontology_concept(clean_term)
        if res.get("matched") and res.get("score", 0.0) >= 0.70:
            return res
    except Exception:
        pass

    # 2. SQLite kb_aliases lookup
    if lookup_ontology_alias is not None:
        try:
            alias_row = lookup_ontology_alias(clean_term)
            if alias_row:
                types = alias_row.get("types", [])
                cat = "disease"
                t_str = " ".join(str(t) for t in types).lower()
                if any(x in t_str for x in ["t007", "bacter", "organism", "virus", "t005"]):
                    cat = "microorganism"
                elif any(x in t_str for x in ["t121", "t200", "pharmacolog", "drug", "chemical"]):
                    cat = "drug_chemical"
                elif any(x in t_str for x in ["t023", "t024", "anatom", "body part"]):
                    cat = "anatomy"
                elif any(x in t_str for x in ["t060", "t061", "procedur", "therap"]):
                    cat = "procedure"

                return {
                    "matched": True,
                    "cui": alias_row.get("cui", ""),
                    "canonical_name": alias_row.get("canonical_name", clean_term.title()),
                    "kb_source": alias_row.get("kb_source", "kb"),
                    "semantic_category": cat,
                    "score": 1.0,
                }
        except Exception:
            pass

    # 3. LibZim Resolution
    try:
        zim_res = resolve_zim_topic(clean_term)
        if zim_res.get("matched"):
            can_title = zim_res.get("canonical_title", clean_term.title())
            return {
                "matched": True,
                "cui": f"ZIM:{normalize_topic_key(can_title)}",
                "canonical_name": can_title,
                "kb_source": "zim",
                "semantic_category": "disease",
                "score": 0.95,
            }
    except Exception:
        pass

    return {
        "matched": False,
        "cui": "",
        "canonical_name": clean_term.title(),
        "score": 0.0,
        "semantic_category": "unknown",
    }


def _resolve_clinical_concept(term: str) -> Dict[str, Any]:
    """Unified multi-tier offline resolver returning mutable dictionary copies."""
    clean_term = term.strip().lower()
    if not clean_term:
        return {
            "matched": False,
            "cui": "",
            "canonical_name": "",
            "score": 0.0,
            "semantic_category": "unknown",
        }
    return dict(_cached_resolve_clinical_concept(clean_term))


def extract_clinical_modifiers(text: str) -> Dict[str, str]:
    """Extracts first clinical modifier flags (Type 1 vs 2, Acute vs Chronic, etc.)."""
    mods: Dict[str, str] = {}
    for mod_name, pattern in SUBTYPE_MODIFIER_PATTERNS.items():
        match = pattern.search(text or "")
        if match:
            raw_val = match.group(1).lower().strip()
            norm_val = ROMAN_NUMERAL_MAP.get(raw_val, raw_val)
            mods[mod_name] = f"{mod_name}_{norm_val}"
    return mods


def extract_all_clinical_modifiers(text: str) -> Dict[str, Set[str]]:
    """Extracts all clinical modifier flags present within a text block to handle comparative sections."""
    mods: Dict[str, Set[str]] = {}
    for mod_name, pattern in SUBTYPE_MODIFIER_PATTERNS.items():
        found = set()
        for match in pattern.finditer(text or ""):
            raw_val = match.group(1).lower().strip()
            norm_val = ROMAN_NUMERAL_MAP.get(raw_val, raw_val)
            found.add(f"{mod_name}_{norm_val}")
        if found:
            mods[mod_name] = found
    return mods


@functools.lru_cache(maxsize=2048)
def _word_boundary_pattern(phrase: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(phrase.lower())}\b", re.IGNORECASE)


def _contains_phrase_bounded(text_lower: str, phrase_lower: str) -> bool:
    """Checks whether phrase_lower occurs in text_lower on strict word boundaries."""
    if not text_lower or not phrase_lower:
        return False
    if text_lower == phrase_lower:
        return True
    if phrase_lower not in text_lower:
        return False
    return bool(_word_boundary_pattern(phrase_lower).search(text_lower))


def generate_candidate_variants(term: str) -> List[str]:
    """Generates acronym, organ-adjective, prepositional, and comma-inverted variants."""
    raw = (term or "").strip()
    if not raw:
        return []

    variants: List[str] = [raw]

    # Comma-inversion (e.g. "transplantation, renal" -> "renal transplantation")
    if "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) == 2:
            inverted = f"{parts[1]} {parts[0]}"
            if inverted not in variants:
                variants.append(inverted)
        elif len(parts) > 2:
            inverted = " ".join(reversed(parts))
            if inverted not in variants:
                variants.append(inverted)

    # Layperson and medical synonym mappings (strict word-boundary matching)
    raw_low = raw.lower()
    clean_low = re.sub(r"^(?:the|a|an)\s+", "", raw_low).strip()
    for lp, med_list in LAYPERSON_MEDICAL_SYNONYMS.items():
        if _contains_phrase_bounded(raw_low, lp) or _contains_phrase_bounded(clean_low, lp):
            for med in med_list:
                if med not in variants:
                    variants.append(med)
        else:
            for med in med_list:
                if _contains_phrase_bounded(raw_low, med) or _contains_phrase_bounded(clean_low, med):
                    if lp not in variants:
                        variants.append(lp)
                    for other_med in med_list:
                        if other_med not in variants:
                            variants.append(other_med)

    # Reverse acronym expansion & contraction
    upper = raw.upper()
    if upper in CLINICAL_ACRONYMS:
        exp = CLINICAL_ACRONYMS[upper]
        if exp not in variants:
            variants.append(exp)

    for acr, exp in CLINICAL_ACRONYMS.items():
        if exp.lower() == raw_low and acr not in variants:
            variants.append(acr)

    # Strip leading articles
    clean = re.sub(r"^(?:the|a|an)\s+", "", raw, flags=re.IGNORECASE).strip()
    if clean and clean not in variants:
        variants.append(clean)

    if "," in clean:
        parts = [p.strip() for p in clean.split(",") if p.strip()]
        if len(parts) == 2:
            inv = f"{parts[1]} {parts[0]}"
            if inv not in variants:
                variants.append(inv)

    current_terms = list(variants)
    for c_term in current_terms:
        c_lower = c_term.lower()

        # 1. Prepositional transposition: "amyloidosis of the heart" -> "cardiac amyloidosis"
        prep_match = PREPOSITIONAL_PATHOLOGY_REGEX.search(c_term)
        if prep_match:
            condition = prep_match.group(1).strip()
            organ = prep_match.group(2).strip().lower()
            if organ in ORGAN_TO_ADJECTIVE:
                adj = ORGAN_TO_ADJECTIVE[organ]
                v = f"{adj} {condition}".strip()
                if v not in variants:
                    variants.append(v)

        # 2. Organ-to-Adjective transposition: "heart amyloidosis" or "amyloidosis heart"
        for org, adj in ORGAN_TO_ADJECTIVE.items():
            if c_lower.startswith(f"{org} "):
                rem = c_term[len(org) + 1 :].strip()
                v = f"{adj} {rem}".strip()
                if v not in variants:
                    variants.append(v)
            elif c_lower.endswith(f" {org}"):
                rem = c_term[: -(len(org) + 1)].strip()
                v = f"{adj} {rem}".strip()
                if v not in variants:
                    variants.append(v)

        # 3. Adjective-to-Organ transposition: "renal transplantation" -> "kidney transplantation"
        for adj, org in ADJECTIVE_TO_ORGAN.items():
            if c_lower.startswith(f"{adj} "):
                rem = c_term[len(adj) + 1 :].strip()
                v = f"{org} {rem}".strip()
                if v not in variants:
                    variants.append(v)
            elif c_lower.endswith(f" {adj}"):
                rem = c_term[: -(len(adj) + 1)].strip()
                v = f"{rem} {org}".strip()
                if v not in variants:
                    variants.append(v)

        # 4. Common clinical verb/noun synonyms
        if "transplantation" in c_lower:
            v = re.sub(
                r"\btransplantation\b", "transplant", c_term, flags=re.IGNORECASE
            ).strip()
            if v not in variants:
                variants.append(v)
        elif "transplant" in c_lower and "transplantation" not in c_lower:
            v = re.sub(
                r"\btransplant\b", "transplantation", c_term, flags=re.IGNORECASE
            ).strip()
            if v not in variants:
                variants.append(v)

        if "insufficiency" in c_lower:
            v = re.sub(
                r"\binsufficiency\b", "failure", c_term, flags=re.IGNORECASE
            ).strip()
            if v not in variants:
                variants.append(v)
        elif "failure" in c_lower:
            v = re.sub(
                r"\bfailure\b", "insufficiency", c_term, flags=re.IGNORECASE
            ).strip()
            if v not in variants:
                variants.append(v)

        # 5. Eponym apostrophe variations
        if "'" in c_term or "’" in c_term:
            no_apos = re.sub(r"['’]s?\b", "", c_term).strip()
            if no_apos and no_apos not in variants:
                variants.append(no_apos)

    return variants


def get_topic_aliases(term: str, max_aliases: int = 20) -> List[str]:
    """
    Comprehensive topic alias resolver bounded to prevent BM25 query expansion explosion.
    Returns primary term, candidate variants (transpositions, inverted forms, acronyms),
    UMLS/Ontology synonyms (via get_concept_aliases), and ZIM redirects/canonical titles.
    """
    if not term or not term.strip():
        return []

    raw = term.strip()
    max_aliases = max(1, int(max_aliases or 20))
    seen: Set[str] = set()
    aliases: List[str] = []

    def _add(a: str) -> None:
        if not a or len(aliases) >= max_aliases:
            return
        a_clean = re.sub(r"^(?:the|a|an)\s+", "", a.strip(), flags=re.IGNORECASE).strip()
        a_low = a_clean.lower()
        if a_clean and len(a_clean) >= 2 and a_low not in seen:
            seen.add(a_low)
            aliases.append(a_clean)

    # 1. Primary term and candidate variants
    variants = generate_candidate_variants(raw)
    for v in variants:
        _add(v)
        if len(aliases) >= max_aliases:
            return aliases

    # 2. UMLS / KB Concept Aliases
    try:
        from parsers.scispacy_linker_engine import get_concept_aliases  # type: ignore

        for v in variants[:3]:
            kb_aliases = get_concept_aliases(v)
            for ka in kb_aliases[:5]:
                _add(ka)
                if len(aliases) >= max_aliases:
                    return aliases
    except Exception as exc:
        logger.debug("get_topic_aliases KB error: %s", exc)

    # 3. ZIM Resolution
    try:
        for v in variants[:2]:
            zim_res = resolve_zim_topic(v)
            if zim_res.get("matched") and zim_res.get("canonical_title"):
                can = zim_res["canonical_title"]
                _add(can)
                if len(aliases) >= max_aliases:
                    return aliases
    except Exception as exc:
        logger.debug("get_topic_aliases ZIM error: %s", exc)

    return aliases


def canonicalize_clinical_phrase(raw_phrase: str) -> Tuple[str, str]:
    """
    Normalizes a clinical phrase into (display_name, canonical_signature).
    Suppresses isolated generic words ('heart', 'patient', 'acute', etc.).
    Guarantees subtype protection: phrases with different clinical modifiers receive distinct signatures.
    """
    clean_phrase = (raw_phrase or "").strip()
    if not clean_phrase:
        return "", ""

    clean_phrase = re.sub(
        r"^(?:the|a|an)\s+", "", clean_phrase, flags=re.IGNORECASE
    ).strip()
    clean_phrase = re.sub(r"^[\W_]+|[\W_]+$", "", clean_phrase).strip()

    norm_key = normalize_topic_key(clean_phrase)
    if (
        not norm_key
        or norm_key in CLINICAL_STOPWORDS
        or norm_key in ISOLATED_GENERIC_WORDS
    ):
        return "", ""

    modifiers = extract_clinical_modifiers(clean_phrase)
    variants = generate_candidate_variants(clean_phrase)

    resolved_cui = ""
    resolved_name = ""

    for var in variants:
        res = _resolve_clinical_concept(var)
        if res.get("matched"):
            resolved_cui = res.get("cui", "")
            resolved_name = res.get("canonical_name", "")
            break

    if not resolved_name:
        resolved_name = clean_phrase.title()

    base_id = resolved_cui if resolved_cui else normalize_topic_key(resolved_name)

    display_name = resolved_name
    if modifiers:
        mod_sig = "_".join(f"{k}:{v}" for k, v in sorted(modifiers.items()))
        canonical_signature = f"{base_id}::{mod_sig}"

        disp_lower = display_name.lower()
        prefix_parts: List[str] = []
        if (
            "chronicity_acute" in mod_sig
            and "acute" not in disp_lower
            and "subacute" not in disp_lower
        ):
            prefix_parts.append("Acute")
        elif "chronicity_chronic" in mod_sig and "chronic" not in disp_lower:
            prefix_parts.append("Chronic")
        elif "chronicity_subacute" in mod_sig and "subacute" not in disp_lower:
            prefix_parts.append("Subacute")

        if "subtype_1" in mod_sig and "type 1" not in disp_lower:
            prefix_parts.append("Type 1")
        elif "subtype_2" in mod_sig and "type 2" not in disp_lower:
            prefix_parts.append("Type 2")

        if "laterality_left" in mod_sig and "left" not in disp_lower:
            prefix_parts.append("Left")
        elif "laterality_right" in mod_sig and "right" not in disp_lower:
            prefix_parts.append("Right")
        elif "laterality_bilateral" in mod_sig and "bilateral" not in disp_lower:
            prefix_parts.append("Bilateral")

        if prefix_parts:
            display_name = f"{' '.join(prefix_parts)} {display_name}".strip()
    else:
        canonical_signature = base_id

    return display_name, canonical_signature


# ---------------------------------------------------------------------------
# Clause Isolation & Negation Filtering
# ---------------------------------------------------------------------------
def is_clause_negated(clause_text: str, preceding_context: str) -> bool:
    """Detects clause-level or carried negation across comma boundaries."""
    if NEGATION_SCOPE_TRIGGER.search(clause_text):
        return True

    if preceding_context:
        boundary_matches = list(CONTRAST_RESET_REGEX.finditer(preceding_context))
        check_window = preceding_context
        if boundary_matches:
            check_window = preceding_context[boundary_matches[-1].end() :]
        if NEGATION_SCOPE_TRIGGER.search(check_window):
            return True

    return False


def isolate_subclauses(text: str) -> List[Tuple[str, bool]]:
    """
    Splits clinical narrative into atomic sub-clauses on punctuation and conjunctions.
    Preserves single-letter genus abbreviations (e.g., 'S. aureus') and clinical abbreviations.
    """
    if not text or not text.strip():
        return []

    cleaned_text = GENERIC_QUESTION_BOILERPLATE.sub(" ", text.strip())
    sentences = SENTENCE_SPLIT_REGEX.split(cleaned_text)
    subclauses: List[Tuple[str, bool]] = []

    for sentence in sentences:
        s_clean = sentence.strip()
        if not s_clean:
            continue

        punct_chunks = PUNCT_SPLIT_REGEX.split(s_clean)
        preceding_acc = ""

        for chunk in punct_chunks:
            chunk_clean = chunk.strip()
            if not chunk_clean:
                continue

            atomic_pieces = CLAUSE_CONJUNCTIONS_REGEX.split(chunk_clean)
            for piece in atomic_pieces:
                piece_clean = piece.strip()
                if not piece_clean:
                    continue

                negated = is_clause_negated(piece_clean, preceding_acc)
                subclauses.append((piece_clean, negated))
                preceding_acc += " " + piece_clean

    return subclauses


# ---------------------------------------------------------------------------
# Candidate Term Extractor
# ---------------------------------------------------------------------------
def is_valid_clinical_candidate(term: str, ignored_keys: Set[str]) -> bool:
    """Validates candidate text against length, stopwords, and generic terms."""
    term_clean = term.strip(".,;:?!'\"()[]{} \t\n\r")
    if not term_clean:
        return False

    words = term_clean.split()
    if not (1 <= len(words) <= 6):
        return False

    if len(term_clean) < 3 and term_clean.upper() not in {
        "MI", "PE", "TB", "RA", "MS", "DM", "CHF", "COPD"
    }:
        return False

    key = normalize_topic_key(term_clean)
    if (
        not key
        or key in ignored_keys
        or key in CLINICAL_STOPWORDS
        or key in ISOLATED_GENERIC_WORDS
    ):
        return False

    if LAB_VALUE_OR_NUMERIC_REGEX.match(term_clean) or AGE_TERM_REGEX.match(term_clean):
        return False

    return True


def extract_regex_medical_entities(text: str) -> List[str]:
    """Extracts binomial microbes, pharmacology stems, and prepositional conditions via precompiled patterns."""
    candidates: List[str] = []

    # 1. Microorganisms: Binomial
    for match in BINOMIAL_SPECIES_REGEX.finditer(text):
        genus = match.group(1)
        species = match.group(2)
        if genus.lower() in KNOWN_GENERA:
            candidates.append(f"{genus} {species}")

    # 2. Microorganisms: Abbreviated species (e.g. S. aureus)
    for match in ABBREV_SPECIES_REGEX.finditer(text):
        init = match.group(1)
        species = match.group(2)
        if any(g.startswith(init.lower()) for g in KNOWN_GENERA):
            candidates.append(f"{init}. {species}")

    # 3. Microorganisms: Viral entities
    t_lower = text.lower()
    for virus in VIRAL_PARASITIC_ENTITIES:
        if virus in t_lower:
            candidates.append(virus.title())

    # 4. Pharmacology: Drugs and stems
    words = re.findall(r"\b[A-Za-z-]{4,}\b", text)
    for w in words:
        wl = w.lower()
        if wl in KNOWN_DRUGS or any(wl.endswith(sfx) for sfx in PHARMA_SUFFIXES):
            if wl not in CLINICAL_STOPWORDS and wl not in ISOLATED_GENERIC_WORDS:
                candidates.append(w)

    # 5. Prepositional clinical conditions (e.g. "amyloidosis of the heart")
    for m in PREPOSITIONAL_PATHOLOGY_REGEX.finditer(text):
        span = m.group(0).strip()
        span_lower = span.lower()
        if not any(
            sw in span_lower
            for sw in ["which of", "history of", "evidence of", "diagnosis of", "years of"]
        ):
            candidates.append(span)

    # 6. Eponymic clinical syndromes
    for m in EPONYMIC_SYNDROME_REGEX.finditer(text):
        eponym = m.group(1).lower()
        if eponym not in {
            "this", "that", "the", "which", "what", "each", "every",
            "another", "same", "current", "present",
        }:
            candidates.append(m.group(0).strip())

    # 7. Clinical Head Nouns and Medical Suffixes
    for m in MEDICAL_SUFFIX_REGEX.finditer(text):
        span = m.group(0).strip()
        span_lower = span.lower()
        if not any(
            sw in span_lower
            for sw in [
                "which of", "history of", "evidence of",
                "diagnosis of", "years of", "most likely",
            ]
        ):
            candidates.append(span)

    for m in HEAD_NOUN_MATCH_REGEX.finditer(text):
        span = m.group(0).strip()
        span_lower = span.lower()
        if not any(
            sw in span_lower
            for sw in [
                "which of", "history of", "evidence of",
                "diagnosis of", "years of", "most likely",
            ]
        ):
            candidates.append(span)

    return candidates


def extract_spacy_entities(nlp_or_pipelines: Any, text: str) -> List[str]:
    """Runs CPU-bound spaCy entity recognition and dependency noun-phrase walking with thread safety."""
    if nlp_or_pipelines is None or not text.strip():
        return []

    pipelines = (
        nlp_or_pipelines
        if isinstance(nlp_or_pipelines, list)
        else [nlp_or_pipelines]
    )
    candidates: List[str] = []

    with _SPACY_EXEC_LOCK:
        for idx, nlp in enumerate(pipelines):
            if nlp is None:
                continue
            try:
                doc = nlp(text)

                # 1. SpaCy Entities (runs across all ensemble NER models)
                for ent in doc.ents:
                    if ent.label_ not in SPACY_EXCLUDED_NER_TYPES:
                        ent_text = ent.text.strip()
                        if len(ent_text) >= 3 and len(ent_text.split()) <= 6:
                            candidates.append(ent_text)

                # 2 & 3. Dependency Noun Chunks & Head Noun Walking (only needed on primary parser)
                if idx == 0:
                    try:
                        if hasattr(doc, "noun_chunks"):
                            for chunk in doc.noun_chunks:
                                chunk_text = chunk.text.strip()
                                if 3 <= len(chunk_text) <= 60 and len(chunk_text.split()) <= 6:
                                    candidates.append(chunk_text)
                    except Exception:
                        pass

                    try:
                        for token in doc:
                            tok_lower = token.text.lower()
                            tok_lemma = token.lemma_.lower()
                            if tok_lower in CLINICAL_HEAD_NOUNS or tok_lemma in CLINICAL_HEAD_NOUNS:
                                left_mods = [
                                    t
                                    for t in token.lefts
                                    if t.dep_ in {"amod", "compound", "nummod"}
                                ]
                                span_tokens = left_mods + [token]

                                for right in token.rights:
                                    if right.dep_ == "prep" and right.text.lower() == "of":
                                        pobjs = [
                                            p for p in right.children if p.dep_ == "pobj"
                                        ]
                                        if pobjs:
                                            pobj = pobjs[0]
                                            span_tokens = (
                                                span_tokens
                                                + [right]
                                                + sorted(list(pobj.subtree), key=lambda t: t.i)
                                            )
                                            break

                                if len(span_tokens) > 1:
                                    unique_tokens = sorted(
                                        list(set(span_tokens)), key=lambda t: t.i
                                    )
                                    span_str = " ".join(t.text for t in unique_tokens).strip()
                                    if 3 <= len(span_str) <= 60 and len(span_str.split()) <= 6:
                                        candidates.append(span_str)
                    except Exception:
                        pass
            except Exception as exc:
                logger.debug("spaCy extraction exception on text: %s", exc)

    return candidates


# ---------------------------------------------------------------------------
# Primary Extraction Pipelines
# ---------------------------------------------------------------------------
def extract_topics_local(
    question: str = "",
    options: Optional[Any] = None,
    explanation: str = "",
    strategy: str = "medspacy",
    use_zim_refinement: bool = False,
    use_tantivy_refinement: bool = False,
    text: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Main extraction interface. Resolves clinical concepts, protects subtypes,
    and returns verified and doubtful classifications conforming to TopicExtractorOutputDict (Section 4).
    """
    input_text = text if (text is not None and not question) else question
    ignored_keys = get_ignored_topics_set()
    active_pipelines = _get_active_spacy_pipelines()

    raw_candidates: List[Dict[str, Any]] = []

    # 1. OPTION ISOLATION: Process answer choices first (high priority signal)
    if options:
        option_texts: List[str] = []
        if isinstance(options, str):
            try:
                parsed = json.loads(options)
                if isinstance(parsed, dict):
                    option_texts = [str(v) for v in parsed.values() if v]
                elif isinstance(parsed, (list, tuple)):
                    option_texts = [str(v) for v in parsed if v]
                else:
                    option_texts = [options]
            except Exception:
                option_texts = [options]
        elif isinstance(options, dict):
            option_texts = [str(v) for v in options.values() if v]
        elif isinstance(options, (list, tuple)):
            option_texts = [str(v) for v in options if v]

        for opt in option_texts:
            opt_clean = str(opt).strip()
            # Strip MCQ option prefixes (e.g., "(A) Gout", "A. Gout", "1. Gout")
            opt_clean = re.sub(
                r"^(?:\(?[A-Ea-e1-5]\)?[\.\)]\s*|\(?[A-Ea-e]\)\s*)", "", opt_clean
            ).strip()

            # Split compound answer choices (e.g., "Asthma / COPD")
            if any(sep in opt_clean for sep in [",", "/", ";", " and "]):
                pieces = [
                    p.strip()
                    for p in re.split(r"[,/;\n]|(?:\band\b)", opt_clean)
                    if p.strip()
                ]
                for piece in pieces:
                    if is_valid_clinical_candidate(piece, ignored_keys):
                        raw_candidates.append(
                            {
                                "term": piece,
                                "source": "option",
                                "priority": 1.25,
                                "is_negated": False,
                            }
                        )

            if is_valid_clinical_candidate(opt_clean, ignored_keys):
                raw_candidates.append(
                    {
                        "term": opt_clean,
                        "source": "option",
                        "priority": 1.20,
                        "is_negated": False,
                    }
                )

    # 2. QUESTION & EXPLANATION ISOLATION: Sub-clause boundary processing
    narratives = [
        (input_text, "question", 1.0),
        (explanation, "explanation", 0.9),
    ]

    for narrative_text, source_type, base_priority in narratives:
        if not narrative_text or not str(narrative_text).strip():
            continue

        subclauses = isolate_subclauses(str(narrative_text))
        for clause, is_negated in subclauses:
            if is_negated:
                continue

            for rent in extract_regex_medical_entities(clause):
                if is_valid_clinical_candidate(rent, ignored_keys):
                    raw_candidates.append(
                        {
                            "term": rent,
                            "source": source_type,
                            "priority": base_priority * 1.15,
                            "is_negated": False,
                        }
                    )

            for sent in extract_spacy_entities(active_pipelines, clause):
                if is_valid_clinical_candidate(sent, ignored_keys):
                    raw_candidates.append(
                        {
                            "term": sent,
                            "source": source_type,
                            "priority": base_priority,
                            "is_negated": False,
                        }
                    )

    # 3. DEDUPLICATION, RESOLUTION & SUBTYPE PROTECTION
    canonical_clusters: Dict[str, Dict[str, Any]] = {}

    for cand in raw_candidates:
        term = cand["term"]
        display_name, signature = canonicalize_clinical_phrase(term)

        if not signature or not display_name:
            continue

        key = normalize_topic_key(display_name)
        if key in ignored_keys:
            continue

        if signature not in canonical_clusters:
            ont_result = _resolve_clinical_concept(term)
            if not ont_result.get("matched") and display_name != term:
                ont_result = _resolve_clinical_concept(display_name)

            cui = str(ont_result.get("cui", "") or "")
            cat = _normalize_semantic_category(
                ont_result.get("semantic_category", "unknown")
            )
            score = float(ont_result.get("score", 0.0) or 0.0)
            ont_matched = bool(ont_result.get("matched", False) and score >= 0.70)

            zim_matched = False
            if use_zim_refinement or not ont_matched:
                zim_res = resolve_zim_topic(term)
                if not zim_res.get("matched") and display_name != term:
                    zim_res = resolve_zim_topic(display_name)
                zim_matched = bool(zim_res.get("matched", False))

            # Invariant 10: Tantivy search_index returns dict of lists {"obsidian":[], ...}
            tantivy_boost = False
            if use_tantivy_refinement:
                t_hits = search_index(display_name, limit_per_source=1)
                tantivy_boost = any(
                    len(source_results) > 0 for source_results in t_hits.values()
                )

            term_lower = term.lower()
            is_microorganism = (
                cat == "microorganism"
                or any(g in term_lower for g in KNOWN_GENERA)
                or any(v in term_lower for v in VIRAL_PARASITIC_ENTITIES)
            )
            is_pharmacology = (
                cat == "drug_chemical"
                or term_lower in KNOWN_DRUGS
                or any(term_lower.endswith(sfx) for sfx in PHARMA_SUFFIXES)
            )

            verified = bool(
                ont_matched
                or zim_matched
                or is_microorganism
                or is_pharmacology
                or (cand["source"] == "option" and score >= 0.60)
                or tantivy_boost
            )

            if cat == "unknown":
                if is_microorganism:
                    cat = "microorganism"
                elif is_pharmacology:
                    cat = "drug_chemical"
                elif any(
                    term_lower.endswith(w)
                    for w in ["nerve", "artery", "vein", "trunk", "canal", "fossa"]
                ):
                    cat = "anatomy"
                elif any(
                    term_lower.endswith(w)
                    for w in ["ectomy", "otomy", "ostomy", "plasty", "scopy", "biopsy"]
                ):
                    cat = "procedure"
                elif any(
                    term_lower.endswith(w)
                    for w in ["syndrome", "disease", "itis", "osis", "oma", "emia", "pathy"]
                ):
                    cat = "disease"

            canonical_clusters[signature] = {
                "name": display_name,
                "verified": verified,
                "selected": verified,
                "confidence": "high" if verified else "low",
                "semantic_category": cat,
                "cui": cui,
                "score": score if score > 0.0 else (0.88 if verified else 0.40),
                "occurrences": 1,
                "sources": {cand["source"]},
                "priority_weight": cand["priority"],
            }
        else:
            cluster = canonical_clusters[signature]
            cluster["occurrences"] += 1
            cluster["sources"].add(cand["source"])
            cluster["priority_weight"] += cand["priority"]

    # 4. RANKING & TOPIC DETAILS ASSEMBLY
    sorted_clusters = sorted(
        canonical_clusters.values(),
        key=lambda c: (
            1 if c["verified"] else 0,
            1 if "option" in c["sources"] else 0,
            c["priority_weight"],
            c["score"],
            c["occurrences"],
        ),
        reverse=True,
    )

    # 5. MAIN TOPIC SELECTION
    main_topic = ""
    for item in sorted_clusters:
        if item["verified"]:
            if item["semantic_category"] in {
                "disease",
                "microorganism",
                "anatomy",
                "drug_chemical",
                "procedure",
            }:
                main_topic = item["name"]
                break

    if not main_topic and sorted_clusters:
        main_topic = sorted_clusters[0]["name"]

    topic_details: List[Dict[str, Any]] = []
    all_topics: List[str] = []

    for item in sorted_clusters:
        t_name = item["name"]
        all_topics.append(t_name)
        is_main = 1 if t_name == main_topic else 0
        topic_details.append(
            {
                "name": t_name,
                "topic_name": t_name,  # Matches extracted_topics SQLite table
                "is_main_topic": is_main,
                "verified": 1 if item["verified"] else 0,
                "selected": 1 if item["selected"] else 0,
                "confidence": item["confidence"],
                "semantic_category": item["semantic_category"],
                "cui": item["cui"],
                "score": round(float(item["score"]), 3),
            }
        )

    return {
        "main_topic": main_topic,
        "all_topics": all_topics,
        "topic_details": topic_details,
    }


def extract_topics(
    text: str,
    explanation: str = "",
    strategy: str = "medspacy",
    use_zim_refinement: bool = False,
    use_tantivy_refinement: bool = False,
    **kwargs: Any,
) -> List[str]:
    """
    Extracts ordered clinical topic strings adhering to blueprint.md signature verbatim.
    Backward-compatible with single-argument and full keyword calls.
    """
    res = extract_topics_local(
        question=text,
        options=kwargs.get("options", None),
        explanation=explanation,
        strategy=strategy,
        use_zim_refinement=use_zim_refinement,
        use_tantivy_refinement=use_tantivy_refinement,
    )
    return res.get("all_topics", [])


# ---------------------------------------------------------------------------
# Sibling Topic Filtering (ChunkDict Contract Compliant)
# ---------------------------------------------------------------------------
SIBLING_MUTUAL_EXCLUSIONS: List[Tuple[Set[str], Set[str]]] = [
    ({"type 1", "type 1 diabetes", "t1d", "t1dm"}, {"type 2", "type 2 diabetes", "t2d", "t2dm"}),
    ({"crohn", "crohn's", "crohn disease"}, {"ulcerative colitis", "uc"}),
    (
        {"hodgkin lymphoma", "hodgkin's lymphoma", "hodgkin disease"},
        {"non-hodgkin lymphoma", "non-hodgkin's lymphoma", "nhl"},
    ),
    ({"small cell lung cancer", "sclc"}, {"non-small cell lung cancer", "nsclc"}),
    ({"bipolar i", "bipolar 1"}, {"bipolar ii", "bipolar 2"}),
    ({"acute pancreatitis"}, {"chronic pancreatitis"}),
    (
        {"acute renal failure", "acute kidney injury", "aki"},
        {"chronic kidney disease", "chronic renal failure", "ckd"},
    ),
    ({"hypothyroidism", "hashimoto"}, {"hyperthyroidism", "graves"}),
    ({"osteoarthritis"}, {"rheumatoid arthritis"}),
    ({"prader-willi syndrome"}, {"angelman syndrome"}),
    ({"alpha-thalassemia"}, {"beta-thalassemia"}),
]


def filter_sibling_topics(
    query: str, chunks: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Prunes search hits addressing conflicting sibling subtypes or contradictory modifiers.
    Uses strict word-boundary checks so short acronyms ('uc', 'aki', 't1d') never
    falsely match substrings inside normal words ('mucosa', 'structure').
    Preserves comparative passages containing both sibling topics.
    """
    if not query or not chunks:
        return chunks

    query_lower = query.lower()
    query_mods = extract_all_clinical_modifiers(query_lower)

    filtered: List[Dict[str, Any]] = []

    for chunk in chunks:
        chunk_title = str(chunk.get("title", "")).lower()
        chunk_topic = str(chunk.get("topic", chunk.get("main_topic", ""))).lower()
        chunk_heading = str(chunk.get("heading", chunk.get("heading_path", ""))).lower()
        chunk_subheading = str(
            chunk.get("subheading", chunk.get("section_title", ""))
        ).lower()
        chunk_snippet = str(chunk.get("snippet", chunk.get("text", "")))[:600].lower()
        chunk_meta = (
            f"{chunk_title} {chunk_topic} {chunk_heading} {chunk_subheading} {chunk_snippet}"
        )

        is_conflict = False

        # 1. Mutual exclusion pairs (strict word-boundary matching)
        for group_a, group_b in SIBLING_MUTUAL_EXCLUSIONS:
            in_a_query = any(_contains_phrase_bounded(query_lower, t) for t in group_a)
            in_b_query = any(_contains_phrase_bounded(query_lower, t) for t in group_b)

            if not (in_a_query ^ in_b_query):
                continue

            in_a_chunk = any(_contains_phrase_bounded(chunk_meta, t) for t in group_a)
            in_b_chunk = any(_contains_phrase_bounded(chunk_meta, t) for t in group_b)

            if in_a_query and in_b_chunk and not in_a_chunk:
                is_conflict = True
                break
            if in_b_query and in_a_chunk and not in_b_chunk:
                is_conflict = True
                break

        # 2. Contradictory subtype modifiers
        if not is_conflict and query_mods:
            chunk_mods = extract_all_clinical_modifiers(chunk_meta)
            for mod_cat, query_vals in query_mods.items():
                if mod_cat in chunk_mods:
                    chunk_vals = chunk_mods[mod_cat]
                    # Conflict exists only if chunk has contradictory values and NONE of the query values
                    if not (query_vals & chunk_vals):
                        is_conflict = True
                        break

        if not is_conflict:
            filtered.append(chunk)

    return filtered if filtered else chunks


# ---------------------------------------------------------------------------
# Module Public Exports
# ---------------------------------------------------------------------------
__all__ = [
    "extract_topics",
    "extract_topics_local",
    "filter_sibling_topics",
    "get_topic_aliases",
    "generate_candidate_variants",
    "add_ignored_topic",
    "remove_ignored_topic",
    "get_ignored_topics_set",
    "canonicalize_clinical_phrase",
    "extract_clinical_modifiers",
    "extract_all_clinical_modifiers",
    "get_medspacy_model",
    "normalize_topic_key",
    "resolve_zim_topic",
]
