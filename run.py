import os
import sys

# Disable CUDA/GPU access for the FastAPI process to protect LM Studio VRAM
os.environ["CUDA_VISIBLE_DEVICES"] = ""

import uvicorn
import webbrowser
import threading
import time
from config import SERVER_HOST, SERVER_PORT, APP_TITLE
from core.database import initialize_database

def open_browser(url):
    time.sleep(1.5)  # Wait for Uvicorn to start
    webbrowser.open(url)

def main():
    for directory in ["data/mcqs", "data/vaults", "data/pdfs", "data/zim", "data/index",
                      "storage/highlighted_html", "storage/reports", "storage/results"]:
        os.makedirs(directory, exist_ok=True)

    initialize_database()

    url = f"http://127.0.0.1:8000/"
    print(f"Starting {APP_TITLE} server at {url}")
    print("Automatically opening the application in your default browser.")

    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    uvicorn.run("server.app:app", host="127.0.0.1", port=8000, log_level="warning", reload=False)

if __name__ == "__main__":
    main()
