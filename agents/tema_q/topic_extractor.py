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
- SQLite-backed persistent ignore list integration.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import unicodedata

# Enforce CPU execution for spaCy / PyTorch before any NLP modules load
os.environ["CUDA_VISIBLE_DEVICES"] = ""

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project Module Imports & Fallbacks
# ---------------------------------------------------------------------------
try:
    import config

    DATA_DIR = Path(getattr(config, "DATA_DIR", "data"))
    LINKERS_DIR = Path(getattr(config, "LINKERS_DIR", DATA_DIR / "linkers"))
    MODELS_DIR = Path(getattr(config, "MODELS_DIR", DATA_DIR / "models"))
    SCISPACY_MODELS_DIR = Path(
        getattr(config, "SCISPACY_MODELS_DIR", MODELS_DIR / "scispacy")
    )
    ONTOLOGY_DB_PATH = Path(
        getattr(config, "ONTOLOGY_DB_PATH", LINKERS_DIR / "ontology_kb.db")
    )
except ImportError:
    DATA_DIR = Path("data")
    LINKERS_DIR = DATA_DIR / "linkers"
    MODELS_DIR = DATA_DIR / "models"
    SCISPACY_MODELS_DIR = MODELS_DIR / "scispacy"
    ONTOLOGY_DB_PATH = LINKERS_DIR / "ontology_kb.db"

try:
    from core.database import (
        get_db_connection,
        lookup_concept_by_cui,
        lookup_ontology_alias,
    )
except ImportError:

    def get_db_connection() -> sqlite3.Connection:
        db_path = Path("data") / "mcq_hub.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=20.0)
        conn.row_factory = sqlite3.Row
        return conn

    def lookup_ontology_alias(term: str) -> Optional[Dict[str, Any]]:
        return None

    def lookup_concept_by_cui(cui: str) -> Optional[Dict[str, Any]]:
        return None

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
    from parsers.zim_reader import resolve_zim_topic
except ImportError:

    def resolve_zim_topic(term: str) -> Dict[str, Any]:
        return {"matched": False, "canonical_title": "", "zim_path": "", "entry_path": ""}

try:
    from parsers.tantivy_engine import search_index
except ImportError:

    def search_index(query_str: str, limit_per_source: int = 1) -> Dict[str, list]:
        return {}


# ---------------------------------------------------------------------------
# Singleton MedSpaCy / SciSpaCy Model Loader
# ---------------------------------------------------------------------------
_SPACY_NLP_SINGLETON: Optional[Any] = None


def get_medspacy_model() -> Any:
    """
    Returns a loaded spaCy / SciSpaCy Language pipeline running strictly on CPU.
    Preserves dependency parser and entity recognizer needed for biomedical noun chunks.
    """
    global _SPACY_NLP_SINGLETON
    if _SPACY_NLP_SINGLETON is not None:
        return _SPACY_NLP_SINGLETON

    try:
        import spacy

        if hasattr(spacy, "require_cpu"):
            spacy.require_cpu()
    except Exception:
        pass

    # 1. Try engine-loaded pipelines
    try:
        pipelines = load_scispacy_pipeline()
        if pipelines and len(pipelines) > 0 and pipelines[0] is not None:
            _SPACY_NLP_SINGLETON = pipelines[0]
            logger.info("Loaded SciSpaCy pipeline from linker engine.")
            return _SPACY_NLP_SINGLETON
    except Exception as e:
        logger.debug("load_scispacy_pipeline invocation note: %s", e)

    # 2. Try loading biomedical models or local folder packages
    try:
        import spacy

        candidate_model_ids = [
            "en_core_sci_md",
            "en_core_sci_sm",
            "en_ner_bionlp13cg_md",
            "en_ner_bc5cdr_md",
            "en_core_web_sm",
        ]

        for model_id in candidate_model_ids:
            # Check installed Python package
            try:
                _SPACY_NLP_SINGLETON = spacy.load(model_id)
                logger.info("Loaded spaCy model '%s' from environment.", model_id)
                return _SPACY_NLP_SINGLETON
            except Exception:
                pass

            # Check extracted directory in SCISPACY_MODELS_DIR
            if SCISPACY_MODELS_DIR:
                local_dir = SCISPACY_MODELS_DIR / model_id
                if local_dir.exists() and (local_dir / "config.cfg").exists():
                    try:
                        _SPACY_NLP_SINGLETON = spacy.load(str(local_dir))
                        logger.info("Loaded spaCy model from local folder: %s", local_dir)
                        return _SPACY_NLP_SINGLETON
                    except Exception:
                        pass

        # 3. Fallback blank pipeline with sentencizer
        blank_nlp = spacy.blank("en")
        if "sentencizer" not in blank_nlp.pipe_names:
            blank_nlp.add_pipe("sentencizer")
        _SPACY_NLP_SINGLETON = blank_nlp
        logger.info("Initialized fallback blank English spaCy pipeline.")
        return _SPACY_NLP_SINGLETON
    except Exception as exc:
        logger.warning("Could not initialize spaCy pipeline: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Ignore List & DB Persistence
# ---------------------------------------------------------------------------
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
    norm = re.sub(r"^(the|a|an)\s+", "", norm)
    norm = re.sub(r"^[\W_]+|[\W_]+$", "", norm)
    norm = re.sub(r"[\s_]+", " ", norm)
    return norm.strip()


def _ensure_ignored_topics_table(conn: sqlite3.Connection) -> None:
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ignored_topics (
                    topic_norm TEXT PRIMARY KEY,
                    original_text TEXT NOT NULL,
                    created_at REAL
                )
                """
            )
    except Exception as exc:
        logger.error("Failed to ensure ignored_topics table: %s", exc)


def get_ignored_topics_set() -> Set[str]:
    """Retrieves all normalized topic keys marked as permanently ignored."""
    try:
        with contextlib.closing(get_db_connection()) as conn:
            _ensure_ignored_topics_table(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT topic_norm FROM ignored_topics")
            rows = cursor.fetchall()
            return {r[0] for r in rows if r and r[0]}
    except Exception as e:
        logger.error("Error loading ignored topics: %s", e)
        return set()


def add_ignored_topic(term: str) -> bool:
    """Adds a term to the persistent ignored_topics SQLite table."""
    key = normalize_topic_key(term)
    if not key:
        return False
    try:
        with contextlib.closing(get_db_connection()) as conn:
            _ensure_ignored_topics_table(conn)
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO ignored_topics (topic_norm, original_text, created_at) VALUES (?, ?, ?)",
                    (key, term.strip(), time.time()),
                )
        return True
    except Exception as e:
        logger.error("Error adding ignored topic '%s': %s", term, e)
        return False


def remove_ignored_topic(term: str) -> bool:
    """Removes a term from the persistent ignored_topics SQLite table."""
    key = normalize_topic_key(term)
    if not key:
        return False
    try:
        with contextlib.closing(get_db_connection()) as conn:
            _ensure_ignored_topics_table(conn)
            with conn:
                conn.execute("DELETE FROM ignored_topics WHERE topic_norm = ?", (key,))
        return True
    except Exception as e:
        logger.error("Error removing ignored topic '%s': %s", term, e)
        return False


# ---------------------------------------------------------------------------
# Clinical Lexicons, Patterns & Stopwords
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
    "which of the following", "what is the", "most likely diagnosis", "next step in management"
}

# Standalone generic words that must never form solitary topics
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
    "stage ii", "stage iii", "stage iv"
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

PUNCT_SPLIT_REGEX = re.compile(r"[,;:|/\\(\\)\[\]\n\r\t—–]")

CLAUSE_CONJUNCTIONS_REGEX = re.compile(
    r"\b(?:and|or|vs\.?|versus|with|without|due to|caused by|secondary to|associated with|followed by)\b",
    re.IGNORECASE,
)

NEGATION_SCOPE_TRIGGER = re.compile(
    r"\b(?:no|denies|denied|without|negative for|absence of|rules out|ruled out|free of|unremarkable for|no evidence of|fails to reveal)\b",
    re.IGNORECASE,
)

CONTRAST_RESET_REGEX = re.compile(r"\b(?:but|however|although|yet|except|nevertheless)\b", re.IGNORECASE)

LAB_VALUE_OR_NUMERIC_REGEX = re.compile(
    r"^(?:\d+(?:\.\d+)?|\d+/\d+)(?:\s*(?:mg/dl|g/dl|mmol/l|meq/l|ug/dl|mcg/dl|u/l|iu/l|mmhg|bpm|/min|%|cells/u[lL]|fl|pg|cm|mm|m|kg|g|mg|mcg|ml|l))?$",
    re.IGNORECASE,
)

AGE_TERM_REGEX = re.compile(
    r"^\d+[- ]?(?:year|yr|month|mo|week|wk|day|hr|hour)[- ]?(?:old)?$",
    re.IGNORECASE,
)

SPACY_EXCLUDED_NER_TYPES: Set[str] = {
    "DATE", "TIME", "PERCENT", "MONEY", "QUANTITY", "ORDINAL", "CARDINAL",
    "GPE", "LOC", "FAC", "LANGUAGE", "PERSON", "NORP"
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
    "moraxella", "nocardia", "actinomyces", "bartonella", "coxiella"
}

VIRAL_PARASITIC_ENTITIES: Set[str] = {
    "cytomegalovirus", "epstein-barr virus", "herpes simplex virus", "varicella-zoster virus",
    "human immunodeficiency virus", "hepatitis a virus", "hepatitis b virus",
    "hepatitis c virus", "hepatitis d virus", "hepatitis e virus", "respiratory syncytial virus",
    "human papillomavirus", "coxsackievirus", "norovirus", "rotavirus", "adenovirus",
    "parvovirus b19", "rabies virus", "dengue virus", "yellow fever virus", "chikungunya virus",
    "zika virus", "ebola virus", "influenza virus"
}

BINOMIAL_SPECIES_REGEX = re.compile(
    r"\b([A-Z][a-z]{2,})\s+([a-z]{3,}(?:-[a-z]{3,})?)\b"
)

ABBREV_SPECIES_REGEX = re.compile(
    r"\b([A-Z])\.\s*([a-z]{3,}(?:-[a-z]{3,})?)\b"
)

PHARMA_SUFFIXES: Tuple[str, ...] = (
    "cillin", "olol", "statin", "mab", "pril", "sartan", "prazole",
    "floxacin", "mycin", "micin", "cycline", "dipine", "terol", "zosin",
    "tidine", "gliptin", "gliflozin", "glutide", "parin", "xaban",
    "grel", "setron", "triptan", "afil", "nib", "tinib", "zumab",
    "ximab", "umab", "vir", "navir", "vudine", "caine", "semide",
    "pam", "lam", "pine", "dronate", "lukast", "coxib", "zole",
    "toin", "barbital", "sone", "olone", "onide"
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
    "hydrochlorothiazide", "mannitol", "acetazolamide"
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
    "hernia", "diverticulitis", "diverticulosis", "colitis", "gastritis", "esophagitis"
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


# ---------------------------------------------------------------------------
# Concept Resolution & Phrase Canonicalization
# ---------------------------------------------------------------------------
def _resolve_clinical_concept(term: str) -> Dict[str, Any]:
    """
    Unified multi-tier offline resolver:
    1. SciSpaCy linker engine (UMLS / MeSH / RxNorm)
    2. Direct SQLite kb_aliases lookup
    3. Zero-copy LibZim B-Tree lookup & redirects
    """
    clean_term = term.strip()
    if not clean_term:
        return {"matched": False, "cui": "", "canonical_name": "", "score": 0.0, "semantic_category": "unknown"}

    # 1. ScispaCy Linker Engine
    try:
        res = resolve_ontology_concept(clean_term)
        if res.get("matched") and res.get("score", 0.0) >= 0.70:
            return res
    except Exception:
        pass

    # 2. SQLite kb_aliases lookup
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


def extract_clinical_modifiers(text: str) -> Dict[str, str]:
    """Extracts clinical modifier flags (Type 1 vs 2, Acute vs Chronic, etc.)."""
    mods: Dict[str, str] = {}
    for mod_name, pattern in SUBTYPE_MODIFIER_PATTERNS.items():
        match = pattern.search(text)
        if match:
            raw_val = match.group(1).lower().strip()
            norm_val = ROMAN_NUMERAL_MAP.get(raw_val, raw_val)
            mods[mod_name] = f"{mod_name}_{norm_val}"
    return mods


def generate_candidate_variants(term: str) -> List[str]:
    """Generates acronym, organ-adjective, and prepositional variants."""
    raw = term.strip()
    if not raw:
        return []

    variants: List[str] = [raw]

    # Acronym expansion
    upper = raw.upper()
    if upper in CLINICAL_ACRONYMS:
        exp = CLINICAL_ACRONYMS[upper]
        if exp not in variants:
            variants.append(exp)

    # Strip leading articles
    clean = re.sub(r"^(the|a|an)\s+", "", raw, flags=re.IGNORECASE).strip()
    if clean and clean not in variants:
        variants.append(clean)

    clean_lower = clean.lower()

    # 1. Prepositional transposition: "amyloidosis of the heart" -> "cardiac amyloidosis"
    prep_match = PREPOSITIONAL_PATHOLOGY_REGEX.search(clean)
    if prep_match:
        condition = prep_match.group(1).strip()
        organ = prep_match.group(2).strip().lower()
        if organ in ORGAN_TO_ADJECTIVE:
            adj = ORGAN_TO_ADJECTIVE[organ]
            v = f"{adj} {condition}".strip()
            if v not in variants:
                variants.append(v)

    # 2. Prefix / suffix organ transposition: "heart amyloidosis" or "amyloidosis heart"
    for org, adj in ORGAN_TO_ADJECTIVE.items():
        if clean_lower.startswith(f"{org} "):
            rem = clean[len(org) + 1:].strip()
            v = f"{adj} {rem}".strip()
            if v not in variants:
                variants.append(v)
        elif clean_lower.endswith(f" {org}"):
            rem = clean[:-(len(org) + 1)].strip()
            v = f"{adj} {rem}".strip()
            if v not in variants:
                variants.append(v)

    # 3. Eponym apostrophe variations
    if "'" in clean or "’" in clean:
        no_apos = re.sub(r"['’]s?\b", "", clean).strip()
        if no_apos and no_apos not in variants:
            variants.append(no_apos)

    return variants


def canonicalize_clinical_phrase(raw_phrase: str) -> Tuple[str, str]:
    """
    Normalizes a clinical phrase into (display_name, canonical_signature).
    Suppresses isolated generic words ('heart', 'patient', 'acute', etc.).
    Guarantees subtype protection: phrases with different clinical modifiers receive distinct signatures.
    """
    clean_phrase = raw_phrase.strip()
    if not clean_phrase:
        return "", ""

    clean_phrase = re.sub(r"^(the|a|an)\s+", "", clean_phrase, flags=re.IGNORECASE).strip()
    clean_phrase = re.sub(r"^[\W_]+|[\W_]+$", "", clean_phrase).strip()

    norm_key = normalize_topic_key(clean_phrase)
    if not norm_key or norm_key in CLINICAL_STOPWORDS or norm_key in ISOLATED_GENERIC_WORDS:
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

    # Subtype-Guarded Signature Construction
    display_name = resolved_name
    if modifiers:
        mod_sig = "_".join(f"{k}:{v}" for k, v in sorted(modifiers.items()))
        canonical_signature = f"{base_id}::{mod_sig}"

        disp_lower = display_name.lower()
        prefix_parts: List[str] = []
        if "chronicity_acute" in mod_sig and "acute" not in disp_lower and "subacute" not in disp_lower:
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
            check_window = preceding_context[boundary_matches[-1].end():]
        if NEGATION_SCOPE_TRIGGER.search(check_window):
            return True

    return False


def isolate_subclauses(text: str) -> List[Tuple[str, bool]]:
    """
    Splits clinical narrative into atomic sub-clauses on punctuation and conjunctions.
    Preserves single-letter genus abbreviations (e.g., 'S. aureus') from sentence chopping.
    """
    if not text or not text.strip():
        return []

    cleaned_text = GENERIC_QUESTION_BOILERPLATE.sub(" ", text.strip())

    # Split into sentences without breaking genus abbreviations (e.g. S. aureus, E. coli)
    sentences = re.split(r"(?<!\b[A-Z])(?<=[.!?])\s+", cleaned_text)
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
    term_clean = term.strip()
    words = term_clean.split()
    if not (1 <= len(words) <= 6):
        return False

    if len(term_clean) < 3 and term_clean.upper() not in {"MI", "PE", "TB", "RA", "MS", "DM", "CHF", "COPD"}:
        return False

    key = normalize_topic_key(term_clean)
    if not key or key in ignored_keys or key in CLINICAL_STOPWORDS or key in ISOLATED_GENERIC_WORDS:
        return False

    if LAB_VALUE_OR_NUMERIC_REGEX.match(term_clean) or AGE_TERM_REGEX.match(term_clean):
        return False

    return True


def extract_regex_medical_entities(text: str) -> List[str]:
    """Extracts binomial microbes, pharmacology stems, and prepositional conditions."""
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
        if not any(sw in span_lower for sw in ["which of", "history of", "evidence of", "diagnosis of", "years of"]):
            candidates.append(span)

    # 6. Eponymic clinical syndromes (excluding generic demonstratives)
    for m in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:syndrome|disease|sign|phenomenon|node|triad|criteria|classification)\b", text):
        eponym = m.group(1).lower()
        if eponym not in {"this", "that", "the", "which", "what", "each", "every", "another", "same", "current", "present"}:
            candidates.append(m.group(0).strip())

    # 7. Clinical Head Nouns and Medical Suffixes (1-3 preceding modifier words)
    medical_suffix_pattern = r"\b(?:[A-Za-z-]{3,}\s+){0,2}[A-Za-z-]{3,}(?:emia|osis|itis|oma|pathy|trophy|plasia|uria|penia|cytosis|ectasis|sclerosis|megaly)\b"
    for m in re.finditer(medical_suffix_pattern, text, re.IGNORECASE):
        span = m.group(0).strip()
        span_lower = span.lower()
        if not any(sw in span_lower for sw in ["which of", "history of", "evidence of", "diagnosis of", "years of", "most likely"]):
            candidates.append(span)

    head_nouns_joined = "|".join(re.escape(h) for h in CLINICAL_HEAD_NOUNS)
    head_noun_pattern = rf"\b(?:[A-Za-z-]{3,}\s+){{0,2}}(?:{head_nouns_joined})\b"
    for m in re.finditer(head_noun_pattern, text, re.IGNORECASE):
        span = m.group(0).strip()
        span_lower = span.lower()
        if not any(sw in span_lower for sw in ["which of", "history of", "evidence of", "diagnosis of", "years of", "most likely"]):
            candidates.append(span)

    return candidates


def extract_spacy_entities(nlp: Any, text: str) -> List[str]:
    """Runs CPU-bound spaCy entity recognition and dependency noun-phrase walking."""
    if nlp is None or not text.strip():
        return []

    candidates: List[str] = []
    try:
        doc = nlp(text)

        # 1. SpaCy Entities
        for ent in doc.ents:
            if ent.label_ not in SPACY_EXCLUDED_NER_TYPES:
                ent_text = ent.text.strip()
                if len(ent_text) >= 3 and len(ent_text.split()) <= 6:
                    candidates.append(ent_text)

        # 2. Dependency Noun Chunks
        try:
            if hasattr(doc, "noun_chunks"):
                for chunk in doc.noun_chunks:
                    chunk_text = chunk.text.strip()
                    if 3 <= len(chunk_text) <= 60 and len(chunk_text.split()) <= 6:
                        candidates.append(chunk_text)
        except Exception:
            pass

        # 3. Head Noun Syntactic Dependency Walking (amod + compound + prep_of)
        try:
            for token in doc:
                tok_lower = token.text.lower()
                tok_lemma = token.lemma_.lower()
                if tok_lower in CLINICAL_HEAD_NOUNS or tok_lemma in CLINICAL_HEAD_NOUNS:
                    left_mods = [t for t in token.lefts if t.dep_ in {"amod", "compound", "nummod"}]
                    span_tokens = left_mods + [token]

                    for right in token.rights:
                        if right.dep_ == "prep" and right.text.lower() == "of":
                            pobjs = [p for p in right.children if p.dep_ == "pobj"]
                            if pobjs:
                                pobj = pobjs[0]
                                span_tokens = span_tokens + [right] + sorted(list(pobj.subtree), key=lambda t: t.i)
                                break

                    if len(span_tokens) > 1:
                        span_tokens = sorted(span_tokens, key=lambda t: t.i)
                        span_str = " ".join(t.text for t in span_tokens).strip()
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
    question: str,
    options: Optional[Any] = None,
    explanation: str = "",
    strategy: str = "medspacy",
    use_zim_refinement: bool = False,
    use_tantivy_refinement: bool = False,
) -> Dict[str, Any]:
    """
    Main extraction interface. Resolves clinical concepts, protects subtypes,
    and returns verified and doubtful classifications.
    """
    ignored_keys = get_ignored_topics_set()
    nlp = get_medspacy_model()

    raw_candidates: List[Dict[str, Any]] = []

    # 1. OPTION ISOLATION: Process answer choices first (high signal)
    if options:
        option_texts: List[str] = []
        if isinstance(options, dict):
            option_texts = [str(v) for v in options.values() if v]
        elif isinstance(options, (list, tuple)):
            option_texts = [str(v) for v in options if v]

        for opt in option_texts:
            opt_clean = opt.strip()
            # Strip MCQ option prefixes (e.g., "(A) Gout", "A. Gout", "1. Gout")
            opt_clean = re.sub(r"^(?:\(?[A-Ea-e1-5]\)?[\.\)]\s*|\(?[A-Ea-e]\)\s*)", "", opt_clean).strip()

            # If option has separators (e.g., "Asthma / COPD"), split into individual candidates
            if any(sep in opt_clean for sep in [",", "/", ";", " and "]):
                pieces = [p.strip() for p in re.split(r"[,/;\n]|(?:\band\b)", opt_clean) if p.strip()]
                for piece in pieces:
                    if is_valid_clinical_candidate(piece, ignored_keys):
                        raw_candidates.append({"term": piece, "source": "option", "priority": 1.25, "is_negated": False})

            if is_valid_clinical_candidate(opt_clean, ignored_keys):
                raw_candidates.append({"term": opt_clean, "source": "option", "priority": 1.20, "is_negated": False})

    # 2. QUESTION & EXPLANATION ISOLATION: Sub-clause boundary processing
    narratives = [
        (question, "question", 1.0),
        (explanation, "explanation", 0.9),
    ]

    for narrative_text, source_type, base_priority in narratives:
        if not narrative_text or not narrative_text.strip():
            continue

        subclauses = isolate_subclauses(narrative_text)
        for clause, is_negated in subclauses:
            if is_negated:
                continue

            # A. Regex and domain pattern extraction
            for rent in extract_regex_medical_entities(clause):
                if is_valid_clinical_candidate(rent, ignored_keys):
                    raw_candidates.append({"term": rent, "source": source_type, "priority": base_priority * 1.15, "is_negated": False})

            # B. SciSpaCy / SpaCy extraction
            for sent in extract_spacy_entities(nlp, clause):
                if is_valid_clinical_candidate(sent, ignored_keys):
                    raw_candidates.append({"term": sent, "source": source_type, "priority": base_priority, "is_negated": False})

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
            # Resolve concept against multi-tier linkers
            ont_result = _resolve_clinical_concept(term)
            if not ont_result.get("matched") and display_name != term:
                ont_result = _resolve_clinical_concept(display_name)

            cui = ont_result.get("cui", "")
            cat = ont_result.get("semantic_category", "unknown")
            score = ont_result.get("score", 0.0)
            ont_matched = ont_result.get("matched", False) and score >= 0.70

            zim_matched = False
            if use_zim_refinement or not ont_matched:
                zim_res = resolve_zim_topic(term)
                if not zim_res.get("matched") and display_name != term:
                    zim_res = resolve_zim_topic(display_name)
                zim_matched = zim_res.get("matched", False)

            tantivy_boost = False
            if use_tantivy_refinement:
                t_hits = search_index(display_name, limit_per_source=1)
                tantivy_boost = bool(t_hits)

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
                elif any(term_lower.endswith(w) for w in ["nerve", "artery", "vein", "trunk", "canal", "fossa"]):
                    cat = "anatomy"
                elif any(term_lower.endswith(w) for w in ["syndrome", "disease", "itis", "osis", "oma", "emia"]):
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

    topic_details: List[Dict[str, Any]] = []
    all_topics: List[str] = []

    for item in sorted_clusters:
        t_name = item["name"]
        all_topics.append(t_name)
        topic_details.append(
            {
                "name": t_name,
                "verified": item["verified"],
                "selected": item["selected"],
                "confidence": item["confidence"],
                "semantic_category": item["semantic_category"],
                "cui": item["cui"],
                "score": round(item["score"], 3),
            }
        )

    # 5. MAIN TOPIC SELECTION
    main_topic = ""
    for item in sorted_clusters:
        if item["verified"]:
            if item["semantic_category"] in {"disease", "microorganism", "anatomy", "drug_chemical"}:
                main_topic = item["name"]
                break

    if not main_topic and sorted_clusters:
        main_topic = sorted_clusters[0]["name"]

    return {
        "main_topic": main_topic,
        "all_topics": all_topics,
        "topic_details": topic_details,
    }


def extract_topics(
    text: str,
    use_zim_refinement: bool = False,
    use_tantivy_refinement: bool = False,
) -> List[str]:
    """Helper extracting topic strings from a standalone clinical query or text."""
    res = extract_topics_local(
        question=text,
        options=None,
        explanation="",
        use_zim_refinement=use_zim_refinement,
        use_tantivy_refinement=use_tantivy_refinement,
    )
    return res.get("all_topics", [])


# ---------------------------------------------------------------------------
# Sibling Topic Filtering
# ---------------------------------------------------------------------------
SIBLING_MUTUAL_EXCLUSIONS: List[Tuple[Set[str], Set[str]]] = [
    ({"type 1", "type 1 diabetes", "t1d"}, {"type 2", "type 2 diabetes", "t2d"}),
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


def filter_sibling_topics(query: str, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prunes search hits addressing conflicting sibling subtypes or contradictory modifiers."""
    if not query or not chunks:
        return chunks

    query_lower = query.lower()
    query_mods = extract_clinical_modifiers(query_lower)

    filtered: List[Dict[str, Any]] = []

    for chunk in chunks:
        chunk_title = str(chunk.get("title", "")).lower()
        chunk_topic = str(chunk.get("topic", chunk.get("main_topic", ""))).lower()
        chunk_heading = str(chunk.get("heading", chunk.get("heading_path", ""))).lower()
        chunk_subheading = str(chunk.get("subheading", "")).lower()
        chunk_meta = f"{chunk_title} {chunk_topic} {chunk_heading} {chunk_subheading}"

        is_conflict = False

        # 1. Mutual exclusion pairs
        for group_a, group_b in SIBLING_MUTUAL_EXCLUSIONS:
            in_a_query = any(t in query_lower for t in group_a)
            in_b_query = any(t in query_lower for t in group_b)

            in_a_chunk = any(t in chunk_meta for t in group_a)
            in_b_chunk = any(t in chunk_meta for t in group_b)

            if in_a_query and not in_b_query and in_b_chunk and not in_a_chunk:
                is_conflict = True
                break
            if in_b_query and not in_a_query and in_a_chunk and not in_b_chunk:
                is_conflict = True
                break

        # 2. Contradictory subtype modifiers
        if not is_conflict and query_mods:
            chunk_mods = extract_clinical_modifiers(chunk_meta)
            for mod_cat, query_val in query_mods.items():
                if mod_cat in chunk_mods:
                    chunk_val = chunk_mods[mod_cat]
                    if query_val != chunk_val:
                        is_conflict = True
                        break

        if not is_conflict:
            filtered.append(chunk)

    return filtered if filtered else chunks
