from fastapi import FastAPI
from core.queue_manager import queue_worker
from server.routes_mcq import router as mcq_router

app = FastAPI(title="Clinical MCQ Hub")

app.include_router(mcq_router)

@app.on_event("startup")
async def startup_event():
    queue_worker.start()

@app.on_event("shutdown")
async def shutdown_event():
    await queue_worker.stop()
