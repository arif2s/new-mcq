import json
import time
import shutil
import asyncio
import logging
from contextlib import closing
import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from config import BASE_DIR, LM_STUDIO_URL
from core.database import get_db_connection
from parsers.embedding_engine import (
    MODEL_DIMENSIONS,
    is_embedding_model_downloaded,
    download_embedding_model,
)
from parsers.reranker import (
    is_flashrank_model_downloaded,
    download_flashrank_model,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["Models"])


def _get_app_state() -> dict:
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row[0] if isinstance(row, tuple) else row["value"])
            except json.JSONDecodeError:
                pass
    return {}


def _save_app_state(state: dict):
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO user_state (key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            ("app_persistence", json.dumps(state), int(time.time())),
        )
        conn.commit()


# --- System Diagnostics Endpoints ---
@router.get("/diagnostics/lm_studio")
async def check_lm_studio():
    try:
        url = (
            LM_STUDIO_URL.replace("/v1", "/v1/models")
            if LM_STUDIO_URL.endswith("/v1")
            else LM_STUDIO_URL.rstrip("/") + "/models"
        )
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return {"status": "ok", "models": resp.json().get("data", [])}
            return {"status": "error"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/diagnostics/db")
def check_db_rows():
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT (SELECT COUNT(*) FROM csv_questions) + (SELECT COUNT(*) FROM extracted_topics) + (SELECT COUNT(*) FROM knowledge_sources) as row_count"
            )
            row = cursor.fetchone()
            count = row[0] if isinstance(row, tuple) else row["row_count"]

            from server.routes_search import lancedb_engine
            lancedb_count = lancedb_engine.count_rows()

            return {
                "status": "ok",
                "row_count": count,
                "sqlite_row_count": count,
                "lancedb_row_count": lancedb_count,
            }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/diagnostics/sumatrapdf")
def check_sumatra():
    sumatra_path = shutil.which("SumatraPDF") or shutil.which("sumatrapdf.exe")
    return {"available": sumatra_path is not None}


@router.get("/diagnostics/tantivy")
def check_tantivy():
    index_path = BASE_DIR / "data" / "index"
    if index_path.exists() and index_path.is_dir() and (index_path / "meta.json").exists():
        return {"exists": True}
    return {"exists": False}


# --- Dynamic Model Management & Button-Triggered Offline Downloader ---
class ModelUpdate(BaseModel):
    model_name: str


class DownloadModelRequest(BaseModel):
    model_type: str  # 'embedding' | 'flashrank'
    model_name: str


@router.get("/cpu_embedding_models")
def get_cpu_embedding_models():
    app_state = _get_app_state()
    active_model = app_state.get("embeddingModel", "Xenova/all-MiniLM-L6-v2")

    raw_models = [
        {"id": "Xenova/all-MiniLM-L6-v2", "name": "MiniLM-L6-v2 (Fast 384d Default)"},
        {"id": "Xenova/bge-small-en-v1.5", "name": "BGE Small EN v1.5 (High Precision 384d)"},
        {"id": "Xenova/SapBERT-from-PubMedBERT-fulltext", "name": "SapBERT (PubMed/UMLS Clinical 768d)"},
        {"id": "onnx-community/Bio_ClinicalBERT-ONNX", "name": "Bio_ClinicalBERT (MIMIC-III Clinical 768d)"},
    ]
    models = [
        {
            **m,
            "dimension": MODEL_DIMENSIONS.get(m["id"], 384),
            "downloaded": is_embedding_model_downloaded(m["id"]),
        }
        for m in raw_models
    ]
    return {"models": models, "active": active_model}


@router.get("/flashrank_models")
def get_flashrank_models():
    app_state = _get_app_state()
    active_model = app_state.get("flashrankModel", "ms-marco-TinyBERT-L-2-v2")

    raw_models = [
        {"id": "ms-marco-TinyBERT-L-2-v2", "name": "ms-marco-TinyBERT-L-2-v2 (Ultra-Fast Default)"},
        {"id": "ms-marco-MiniLM-L-12-v2", "name": "ms-marco-MiniLM-L-12-v2 (Balanced Speed & Accuracy)"},
        {"id": "bge-reranker-base", "name": "bge-reranker-base (High Accuracy Cross-Encoder)"},
    ]
    models = [
        {
            **m,
            "downloaded": is_flashrank_model_downloaded(m["id"]),
        }
        for m in raw_models
    ]
    return {"models": models, "active": active_model}


@router.post("/set_embedding_model")
def set_embedding_model(payload: ModelUpdate):
    app_state = _get_app_state()
    app_state["embeddingModel"] = payload.model_name
    _save_app_state(app_state)

    downloaded = is_embedding_model_downloaded(payload.model_name)
    if downloaded:
        try:
            from server.routes_search import embedder
            embedder.model_id = payload.model_name
            embedder.session = None
            embedder._load_model()
        except Exception as e:
            logger.warning(f"Could not hot-reload embedding model: {e}")

    return {
        "status": "success",
        "model": payload.model_name,
        "downloaded": downloaded,
    }


@router.post("/set_flashrank_model")
def set_flashrank_model(payload: ModelUpdate):
    app_state = _get_app_state()
    app_state["flashrankModel"] = payload.model_name
    _save_app_state(app_state)
    return {
        "status": "success",
        "model": payload.model_name,
        "downloaded": is_flashrank_model_downloaded(payload.model_name),
    }


@router.post("/download")
async def download_model_for_offline_use(payload: DownloadModelRequest):
    """
    Explicit button-triggered model downloader in Settings.
    Downloads the selected CPU Embedding or FlashRank model into ./data/models/
    and activates it for 100% offline use.
    """
    m_type = (payload.model_type or "").lower().strip()
    m_name = (payload.model_name or "").strip()

    if m_type == "embedding":
        res = await asyncio.to_thread(download_embedding_model, m_name)
        if res.get("status") == "success":
            app_state = _get_app_state()
            app_state["embeddingModel"] = m_name
            _save_app_state(app_state)
            try:
                from server.routes_search import embedder
                embedder.model_id = m_name
                embedder.session = None
                await asyncio.to_thread(embedder._load_model)
            except Exception as e:
                logger.warning(f"Hot-reload after download warning: {e}")
        return res

    if m_type == "flashrank":
        res = await asyncio.to_thread(download_flashrank_model, m_name)
        if res.get("status") == "success":
            app_state = _get_app_state()
            app_state["flashrankModel"] = m_name
            _save_app_state(app_state)
        return res

    return {"status": "error", "message": f"Unknown model_type: {payload.model_type}", "downloaded": False}
