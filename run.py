import os
import sys
from pathlib import Path

# 1. Disable CUDA/GPU access for the FastAPI process to reserve 100% of 6GB VRAM for LM Studio
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from config import (
    SERVER_HOST,
    SERVER_PORT,
    APP_TITLE,
    CSV_DIR,
    MCQS_DIR,
    VAULTS_DIR,
    PDFS_DIR,
    ZIM_DIR,
    MODELS_DIR,
    HF_CACHE_DIR,
    FLASHRANK_CACHE_DIR,
    INDEX_PATH,
    LANCEDB_PATH,
    REPORTS_DIR,
    HIGHLIGHTED_DIR,
    RESULTS_DIR,
    VENDOR_DIR,
    VENDOR_ASSETS,
)

# Pin HuggingFace cache to local project directory for strict offline portability
os.environ["HF_HOME"] = str(HF_CACHE_DIR)
os.environ["HF_HUB_CACHE"] = str(HF_CACHE_DIR)

import time
import threading
import webbrowser
import urllib.request
import logging
import uvicorn
from core.database import initialize_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("bootstrap")


def ensure_directories():
    """Creates all required data, index, storage, and vendor directories on startup."""
    required_dirs = [
        CSV_DIR,
        MCQS_DIR,
        VAULTS_DIR,
        PDFS_DIR,
        ZIM_DIR,
        MODELS_DIR,
        HF_CACHE_DIR,
        FLASHRANK_CACHE_DIR,
        INDEX_PATH,
        LANCEDB_PATH,
        REPORTS_DIR,
        HIGHLIGHTED_DIR,
        RESULTS_DIR,
        VENDOR_DIR,
    ]
    for directory in required_dirs:
        os.makedirs(directory, exist_ok=True)


def ensure_offline_vendor_assets() -> dict:
    """
    Downloads UI CDN dependencies (Tailwind, Marked, DOMPurify, KaTeX) into ui/vendor/
    on the first online boot so the application runs 100% offline thereafter.
    Never raises an exception if offline.
    """
    status = {}
    for filename, url in VENDOR_ASSETS.items():
        target_path = VENDOR_DIR / filename
        if target_path.exists() and target_path.stat().st_size > 128:
            status[filename] = "cached"
            continue

        try:
            logger.info(f"Caching UI dependency for offline use: {filename} ...")
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                content = resp.read()
                if content and len(content) > 128:
                    temp_path = target_path.with_suffix(".tmp")
                    temp_path.write_bytes(content)
                    os.replace(temp_path, target_path)
                    status[filename] = "downloaded"
                else:
                    status[filename] = "missing"
        except Exception as e:
            logger.warning(
                f"Offline mode or network unreachable while fetching {filename} ({e}). "
                f"Fallback handling active."
            )
            status[filename] = "missing"
    return status


def open_browser(url: str):
    time.sleep(1.5)
    try:
        webbrowser.open(url)
    except Exception as e:
        logger.warning(f"Could not auto-launch browser: {e}")


def main():
    ensure_directories()
    vendor_status = ensure_offline_vendor_assets()
    missing_assets = [k for k, v in vendor_status.items() if v == "missing"]
    if missing_assets:
        logger.warning(
            f"Missing vendor assets (connect to internet once to cache): {', '.join(missing_assets)}"
        )
    else:
        logger.info("All UI vendor libraries are cached locally for 100% offline execution.")

    initialize_database()

    url = f"http://{SERVER_HOST}:{SERVER_PORT}/"
    print(f"Starting {APP_TITLE} server at {url}")
    print("Automatically opening the application in your default browser.")

    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    uvicorn.run("server.app:app", host=SERVER_HOST, port=SERVER_PORT, log_level="warning", reload=False)


if __name__ == "__main__":
    main()
