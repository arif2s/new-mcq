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
    # Multi-Stage Hybrid Search: LanceDB (Vector + FTS) + Tantivy (BM25) -> Sibling Gating -> FlashRank
    query = req.query
    query_vector = embedder.embed_single(query)

    # 1. Query LanceDB (Semantic + FTS)
    vec_results = lancedb_engine.vector_search(query_vector, limit=30)
    fts_results = lancedb_engine.fts_search(query, limit=30)

    # 2. Query Tantivy (BM25 for ZIM, Obsidian, PDF)
    tantivy_results = search_index(query, limit_per_source=10)

    combined = {}

    # Merge LanceDB Vector
    for i, r in enumerate(vec_results):
        score = r.get('_distance', 0)
        combined[r['id']] = r
        combined[r['id']]['hybrid_score'] = 1.0 / (1.0 + score) # simple fusion score
        combined[r['id']]['source'] = 'lancedb'

    # Merge LanceDB FTS
    for i, r in enumerate(fts_results):
        if r['id'] in combined:
             combined[r['id']]['hybrid_score'] += 1.0 / (i + 1.0)
        else:
             combined[r['id']] = r
             combined[r['id']]['hybrid_score'] = 1.0 / (i + 1.0)
             combined[r['id']]['source'] = 'lancedb'

    # Merge Tantivy BM25
    for source_type, docs in tantivy_results.items():
        for i, doc in enumerate(docs):
            doc_id = doc["doc_id"]
            if doc_id in combined:
                combined[doc_id]['hybrid_score'] += 1.0 / (i + 1.0)
            else:
                # Normalize Tantivy dictionary format to LanceDB chunk format
                combined[doc_id] = {
                    "id": doc_id,
                    "source_article": doc["file_path"],
                    "page_number": doc.get("page", 0),
                    "title": doc["title"],
                    "main_topic": doc["title"],
                    "parent_content": "", # Tantivy doesn't return full body here; fallback to title for ranking
                    "child_content": "",
                    "hybrid_score": 1.0 / (i + 1.0), # RRF fusion score
                    "source": source_type
                }

    top_30_candidates = list(combined.values())
    top_30_candidates.sort(key=lambda x: x['hybrid_score'], reverse=True)
    top_30_candidates = top_30_candidates[:30]

    # Taxonomy & Sibling Gating
    gated_candidates = filter_sibling_topics(query, top_30_candidates)
    top_20 = gated_candidates[:20]

    # Zero-VRAM FlashRank Cross-Encoder
    top_3 = rerank_chunks(query, top_20, top_k=3)

    # Format the results and group by source for frontend compatibility
    final_results = []
    grouped_results = {"lancedb": [], "obsidian": [], "pdf": [], "zim": []}

    for chunk in top_3:
        formatted_chunk = {
            "parent_content": chunk.get("parent_content", ""),
            "file_path": chunk.get("source_article", ""),
            "page_number": chunk.get("page_number", 0),
            "title": chunk.get("main_topic", chunk.get("title", "")),
            "score": chunk.get("rerank_score", chunk.get("hybrid_score", 0)),
            "doc_id": chunk.get("id", ""),
            "source": chunk.get("source", "lancedb")
        }
        final_results.append(formatted_chunk)

        src = formatted_chunk["source"]
        if src not in grouped_results:
            grouped_results[src] = []
        grouped_results[src].append(formatted_chunk)

    top_hit = final_results[0] if final_results else None

    return {
        "status": "success",
        "top_hit": top_hit,
        "all_results": grouped_results,
        "candidates": final_results
    }

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
