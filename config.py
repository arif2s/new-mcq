import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.absolute()

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
APP_TITLE = "Clinical MCQ Hub"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800

DB_PATH = BASE_DIR / "data" / "mcq_hub.db"
INDEX_PATH = BASE_DIR / "data" / "index"
REPORTS_DIR = BASE_DIR / "storage" / "reports"

LM_STUDIO_URL = "http://localhost:1234/v1"
LM_STUDIO_TIMEOUT = 60.0
DEFAULT_MODEL = "local-model"
MAX_CONTEXT_TOKENS = 4096
