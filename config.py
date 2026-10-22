import logging
import os
from pathlib import Path
import shutil
import threading
import urllib.error
import urllib.request

# Enforce Invariant 1 (Zero VRAM for Backend) at the earliest configuration import
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

logger = logging.getLogger("medisearch.config")


def _get_env_int(key: str, default: int) -> int:
    val = os.environ.get(key, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; falling back to %d", key, val, default)
        return default


def _get_env_float(key: str, default: float) -> float:
    val = os.environ.get(key, "").strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        logger.warning("Invalid float for %s=%r; falling back to %s", key, val, default)
        return default


# Base Directory
BASE_DIR: Path = Path(__file__).resolve().parent

# Server & Window Configuration
SERVER_HOST: str = os.environ.get("SERVER_HOST", "127.0.0.1").strip() or "127.0.0.1"
SERVER_PORT: int = _get_env_int("SERVER_PORT", 8000)
APP_TITLE: str = "Clinical MCQ Hub"
WINDOW_WIDTH: int = _get_env_int("WINDOW_WIDTH", 1280)
WINDOW_HEIGHT: int = _get_env_int("WINDOW_HEIGHT", 800)

# Data & Knowledge Base Directories
DATA_DIR: Path = BASE_DIR / "data"
CSV_DIR: Path = DATA_DIR / "csv"
MCQS_DIR: Path = DATA_DIR / "mcqs"
VAULTS_DIR: Path = DATA_DIR / "vaults"
PDFS_DIR: Path = DATA_DIR / "pdfs"
ZIM_DIR: Path = DATA_DIR / "zim"

# Machine Learning & NLP Model Caches (Strict Zero-VRAM / CPU-Only)
MODELS_DIR: Path = DATA_DIR / "models"
HF_CACHE_DIR: Path = MODELS_DIR / "huggingface"
FLASHRANK_CACHE_DIR: Path = MODELS_DIR / "flashrank"
SCISPACY_MODELS_DIR: Path = MODELS_DIR / "scispacy"

# Pin local HuggingFace / Transformers caches into DATA_DIR so models stay self-contained
os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE_DIR))

# Offline SciSpaCy Ontology Linkers & Knowledge Bases (UMLS, MeSH, RxNorm, HPO, GO)
LINKERS_DIR: Path = DATA_DIR / "linkers"
KBS_DIR: Path = LINKERS_DIR / "kbs"
ONTOLOGY_DB_PATH: Path = LINKERS_DIR / "ontology_kb.db"

# Primary Database, Inverted Index & Vector Store
DB_PATH: Path = DATA_DIR / "mcq_hub.db"
INDEX_PATH: Path = DATA_DIR / "index"
LANCEDB_PATH: Path = DATA_DIR / "lancedb"

# Storage & Dossier Output Directories
STORAGE_DIR: Path = BASE_DIR / "storage"
REPORTS_DIR: Path = STORAGE_DIR / "reports"
HIGHLIGHTED_DIR: Path = STORAGE_DIR / "highlighted_html"
RESULTS_DIR: Path = STORAGE_DIR / "results"

# Frontend & Offline Vendor Asset Cache
UI_DIR: Path = BASE_DIR / "ui"
VENDOR_DIR: Path = UI_DIR / "vendor"
MIN_VENDOR_ASSET_BYTES: int = 128

# Local LLM (LM Studio) Configuration
_raw_lm_url = (
    os.environ.get("LM_STUDIO_URL")
    or os.environ.get("LM_STUDIO_BASE_URL")
    or "http://127.0.0.1:1234/v1"
)
LM_STUDIO_URL: str = _raw_lm_url.strip().rstrip("/")
LM_STUDIO_TIMEOUT: float = _get_env_float("LM_STUDIO_TIMEOUT", 60.0)
DEFAULT_MODEL: str = os.environ.get("DEFAULT_MODEL", "local-model").strip() or "local-model"
DEFAULT_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_FLASHRANK_MODEL: str = "ms-marco-TinyBERT-L-2-v2"
MAX_CONTEXT_TOKENS: int = 4096

# Compatibility aliases (ensures backward/blueprint compatibility)
TANTIVY_DIR: Path = INDEX_PATH
LANCEDB_DIR: Path = LANCEDB_PATH
DEFAULT_PORT: int = SERVER_PORT
LM_STUDIO_BASE_URL: str = LM_STUDIO_URL

VENDOR_ASSETS: dict[str, str] = {
    "tailwind.js": "https://cdn.tailwindcss.com",
    "marked.min.js": "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
    "purify.min.js": "https://cdn.jsdelivr.net/npm/dompurify/dist/purify.min.js",
    "katex.min.js": "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js",
    "katex.min.css": "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css",
    "auto-render.min.js": "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js",
}

# SciSpaCy S3 Model Catalog & Metadata
SCISPACY_S3_BASE: str = "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy"
SCISPACY_MODEL_CATALOG: dict[str, dict] = {
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

_VENDOR_DOWNLOAD_LOCK = threading.Lock()


def ensure_directories() -> None:
    """Idempotently creates all required data, model, cache, and storage directories."""
    directories = (
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
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def ensure_offline_vendor_assets() -> None:
    """Caches UI vendor assets safely into ui/vendor/ using thread-safe atomic writes."""
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    chunk_size = 64 * 1024

    with _VENDOR_DOWNLOAD_LOCK:
        for filename, url in VENDOR_ASSETS.items():
            destination = VENDOR_DIR / filename
            # Re-download if file is missing or smaller than the 128-byte validity threshold
            if not destination.exists() or destination.stat().st_size <= MIN_VENDOR_ASSET_BYTES:
                temp_path = VENDOR_DIR / f".{filename}.{os.getpid()}.{threading.get_ident()}.tmp"
                try:
                    request = urllib.request.Request(
                        url, headers={"User-Agent": "MedisearchAI-AssetCacher/1.0"}
                    )
                    with urllib.request.urlopen(request, timeout=15) as response, open(
                        temp_path, "wb"
                    ) as out_file:
                        shutil.copyfileobj(response, out_file, length=chunk_size)

                    # Validate size exceeds minimum threshold before atomic replacement
                    if temp_path.exists() and temp_path.stat().st_size > MIN_VENDOR_ASSET_BYTES:
                        temp_path.replace(destination)
                        logger.info("Successfully cached offline vendor asset: %s", filename)
                    else:
                        if temp_path.exists():
                            temp_path.unlink(missing_ok=True)
                        logger.warning(
                            "Incomplete download (<= %d bytes) for asset '%s', skipped replacing.",
                            MIN_VENDOR_ASSET_BYTES,
                            filename,
                        )
                except Exception as exc:
                    if temp_path.exists():
                        try:
                            temp_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    logger.warning(
                        "Could not pre-cache vendor asset '%s' (%s). Will retry on next boot.",
                        filename,
                        exc,
                    )
