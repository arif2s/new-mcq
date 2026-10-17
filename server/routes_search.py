import math
import shutil
import httpx
from typing import Dict, Any, List
from contextlib import closing
from fastapi import APIRouter
from pydantic import BaseModel

from config import BASE_DIR, LM_STUDIO_URL
from core.database import get_db_connection
from parsers.tantivy_engine import search_index
from parsers.lancedb_engine import LanceDBEngine
from parsers.embedding_engine import CPUEmbeddingEngine, is_embedding_model_downloaded
from agents.tema_q.topic_extractor import filter_sibling_topics
from parsers.reranker import rerank_chunks

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


embedder = CPUEmbeddingEngine()
lancedb_engine = LanceDBEngine(vector_dim=embedder.get_dimension())


@router.post("")
def perform_search(req: SearchRequest) -> Dict[str, Any]:
    """
    Multi-Stage Depth-Weighted Hybrid Search:
    1. Tantivy BM25 + Section Heading Match + Topic Mention Density + log10(article_length)
    2. LanceDB Semantic Vector + FTS Search (if embedding model is downloaded locally)
    3. Reciprocal Rank Fusion (RRF) + Comprehensiveness/Depth Bonus
    4. Sibling Condition Gating -> Zero-VRAM FlashRank Cross-Encoder Reranking
    """
    query = (req.query or "").strip()
    if not query:
        return {
            "status": "success",
            "top_hit": None,
            "all_results": {"lancedb": [], "obsidian": [], "pdf": [], "zim": []},
            "candidates": [],
        }

    try:
        from agents.topic_extractor import get_topic_aliases
        topic_aliases = get_topic_aliases(query)
    except Exception:
        topic_aliases = [query]
        if "," in query:
            parts = [p.strip() for p in query.split(",") if p.strip()]
            if len(parts) == 2:
                topic_aliases.append(f"{parts[1]} {parts[0]}")

    # 1. Query LanceDB (Semantic + FTS across topic aliases) if offline model is available
    vec_results: List[Dict[str, Any]] = []
    fts_results: List[Dict[str, Any]] = []
    primary_query = topic_aliases[0] if topic_aliases else query
    if is_embedding_model_downloaded(embedder.model_id):
        query_vector = embedder.embed_single(primary_query)
        if query_vector:
            vec_results = lancedb_engine.vector_search(query_vector, limit=25)

    fts_results = lancedb_engine.fts_search(query, limit=25)
    for alias_q in topic_aliases[1:3]:
        if len(fts_results) >= 25:
            break
        extra_fts = lancedb_engine.fts_search(alias_q, limit=15)
        seen_ids = {r.get("id") for r in fts_results if r.get("id")}
        for r in extra_fts:
            if r.get("id") not in seen_ids:
                fts_results.append(r)
                seen_ids.add(r.get("id"))

    # 2. Query Tantivy + >10GB ZIM Direct B-Tree
    tantivy_results = search_index(query, limit_per_source=8)

    combined: Dict[str, Dict[str, Any]] = {}
    q_lowers = [a.lower() for a in topic_aliases[:3]]
    q_terms = {t for a in q_lowers for t in a.split() if len(t) >= 2}

    def _calc_depth_bonus(text: str, heading: str, art_len: int) -> float:
        t_low = (text or "").lower()
        h_low = (heading or "").lower()
        mentions = sum(t_low.count(ql) for ql in q_lowers if len(ql) >= 3)
        term_hits = sum(t_low.count(w) for w in q_terms)
        h_match = 3.0 if any(ql in h_low for ql in q_lowers) else 0.0
        len_bonus = math.log10(max(art_len, len(text), 10)) * 1.2
        density_bonus = min(mentions * 1.5 + term_hits * 0.3, 8.0)
        return h_match + len_bonus + density_bonus

    # Merge LanceDB Vector hits
    for i, r in enumerate(vec_results):
        cid = r.get("id") or f"vec_{i}"
        dist = float(r.get("_distance", 0.5))
        art_len = int(r.get("article_length", len(r.get("parent_content", "") or "")))
        heading = r.get("heading_path") or r.get("subheading") or r.get("main_topic") or ""
        d_bonus = _calc_depth_bonus(r.get("parent_content", ""), heading, art_len)

        combined[cid] = {
            **r,
            "id": cid,
            "file_path": r.get("file_path") or r.get("source_article", ""),
            "title": r.get("source_article") or r.get("main_topic", "Document"),
            "heading_path": heading,
            "article_length": art_len,
            "depth_bonus": d_bonus,
            "hybrid_score": (1.0 / (1.0 + max(0.0, dist))) * 10.0 + (1.0 / (i + 1.0)) * 5.0 + d_bonus,
            "source": "pdf" if str(r.get("source_article", "")).lower().endswith(".pdf") else "obsidian",
        }

    # Merge LanceDB FTS hits
    for i, r in enumerate(fts_results):
        cid = r.get("id") or f"fts_{i}"
        rrf = (1.0 / (i + 1.0)) * 5.0
        if cid in combined:
            combined[cid]["hybrid_score"] += rrf
        else:
            art_len = int(r.get("article_length", len(r.get("parent_content", "") or "")))
            heading = r.get("heading_path") or r.get("subheading") or r.get("main_topic") or ""
            d_bonus = _calc_depth_bonus(r.get("parent_content", ""), heading, art_len)
            combined[cid] = {
                **r,
                "id": cid,
                "file_path": r.get("file_path") or r.get("source_article", ""),
                "title": r.get("source_article") or r.get("main_topic", "Document"),
                "heading_path": heading,
                "article_length": art_len,
                "depth_bonus": d_bonus,
                "hybrid_score": rrf + d_bonus,
                "source": "pdf" if str(r.get("source_article", "")).lower().endswith(".pdf") else "obsidian",
            }

    # Merge Tantivy BM25 & ZIM hits
    for source_type, docs in tantivy_results.items():
        for i, doc in enumerate(docs):
            doc_id = doc.get("doc_id") or f"{source_type}_{i}"
            rrf = (1.0 / (i + 1.0)) * 6.0
            snippet = doc.get("snippet", "")
            heading = doc.get("heading") or doc.get("title", "")
            art_len = int(doc.get("doc_length", len(snippet)))
            d_bonus = _calc_depth_bonus(snippet, heading, art_len)

            if doc_id in combined:
                combined[doc_id]["hybrid_score"] += rrf + d_bonus
            else:
                combined[doc_id] = {
                    "id": doc_id,
                    "source_article": doc.get("file_path", ""),
                    "file_path": doc.get("file_path", ""),
                    "article_id": doc.get("article_id", doc.get("title", "")),
                    "page_number": doc.get("page", 1),
                    "title": doc.get("title", "Untitled"),
                    "main_topic": doc.get("title", "Untitled"),
                    "heading_path": heading,
                    "article_length": art_len,
                    "parent_content": snippet,
                    "child_content": snippet,
                    "snippet": snippet,
                    "depth_bonus": d_bonus,
                    "hybrid_score": rrf + d_bonus + (float(doc.get("score", 0.0)) * 0.05),
                    "source": source_type,
                }

    candidates = list(combined.values())
    candidates.sort(key=lambda x: x["hybrid_score"], reverse=True)
    top_30 = candidates[:30]

    gated_candidates = filter_sibling_topics(query, top_30)
    top_20 = gated_candidates[:20]

    reranked = rerank_chunks(query, top_20, top_k=max(6, req.limit))

    final_results = []
    grouped_results: Dict[str, List[Dict[str, Any]]] = {"lancedb": [], "obsidian": [], "pdf": [], "zim": []}

    for chunk in reranked:
        src = chunk.get("source", "obsidian")
        snippet = chunk.get("child_content") or chunk.get("snippet") or chunk.get("parent_content") or ""
        formatted_chunk = {
            "doc_id": chunk.get("id", ""),
            "source": src,
            "file_path": chunk.get("file_path") or chunk.get("source_article", ""),
            "article_id": chunk.get("article_id") or chunk.get("title", ""),
            "page_number": int(chunk.get("page_number", 1) or 1),
            "page": int(chunk.get("page_number", 1) or 1),
            "title": chunk.get("title") or chunk.get("main_topic", "Untitled"),
            "heading": chunk.get("heading_path") or chunk.get("title", ""),
            "article_length": int(chunk.get("article_length", len(snippet))),
            "parent_content": (chunk.get("parent_content") or snippet)[:1200],
            "snippet": snippet[:450],
            "score": round(float(chunk.get("rerank_score", chunk.get("hybrid_score", 0.0))), 4),
        }
        final_results.append(formatted_chunk)
        if src not in grouped_results:
            grouped_results[src] = []
        grouped_results[src].append(formatted_chunk)

    return {
        "status": "success",
        "top_hit": final_results[0] if final_results else None,
        "all_results": grouped_results,
        "candidates": final_results,
    }


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
                return {"status": "ok"}
            return {"status": "error"}
    except Exception:
        return {"status": "error"}


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
            lancedb_count = lancedb_engine.count_rows()
            return {"status": "ok", "row_count": count, "sqlite_row_count": count, "lancedb_row_count": lancedb_count}
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
    # Also return True if an enabled ZIM archive is configured so search works directly on ZIM
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM knowledge_sources WHERE enabled = 1 LIMIT 1")
        if cursor.fetchone():
            return {"exists": True}
    return {"exists": False}
