import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
APP_TITLE = "Clinical MCQ Hub"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800

# Data & Index Directories
DATA_DIR = BASE_DIR / "data"
CSV_DIR = DATA_DIR / "csv"
MCQS_DIR = DATA_DIR / "mcqs"
VAULTS_DIR = DATA_DIR / "vaults"
PDFS_DIR = DATA_DIR / "pdfs"
ZIM_DIR = DATA_DIR / "zim"
MODELS_DIR = DATA_DIR / "models"
HF_CACHE_DIR = MODELS_DIR / "huggingface"
FLASHRANK_CACHE_DIR = MODELS_DIR / "flashrank"

DB_PATH = DATA_DIR / "mcq_hub.db"
INDEX_PATH = DATA_DIR / "index"
LANCEDB_PATH = DATA_DIR / "lancedb"

# Storage & Output Directories
STORAGE_DIR = BASE_DIR / "storage"
REPORTS_DIR = STORAGE_DIR / "reports"
HIGHLIGHTED_DIR = STORAGE_DIR / "highlighted_html"
RESULTS_DIR = STORAGE_DIR / "results"

# Frontend & Offline Vendor Cache
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

# Local LLM (LM Studio) Configuration
LM_STUDIO_URL = os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234/v1")
LM_STUDIO_TIMEOUT = 60.0
DEFAULT_MODEL = "local-model"
MAX_CONTEXT_TOKENS = 4096
