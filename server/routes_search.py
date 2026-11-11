from contextlib import closing
import logging
import math
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter, HTTPException
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


class SynthesizeRequest(BaseModel):
    topic: str
    synthesis_mode: Optional[str] = None
    max_words: Optional[int] = None
    user_max_words_override: Optional[int] = None
    mcq_context: Optional[Dict[str, Any]] = None
    skip_lm: Optional[bool] = False
    model_id: Optional[str] = None


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

# --- Phase 6 Concurrent Tri-Modal Hybrid Search & RRF Fusion ---
import asyncio as _asyncio
from typing import Any as _Any, Awaitable as _Awaitable, Callable as _Callable
from parsers.reranker import reciprocal_rank_fusion as _reciprocal_rank_fusion


async def execute_parallel_hybrid_search(
    query: str,
    search_coroutines: list[_Awaitable[list[dict[str, _Any]]]],
    top_n: int = 20,
    timeout_sec: float = 5.0,
) -> list[dict[str, _Any]]:
    """
    Execute Tantivy BM25, LanceDB vector, and SciSpaCy alias searches concurrently,
    tolerating individual engine timeouts, and fuse ranked results via RRF.
    """
    if not query or not query.strip() or not search_coroutines:
        return []

    async def _safe_run(coro: _Awaitable[list[dict[str, _Any]]]) -> list[dict[str, _Any]]:
        try:
            res = await _asyncio.wait_for(coro, timeout=timeout_sec)
            return res if isinstance(res, list) else []
        except Exception:
            return []

    ranked_lists = await _asyncio.gather(*(_safe_run(c) for c in search_coroutines))
    return _reciprocal_rank_fusion(list(ranked_lists), k=60, top_n=top_n)


@router.post("/synthesize")
async def search_and_synthesize_topic(req: SynthesizeRequest) -> Dict[str, Any]:
    """
    On-demand multi-source search and synthesis endpoint accepting synthesis_mode and word limits.
    """
    topic = (req.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic must not be empty.")

    from agents.tema_q.pipeline import run_tema_q_synthesis
    search_res = await _asyncio.to_thread(search_index, topic, limit_per_source=3)
    max_w = req.max_words or req.user_max_words_override

    result = await run_tema_q_synthesis(
        topic=topic,
        search_results=search_res,
        mcq_context=req.mcq_context,
        skip_lm=bool(req.skip_lm),
        model_id=req.model_id,
        synthesis_mode=req.synthesis_mode,
        user_max_words_override=max_w,
    )
    return {"status": "success", **result}


# ===========================================================================
# Issues #14, #15, #16: Semantic Search Intent Decomposition & Section Popups
# ===========================================================================

import json
import re
from urllib.parse import quote

CLINICAL_ASPECT_TAXONOMY: dict[str, list[str]] = {
    "treatment": [
        "treatment", "therapy", "management", "therapeutics", "regimen",
        "medication", "drug", "drug of choice", "doc", "first-line",
        "second-line", "transplant", "chemotherapy", "surgery",
        "intervention", "prophylaxis", "dose", "dosing", "antidote",
    ],
    "diagnosis": [
        "diagnosis", "diagnostic", "workup", "investigation", "biopsy",
        "imaging", "laboratory", "lab", "criteria", "histopathology",
        "microscopy", "stain", "staining", "congo red", "birefringence",
        "gold standard", "screening", "ct", "mri", "ultrasound", "hallmark",
    ],
    "pathophysiology": [
        "pathophysiology", "pathogenesis", "mechanism", "etiology",
        "cause", "causes", "transmission", "vector", "genetics",
        "mutation", "fibril", "misfolding", "pathology", "risk factors",
    ],
    "clinical_features": [
        "clinical features", "symptoms", "signs", "presentation",
        "manifestations", "triad", "tetrad", "physical exam", "findings",
    ],
    "complications": [
        "complications", "prognosis", "sequelae", "mortality", "outcome",
    ],
    "epidemiology": [
        "epidemiology", "incidence", "prevalence", "definition", "overview",
    ],
    "adverse_effects": [
        "adverse effects", "side effects", "toxicity", "contraindications",
        "interactions", "overdose",
    ],
    "morphology_staining": [
        "morphology", "staining", "stain", "microscopy", "histology",
    ],
    "pharmacokinetics": [
        "pharmacokinetics", "half-life", "clearance", "metabolism",
    ],
    "anatomy_relations": [
        "anatomy", "relations", "innervation", "blood supply",
    ],
    "medicolegal": [
        "medicolegal", "autopsy", "jurisprudence", "forensic",
    ],
    "prevention": [
        "prevention", "vaccine", "vaccination", "immunization", "chemoprophylaxis",
    ],
}

_STOP_PREPOSITIONS_AND_PREFIXES: set[str] = {
    "of", "for", "in", "with", "the", "a", "an", "to", "on", "by",
    "what", "is", "how", "are", "used", "treat", "diagnose",
    "from", "at", "about", "and", "or",
}


def decompose_semantic_clinical_query(raw_query: str) -> dict[str, Any]:
    """
    Decomposes a clinical query (e.g. 'treatment of amyloidosis' or 'how to treat cardiac amyloidosis')
    into core clinical entity, detected clinical aspect key, and synonymous aspect search terms.
    """
    q_str = (raw_query or "").strip()
    if not q_str:
        return {
            "raw_query": "",
            "core_entity": "",
            "aspect_key": "",
            "aspect_synonyms": [],
        }

    q_lower = q_str.lower()
    matched_aspect = ""
    matched_tokens: list[str] = []

    # 1. Lexical matching against CLINICAL_ASPECT_TAXONOMY
    # Check longer multi-word tokens first
    best_match_len = 0
    for aspect_key, synonyms in CLINICAL_ASPECT_TAXONOMY.items():
        sorted_syns = sorted(synonyms, key=lambda s: len(s), reverse=True)
        for syn in sorted_syns:
            pattern = r"\b" + re.escape(syn) + r"\b"
            if re.search(pattern, q_lower):
                if len(syn) > best_match_len:
                    best_match_len = len(syn)
                    matched_aspect = aspect_key
                    matched_tokens = [syn]

    # 2. Embedding semantic fallback if query >= 3 words and no lexical aspect matched
    words = re.findall(r"[a-zA-Z0-9_-]+", q_lower)
    if not matched_aspect and len(words) >= 3:
        try:
            anchor_questions = [
                f"What is the {aspect.replace('_', ' ')}?"
                for aspect in CLINICAL_ASPECT_TAXONOMY.keys()
            ]
            aspect_keys_list = list(CLINICAL_ASPECT_TAXONOMY.keys())
            sim_mat = get_embedding_engine().score_texts_against_anchors([raw_query], anchor_questions)
            if sim_mat is not None and sim_mat.size > 0:
                best_idx = int(sim_mat[0].argmax())
                best_sim = float(sim_mat[0, best_idx])
                if best_sim >= 0.42:
                    matched_aspect = aspect_keys_list[best_idx]
        except Exception as exc:
            logger.debug("Semantic query decomposition embedding anchor scoring skipped: %s", exc)

    # 3. Clean and isolate core_entity
    # Remove matched aspect tokens and conversational stopwords
    cleaned_q = q_lower
    if matched_aspect:
        for syn in CLINICAL_ASPECT_TAXONOMY.get(matched_aspect, []):
            pattern = r"\b" + re.escape(syn) + r"\b"
            cleaned_q = re.sub(pattern, " ", cleaned_q)

    # Tokenize remaining words and filter stopwords/prefixes
    rem_tokens = re.findall(r"[a-zA-Z0-9_\-]+", cleaned_q)
    entity_tokens = [t for t in rem_tokens if t not in _STOP_PREPOSITIONS_AND_PREFIXES]

    core_entity = " ".join(entity_tokens).strip()
    if not core_entity:
        core_entity = q_str.strip()

    aspect_synonyms = CLINICAL_ASPECT_TAXONOMY.get(matched_aspect, []) if matched_aspect else []

    return {
        "raw_query": q_str,
        "core_entity": core_entity,
        "aspect_key": matched_aspect,
        "aspect_synonyms": aspect_synonyms,
    }


def slice_section_by_heading(
    markdown_or_html: str,
    core_entity: str = "",
    aspect_synonyms: Optional[list[str]] = None,
) -> tuple[str, str]:
    """
    Given raw Markdown or HTML/text, converts any HTML <h1>..<h6> tags into Markdown
    #..###### headings and slices the document into headed sections, returning
    (best_heading, isolated_section_markdown).
    """
    raw_text = (markdown_or_html or "").strip()
    if not raw_text:
        return "", ""

    # Convert HTML headings to Markdown headings
    converted = re.sub(
        r"<h([1-6])(?:\s+[^>]*?)?>([\s\S]*?)</h\1>",
        lambda m: f"\n\n{'#' * int(m.group(1))} {re.sub(r'<[^>]*?>', ' ', m.group(2)).strip()}\n\n",
        raw_text,
        flags=re.IGNORECASE,
    )
    # Strip remaining dangerous HTML tags while preserving line spacing
    converted = re.sub(r"<(?:p|div|br\s*/?|li)(?:\s+[^>]*?)?>", "\n", converted, flags=re.IGNORECASE)
    converted = re.sub(r"</(?:p|div|li)>", "\n", converted, flags=re.IGNORECASE)
    converted = re.sub(r"<[^>]+>", " ", converted)
    converted = re.sub(r"\n{3,}", "\n\n", converted).strip()

    # Split into headed sections using markdown heading lines
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", flags=re.MULTILINE)
    matches = list(heading_pattern.finditer(converted))

    if not matches:
        # No headings found, return overview
        return "Overview", converted

    sections: list[tuple[str, str]] = []
    # If there is preamble before first heading
    if matches[0].start() > 0:
        preamble = converted[: matches[0].start()].strip()
        if preamble:
            sections.append(("Overview", preamble))

    for i, m in enumerate(matches):
        h_title = m.group(2).strip()
        start_pos = m.start()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(converted)
        sec_text = converted[start_pos:end_pos].strip()
        sections.append((h_title, sec_text))

    aspect_syns = [s.lower() for s in (aspect_synonyms or []) if s]
    entity_low = (core_entity or "").strip().lower()

    best_score = -1.0
    best_heading, best_text = sections[0]

    for h_title, sec_text in sections:
        score = 0.0
        h_low = h_title.lower()
        body_low = sec_text.lower()

        # Score heading matches
        for syn in aspect_syns:
            if syn in h_low:
                score += 6.0
                break

        # Entity appearance in heading or body
        if entity_low and (entity_low in h_low or entity_low in body_low):
            score += 2.5

        # Aspect synonyms in body
        for syn in aspect_syns:
            if syn in body_low:
                score += 1.5

        if score > best_score:
            best_score = score
            best_heading = h_title
            best_text = sec_text

    return best_heading, best_text


def render_pdf_section_via_indra_ocr(
    file_path: str,
    page_number: int = 1,
    end_page: Optional[int] = None,
    core_entity: str = "",
    aspect_synonyms: Optional[list[str]] = None,
    heading_hint: str = "",
) -> dict[str, Any]:
    """
    Renders and isolates a PDF section using Indra Base OCR + Heading Optimization,
    checking layout_analysis_progress override and active user_state profile.
    """
    if end_page is None or end_page < page_number:
        end_page = page_number

    p_start = max(1, page_number)
    p_end = min(end_page, p_start + 1)  # Bound to max 2 pages to protect latency

    # 1. Check layout_analysis_progress in SQLite
    final_override = ""
    configured_engine = "indra_base_heading_opt"
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT final_markdown_override FROM layout_analysis_progress WHERE file_path = ? OR file_path LIKE ?",
                (file_path, f"%{Path(file_path).name}"),
            )
            row = cursor.fetchone()
            if row and row[0]:
                final_override = str(row[0]).strip()

            # Read user_state for pdfParser
            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            p_row = cursor.fetchone()
            if p_row and p_row[0]:
                try:
                    p_obj = json.loads(p_row[0]) if isinstance(p_row[0], str) else p_row[0]
                    if isinstance(p_obj, dict) and p_obj.get("pdfParser"):
                        configured_engine = str(p_obj["pdfParser"])
                except Exception:
                    pass
    except Exception as exc:
        logger.debug("Failed querying layout_analysis_progress / user_state: %s", exc)

    extracted_pages_md: list[str] = []
    used_engine = configured_engine

    if final_override:
        extracted_pages_md.append(final_override)
        used_engine = "layout_analysis_override"
    else:
        for pg in range(p_start, p_end + 1):
            pg_text = ""
            try:
                from parsers.indra import extract_page_with_indra_base_heading_opt
                indra_res = extract_page_with_indra_base_heading_opt(
                    file_path, page_number=pg, render_page_image=False
                )
                if isinstance(indra_res, dict):
                    pg_text = str(indra_res.get("markdown") or indra_res.get("text") or "")
            except Exception as exc:
                logger.debug("extract_page_with_indra_base_heading_opt failed for page %d: %s", pg, exc)
                try:
                    from parsers.docling import extract_structured_layout_v1_heading_opt
                    docling_res = extract_structured_layout_v1_heading_opt(file_path, pg)
                    if isinstance(docling_res, dict):
                        pg_text = str(docling_res.get("markdown") or docling_res.get("text") or "")
                    used_engine = "docling_v1_heading_opt"
                except Exception:
                    try:
                        from parsers.pdf_extractor import extract_page_slice
                        pg_text = extract_page_slice(file_path, pg)
                        used_engine = "fitz_fallback"
                    except Exception as err:
                        logger.warning("All PDF page extractors failed for %s p.%d: %s", file_path, pg, err)
                        pg_text = ""

            if pg_text:
                extracted_pages_md.append(pg_text)

    combined_md = "\n\n".join(extracted_pages_md)
    if not combined_md.strip():
        combined_md = f"*(No text could be extracted from page {p_start} of {Path(file_path).name})*"

    # Slice target heading section
    effective_syns = list(aspect_synonyms or [])
    if heading_hint and heading_hint not in effective_syns:
        effective_syns.insert(0, heading_hint)

    best_heading, isolated_text = slice_section_by_heading(
        combined_md,
        core_entity=core_entity,
        aspect_synonyms=effective_syns,
    )

    return {
        "status": "ok",
        "file_path": file_path,
        "page": p_start,
        "end_page": p_end,
        "heading": best_heading or heading_hint or f"Page {p_start}",
        "engine": used_engine,
        "rendered_text": isolated_text or combined_md,
    }


@router.get("/semantic-hybrid")
def search_semantic_hybrid(q: str = "", limit: int = 15) -> dict[str, Any]:
    """
    Two-Tier Semantic Clinical Search:
    Decomposes queries like 'treatment of amyloidosis' into core entity 'amyloidosis'
    and clinical intent 'treatment', searches across pdf_topic_index, obsidian_notes,
    and hybrid LanceDB/Tantivy/ZIM indexes, and produces:
      1. matching_files: Deduplicated parent file records for full browsing.
      2. section_popup_links: Granular section deep-links with isolated headings and preview.
    """
    raw_q = (q or "").strip()
    if not raw_q:
        return {
            "status": "ok",
            "query": "",
            "query_decomposition": {
                "raw_query": "",
                "core_entity": "",
                "aspect_key": "",
                "aspect_synonyms": [],
            },
            "matching_files": [],
            "section_popup_links": [],
        }

    intent = decompose_semantic_clinical_query(raw_q)
    core_entity = intent["core_entity"]
    aspect_key = intent["aspect_key"]
    aspect_synonyms = intent["aspect_synonyms"]

    matching_files_map: dict[str, dict[str, Any]] = {}
    section_popups: list[dict[str, Any]] = []

    def _register_file(
        f_path: str,
        src_type: str,
        title: str,
        matched_ent: str,
        page_num: int = 1,
    ) -> None:
        if not f_path and not title:
            return
        key = f"{src_type}::{f_path or title}"
        if key not in matching_files_map:
            matching_files_map[key] = {
                "file_path": f_path,
                "source_type": src_type,
                "title": title or Path(f_path).stem if f_path else "Document",
                "matched_entity": matched_ent,
                "page": max(1, page_num),
            }

    # -------------------------------------------------------------
    # Source 1: SQLite pdf_topic_index + lookup_pdf_topic_pages
    # -------------------------------------------------------------
    try:
        from parsers.pdf_topic_indexer import lookup_pdf_topic_pages
        pdf_hits = lookup_pdf_topic_pages(core_entity, subtopic=aspect_key or None)
        for h in (pdf_hits or []):
            fp = str(h.get("pdf_path") or "")
            t_title = str(h.get("topic_title") or Path(fp).stem)
            st_pg = max(1, int(h.get("start_page") or 1))
            end_pg = max(st_pg, int(h.get("end_page") or st_pg))
            h_path = str(h.get("heading_path") or t_title)

            _register_file(fp, "pdf", h.get("pdf_title") or Path(fp).stem, core_entity, st_pg)

            section_popups.append({
                "source_type": "pdf",
                "file_path": fp,
                "title": str(h.get("pdf_title") or Path(fp).stem),
                "page": st_pg,
                "end_page": end_pg,
                "section_heading": h_path or t_title,
                "aspect_matched": aspect_key or "overview",
                "preview": f"{t_title} (Pages {st_pg}-{end_pg}) in {Path(fp).stem}",
                "isolated_content": "",
                "popup_url": (
                    f"/api/search/section-popup?source_type=pdf"
                    f"&file_path={quote(fp)}&page={st_pg}&end_page={end_pg}"
                    f"&title={quote(t_title)}&entity={quote(core_entity)}"
                    f"&aspect={quote(aspect_key)}"
                ),
            })
    except Exception as exc:
        logger.debug("Failed querying lookup_pdf_topic_pages: %s", exc)

    # Direct query into pdf_topic_index
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            like_pat = f"%{core_entity.lower()}%"
            cursor.execute(
                """
                SELECT pdf_path, pdf_title, topic_title, parent_topic, heading_path, start_page, end_page
                FROM pdf_topic_index
                WHERE LOWER(topic_norm) LIKE ? OR LOWER(parent_norm) LIKE ? OR LOWER(heading_path_norm) LIKE ?
                LIMIT 20
                """,
                (like_pat, like_pat, like_pat),
            )
            rows = cursor.fetchall()
            for r in rows:
                fp = str(r[0] if isinstance(r, tuple) else r["pdf_path"])
                pdf_t = str(r[1] if isinstance(r, tuple) else r["pdf_title"])
                top_t = str(r[2] if isinstance(r, tuple) else r["topic_title"])
                head_p = str(r[4] if isinstance(r, tuple) else r["heading_path"])
                s_pg = max(1, int(r[5] if isinstance(r, tuple) else r["start_page"] or 1))
                e_pg = max(s_pg, int(r[6] if isinstance(r, tuple) else r["end_page"] or s_pg))

                _register_file(fp, "pdf", pdf_t or Path(fp).stem, core_entity, s_pg)

                # Prioritize or highlight if heading matches aspect synonyms
                matched_sec_heading = head_p or top_t
                section_popups.append({
                    "source_type": "pdf",
                    "file_path": fp,
                    "title": pdf_t or Path(fp).stem,
                    "page": s_pg,
                    "end_page": e_pg,
                    "section_heading": matched_sec_heading,
                    "aspect_matched": aspect_key or "overview",
                    "preview": f"{matched_sec_heading} ({pdf_t} p.{s_pg})",
                    "isolated_content": "",
                    "popup_url": (
                        f"/api/search/section-popup?source_type=pdf"
                        f"&file_path={quote(fp)}&page={s_pg}&end_page={e_pg}"
                        f"&title={quote(pdf_t)}&entity={quote(core_entity)}"
                        f"&aspect={quote(aspect_key)}"
                    ),
                })
    except Exception as exc:
        logger.debug("Direct query on pdf_topic_index skipped: %s", exc)

    # -------------------------------------------------------------
    # Source 2: SQLite obsidian_notes
    # -------------------------------------------------------------
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            like_pat = f"%{core_entity.lower()}%"
            cursor.execute(
                """
                SELECT abs_path, clean_title, book_name, headings_json, breadcrumb_path, content_markdown
                FROM obsidian_notes
                WHERE LOWER(clean_title) LIKE ? OR LOWER(contextual_search_text) LIKE ?
                LIMIT 15
                """,
                (like_pat, like_pat),
            )
            obs_rows = cursor.fetchall()
            for r in obs_rows:
                abs_p = str(r[0] if isinstance(r, tuple) else r["abs_path"])
                c_title = str(r[1] if isinstance(r, tuple) else r["clean_title"])
                b_name = str(r[2] if isinstance(r, tuple) else r["book_name"])
                c_md = str(r[5] if isinstance(r, tuple) else r["content_markdown"])

                _register_file(abs_p, "obsidian", c_title or b_name, core_entity, 1)

                h_name, isolated_md = slice_section_by_heading(
                    c_md, core_entity=core_entity, aspect_synonyms=aspect_synonyms
                )
                section_popups.append({
                    "source_type": "obsidian",
                    "file_path": abs_p,
                    "title": c_title,
                    "page": 1,
                    "end_page": 1,
                    "section_heading": h_name or c_title,
                    "aspect_matched": aspect_key or "overview",
                    "preview": (isolated_md[:240] or c_title),
                    "isolated_content": isolated_md,
                    "popup_url": (
                        f"/api/search/section-popup?source_type=obsidian"
                        f"&file_path={quote(abs_p)}&page=1&end_page=1"
                        f"&title={quote(c_title)}&entity={quote(core_entity)}"
                        f"&aspect={quote(aspect_key)}"
                    ),
                })
    except Exception as exc:
        logger.debug("Failed querying obsidian_notes: %s", exc)

    # -------------------------------------------------------------
    # Source 3: Tantivy + ZIM + LanceDB Hybrid Search
    # -------------------------------------------------------------
    try:
        hits_map = search_index(core_entity, limit_per_source=max(5, limit // 2))
        all_hits: list[dict[str, Any]] = []
        for src, h_list in (hits_map or {}).items():
            if isinstance(h_list, list):
                all_hits.extend(h_list)

        # If raw_q differs from core_entity, merge hits from raw_q
        if raw_q.lower() != core_entity.lower():
            raw_hits_map = search_index(raw_q, limit_per_source=5)
            for src, h_list in (raw_hits_map or {}).items():
                if isinstance(h_list, list):
                    all_hits.extend(h_list)

        for hit in all_hits:
            src = str(hit.get("source") or hit.get("source_type") or "zim").lower()
            fp = str(hit.get("file_path") or "")
            doc_title = str(hit.get("title") or hit.get("article_id") or "Document")
            pg = max(1, int(hit.get("page") or hit.get("page_number") or 1))
            hit_text = str(hit.get("text") or hit.get("snippet") or "")

            _register_file(fp or doc_title, src, doc_title, core_entity, pg)

            h_matched, isolated_md = slice_section_by_heading(
                hit_text, core_entity=core_entity, aspect_synonyms=aspect_synonyms
            )
            section_popups.append({
                "source_type": src,
                "file_path": fp,
                "title": doc_title,
                "page": pg,
                "end_page": pg,
                "section_heading": h_matched or doc_title,
                "aspect_matched": aspect_key or "overview",
                "preview": (isolated_md[:240] or hit_text[:240]),
                "isolated_content": isolated_md,
                "popup_url": (
                    f"/api/search/section-popup?source_type={quote(src)}"
                    f"&file_path={quote(fp)}&page={pg}&end_page={pg}"
                    f"&title={quote(doc_title)}&entity={quote(core_entity)}"
                    f"&aspect={quote(aspect_key)}"
                ),
            })
    except Exception as exc:
        logger.debug("Failed hybrid search_index for semantic query: %s", exc)

    # Deduplicate section popups by (source_type, file_path, section_heading, page)
    deduped_popups: list[dict[str, Any]] = []
    seen_popup_keys: set[str] = set()

    # Sort popups prioritizing exact aspect matches
    def _popup_priority(p: dict[str, Any]) -> float:
        h_l = (p.get("section_heading") or "").lower()
        pr = 0.0
        for syn in aspect_synonyms:
            if syn in h_l:
                pr += 10.0
                break
        if core_entity.lower() in h_l:
            pr += 3.0
        return pr

    section_popups.sort(key=_popup_priority, reverse=True)

    for p in section_popups:
        pk = f"{p['source_type']}::{p['file_path']}::{p['section_heading']}::{p['page']}"
        if pk not in seen_popup_keys:
            seen_popup_keys.add(pk)
            deduped_popups.append(p)
            if len(deduped_popups) >= limit * 2:
                break

    matching_files = list(matching_files_map.values())[:limit]

    return {
        "status": "ok",
        "query": raw_q,
        "query_decomposition": intent,
        "matching_files": matching_files,
        "section_popup_links": deduped_popups[:limit],
    }


@router.get("/section-popup")
def get_section_popup(
    source_type: str,
    file_path: str = "",
    page: int = 1,
    end_page: int = 1,
    title: str = "",
    entity: str = "",
    aspect: str = "",
) -> dict[str, Any]:
    """
    Renders and isolates only the matching clinical section for granular popup viewing.
    Supports PDF (via Indra Base OCR + Heading Opt), Obsidian (.md), and ZIM Wikipedia.
    """
    src = (source_type or "").strip().lower()
    fp = (file_path or "").strip()
    doc_title = (title or "").strip()
    clean_entity = (entity or "").strip()
    aspect_key = (aspect or "").strip().lower()

    aspect_syns = CLINICAL_ASPECT_TAXONOMY.get(aspect_key, [aspect_key] if aspect_key else [])

    if src == "pdf":
        rendered = render_pdf_section_via_indra_ocr(
            file_path=fp,
            page_number=page,
            end_page=end_page,
            core_entity=clean_entity,
            aspect_synonyms=aspect_syns,
            heading_hint=doc_title,
        )
        return {
            "status": "ok",
            "source_type": "pdf",
            "file_path": fp,
            "title": doc_title or Path(fp).stem,
            "page": page,
            "heading": rendered.get("heading") or f"Page {page}",
            "engine_profile": rendered.get("engine", "indra_base_heading_opt"),
            "content_markdown": rendered.get("rendered_text", ""),
        }

    elif src == "obsidian":
        raw_md = ""
        if fp and os.path.exists(fp):
            try:
                from parsers.obsidian_reader import read_markdown_body
                raw_md = read_markdown_body(fp)
            except Exception as exc:
                logger.debug("Failed reading obsidian file from disk %s: %s", fp, exc)

        if not raw_md:
            try:
                with closing(get_db_connection()) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT content_markdown FROM obsidian_notes WHERE abs_path = ? OR clean_title = ?",
                        (fp, doc_title or clean_entity),
                    )
                    r = cursor.fetchone()
                    if r and r[0]:
                        raw_md = str(r[0])
            except Exception:
                pass

        if not raw_md:
            raw_md = f"# {doc_title or clean_entity}\n\n*(Obsidian note body could not be located.)*"

        heading, isolated_md = slice_section_by_heading(
            raw_md, core_entity=clean_entity, aspect_synonyms=aspect_syns
        )
        return {
            "status": "ok",
            "source_type": "obsidian",
            "file_path": fp,
            "title": doc_title or clean_entity,
            "page": 1,
            "heading": heading or doc_title,
            "engine_profile": "obsidian_vault_reader",
            "content_markdown": isolated_md or raw_md,
        }

    elif src == "zim":
        raw_zim = ""
        try:
            from parsers.zim_reader import get_zim_article
            raw_zim = get_zim_article(fp, doc_title or clean_entity, max_chars=10000)
        except Exception as exc:
            logger.debug("get_zim_article failed for %s: %s", doc_title, exc)

        if not raw_zim:
            raw_zim = f"# {doc_title or clean_entity}\n\n*(ZIM article text could not be located in archive.)*"

        heading, isolated_md = slice_section_by_heading(
            raw_zim, core_entity=clean_entity, aspect_synonyms=aspect_syns
        )
        return {
            "status": "ok",
            "source_type": "zim",
            "file_path": fp,
            "title": doc_title or clean_entity,
            "page": 1,
            "heading": heading or doc_title,
            "engine_profile": "zim_b_tree_reader",
            "content_markdown": isolated_md or raw_zim,
        }

    else:
        # Fallback generic section slicer
        return {
            "status": "ok",
            "source_type": src or "unknown",
            "file_path": fp,
            "title": doc_title,
            "page": page,
            "heading": doc_title or "Overview",
            "engine_profile": "generic_reader",
            "content_markdown": f"# {doc_title}\n\nSection content for {clean_entity}.",
        }
