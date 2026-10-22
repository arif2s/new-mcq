from contextlib import closing
import logging
import math
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter
import httpx
from pydantic import BaseModel, Field

from agents.tema_q.topic_extractor import (
    filter_sibling_topics,
    get_topic_aliases,
)
from config import (
    INDEX_PATH,
    LANCEDB_PATH,
    LM_STUDIO_TIMEOUT,
    LM_STUDIO_URL,
)
from core.database import get_db_connection
from parsers.embedding_engine import (
    get_configured_embedding_model,
    get_embedding_engine,
    is_embedding_model_downloaded,
)
from parsers.lancedb_engine import get_lancedb_engine
from parsers.reranker import rerank_chunks
from parsers.tantivy_engine import search_index
from parsers.zim_reader import expand_query_via_zim, search_zim_articles

logger = logging.getLogger("medisearch.routes_search")

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    source_type: str = Field(default="all")
    enable_rerank: bool = Field(default=True)


def _get_file_key(doc: Dict[str, Any]) -> str:
    """
    Normalizes document file path or ZIM article ID to deduplicate by single physical file
    or distinct ZIM entry (Invariant 11 & Invariant 12).
    """
    src = (
        str(doc.get("source") or doc.get("source_type") or "").strip().lower()
    )
    fp = (
        str(doc.get("file_path") or doc.get("source_article") or "")
        .strip()
        .replace("\\", "/")
    )
    title = (
        str(
            doc.get("article_id")
            or doc.get("title")
            or doc.get("main_topic")
            or ""
        )
        .strip()
        .lower()
    )

    if src == "zim" or fp.lower().endswith(".zim"):
        return f"zim::{fp.lower()}::{title or doc.get('doc_id') or doc.get('id') or ''}"

    if fp:
        return fp.lower()
    if title:
        return title
    return str(doc.get("doc_id") or doc.get("id") or "").lower()


@router.post("")
def perform_search(req: SearchRequest) -> Dict[str, Any]:
    """
    Multi-Stage Depth-Weighted Hybrid Search with Alias Expansion & Single-File Deduplication (<250ms CPU):
    1. Alias & Layperson Resolution (e.g. 'heart attack' -> 'myocardial infarction') + ZIM term expansion.
    2. Tantivy BM25 + LanceDB (CPU Vector + FTS) + Direct ZIM B-Tree reader.
    3. Single-File Deduplication (Invariant 12): Merges passages by file key and atomically syncs page/heading/text/snippet to highest base-scoring hit.
    4. Sibling Condition Gating -> Zero-VRAM FlashRank Cross-Encoder Reranking (Invariant 9).
    """
    query = (req.query or "").strip()
    if not query:
        return {
            "status": "success",
            "top_hit": None,
            "all_results": {"obsidian": [], "pdf": [], "zim": [], "lancedb": []},
            "candidates": [],
            "query": query,
            "aliases": [],
        }

    # Invariant 8: Retrieve process-wide cached singletons dynamically so model changes sync automatically
    embedder = get_embedding_engine()
    lancedb_engine = get_lancedb_engine(
        db_path=str(LANCEDB_PATH),
        vector_dim=embedder.get_dimension(),
    )

    # 1. Topic Alias & Layperson Synonym Expansion
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
    elif query.lower() not in [a.lower() for a in topic_aliases]:
        topic_aliases.insert(0, query)

    # ZIM term expansion
    try:
        zim_exp = expand_query_via_zim(query)
        if zim_exp and zim_exp.lower() not in [
            a.lower() for a in topic_aliases
        ]:
            topic_aliases.append(zim_exp)
    except Exception:
        pass

    target_source = (req.source_type or "all").strip().lower()

    # 2. Dense Vector & FTS (LanceDB) - Batched embedding to stay strictly < 250ms on CPU
    vec_results: List[Dict[str, Any]] = []
    fts_results: List[Dict[str, Any]] = []

    if (
        target_source in ("all", "obsidian", "pdf", "lancedb")
        and lancedb_engine.count_rows() > 0
    ):
        model_id = (
            getattr(embedder, "model_id", None)
            or get_configured_embedding_model()
        )
        alias_batch = topic_aliases[:2]
        if is_embedding_model_downloaded(model_id) and alias_batch:
            try:
                q_vecs = embedder.embed(alias_batch)
                for q_vec in q_vecs:
                    if q_vec:
                        vec_results.extend(
                            lancedb_engine.vector_search(q_vec, limit=20)
                        )
            except Exception as exc:
                logger.debug("LanceDB vector search skipped: %s", exc)

        for alias_q in alias_batch:
            try:
                fts_hits = lancedb_engine.fts_search(alias_q, limit=20)
                fts_results.extend(fts_hits)
            except Exception as exc:
                logger.debug("LanceDB FTS search skipped: %s", exc)

    # 3. Tantivy BM25 Lexical Search (search_index already expands aliases internally)
    tantivy_results: Dict[str, List[Dict[str, Any]]] = {}
    if target_source in ("all", "obsidian", "pdf"):
        try:
            tantivy_results = search_index(query, limit_per_source=12)
        except Exception as exc:
            logger.debug("Tantivy search skipped: %s", exc)
            tantivy_results = {}

    # 4. Direct ZIM B-Tree Reader
    zim_results: List[Dict[str, Any]] = []
    if target_source in ("all", "zim"):
        # Include any ZIM hits already gathered by search_index
        if isinstance(tantivy_results.get("zim"), list):
            zim_results.extend(tantivy_results["zim"])
        try:
            zim_hits = search_zim_articles(query, limit=5)
            zim_results.extend(zim_hits)
        except Exception:
            pass

    # Depth-Scoring Heuristics
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

    # 5. Document Merging & Single-File Deduplication (Invariant 12)
    file_clusters: Dict[str, Dict[str, Any]] = {}

    def _add_or_merge_doc(
        raw_doc: Dict[str, Any],
        base_score: float,
        default_src: str,
        from_lancedb: bool = False,
    ) -> None:
        file_key = _get_file_key(raw_doc)
        if not file_key:
            return

        file_path = str(
            raw_doc.get("file_path") or raw_doc.get("source_article") or ""
        ).strip()
        src = (
            str(
                raw_doc.get("source")
                or raw_doc.get("source_type")
                or default_src
            )
            .strip()
            .lower()
        )
        if not src or src == "lancedb":
            if file_path.lower().endswith(".pdf"):
                src = "pdf"
            elif file_path.lower().endswith(".zim"):
                src = "zim"
            else:
                src = "obsidian"

        if (
            target_source not in ("all", "lancedb")
            and src != target_source
        ):
            return
        if target_source == "lancedb" and not from_lancedb:
            return

        passage_text = str(
            raw_doc.get("text")
            or raw_doc.get("child_content")
            or raw_doc.get("snippet")
            or raw_doc.get("parent_content")
            or raw_doc.get("obsidian_body")
            or ""
        )
        snippet_text = str(
            raw_doc.get("snippet")
            or raw_doc.get("child_content")
            or raw_doc.get("text")
            or raw_doc.get("parent_content")
            or ""
        )
        parent_text = str(
            raw_doc.get("parent_content")
            or raw_doc.get("obsidian_body")
            or passage_text
        )
        heading = str(
            raw_doc.get("heading_path")
            or raw_doc.get("heading")
            or raw_doc.get("subheading")
            or raw_doc.get("section_title")
            or raw_doc.get("main_topic")
            or ""
        )
        title = str(
            raw_doc.get("title")
            or raw_doc.get("source_article")
            or raw_doc.get("main_topic")
            or "Document"
        )
        page_no = max(
            1, int(raw_doc.get("page_number") or raw_doc.get("page") or 1)
        )
        art_len = int(
            raw_doc.get("doc_length")
            or raw_doc.get("article_length")
            or len(parent_text)
            or len(passage_text)
            or 10
        )
        doc_id = str(raw_doc.get("id") or raw_doc.get("doc_id") or file_key)

        d_bonus = _calc_depth_bonus(passage_text, heading, art_len)
        total_score = base_score + d_bonus

        candidate_obj = {
            "id": doc_id,
            "doc_id": doc_id,
            "text": passage_text,  # Invariant 9: Mandatory 'text' key for FlashRank reranker
            "source_article": file_path,
            "file_path": file_path,
            "article_id": raw_doc.get("article_id") or title,
            "page_number": page_no,
            "page": page_no,
            "title": title,
            "main_topic": title,
            "heading_path": heading,
            "heading": heading,
            "doc_length": art_len,
            "article_length": art_len,
            "parent_content": parent_text,
            "child_content": snippet_text,
            "snippet": snippet_text,
            "depth_bonus": d_bonus,
            "hybrid_score": total_score,
            "score": total_score,
            "base_score": base_score,
            "source": src,
            "is_lancedb_hit": bool(from_lancedb),
            "alias_hit_count": 1,
            "parent_id": raw_doc.get("parent_id"),
        }

        if file_key not in file_clusters:
            file_clusters[file_key] = candidate_obj
        else:
            existing = file_clusters[file_key]
            existing["alias_hit_count"] += 1
            if from_lancedb:
                existing["is_lancedb_hit"] = True

            # Invariant 12: Atomic passage synchronization — keep page, page_number,
            # heading, text, and snippet matched to the highest base-scoring hit
            if total_score > (
                existing.get("base_score", 0.0)
                + existing.get("depth_bonus", 0.0)
            ):
                existing["id"] = candidate_obj["id"]
                existing["doc_id"] = candidate_obj["doc_id"]
                existing["text"] = candidate_obj["text"]
                existing["snippet"] = candidate_obj["snippet"]
                existing["child_content"] = candidate_obj["child_content"]
                existing["parent_content"] = candidate_obj["parent_content"]
                existing["page"] = candidate_obj["page"]
                existing["page_number"] = candidate_obj["page_number"]
                existing["heading"] = candidate_obj["heading"]
                existing["heading_path"] = candidate_obj["heading_path"]
                existing["doc_length"] = candidate_obj["doc_length"]
                existing["article_length"] = candidate_obj["article_length"]
                existing["base_score"] = base_score
                existing["depth_bonus"] = d_bonus

            existing["hybrid_score"] = (
                max(existing["hybrid_score"], total_score) + 2.0
            )
            existing["score"] = existing["hybrid_score"]

    # Ingest LanceDB vector hits
    for i, r in enumerate(vec_results):
        dist = float(r.get("_distance", 0.5))
        score = (1.0 / (1.0 + max(0.0, dist))) * 10.0 + (
            1.0 / (i + 1.0)
        ) * 5.0
        _add_or_merge_doc(r, score, "pdf", from_lancedb=True)

    # Ingest LanceDB FTS hits
    for i, r in enumerate(fts_results):
        score = (1.0 / (i + 1.0)) * 5.0
        _add_or_merge_doc(r, score, "pdf", from_lancedb=True)

    # Ingest Tantivy hits (excluding ZIM which is handled in step 4)
    for source_type, docs in tantivy_results.items():
        if source_type == "zim":
            continue
        for i, doc in enumerate(docs):
            score = (1.0 / (i + 1.0)) * 6.0 + (
                float(doc.get("score", 0.0)) * 0.05
            )
            _add_or_merge_doc(doc, score, source_type, from_lancedb=False)

    # Ingest ZIM hits
    for i, doc in enumerate(zim_results):
        score = (1.0 / (i + 1.0)) * 7.0 + float(doc.get("score", 0.0))
        _add_or_merge_doc(doc, score, "zim", from_lancedb=False)

    candidates = list(file_clusters.values())
    candidates.sort(key=lambda x: x["hybrid_score"], reverse=True)

    # 6. Sibling Topic Filtering
    try:
        gated_candidates = filter_sibling_topics(query, candidates[:30])
        if not gated_candidates:
            gated_candidates = candidates[:30]
    except Exception:
        gated_candidates = candidates[:30]

    top_candidates = gated_candidates[:25]
    limit_val = max(1, int(req.limit or 10))

    # 7. Zero-VRAM FlashRank Cross-Encoder Reranking (Invariant 9)
    if req.enable_rerank and top_candidates:
        try:
            reranked = rerank_chunks(query, top_candidates, top_k=limit_val)
            if not reranked:
                reranked = top_candidates[:limit_val]
        except Exception:
            reranked = top_candidates[:limit_val]
    else:
        reranked = top_candidates[:limit_val]

    # 8. Output Assembly with verbatim ChunkDict and SearchResultsDict adherence (Section 4)
    final_results: List[Dict[str, Any]] = []
    grouped_results: Dict[str, List[Dict[str, Any]]] = {
        "obsidian": [],
        "pdf": [],
        "zim": [],
        "lancedb": [],
    }
    seen_file_keys = set()

    for chunk in reranked:
        f_key = _get_file_key(chunk)
        if f_key in seen_file_keys:
            continue
        seen_file_keys.add(f_key)

        src = str(chunk.get("source") or "obsidian")
        snippet = str(
            chunk.get("snippet")
            or chunk.get("child_content")
            or chunk.get("parent_content")
            or chunk.get("text")
            or ""
        )
        text = str(chunk.get("text") or snippet)
        parent_content = str(chunk.get("parent_content") or text)
        heading = str(
            chunk.get("heading_path")
            or chunk.get("heading")
            or chunk.get("title")
            or ""
        )
        doc_id = str(chunk.get("id") or chunk.get("doc_id") or f_key)
        page_val = max(1, int(chunk.get("page_number") or chunk.get("page") or 1))
        doc_len = int(
            chunk.get("doc_length")
            or chunk.get("article_length")
            or len(text)
        )
        final_score = round(
            float(
                chunk.get(
                    "rerank_score",
                    chunk.get("hybrid_score", chunk.get("score", 0.0)),
                )
            ),
            4,
        )

        formatted_chunk = {
            "id": doc_id,
            "doc_id": doc_id,
            "text": text,
            "snippet": snippet[:450],
            "title": str(
                chunk.get("title")
                or chunk.get("main_topic")
                or "Untitled Document"
            ),
            "heading": heading,
            "heading_path": heading,
            "source": src,
            "file_path": str(
                chunk.get("file_path") or chunk.get("source_article") or ""
            ),
            "page": page_val,
            "page_number": page_val,
            "doc_length": doc_len,
            "article_length": doc_len,
            "score": final_score,
            "rerank_score": final_score,
            "parent_id": chunk.get("parent_id"),
            "parent_content": parent_content[:1200],
            "article_id": str(
                chunk.get("article_id") or chunk.get("title") or ""
            ),
            "alias_hit_count": int(chunk.get("alias_hit_count", 1)),
        }
        final_results.append(formatted_chunk)
        if src in grouped_results:
            grouped_results[src].append(formatted_chunk)
        else:
            grouped_results[src] = [formatted_chunk]

        if chunk.get("is_lancedb_hit"):
            grouped_results["lancedb"].append(formatted_chunk)

    return {
        "status": "success",
        "top_hit": final_results[0] if final_results else None,
        "all_results": grouped_results,
        "candidates": final_results,
        "query": query,
        "aliases": topic_aliases,
    }


@router.get("/diagnostics/lm_studio")
async def check_lm_studio() -> Dict[str, Any]:
    """Legacy compatibility alias for /api/models/diagnostics/lm_studio (Section 3)."""
    try:
        base = LM_STUDIO_URL.rstrip("/")
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        timeout_sec = min(float(LM_STUDIO_TIMEOUT), 2.5)
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return {"status": "ok", "online": True}
            return {
                "status": "error",
                "online": False,
                "code": resp.status_code,
            }
    except Exception as exc:
        return {"status": "error", "online": False, "detail": str(exc)}


@router.get("/diagnostics/db")
def check_db_rows() -> Dict[str, Any]:
    """Legacy compatibility alias for /api/models/diagnostics/db (Section 3)."""
    try:
        sqlite_count = 0
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM csv_questions) +
                    (SELECT COUNT(*) FROM extracted_topics) +
                    (SELECT COUNT(*) FROM knowledge_sources) AS total_count
                """
            )
            row = cursor.fetchone()
            sqlite_count = int(
                row[0]
                if isinstance(row, tuple)
                else (row["total_count"] if row else 0)
            )

        lancedb_count = 0
        try:
            lancedb_count = get_lancedb_engine().count_rows()
        except Exception:
            lancedb_count = 0

        return {
            "status": "ok",
            "row_count": sqlite_count,
            "sqlite_row_count": sqlite_count,
            "lancedb_row_count": lancedb_count,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "row_count": 0,
            "sqlite_row_count": 0,
            "lancedb_row_count": 0,
        }


@router.get("/diagnostics/sumatrapdf")
def check_sumatra() -> Dict[str, Any]:
    """Legacy compatibility alias for /api/models/diagnostics/sumatrapdf (Section 3)."""
    sumatra_path = shutil.which("SumatraPDF") or shutil.which("sumatrapdf.exe")
    if not sumatra_path:
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        prog_files_x86 = os.environ.get(
            "ProgramFiles(x86)", "C:\\Program Files (x86)"
        )
        candidates = [
            Path(local_app_data) / "SumatraPDF" / "SumatraPDF.exe",
            Path(prog_files) / "SumatraPDF" / "SumatraPDF.exe",
            Path(prog_files_x86) / "SumatraPDF" / "SumatraPDF.exe",
        ]
        for c in candidates:
            if c.exists():
                sumatra_path = str(c)
                break
    return {
        "status": "ok" if sumatra_path else "missing",
        "available": sumatra_path is not None,
        "path": sumatra_path,
    }


@router.get("/diagnostics/tantivy")
def check_tantivy() -> Dict[str, Any]:
    """Legacy compatibility alias for /api/models/diagnostics/tantivy (Section 3)."""
    meta_file = INDEX_PATH / "meta.json"
    is_built = (
        INDEX_PATH.exists() and INDEX_PATH.is_dir() and meta_file.exists()
    )
    has_sources = False
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM knowledge_sources WHERE enabled = 1 LIMIT 1"
            )
            has_sources = cursor.fetchone() is not None
    except Exception:
        pass
    return {
        "status": "ok" if (is_built or has_sources) else "empty",
        "exists": is_built or has_sources,
        "is_built": is_built,
        "has_sources": has_sources,
        "path": str(INDEX_PATH),
    }