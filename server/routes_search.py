from fastapi import APIRouter
from pydantic import BaseModel
from parsers.tantivy_engine import search_index
from typing import Dict, Any
import httpx
import os
import shutil
from core.database import get_db_connection
from config import BASE_DIR
from parsers.lancedb_engine import LanceDBEngine
from parsers.embedding_engine import CPUEmbeddingEngine
from agents.tema_q.topic_extractor import filter_sibling_topics
from parsers.reranker import rerank_chunks

router = APIRouter(prefix="/api/search", tags=["search"])

class SearchRequest(BaseModel):
    query: str

embedder = CPUEmbeddingEngine()
lancedb_engine = LanceDBEngine()

@router.post("")
def perform_search(req: SearchRequest) -> Dict[str, Any]:
    # Two-Stage Hybrid Search: LanceDB (Vector + FTS) -> Sibling Gating -> FlashRank
    query = req.query
    query_vector = embedder.embed_single(query)

    # 1. Query LanceDB (Hybrid approach)
    vec_results = lancedb_engine.vector_search(query_vector, limit=30)
    fts_results = lancedb_engine.fts_search(query, limit=30)

    combined = {}
    for r in vec_results:
        score = r.get('_distance', 0)
        combined[r['id']] = r
        combined[r['id']]['hybrid_score'] = 1.0 / (1.0 + score) # simple fusion score

    for i, r in enumerate(fts_results):
        score = r.get('score', 0)
        if r['id'] in combined:
             combined[r['id']]['hybrid_score'] += 1.0 / (i + 1.0)
        else:
             combined[r['id']] = r
             combined[r['id']]['hybrid_score'] = 1.0 / (i + 1.0)

    top_30_candidates = list(combined.values())
    top_30_candidates.sort(key=lambda x: x['hybrid_score'], reverse=True)
    top_30_candidates = top_30_candidates[:30]

    # 2. Taxonomy & Sibling Gating
    gated_candidates = filter_sibling_topics(query, top_30_candidates)
    top_20 = gated_candidates[:20]

    # 3. Zero-VRAM FlashRank Cross-Encoder
    top_3 = rerank_chunks(query, top_20, top_k=3)

    # Format the results
    final_results = []
    for chunk in top_3:
        final_results.append({
            "parent_content": chunk.get("parent_content", ""),
            "file_path": chunk.get("source_article", ""),
            "page_number": chunk.get("page_number", 0),
            "title": chunk.get("main_topic", chunk.get("title", "")),
            "score": chunk.get("rerank_score", chunk.get("hybrid_score", 0)),
            "doc_id": chunk.get("id", "")
        })

    top_hit = final_results[0] if final_results else None
    if top_hit:
        top_hit["source"] = "lancedb"

    return {"status": "success", "top_hit": top_hit, "all_results": {"lancedb": final_results}, "candidates": final_results}

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
    sumatra_path = shutil.which("SumatraPDF") or shutil.which("sumatrapdf.exe")
    return {"available": sumatra_path is not None}

@router.get("/diagnostics/tantivy")
def check_tantivy():
    index_path = BASE_DIR / "data" / "index"
    if index_path.exists() and index_path.is_dir():
        if (index_path / "meta.json").exists():
            return {"exists": True}
    return {"exists": False}
