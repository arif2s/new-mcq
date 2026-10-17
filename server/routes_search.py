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
from agents.tema_q.topic_extractor import filter_sibling_topics, get_topic_aliases
from parsers.reranker import rerank_chunks

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str
    limit: int = 50


embedder = CPUEmbeddingEngine()
lancedb_engine = LanceDBEngine(vector_dim=embedder.get_dimension())


def _get_file_key(doc: Dict[str, Any]) -> str:
    """Normalizes document file path or ZIM article ID to deduplicate by single file."""
    fp = (doc.get("file_path") or doc.get("source_article") or "").strip()
    if fp:
        return fp.replace("\\", "/").lower()
    art_id = (doc.get("article_id") or "").strip()
    if art_id:
        return f"zim_{art_id.lower()}"
    title = (doc.get("title") or doc.get("main_topic") or "").strip()
    if title:
        return title.lower()
    return str(doc.get("doc_id") or doc.get("id") or "").lower()


@router.post("")
def perform_search(req: SearchRequest) -> Dict[str, Any]:
    """
    Multi-Stage Depth-Weighted Hybrid Search with Alias Expansion & Single-File Deduplication:
    1. Alias Resolution (includes layperson & clinical synonyms e.g. 'heart attack' -> 'myocardial infarction', 'myocardial ischemia')
    2. Tantivy BM25 + LanceDB Vector/FTS across query & aliases
    3. Single-File Deduplication: collapses all hits/aliases pointing to the same document into 1 top file entry.
    4. Sibling Condition Gating -> Zero-VRAM FlashRank Cross-Encoder Reranking
    """
    query = (req.query or "").strip()
    if not query:
        return {
            "status": "success",
            "top_hit": None,
            "all_results": {"lancedb": [], "obsidian": [], "pdf": [], "zim": []},
            "candidates": [],
            "query": query,
            "aliases": [],
        }

    try:
        topic_aliases = get_topic_aliases(query)
    except Exception:
        topic_aliases = [query]
        if "," in query:
            parts = [p.strip() for p in query.split(",") if p.strip()]
            if len(parts) == 2:
                topic_aliases.append(f"{parts[1]} {parts[0]}")

    if not topic_aliases:
        topic_aliases = [query]

    # 1. Query LanceDB (Vector + FTS) across query and aliases
    vec_results: List[Dict[str, Any]] = []
    fts_results: List[Dict[str, Any]] = []

    if is_embedding_model_downloaded(embedder.model_id):
        for alias_q in topic_aliases[:3]:
            q_vec = embedder.embed_single(alias_q)
            if q_vec:
                vec_hits = lancedb_engine.vector_search(q_vec, limit=20)
                vec_results.extend(vec_hits)

    for alias_q in topic_aliases[:3]:
        fts_hits = lancedb_engine.fts_search(alias_q, limit=20)
        fts_results.extend(fts_hits)

    # 2. Query Tantivy + ZIM Direct B-Tree across query and top aliases
    tantivy_results = search_index(query, limit_per_source=10)
    for alias_q in topic_aliases[1:3]:
        alias_tantivy = search_index(alias_q, limit_per_source=8)
        for src, docs in alias_tantivy.items():
            if src not in tantivy_results:
                tantivy_results[src] = []
            tantivy_results[src].extend(docs)

    q_lowers = [a.lower() for a in topic_aliases[:5]]
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

    # 3. Collect and Deduplicate candidates by single File Key
    file_clusters: Dict[str, Dict[str, Any]] = {}

    def _add_or_merge_doc(raw_doc: Dict[str, Any], base_score: float, default_src: str):
        file_key = _get_file_key(raw_doc)
        if not file_key:
            return

        art_len = int(raw_doc.get("article_length") or raw_doc.get("doc_length") or len(raw_doc.get("parent_content") or raw_doc.get("snippet") or ""))
        heading = raw_doc.get("heading_path") or raw_doc.get("heading") or raw_doc.get("subheading") or raw_doc.get("main_topic") or ""
        snippet = raw_doc.get("child_content") or raw_doc.get("snippet") or raw_doc.get("parent_content") or ""
        d_bonus = _calc_depth_bonus(snippet, heading, art_len)
        total_score = base_score + d_bonus

        file_path = raw_doc.get("file_path") or raw_doc.get("source_article") or ""
        src = raw_doc.get("source") or default_src
        if not src or src == "lancedb":
            src = "pdf" if str(file_path).lower().endswith(".pdf") else "obsidian"

        candidate_obj = {
            "id": raw_doc.get("id") or raw_doc.get("doc_id") or file_key,
            "doc_id": raw_doc.get("doc_id") or raw_doc.get("id") or file_key,
            "source_article": file_path,
            "file_path": file_path,
            "article_id": raw_doc.get("article_id") or raw_doc.get("title", ""),
            "page_number": int(raw_doc.get("page_number") or raw_doc.get("page") or 1),
            "page": int(raw_doc.get("page_number") or raw_doc.get("page") or 1),
            "title": raw_doc.get("title") or raw_doc.get("main_topic") or "Document",
            "main_topic": raw_doc.get("main_topic") or raw_doc.get("title") or "Document",
            "heading_path": heading,
            "heading": heading,
            "article_length": art_len,
            "parent_content": snippet,
            "child_content": snippet,
            "snippet": snippet,
            "depth_bonus": d_bonus,
            "hybrid_score": total_score,
            "source": src,
            "alias_hit_count": 1,
        }

        if file_key not in file_clusters:
            file_clusters[file_key] = candidate_obj
        else:
            existing = file_clusters[file_key]
            existing["hybrid_score"] = max(existing["hybrid_score"], total_score) + 2.0
            existing["alias_hit_count"] += 1
            if len(snippet) > len(existing["snippet"]):
                existing["snippet"] = snippet
                existing["child_content"] = snippet
                existing["parent_content"] = snippet
            if heading and not existing["heading_path"]:
                existing["heading_path"] = heading
                existing["heading"] = heading

    for i, r in enumerate(vec_results):
        dist = float(r.get("_distance", 0.5))
        score = (1.0 / (1.0 + max(0.0, dist))) * 10.0 + (1.0 / (i + 1.0)) * 5.0
        _add_or_merge_doc(r, score, "obsidian")

    for i, r in enumerate(fts_results):
        score = (1.0 / (i + 1.0)) * 5.0
        _add_or_merge_doc(r, score, "obsidian")

    for source_type, docs in tantivy_results.items():
        for i, doc in enumerate(docs):
            score = (1.0 / (i + 1.0)) * 6.0 + (float(doc.get("score", 0.0)) * 0.05)
            _add_or_merge_doc(doc, score, source_type)

    candidates = list(file_clusters.values())
    candidates.sort(key=lambda x: x["hybrid_score"], reverse=True)

    gated_candidates = filter_sibling_topics(query, candidates[:30])
    top_candidates = gated_candidates[:25]

    limit_val = max(1, req.limit) if req.limit else 50
    reranked = rerank_chunks(query, top_candidates, top_k=limit_val)

    final_results = []
    grouped_results: Dict[str, List[Dict[str, Any]]] = {"lancedb": [], "obsidian": [], "pdf": [], "zim": []}
    seen_file_keys = set()

    for chunk in reranked:
        f_key = _get_file_key(chunk)
        if f_key in seen_file_keys:
            continue
        seen_file_keys.add(f_key)

        src = chunk.get("source", "obsidian")
        snippet = chunk.get("child_content") or chunk.get("snippet") or chunk.get("parent_content") or ""
        formatted_chunk = {
            "doc_id": chunk.get("id") or chunk.get("doc_id") or f_key,
            "source": src,
            "file_path": chunk.get("file_path") or chunk.get("source_article", ""),
            "article_id": chunk.get("article_id") or chunk.get("title", ""),
            "page_number": int(chunk.get("page_number", 1) or 1),
            "page": int(chunk.get("page_number", 1) or 1),
            "title": chunk.get("title") or chunk.get("main_topic", "Untitled Document"),
            "heading": chunk.get("heading_path") or chunk.get("heading") or chunk.get("title", ""),
            "article_length": int(chunk.get("article_length", len(snippet))),
            "parent_content": (chunk.get("parent_content") or snippet)[:1200],
            "snippet": snippet[:450],
            "score": round(float(chunk.get("rerank_score", chunk.get("hybrid_score", 0.0))), 4),
            "alias_hit_count": chunk.get("alias_hit_count", 1),
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
        "query": query,
        "aliases": topic_aliases,
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
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM knowledge_sources WHERE enabled = 1 LIMIT 1")
        if cursor.fetchone():
            return {"exists": True}
    return {"exists": False}
