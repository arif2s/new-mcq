import os, sys, uvicorn
from config import SERVER_HOST, SERVER_PORT, APP_TITLE
from core.database import initialize_database

def main():
    for directory in ["data/mcqs", "data/vaults", "data/pdfs", "data/zim", "data/index",
                      "storage/highlighted_html", "storage/reports", "storage/results"]:
        os.makedirs(directory, exist_ok=True)

    initialize_database()

    url = f"http://{SERVER_HOST}:{SERVER_PORT}/"
    print(f"Starting {APP_TITLE} server at {url}")
    print("Open the link above in any browser to access the application.")

    uvicorn.run("server.app:app", host=SERVER_HOST, port=SERVER_PORT, log_level="warning", reload=False)

if __name__ == "__main__":
    main()
