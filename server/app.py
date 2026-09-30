import os
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from core.queue_manager import queue_worker
from server.routes_mcq import router as mcq_router
from config import BASE_DIR

app = FastAPI(title="Clinical MCQ Hub")

app.include_router(mcq_router)

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

@app.on_event("shutdown")
async def shutdown_event():
    await queue_worker.stop()
