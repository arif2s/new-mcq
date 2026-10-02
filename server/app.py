from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from config import BASE_DIR
from server.routes_mcq import router as mcq_router
from server.routes_sources import router as sources_router
from server.routes_csv import router as csv_router
from server.routes_queue import router as queue_router
from server.routes_sessions import router as sessions_router
from server.routes_state import router as state_router
from server.ws_manager import ws_manager
from core.queue_manager import queue_worker
from server.routes_csv_loader import load_csvs_background

# 1. Replace deprecated @app.on_event with modern async context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup execution
    queue_worker.start()
    load_csvs_background()
    yield
    # Shutdown execution
    await queue_worker.stop()

app = FastAPI(title="Clinical MCQ Hub", lifespan=lifespan)

# 2. Consolidate router inclusions for cleaner initialization
for router in (mcq_router, sources_router, csv_router, queue_router, sessions_router, state_router):
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
        # 3. Catch general exceptions to prevent unhandled connection drops on Windows sockets
        ws_manager.disconnect(websocket)

# 4. Pre-compute path resolution at module load rather than per-request
dist_path = BASE_DIR / "dist"
index_path = dist_path / "index.html"

# 5. Make the endpoint async to avoid FastAPI spinning up a separate thread just to check a path
@app.get("/")
async def serve_frontend():
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(
        "<h1>Frontend not built</h1><p>Please run <code>npm install && npm run build</code>.</p>",
        status_code=404
    )

if dist_path.exists():
    app.mount("/", StaticFiles(directory=dist_path, html=True), name="static")
