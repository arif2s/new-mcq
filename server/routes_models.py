from fastapi import APIRouter
from pydantic import BaseModel
import httpx
import shutil
from core.database import get_db_connection
from config import BASE_DIR

router = APIRouter(prefix="/api/models", tags=["Models"])

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

            # also get LanceDB row count if possible, but LanceDB uses its own logic.
            lancedb_count = 0
            try:
                import lancedb
                lancedb_path = BASE_DIR / "data" / "lancedb"
                if lancedb_path.exists():
                    db = lancedb.connect(str(lancedb_path))
                    if "knowledge_chunks" in db.table_names():
                        table = db.open_table("knowledge_chunks")
                        lancedb_count = len(table.search().limit(0).to_list()) # A way to count, maybe not perfect
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

class ModelUpdate(BaseModel):
    model_name: str

@router.get("/cpu_embedding_models")
def get_cpu_embedding_models():
    # Return some hardcoded options or local files
    models = [
        {"id": "tavakolih/all-MiniLM-L6-v2-pubmed-full", "name": "all-MiniLM-L6-v2-pubmed-full"},
        {"id": "Xenova/all-MiniLM-L6-v2", "name": "all-MiniLM-L6-v2"}
    ]
    return {"models": models, "active": "tavakolih/all-MiniLM-L6-v2-pubmed-full"}

@router.get("/flashrank_models")
def get_flashrank_models():
    models = [
        {"id": "ms-marco-TinyBERT-L-2-v2", "name": "ms-marco-TinyBERT-L-2-v2"}
    ]
    return {"models": models, "active": "ms-marco-TinyBERT-L-2-v2"}

@router.post("/set_embedding_model")
def set_embedding_model(payload: ModelUpdate):
    # Just a mock update endpoint for the UI selector
    return {"status": "success", "model": payload.model_name}

@router.post("/set_flashrank_model")
def set_flashrank_model(payload: ModelUpdate):
    return {"status": "success", "model": payload.model_name}
