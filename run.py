import os, sys, threading, time, uvicorn, webview
from config import SERVER_HOST, SERVER_PORT, APP_TITLE, WINDOW_WIDTH, WINDOW_HEIGHT
from core.database import initialize_database

def start_server():
    uvicorn.run("server.app:app", host=SERVER_HOST, port=SERVER_PORT, log_level="warning", reload=False)

def main():
    for directory in ["data/mcqs", "data/vaults", "data/pdfs", "data/zim", "data/index",
                      "storage/highlighted_html", "storage/reports", "storage/results"]:
        os.makedirs(directory, exist_ok=True)

    initialize_database()

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    time.sleep(1.5)
    window = webview.create_window(title=APP_TITLE, url=f"http://{SERVER_HOST}:{SERVER_PORT}/",
                                   width=WINDOW_WIDTH, height=WINDOW_HEIGHT, background_color="#121316")
    webview.start(gui="edgechromium", debug=False)
    sys.exit(0)

if __name__ == "__main__":
    main()
