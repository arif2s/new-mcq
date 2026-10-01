import os
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

app = FastAPI(title="Clinical MCQ Hub")

app.include_router(mcq_router)
app.include_router(sources_router)
app.include_router(csv_router)
app.include_router(queue_router)
app.include_router(sessions_router)
app.include_router(state_router)

@app.websocket("/ws/indexing")
async def websocket_indexing(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

@app.get("/")
def serve_frontend():
    index_path = BASE_DIR / "dist" / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse("<h1>Frontend not built</h1><p>Please run <code>npm install && npm run build</code>.</p>", status_code=404)

dist_path = BASE_DIR / "dist"
if dist_path.exists():
    app.mount("/", StaticFiles(directory=dist_path, html=True), name="static")

@app.on_event("startup")
async def startup_event():
    queue_worker.start()
    load_csvs_background()

@app.on_event("shutdown")
async def shutdown_event():
    await queue_worker.stop()
