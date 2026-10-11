from fastapi import APIRouter
from pydantic import BaseModel
from parsers.tantivy_engine import search_index
from typing import Dict, Any
import httpx
import os
import shutil
from core.database import get_db_connection
from config import BASE_DIR

router = APIRouter(prefix="/api/search", tags=["search"])

class SearchRequest(BaseModel):
    query: str

@router.post("")
def perform_search(req: SearchRequest) -> Dict[str, Any]:
    # Route the top hit
    results = search_index(req.query, limit_per_source=1)

    # Return the top hit across all sources
    # Since search_index limits to 1 per source, we just find the absolute best among the returned
    top_hit = None
    max_score = -1.0

    for source, docs in results.items():
        if docs:
            doc = docs[0]
            if doc["score"] > max_score:
                max_score = doc["score"]
                top_hit = {
                    "source": source,
                    "doc_id": doc["doc_id"],
                    "title": doc["title"],
                    "score": doc["score"]
                }

    return {"status": "success", "top_hit": top_hit, "all_results": results}


@router.get("/diagnostics/lm_studio")
async def check_lm_studio():
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://localhost:1234/v1/models")
            if resp.status_code == 200:
                return {"status": "ok"}
            return {"status": "error"}
    except Exception:
        return {"status": "error"}

@router.get("/diagnostics/db")
def check_db_rows():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Just summing up some important tables for row count
            cursor.execute("SELECT (SELECT COUNT(*) FROM extracted_topics) + (SELECT COUNT(*) FROM knowledge_sources) as row_count")
            row = cursor.fetchone()
            count = row[0] if isinstance(row, tuple) else row['row_count']
            return {"status": "ok", "row_count": count}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@router.get("/diagnostics/sumatrapdf")
def check_sumatra():
    # In Windows, we'd check sumatrapdf.exe
    # We can check if it exists in PATH, or just mock it or check a known path if not in PATH.
    # For now, shutil.which is a good proxy.
    sumatra_path = shutil.which("SumatraPDF") or shutil.which("sumatrapdf.exe")
    return {"available": sumatra_path is not None}

@router.get("/diagnostics/tantivy")
def check_tantivy():
    index_path = BASE_DIR / "data" / "index"
    if index_path.exists() and index_path.is_dir():
        # Check if it has index files (meta.json is usually present in tantivy indices)
        if (index_path / "meta.json").exists():
            return {"exists": True}
    return {"exists": False}
