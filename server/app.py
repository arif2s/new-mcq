# Pre-warm native C++/Rust extensions at top of server/app.py for Windows stability
try:
    import onnxruntime
except ImportError:
    pass
try:
    import lancedb
except ImportError:
    pass
try:
    import tantivy
except ImportError:
    pass

import json
import asyncio
from contextlib import asynccontextmanager, closing
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import UI_DIR, VENDOR_DIR
from server.routes_mcq import router as mcq_router
from server.routes_sources import router as sources_router
from server.routes_csv import router as csv_router
from server.routes_queue import router as queue_router
from server.routes_sessions import router as sessions_router
from server.routes_state import router as state_router
from server.routes_notes import router as notes_router
from server.routes_search import router as search_router
from server.routes_models import router as models_router
from server.ws_manager import ws_manager

from core.database import initialize_database, get_db_connection
from core.queue_manager import queue_worker


def _init_db_and_enqueue():
    initialize_database()
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM task_queue WHERE task_type = 'BATCH_PRECOMPUTE' AND status IN ('PENDING', 'PROCESSING') LIMIT 1"
        )
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO task_queue (task_type, priority, topic_name, payload) VALUES (?, ?, ?, ?)",
                ("BATCH_PRECOMPUTE", 3, "Load CSV Question Bank", json.dumps({"action": "load_csvs"})),
            )
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(_init_db_and_enqueue)
    queue_worker.main_loop = asyncio.get_running_loop()
    queue_worker.start()
    yield
    queue_worker.stop()


app = FastAPI(title="Clinical MCQ Hub", lifespan=lifespan)

for router in (
    mcq_router,
    sources_router,
    csv_router,
    queue_router,
    sessions_router,
    state_router,
    notes_router,
    search_router,
    models_router,
):
    app.include_router(router)


@app.websocket("/ws/indexing")
async def websocket_indexing(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


@app.websocket("/ws/tasks")
async def websocket_tasks(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


VENDOR_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/vendor", StaticFiles(directory=VENDOR_DIR), name="vendor")


@app.get("/")
async def serve_frontend():
    if (UI_DIR / "index.html").exists():
        return FileResponse(UI_DIR / "index.html")
    return {"error": "Frontend UI directory not found."}


if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="static")
