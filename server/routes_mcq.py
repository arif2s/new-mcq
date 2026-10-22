from contextlib import closing
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agents.tema_q.topic_extractor import (
    extract_topics_local,
    get_ignored_topics_set,
    normalize_topic_key as _raw_normalize_topic_key,
)
from core.database import get_db_connection
from core.queue_manager import is_llm_processing_enabled
from core.reporter import generate_topic_report
from core.scheduler import calculate_fsrs_next_interval
from parsers.tantivy_engine import search_index
from parsers.zim_reader import search_zim_articles

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcq", tags=["mcq"])


def normalize_topic_key(text: str) -> str:
    """Normalizes topic text using the canonical topic_extractor normalizer."""
    try:
        return _raw_normalize_topic_key(text)
    except Exception:
        return (text or "").strip().lower()


def _to_synthesis_key(text: str) -> str:
    """Normalizes topic text to the hyphenated cached_syntheses.topic_key convention."""
    norm = normalize_topic_key(text)
    if not norm:
        norm = (text or "").strip().lower()
    return re.sub(r"[\s_]+", "-", norm)


def _has_references(refs: Any) -> bool:
    """Polymorphically checks if references exist across dict, list, JSON string, or None (Invariant 13)."""
    if not refs:
        return False
    if isinstance(refs, str):
        raw = refs.strip()
        if not raw or raw in ("{}", "[]", "null"):
            return False
        try:
            refs = json.loads(raw)
        except Exception:
            return False
    if isinstance(refs, dict):
        if any(k in refs for k in ("title", "file_path", "snippet")):
            return True
        return any(
            isinstance(v, (list, dict, set)) and len(v) > 0 for v in refs.values()
        )
    if isinstance(refs, (list, tuple)):
        return len(refs) > 0
    return False


def _has_synthesized_note(raw_article: Any) -> bool:
    """Verifies whether unified_article contains substantive synthesized content."""
    if not raw_article:
        return False
    art_str = str(raw_article).strip()
    if (
        not art_str
        or art_str in ("{}", "[]", "null")
        or "LM Studio processing skipped" in art_str
        or "Please add reference material" in art_str
        or "No local reference files found" in art_str
        or "No relevant documents passed" in art_str
    ):
        return False
    try:
        parsed = json.loads(art_str)
        if isinstance(parsed, dict):
            pts = parsed.get("clinical_points")
            raw_txt = str(parsed.get("raw_text") or "").strip()
            return bool((isinstance(pts, list) and len(pts) > 0) or raw_txt)
    except Exception:
        return len(art_str) > 25
    return False


class TopicExtractionPayload(BaseModel):
    mcq_text: str = ""
    question: Optional[str] = None
    options: Optional[Union[Dict[str, str], List[str], Any]] = None
    explanation: str = ""
    strategy: Optional[str] = None
    use_zim_refinement: bool = False
    use_tantivy_refinement: bool = False


@router.post("/extract_topics")
@router.post("/topics/extract")
def extract_mcq_topics(payload: TopicExtractionPayload) -> Dict[str, Any]:
    """
    Extracts canonical medical entities from an MCQ stem, options, and explanation.
    Classifies topics into verified (pre-selected) and secondary/doubtful topics.
    """
    stem = (payload.mcq_text or payload.question or "").strip()
    if not stem and not payload.options and not payload.explanation:
        return {
            "status": "success",
            "main_topic": "General Medical Concept",
            "topics": [],
            "all_topics": [],
            "topic_details": [],
        }

    strategy = payload.strategy
    if not strategy:
        strategy = "medspacy"
        try:
            with closing(get_db_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT value FROM user_state WHERE key = 'app_persistence'"
                )
                row = cursor.fetchone()
                if row:
                    app_state = json.loads(
                        row[0] if isinstance(row, tuple) else row["value"]
                    )
                    if isinstance(app_state, dict):
                        strategy = str(app_state.get("extractorStrategy") or "medspacy")
        except Exception:
            strategy = "medspacy"

    try:
        res = extract_topics_local(
            question=stem,
            options=payload.options or {},
            explanation=payload.explanation,
            strategy=strategy,
            use_zim_refinement=payload.use_zim_refinement,
            use_tantivy_refinement=payload.use_tantivy_refinement,
        )
    except Exception as exc:
        logger.error("Topic extraction failed for stem '%s': %s", stem[:50], exc)
        res = {
            "main_topic": "General Medical Concept",
            "all_topics": [],
            "topic_details": [],
        }

    topics = [
        t for t in res.get("all_topics", []) if t and t != "General Medical Concept"
    ]
    return {
        "status": "success",
        "main_topic": res.get("main_topic") or "General Medical Concept",
        "topics": topics,
        "all_topics": topics,
        "topic_details": res.get("topic_details", []),
    }


class ScorePayload(BaseModel):
    mcq_id: Union[int, str]
    rating: int = Field(ge=1, le=4, description="1=Again, 2=Hard, 3=Good, 4=Easy")
    session_id: Optional[str] = None


@router.post("/score")
def score_question(payload: ScorePayload) -> Dict[str, Any]:
    """
    Calculates the next FSRS v4.5 review interval and updates either csv_questions (string ID)
    or mcq_bank (integer ID).
    """
    mcq_id_str = str(payload.mcq_id).strip()
    if not mcq_id_str:
        raise HTTPException(status_code=400, detail="mcq_id is required")

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        # 1. Check csv_questions first (primary question table for CSV sets)
        cursor.execute(
            "SELECT state, difficulty, stability, reps, lapses FROM csv_questions WHERE id = ?",
            (mcq_id_str,),
        )
        card = cursor.fetchone()
        target_table = "csv_questions"
        lookup_id: Union[str, int] = mcq_id_str

        # 2. Fallback to mcq_bank if numeric ID
        if not card and mcq_id_str.isdigit():
            lookup_id = int(mcq_id_str)
            cursor.execute(
                "SELECT state, difficulty, stability, reps, lapses FROM mcq_bank WHERE id = ?",
                (lookup_id,),
            )
            card = cursor.fetchone()
            target_table = "mcq_bank"

        if not card:
            raise HTTPException(
                status_code=404, detail=f"MCQ '{mcq_id_str}' not found"
            )

        state_val = int(card["state"] if hasattr(card, "keys") else card[0] or 0)
        diff_val = float(card["difficulty"] if hasattr(card, "keys") else card[1] or 0.0)
        stab_val = float(card["stability"] if hasattr(card, "keys") else card[2] or 0.0)
        reps_val = int(card["reps"] if hasattr(card, "keys") else card[3] or 0)
        laps_val = int(card["lapses"] if hasattr(card, "keys") else card[4] or 0)

        current_time = float(time.time())
        new_stats = calculate_fsrs_next_interval(
            rating=payload.rating,
            state=state_val,
            difficulty=diff_val,
            stability=stab_val,
            reps=reps_val,
            lapses=laps_val,
            now=current_time,
        )

        interval_seconds = float(new_stats.get("interval_seconds", 0))
        due_timestamp = float(
            new_stats.get("due_timestamp", current_time + interval_seconds)
        )

        if target_table == "csv_questions":
            cursor.execute(
                """
                UPDATE csv_questions
                SET state = ?, difficulty = ?, stability = ?, reps = ?, lapses = ?,
                    last_review = ?, due_timestamp = ?
                WHERE id = ?
                """,
                (
                    int(new_stats.get("state", 0)),
                    float(new_stats.get("difficulty", 0.0)),
                    float(new_stats.get("stability", 0.0)),
                    int(new_stats.get("reps", 0)),
                    int(new_stats.get("lapses", 0)),
                    current_time,
                    due_timestamp,
                    str(lookup_id),
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE mcq_bank
                SET state = ?, difficulty = ?, stability = ?, reps = ?, lapses = ?,
                    last_review = ?, due_timestamp = ?
                WHERE id = ?
                """,
                (
                    int(new_stats.get("state", 0)),
                    float(new_stats.get("difficulty", 0.0)),
                    float(new_stats.get("stability", 0.0)),
                    int(new_stats.get("reps", 0)),
                    int(new_stats.get("lapses", 0)),
                    current_time,
                    due_timestamp,
                    int(lookup_id),
                ),
            )
        conn.commit()

    return {
        "status": "success",
        "mcq_id": mcq_id_str,
        "next_due": int(interval_seconds),
        "due_timestamp": due_timestamp,
        "fsrs": new_stats,
    }


@router.get("/synthesis")
def get_synthesis(topic: str, resolve_if_missing: bool = True) -> Dict[str, Any]:
    """
    Fetches the cached Tema_Q synthesis and reference links for the Right Sidebar Inspector.
    Resolves reference links across Tantivy and LibZim on the fly if missing (Invariant 13).
    """
    if not topic or not topic.strip():
        raise HTTPException(status_code=400, detail="Topic parameter is required")

    clean_topic = topic.strip()
    hyphen_key = _to_synthesis_key(clean_topic)
    space_key = normalize_topic_key(clean_topic)
    llm_enabled = is_llm_processing_enabled()

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT topic_key, display_title, enhanced_explanation, distractor_analysis,
                   unified_article, variance_audit, top_references,
                   has_obsidian_note, has_pdf_match, has_zim_match
            FROM cached_syntheses
            WHERE topic_key IN (?, ?)
            ORDER BY CASE WHEN topic_key = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (hyphen_key, space_key, hyphen_key),
        )
        row = cursor.fetchone()

        references: Union[Dict[str, Any], List[Any]] = {}
        if row and row["top_references"]:
            try:
                references = json.loads(row["top_references"])
            except Exception:
                references = {}

        has_refs = _has_references(references)

    # On-the-fly reference resolution if missing (executed outside DB lock)
    if (not row or not has_refs) and resolve_if_missing:
        try:
            refs: Dict[str, List[Any]] = search_index(
                clean_topic, limit_per_source=3
            )
            if not isinstance(refs, dict):
                refs = {"obsidian": [], "pdf": [], "zim": []}

            if not refs.get("zim"):
                try:
                    zim_hits = search_zim_articles(clean_topic, limit=3)
                    if zim_hits:
                        refs["zim"] = zim_hits
                except Exception:
                    pass

            has_obs = 1 if refs.get("obsidian") else 0
            has_pdf = 1 if refs.get("pdf") else 0
            has_zim = 1 if refs.get("zim") else 0

            with closing(get_db_connection()) as conn:
                conn.execute(
                    """
                    INSERT INTO cached_syntheses (
                        topic_key, display_title, enhanced_explanation, distractor_analysis,
                        unified_article, variance_audit, top_references,
                        has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                    ) VALUES (?, ?, '', '', '', '', ?, ?, ?, ?, unixepoch('now'))
                    ON CONFLICT(topic_key) DO UPDATE SET
                        top_references = excluded.top_references,
                        has_obsidian_note = excluded.has_obsidian_note,
                        has_pdf_match = excluded.has_pdf_match,
                        has_zim_match = excluded.has_zim_match,
                        updated_at = unixepoch('now')
                    """,
                    (
                        hyphen_key,
                        clean_topic,
                        json.dumps(refs),
                        has_obs,
                        has_pdf,
                        has_zim,
                    ),
                )
                conn.commit()

            references = refs
            has_refs = _has_references(references)

            try:
                generate_topic_report(
                    topic=clean_topic,
                    search_results=refs,
                    synthesis_output={
                        "topic_key": hyphen_key,
                        "display_title": clean_topic,
                        "top_references": refs,
                    },
                )
            except Exception:
                pass
        except Exception as exc:
            logger.error(
                "Failed to resolve references on the fly for '%s': %s",
                clean_topic,
                exc,
            )

    if not row and not has_refs and not resolve_if_missing:
        raise HTTPException(
            status_code=404,
            detail="Synthesis and references not found for this topic.",
        )

    display_title = (
        row["display_title"]
        if (row and row["display_title"])
        else clean_topic
    )

    # Polymorphically inspect reference source presence (Invariant 13)
    if isinstance(references, dict):
        obs_present = bool(references.get("obsidian"))
        pdf_present = bool(references.get("pdf"))
        zim_present = bool(references.get("zim"))
    elif isinstance(references, list):
        obs_present = any(
            isinstance(x, dict) and str(x.get("source") or x.get("source_type")).lower() == "obsidian"
            for x in references
        )
        pdf_present = any(
            isinstance(x, dict) and str(x.get("source") or x.get("source_type")).lower() == "pdf"
            for x in references
        )
        zim_present = any(
            isinstance(x, dict) and str(x.get("source") or x.get("source_type")).lower() == "zim"
            for x in references
        )
    else:
        obs_present = False
        pdf_present = False
        zim_present = False

    return {
        "status": "success",
        "topic": display_title,
        "display_title": display_title,
        "topic_key": hyphen_key,
        "llm_enabled": llm_enabled,
        "enhanced_explanation": (row["enhanced_explanation"] or "")
        if (row and llm_enabled)
        else "",
        "distractor_analysis": (row["distractor_analysis"] or "")
        if (row and llm_enabled)
        else "",
        "unified_article": (row["unified_article"] or "")
        if (row and llm_enabled)
        else "",
        "variance_audit": (row["variance_audit"] or "")
        if (row and llm_enabled and "variance_audit" in row.keys())
        else "",
        "references": references,
        "top_references": references,
        "has_obsidian_note": bool(row["has_obsidian_note"])
        if row
        else obs_present,
        "has_pdf_match": bool(row["has_pdf_match"]) if row else pdf_present,
        "has_zim_match": bool(row["has_zim_match"]) if row else zim_present,
    }


class BatchTopicRequest(BaseModel):
    topics: List[str]
    mode: str = "missing"  # "missing" or "all"


class TopicStatusRequest(BaseModel):
    topics: List[str] = []


@router.post("/topics/status")
@router.post("/topics_status")
def get_topics_status(payload: TopicStatusRequest) -> Dict[str, Any]:
    """
    Returns reference and LLM note availability statuses for a list of topics (Invariant 13).
    Includes both the `topics` status map and the `existing_topics` / `referenced_topics` / `ignored_topics`
    lists so all callers across `/api/mcq/topics_status` and `/api/csv/topics_status` succeed.
    """
    clean_topics = [
        str(t).strip() for t in (payload.topics or []) if t and str(t).strip()
    ]
    if not clean_topics:
        return {
            "status": "success",
            "topics": {},
            "existing_topics": [],
            "referenced_topics": [],
            "ignored_topics": [],
        }

    ignored_set = get_ignored_topics_set()
    ignored_matches: List[str] = []
    existing_notes: List[str] = []
    referenced_topics: List[str] = []
    statuses = {
        t: {"has_refs": False, "has_note": False, "ignored": False}
        for t in clean_topics
    }

    key_to_topics: Dict[str, List[str]] = {}
    for t in clean_topics:
        norm_k = normalize_topic_key(t)
        hyph_k = _to_synthesis_key(t)
        raw_k = t.lower()
        if norm_k in ignored_set:
            ignored_matches.append(t)
            statuses[t]["ignored"] = True
        for k in (hyph_k, norm_k, raw_k):
            if k:
                key_to_topics.setdefault(k, []).append(t)

    all_keys = list(key_to_topics.keys())
    if all_keys:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            chunk_size = 150
            for i in range(0, len(all_keys), chunk_size):
                chunk = all_keys[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"""
                    SELECT topic_key, unified_article, top_references
                    FROM cached_syntheses
                    WHERE topic_key IN ({placeholders})
                    """,
                    chunk,
                )
                for row in cursor.fetchall():
                    t_key = row["topic_key"]
                    orig_list = key_to_topics.get(t_key, [])
                    has_refs = _has_references(row["top_references"])
                    has_note = _has_synthesized_note(row["unified_article"])

                    for orig_topic in orig_list:
                        if has_refs:
                            statuses[orig_topic]["has_refs"] = True
                            referenced_topics.append(orig_topic)
                        if has_note:
                            statuses[orig_topic]["has_note"] = True
                            existing_notes.append(orig_topic)

    return {
        "status": "success",
        "topics": statuses,
        "existing_topics": list(dict.fromkeys(existing_notes)),
        "referenced_topics": list(dict.fromkeys(referenced_topics)),
        "ignored_topics": list(dict.fromkeys(ignored_matches)),
    }


@router.post("/batch_generate_references")
@router.post("/topics/generate_references")
def batch_generate_references(payload: BatchTopicRequest) -> Dict[str, Any]:
    """
    Resolves and caches reference links across Tantivy and LibZim for a list of topics.
    Decouples hybrid search execution from SQLite write transactions to prevent DB locks.
    """
    processed: List[str] = []
    skipped: List[str] = []
    clean_topics = list(
        dict.fromkeys(
            str(t).strip() for t in (payload.topics or []) if t and str(t).strip()
        )
    )
    if not clean_topics:
        return {"status": "success", "processed": [], "skipped": [], "count": 0}

    ignored_set = get_ignored_topics_set()
    topics_to_search: List[str] = []

    # Phase 1: Fast read check for already-cached references
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        for topic in clean_topics:
            if normalize_topic_key(topic) in ignored_set:
                skipped.append(topic)
                continue
            if payload.mode == "missing":
                h_key = _to_synthesis_key(topic)
                s_key = normalize_topic_key(topic)
                cursor.execute(
                    "SELECT top_references FROM cached_syntheses WHERE topic_key IN (?, ?)",
                    (h_key, s_key),
                )
                rows = cursor.fetchall()
                if any(_has_references(r["top_references"]) for r in rows):
                    skipped.append(topic)
                    continue
            topics_to_search.append(topic)

    # Phase 2: Execute searches outside SQLite locks
    resolved_rows: List[tuple] = []
    for topic in topics_to_search:
        try:
            refs = search_index(topic, limit_per_source=3)
            if not isinstance(refs, dict):
                refs = {"obsidian": [], "pdf": [], "zim": []}

            if not refs.get("zim"):
                try:
                    zim_hits = search_zim_articles(topic, limit=3)
                    if zim_hits:
                        refs["zim"] = zim_hits
                except Exception:
                    pass

            h_key = _to_synthesis_key(topic)
            has_obs = 1 if refs.get("obsidian") else 0
            has_pdf = 1 if refs.get("pdf") else 0
            has_zim = 1 if refs.get("zim") else 0

            resolved_rows.append(
                (h_key, topic, json.dumps(refs), has_obs, has_pdf, has_zim)
            )
            processed.append(topic)
        except Exception as exc:
            logger.error("Error generating references for '%s': %s", topic, exc)

    # Phase 3: Batch write into SQLite
    if resolved_rows:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO cached_syntheses (
                    topic_key, display_title, enhanced_explanation, distractor_analysis,
                    unified_article, variance_audit, top_references,
                    has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                ) VALUES (?, ?, '', '', '', '', ?, ?, ?, ?, unixepoch('now'))
                ON CONFLICT(topic_key) DO UPDATE SET
                    top_references = excluded.top_references,
                    has_obsidian_note = excluded.has_obsidian_note,
                    has_pdf_match = excluded.has_pdf_match,
                    has_zim_match = excluded.has_zim_match,
                    updated_at = unixepoch('now')
                """,
                resolved_rows,
            )
            conn.commit()

    return {
        "status": "success",
        "processed": processed,
        "skipped": skipped,
        "count": len(processed),
    }


@router.post("/batch_generate_explanations")
@router.post("/topics/generate_explanations")
def batch_generate_explanations(payload: BatchTopicRequest) -> Dict[str, Any]:
    """
    Enqueues synthesis tasks in task_queue for LM Studio LLM note/explanation generation.
    Enforces Invariant 14 deduplication via idx_task_dedup (task_type = 'SYNTHESIZE_TOPIC').
    """
    queued: List[str] = []
    skipped: List[str] = []
    clean_topics = list(
        dict.fromkeys(
            str(t).strip() for t in (payload.topics or []) if t and str(t).strip()
        )
    )
    if not clean_topics:
        return {"status": "success", "queued": [], "skipped": [], "count": 0}

    ignored_set = get_ignored_topics_set()

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        for topic in clean_topics:
            if normalize_topic_key(topic) in ignored_set:
                skipped.append(topic)
                continue

            h_key = _to_synthesis_key(topic)
            s_key = normalize_topic_key(topic)

            if payload.mode == "missing":
                cursor.execute(
                    "SELECT unified_article FROM cached_syntheses WHERE topic_key IN (?, ?)",
                    (h_key, s_key),
                )
                rows = cursor.fetchall()
                if any(_has_synthesized_note(r["unified_article"]) for r in rows):
                    skipped.append(topic)
                    continue

            # Invariant 14: Deduplicate active SYNTHESIZE_TOPIC tasks using idx_task_dedup
            cursor.execute(
                """
                SELECT 1 FROM task_queue
                WHERE task_type = 'SYNTHESIZE_TOPIC'
                  AND topic_name = ?
                  AND status IN ('pending', 'processing')
                LIMIT 1
                """,
                (topic,),
            )
            if cursor.fetchone():
                skipped.append(topic)
                continue

            cursor.execute(
                """
                INSERT INTO task_queue (
                    task_type, priority, topic_name, payload, status, created_at, updated_at
                )
                VALUES ('SYNTHESIZE_TOPIC', 2, ?, ?, 'pending', unixepoch('now'), unixepoch('now'))
                """,
                (topic, json.dumps({"topic": topic, "topic_name": topic})),
            )
            queued.append(topic)
        conn.commit()

    return {
        "status": "success",
        "queued": queued,
        "skipped": skipped,
        "count": len(queued),
    }