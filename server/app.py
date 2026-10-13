# Add these to the VERY TOP of server/app.py to pre-warm Windows DLLs safely
import onnxruntime
import lancedb
import tantivy

import json
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import BASE_DIR
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
from server.routes_csv_loader import load_csvs_background


def _init_db_and_enqueue():
    initialize_database()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Seed the DB with CSV pre-computation if needed
        cursor.execute("SELECT 1 FROM task_queue WHERE task_type = 'BATCH_PRECOMPUTE' LIMIT 1")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO task_queue (task_type, priority, payload) VALUES (?, ?, ?)",
                ("BATCH_PRECOMPUTE", 3, json.dumps({"action": "load_csvs"}))
            )
        conn.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup execution
    await asyncio.to_thread(_init_db_and_enqueue)
    queue_worker.main_loop = asyncio.get_running_loop()
    queue_worker.start()
    yield
    # Shutdown execution
    queue_worker.stop()

app = FastAPI(title="Clinical MCQ Hub", lifespan=lifespan)

# Consolidate router inclusions
for router in (mcq_router, sources_router, csv_router, queue_router, sessions_router, state_router, notes_router, search_router, models_router):
    app.include_router(router)

# WebSocket for Document Indexing Progress
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

# WebSocket for Real-Time LLM Queue Processing
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

# Mount the Vanilla JS UI directory at the root
ui_path = BASE_DIR / "ui"

@app.get("/")
async def serve_frontend():
    if (ui_path / "index.html").exists():
        return FileResponse(ui_path / "index.html")
    return {"error": "Frontend UI directory not found."}

if ui_path.exists():
    app.mount("/", StaticFiles(directory=ui_path, html=True), name="static")
