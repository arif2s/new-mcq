# Pre-warm native C++/Rust extensions at top of server/app.py for Windows stability (Invariant 2)
import os

# Enforce Invariant 1: Zero VRAM for backend processes before any native ML/DB imports
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

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

import asyncio
from contextlib import asynccontextmanager, closing
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import (
    APP_TITLE,
    REPORTS_DIR,
    UI_DIR,
    VENDOR_DIR,
    ensure_directories,
)
from core.database import get_db_connection, initialize_database
from core.queue_manager import queue_worker

# API Routers
from server.routes_csv import router as csv_router
from server.routes_mcq import router as mcq_router
from server.routes_models import router as models_router
from server.routes_notes import router as notes_router
from server.routes_queue import router as queue_router
from server.routes_search import router as search_router
from server.routes_sessions import router as sessions_router
from server.routes_sources import router as sources_router
from server.routes_state import router as state_router

try:
    from server.routes_csv_loader import router as csv_loader_router
except (ImportError, AttributeError):
    csv_loader_router = None

# Resilient import of WebSocket ConnectionManager singleton
try:
    from server.ws_manager import manager as ws_manager
except (ImportError, AttributeError):
    from server.ws_manager import ws_manager

logger = logging.getLogger(__name__)


def _init_db_and_enqueue() -> None:
    """Initializes directories, runs schema migrations, and enqueues initial CSV precompute."""
    try:
        ensure_directories()
    except Exception as exc:
        logger.warning("ensure_directories encountered warning: %s", exc)

    initialize_database()

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM task_queue
            WHERE task_type = 'BATCH_PRECOMPUTE'
              AND topic_name = 'Load CSV Question Bank'
              AND LOWER(status) IN ('pending', 'processing')
            LIMIT 1
            """
        )
        if not cursor.fetchone():
            cursor.execute(
                """
                INSERT INTO task_queue (
                    task_type, priority, topic_name, payload, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'pending', unixepoch('now'), unixepoch('now'))
                """,
                (
                    "BATCH_PRECOMPUTE",
                    3,
                    "Load CSV Question Bank",
                    json.dumps({"action": "load_csvs"}),
                ),
            )
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure directories and database schemas on worker thread
    await asyncio.to_thread(_init_db_and_enqueue)

    # Invariant 16 & Section 3: Bind primary asyncio event loop to ws_manager and queue_worker
    running_loop = asyncio.get_running_loop()
    if hasattr(ws_manager, "set_main_loop"):
        ws_manager.set_main_loop(running_loop)
    else:
        ws_manager._main_loop = running_loop

    queue_worker.main_loop = running_loop
    queue_worker.start()

    yield

    # Teardown (Invariant 16): Stop queue worker safely off-thread to prevent blocking event loop
    try:
        await asyncio.to_thread(queue_worker.stop)
    except Exception as exc:
        logger.warning("Error stopping queue_worker: %s", exc)

    # Teardown (Invariant 16): Close shared asynchronous HTTP clients to prevent socket leaks
    try:
        from agents.tema_q.pipeline import close_http_client

        await close_http_client()
    except Exception:
        pass


app = FastAPI(title=APP_TITLE, lifespan=lifespan)

# Allow cross-origin requests for local browser and pywebview frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes
routers = [
    mcq_router,
    sources_router,
    csv_router,
    queue_router,
    sessions_router,
    state_router,
    notes_router,
    search_router,
    models_router,
]
if csv_loader_router is not None:
    routers.append(csv_loader_router)

for r in routers:
    app.include_router(r)


# Live WebSocket Endpoints (Unified via ws_manager ConnectionManager)
async def _handle_websocket(websocket: WebSocket, channel: str = "all") -> None:
    await ws_manager.connect(websocket, channel=channel)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            res = ws_manager.disconnect(websocket)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            pass


@app.websocket("/ws")
@app.websocket("/ws/")
async def websocket_main(websocket: WebSocket):
    await _handle_websocket(websocket, channel="all")


@app.websocket("/ws/indexing")
async def websocket_indexing(websocket: WebSocket):
    await _handle_websocket(websocket, channel="indexing")


@app.websocket("/ws/tasks")
async def websocket_tasks(websocket: WebSocket):
    await _handle_websocket(websocket, channel="tasks")


# Ensure static mount directories exist prior to mounting so fresh clones never skip /reports
ensure_directories()

app.mount("/vendor", StaticFiles(directory=str(VENDOR_DIR)), name="vendor")

if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui_assets")

app.mount("/reports", StaticFiles(directory=str(REPORTS_DIR)), name="reports")


@app.get("/")
async def serve_frontend():
    index_path = UI_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"error": "Frontend UI directory not found."}


if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="static")