from fastapi import APIRouter
from pydantic import BaseModel
import httpx
import shutil
import json
import time
import logging
from core.database import get_db_connection
from config import BASE_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["Models"])

# --- Helper methods for persistent state management ---
def _get_app_state() -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
        row = cursor.fetchone()
        if row:
            try:
                # Handle both dict-like Row access and tuple access gracefully
                return json.loads(row[0] if isinstance(row, tuple) else row["value"])
            except json.JSONDecodeError:
                pass
    return {}

def _save_app_state(state: dict):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO user_state (key, value, updated_at)
            VALUES (?, ?, ?)
        """, ("app_persistence", json.dumps(state), int(time.time())))
        conn.commit()


# --- System Diagnostics Endpoints ---
@router.get("/diagnostics/lm_studio")
async def check_lm_studio():
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://127.0.0.1:1234/v1/models")
            if resp.status_code == 200:
                return {"status": "ok", "models": resp.json().get("data", [])}
            return {"status": "error"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@router.get("/diagnostics/db")
def check_db_rows():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT (SELECT COUNT(*) FROM extracted_topics) + (SELECT COUNT(*) FROM knowledge_sources) as row_count")
            row = cursor.fetchone()
            count = row[0] if isinstance(row, tuple) else row['row_count']

            # Cross-reference LanceDB row count[span_0](start_span)[span_0](end_span)
            lancedb_count = 0
            try:
                import lancedb
                lancedb_path = BASE_DIR / "data" / "lancedb"
                if lancedb_path.exists():
                    db = lancedb.connect(str(lancedb_path))
                    if "medical_knowledge" in db.table_names():
                        table = db.open_table("medical_knowledge")
                        # Native way to get length in LanceDB without fetching all vectors
                        lancedb_count = len(table.search().limit(0).to_list())
            except Exception:
                pass

            return {"status": "ok", "sqlite_row_count": count, "lancedb_row_count": lancedb_count}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@router.get("/diagnostics/sumatrapdf")
def check_sumatra():
    sumatra_path = shutil.which("SumatraPDF") or shutil.which("sumatrapdf.exe")
    return {"available": sumatra_path is not None}

@router.get("/diagnostics/tantivy")
def check_tantivy():
    index_path = BASE_DIR / "data" / "index"
    if index_path.exists() and index_path.is_dir():
        if (index_path / "meta.json").exists():
            return {"exists": True}
    return {"exists": False}


# --- Dynamic Model Management Endpoints ---
class ModelUpdate(BaseModel):
    model_name: str

@router.get("/cpu_embedding_models")
def get_cpu_embedding_models():
    app_state = _get_app_state()
    active_model = app_state.get("embeddingModel", "tavakolih/all-MiniLM-L6-v2-pubmed-full")

    models = [
        {"id": "Xenova/SapBERT-from-PubMedBERT-fulltext", "name": "SapBERT (PubMed/UMLS Clinical)"},
        {"id": "onnx-community/Bio_ClinicalBERT-ONNX", "name": "Bio_ClinicalBERT (MIMIC-III)"},
        {"id": "Xenova/bge-small-en-v1.5", "name": "BGE Small (Fast Hybrid)"}
    ]
    return {"models": models, "active": active_model}

@router.get("/flashrank_models")
def get_flashrank_models():
    app_state = _get_app_state()
    active_model = app_state.get("flashrankModel", "ms-marco-TinyBERT-L-2-v2")

    models = [
        {"id": "ms-marco-MiniLM-L-12-v2", "name": "ms-marco-MiniLM-L-12-v2 (Best balance of speed and accuracy)"},
        {"id": "bge-reranker-base", "name": "bge-reranker-base (State-of-the-art open-source cross-encoding)"},
        {"id": "bge-reranker-v2-m3", "name": "bge-reranker-v2-m3 (Multi-lingual and dense reranker)"},
        {"id": "ms-marco-TinyBERT-L-2-v2", "name": "ms-marco-TinyBERT-L-2-v2 (Fast Default)"}
    ]
    return {"models": models, "active": active_model}

@router.post("/set_embedding_model")
def set_embedding_model(payload: ModelUpdate):
    # 1. Save the configuration to persistent SQLite storage
    app_state = _get_app_state()
    app_state["embeddingModel"] = payload.model_name
    _save_app_state(app_state)

    # 2. Hot-reload the model in memory within the global embedder instance used by routes_search
    try:
        from server.routes_search import embedder
        if embedder.model_id != payload.model_name:
            logger.info(f"Hot-reloading CPUEmbeddingEngine from {embedder.model_id} to {payload.model_name}")
            embedder.model_id = payload.model_name
            embedder._load_model()
    except Exception as e:
        logger.error(f"Failed to hot-reload embedding model in memory: {e}")
        return {"status": "error", "message": f"Saved to state, but failed to reload in memory: {e}"}

    return {"status": "success", "model": payload.model_name}

@router.post("/set_flashrank_model")
def set_flashrank_model(payload: ModelUpdate):
    # 1. Save the configuration to persistent SQLite storage
    app_state = _get_app_state()
    app_state["flashrankModel"] = payload.model_name
    _save_app_state(app_state)

    # FlashRank is instantiated per request in parsers/reranker.py.
    # Saving it to the SQLite user_state enables parsers/reranker.py to fetch it on the very next hybrid search request.
    return {"status": "success", "model": payload.model_name}
