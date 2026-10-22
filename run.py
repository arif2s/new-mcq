import logging
import os
import socket
import sys
import threading
import time
import webbrowser

# 1. Enforce Invariant 1: Zero VRAM for Backend (reserve 100% of 6GB VRAM for LM Studio)
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from config import (
    APP_TITLE,
    HF_CACHE_DIR,
    MIN_VENDOR_ASSET_BYTES,
    SERVER_HOST,
    SERVER_PORT,
    VENDOR_ASSETS,
    VENDOR_DIR,
    ensure_directories,
    ensure_offline_vendor_assets,
)

# Pin HuggingFace cache to local project directory for strict offline portability
os.environ["HF_HOME"] = str(HF_CACHE_DIR)
os.environ["HF_HUB_CACHE"] = str(HF_CACHE_DIR)
os.environ["HUGGINGFACE_HUB_CACHE"] = str(HF_CACHE_DIR)

# 2. Enforce Invariant 2: Main-thread DLL pre-warming on Windows to prevent Access Violations
for _native_mod in ("onnxruntime", "lancedb", "tantivy"):
    try:
        __import__(_native_mod)
    except Exception:
        pass

import uvicorn
from core.database import initialize_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bootstrap")


def open_browser(url: str) -> None:
    """Waits for the Uvicorn socket to accept connections before launching the default browser."""
    connect_host = "127.0.0.1" if SERVER_HOST in ("0.0.0.0", "::") else SERVER_HOST
    deadline = time.monotonic() + 15.0

    while time.monotonic() < deadline:
        try:
            with socket.create_connection((connect_host, SERVER_PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)

    try:
        webbrowser.open(url)
    except Exception as exc:
        logger.warning("Could not auto-launch browser: %s", exc)


def main() -> None:
    """Application entry point: sets environment, caches vendor assets, initializes DB, and boots server."""
    ensure_directories()
    ensure_offline_vendor_assets()

    missing_assets = [
        filename
        for filename in VENDOR_ASSETS
        if not (VENDOR_DIR / filename).exists()
        or (VENDOR_DIR / filename).stat().st_size <= MIN_VENDOR_ASSET_BYTES
    ]
    if missing_assets:
        logger.warning(
            "Missing vendor assets (connect to internet once to cache): %s",
            ", ".join(missing_assets),
        )
    else:
        logger.info("All UI vendor libraries are cached locally for 100% offline execution.")

    initialize_database()

    browser_host = "127.0.0.1" if SERVER_HOST in ("0.0.0.0", "::") else SERVER_HOST
    url = f"http://{browser_host}:{SERVER_PORT}/"
    print(f"Starting {APP_TITLE} server at {url}")
    print("Automatically opening the application in your default browser.")

    threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    uvicorn.run(
        "server.app:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level="warning",
        reload=False,
    )


if __name__ == "__main__":
    main()
