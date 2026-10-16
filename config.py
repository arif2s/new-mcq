import logging
import os
from pathlib import Path
import urllib.request

logger = logging.getLogger("medisearch.config")

# Base Directory
BASE_DIR = Path(__file__).parent.absolute()

# Server & Window Configuration
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
APP_TITLE = "Clinical MCQ Hub"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800

# Data & Knowledge Base Directories
DATA_DIR = BASE_DIR / "data"
CSV_DIR = DATA_DIR / "csv"
MCQS_DIR = DATA_DIR / "mcqs"
VAULTS_DIR = DATA_DIR / "vaults"
PDFS_DIR = DATA_DIR / "pdfs"
ZIM_DIR = DATA_DIR / "zim"

# Machine Learning & NLP Model Caches (Strict Zero-VRAM / CPU-Only)
MODELS_DIR = DATA_DIR / "models"
HF_CACHE_DIR = MODELS_DIR / "huggingface"
FLASHRANK_CACHE_DIR = MODELS_DIR / "flashrank"
SCISPACY_MODELS_DIR = MODELS_DIR / "scispacy"

# Offline SciSpaCy Ontology Linkers & Knowledge Bases (UMLS, MeSH, RxNorm, HPO, GO)
LINKERS_DIR = DATA_DIR / "linkers"
KBS_DIR = LINKERS_DIR / "kbs"
ONTOLOGY_DB_PATH = LINKERS_DIR / "ontology_kb.db"

# Primary Database, Inverted Index & Vector Store
DB_PATH = DATA_DIR / "mcq_hub.db"
INDEX_PATH = DATA_DIR / "index"
LANCEDB_PATH = DATA_DIR / "lancedb"

# Storage & Dossier Output Directories
STORAGE_DIR = BASE_DIR / "storage"
REPORTS_DIR = STORAGE_DIR / "reports"
HIGHLIGHTED_DIR = STORAGE_DIR / "highlighted_html"
RESULTS_DIR = STORAGE_DIR / "results"

# Frontend & Offline Vendor Asset Cache
UI_DIR = BASE_DIR / "ui"
VENDOR_DIR = UI_DIR / "vendor"

VENDOR_ASSETS = {
    "tailwind.js": "https://cdn.tailwindcss.com",
    "marked.min.js": "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
    "purify.min.js": "https://cdn.jsdelivr.net/npm/dompurify/dist/purify.min.js",
    "katex.min.js": "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js",
    "katex.min.css": "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css",
    "auto-render.min.js": "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js",
}

# SciSpaCy S3 Model Catalog & Metadata
SCISPACY_S3_BASE = "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy"
SCISPACY_MODEL_CATALOG = {
    "en_core_sci_md": {
        "id": "en_core_sci_md",
        "name": "en_core_sci_md (Recommended — 360k Vocab + 50k Vectors, Anatomy/Microbes/Diseases/Subtypes)",
        "url": f"{SCISPACY_S3_BASE}/releases/v0.5.4/en_core_sci_md-0.5.4.tar.gz",
        "version": "0.5.4",
        "size_mb": 50,
        "ram_mb": 180,
    },
    "en_core_sci_sm": {
        "id": "en_core_sci_sm",
        "name": "en_core_sci_sm (Fast Lightweight — 100k Vocab, Low RAM)",
        "url": f"{SCISPACY_S3_BASE}/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz",
        "version": "0.5.4",
        "size_mb": 15,
        "ram_mb": 90,
    },
    "en_ner_bionlp13cg_md": {
        "id": "en_ner_bionlp13cg_md",
        "name": "en_ner_bionlp13cg_md (16 Labels: Organ, Tissue, Organism/Microbe, Cancer, Chemical)",
        "url": f"{SCISPACY_S3_BASE}/releases/v0.5.4/en_ner_bionlp13cg_md-0.5.4.tar.gz",
        "version": "0.5.4",
        "size_mb": 115,
        "ram_mb": 150,
    },
    "en_ner_bc5cdr_md": {
        "id": "en_ner_bc5cdr_md",
        "name": "en_ner_bc5cdr_md (PubMed Disease & Chemical/Drug Specialist)",
        "url": f"{SCISPACY_S3_BASE}/releases/v0.5.4/en_ner_bc5cdr_md-0.5.4.tar.gz",
        "version": "0.5.4",
        "size_mb": 115,
        "ram_mb": 150,
    },
    "en_core_sci_lg": {
        "id": "en_core_sci_lg",
        "name": "en_core_sci_lg (Large — 785k Vocab + 600k Word Vectors)",
        "url": f"{SCISPACY_S3_BASE}/releases/v0.5.4/en_core_sci_lg-0.5.4.tar.gz",
        "version": "0.5.4",
        "size_mb": 550,
        "ram_mb": 750,
    },
}

# Local LLM (LM Studio) Configuration
LM_STUDIO_URL = os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234/v1")
LM_STUDIO_TIMEOUT = 60.0
DEFAULT_MODEL = "local-model"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_FLASHRANK_MODEL = "ms-marco-TinyBERT-L-2-v2"
MAX_CONTEXT_TOKENS = 4096


def ensure_directories() -> None:
    """Idempotently creates all required data, model, cache, and storage directories."""
    directories = [
        DATA_DIR,
        CSV_DIR,
        MCQS_DIR,
        VAULTS_DIR,
        PDFS_DIR,
        ZIM_DIR,
        MODELS_DIR,
        HF_CACHE_DIR,
        FLASHRANK_CACHE_DIR,
        SCISPACY_MODELS_DIR,
        LINKERS_DIR,
        KBS_DIR,
        INDEX_PATH,
        LANCEDB_PATH,
        STORAGE_DIR,
        REPORTS_DIR,
        HIGHLIGHTED_DIR,
        RESULTS_DIR,
        UI_DIR,
        VENDOR_DIR,
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def ensure_offline_vendor_assets() -> None:
    """Caches UI vendor assets (Tailwind, Marked, KaTeX, DOMPurify) into ui/vendor/ for offline execution."""
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    for filename, url in VENDOR_ASSETS.items():
        destination = VENDOR_DIR / filename
        if not destination.exists():
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "MedisearchAI-AssetCacher/1.0"}
                )
                with urllib.request.urlopen(request, timeout=10) as response, open(
                    destination, "wb"
                ) as out_file:
                    out_file.write(response.read())
                logger.info("Cached offline vendor asset: %s", filename)
            except Exception as exc:
                logger.warning(
                    "Could not pre-cache vendor asset '%s' (%s). Will rely on local cache or retry on next boot.",
                    filename,
                    exc,
                )
