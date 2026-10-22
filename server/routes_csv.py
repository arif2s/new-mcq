"""
server/routes_csv.py - CSV Question Bank Management, Set Generation, and FSRS Curation.
Enforces zero VRAM usage, dual /api/csv & /api/mcq path contracts, non-blocking probes, and FTS5 safety.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import urllib.error
import urllib.request

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

try:
    from pydantic import ConfigDict

    _CSV_CONFIG = ConfigDict(extra="allow")
except ImportError:
    _CSV_CONFIG = None

from agents.tema_q.topic_extractor import (
    get_ignored_topics_set,
    normalize_topic_key,
)
from config import LM_STUDIO_URL
from core.database import get_db_connection
from core.scheduler import map_fsrs_state_to_status
from server.routes_csv_loader import load_csvs_background

logger = logging.getLogger(__name__)

router = APIRouter(tags=["csv"])


# ============================================================================
# 1. SCHEMAS & MODELS
# ============================================================================

class SubjectResponse(BaseModel):
    subject: str
    count: int


class TopicResponse(BaseModel):
    topic: str
    count: int


class GenerateSetRequest(BaseModel):
    if _CSV_CONFIG is not None:
        model_config = _CSV_CONFIG
    else:

        class Config:
            extra = "allow"

    subject: Optional[str] = None
    subjects: Optional[Union[List[str], str]] = None
    topics: Optional[Union[List[str], str]] = None
    count: int = 10
    mode: Optional[str] = "learn"
    keyword: Optional[str] = None
    query: Optional[str] = None
    spaced_repetition: bool = True

    def get_subjects_list(self) -> List[str]:
        """Resolves subjects across singular, plural list, and string formats."""
        res: List[str] = []
        if self.subjects:
            if isinstance(self.subjects, list):
                res.extend(
                    str(s).strip()
                    for s in self.subjects
                    if s and str(s).strip()
                )
            elif isinstance(self.subjects, str) and self.subjects.strip():
                res.append(self.subjects.strip())
        if self.subject and self.subject.strip():
            res.append(self.subject.strip())
        return [s for s in dict.fromkeys(res) if s.lower() != "all"]

    def get_topics_list(self) -> List[str]:
        """Resolves topics across list and string formats."""
        if not self.topics:
            return []
        if isinstance(self.topics, list):
            return [
                str(t).strip()
                for t in self.topics
                if t and str(t).strip() and str(t).strip().lower() != "all"
            ]
        clean_t = str(self.topics).strip()
        return [clean_t] if clean_t and clean_t.lower() != "all" else []

    def get_keyword(self) -> str:
        return (self.keyword or self.query or "").strip()


class TopicsStatusRequest(BaseModel):
    if _CSV_CONFIG is not None:
        model_config = _CSV_CONFIG
    else:

        class Config:
            extra = "allow"

    topics: List[str] = []


def _has_valid_references(raw: Any) -> bool:
    """Polymorphic reference validator satisfying Invariant 13."""
    if not raw:
        return False
    data = raw
    if isinstance(raw, str):
        raw_str = raw.strip()
        if not raw_str or raw_str in ("{}", "[]", "null"):
            return False
        try:
            data = json.loads(raw_str)
        except Exception:
            return False

    if isinstance(data, list):
        return len(data) > 0
    if isinstance(data, dict):
        if any(k in data for k in ("title", "file_path", "snippet")):
            return True
        return any(
            isinstance(v, (list, dict, set)) and len(v) > 0
            for v in data.values()
        )
    return False


def _has_valid_synthesis_article(raw_article: Any) -> bool:
    """Checks whether unified_article has actual synthesized clinical points or prose."""
    if not raw_article:
        return False
    art_str = str(raw_article).strip()
    if (
        len(art_str) <= 20
        or "LM Studio processing skipped" in art_str
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
        return len(art_str) > 50
    return False


# ============================================================================
# 2. QUERY BUILDER HELPER
# ============================================================================

def _build_mcq_query(
    req: GenerateSetRequest, is_count: bool = False
) -> Tuple[str, list]:
    """Builds parameterized SQL queries for count checks and question set curation."""
    base_select = "SELECT COUNT(*) as count" if is_count else "SELECT q.*"
    query = f"{base_select} FROM csv_questions q"
    params: list = []
    conditions: List[str] = []

    kw = req.get_keyword()
    if kw:
        # Sanitize FTS5 special control characters and hyphens
        clean_kw = re.sub(r"[^\w\s]", " ", kw).strip()
        tokens = [
            t.strip()
            for t in clean_kw.split()
            if t.strip() and t.upper() not in {"AND", "OR", "NOT", "NEAR"}
        ]
        if tokens:
            fts_terms = [f'"{token}"*' for token in tokens]
            query += " JOIN csv_questions_fts f ON q.rowid = f.rowid"
            conditions.append("f.csv_questions_fts MATCH ?")
            params.append(" AND ".join(fts_terms))

    subjects = req.get_subjects_list()
    if subjects:
        subject_conditions = ["q.file_path LIKE ?" for _ in subjects]
        for s in subjects:
            params.append(f"%{s}%")
        conditions.append(f"({' OR '.join(subject_conditions)})")

    topics = req.get_topics_list()
    if topics:
        placeholders = ",".join("?" * len(topics))
        conditions.append(f"q.topic_name IN ({placeholders})")
        params.extend(topics)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    return query, params


# ============================================================================
# 3. ROUTE ENDPOINTS (DUAL PATH BINDINGS)
# ============================================================================

@router.get("/api/csv/subjects", response_model=List[SubjectResponse])
@router.post("/api/csv/subjects", response_model=List[SubjectResponse])
@router.get("/api/mcq/subjects", response_model=List[SubjectResponse])
@router.post("/api/mcq/subjects", response_model=List[SubjectResponse])
def get_subjects() -> List[Dict[str, Any]]:
    """Returns question bank subject counts aggregated by file stem."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT file_path, COUNT(*) as count FROM csv_questions GROUP BY file_path"
        )
        aggregated: Dict[str, int] = {}
        for r in cursor.fetchall():
            stem = Path(r["file_path"]).stem if r["file_path"] else "General"
            aggregated[stem] = aggregated.get(stem, 0) + int(r["count"] or 0)
        return [
            {"subject": k, "count": v} for k, v in sorted(aggregated.items())
        ]


@router.get("/api/csv/topics", response_model=List[TopicResponse])
@router.post("/api/csv/topics", response_model=List[TopicResponse])
@router.get("/api/mcq/topics", response_model=List[TopicResponse])
@router.post("/api/mcq/topics", response_model=List[TopicResponse])
def get_topics(subject: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns question counts per clinical topic with optional subject filtering."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        if subject and subject.strip().lower() != "all":
            clean_sub = subject.strip()
            cursor.execute(
                """
                SELECT topic_name, COUNT(*) as count
                FROM csv_questions
                WHERE file_path LIKE ?
                GROUP BY topic_name
                ORDER BY topic_name ASC
                """,
                (f"%{clean_sub}%",),
            )
        else:
            cursor.execute(
                """
                SELECT topic_name, COUNT(*) as count
                FROM csv_questions
                GROUP BY topic_name
                ORDER BY topic_name ASC
                """
            )
        return [
            {"topic": r["topic_name"] or "General", "count": int(r["count"] or 0)}
            for r in cursor.fetchall()
        ]


@router.post("/api/csv/search_count")
@router.post("/api/mcq/search_count")
def search_count(req: GenerateSetRequest) -> Dict[str, Any]:
    """Returns the total number of matching questions for a given filter set."""
    query, params = _build_mcq_query(req, is_count=True)
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            row = cursor.fetchone()
            count_val = (
                int(row["count"])
                if row and "count" in row.keys()
                else (int(row[0]) if row else 0)
            )
            return {"status": "success", "count": count_val}
        except Exception as exc:
            logger.debug("search_count query error: %s", exc)
            return {"status": "success", "count": 0}


@router.post("/api/csv/generate_set")
@router.post("/api/mcq/generate_set")
def generate_set(req: GenerateSetRequest) -> Dict[str, Any]:
    """
    Curates a question set prioritizing overdue reviews and learning-state cards
    when spaced repetition is enabled.
    """
    query, params = _build_mcq_query(req, is_count=False)

    if req.spaced_repetition:
        query += (
            " ORDER BY CASE "
            "WHEN q.due_timestamp > 0 AND q.due_timestamp <= unixepoch('now') THEN 0 "
            "WHEN q.state IN (1, 3) THEN 1 "
            "ELSE 2 END, RANDOM()"
        )
    else:
        query += " ORDER BY RANDOM()"

    target_count = max(1, int(req.count or 10))
    query += " LIMIT ?"
    params.append(target_count)

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

        questions = []
        for row in rows:
            file_stem = (
                Path(row["file_path"]).stem if row["file_path"] else "General"
            )
            state_val = int(row["state"] or 0)
            stability_val = float(row["stability"] or 0.0)
            reps_val = int(row["reps"] or 0)
            topic_str = row["topic_name"] or file_stem

            questions.append({
                "id": str(row["id"]),
                "question_id": str(row["id"]),
                "subject_name": file_stem,
                "csv_topic_name": topic_str,
                "topic_name": topic_str,
                "topic": topic_str,
                "question": row["question"] or "",
                "question_text": row["question"] or "",
                "option_a": row["opt_a"] or "",
                "option_b": row["opt_b"] or "",
                "option_c": row["opt_c"] or "",
                "option_d": row["opt_d"] or "",
                "opt_a": row["opt_a"] or "",
                "opt_b": row["opt_b"] or "",
                "opt_c": row["opt_c"] or "",
                "opt_d": row["opt_d"] or "",
                "correct_answer": (row["correct_answer"] or "A").strip().upper(),
                "explanation": row["explanation"] or "",
                "llm_explanation": row["llm_explanation"] or "",
                "state": state_val,
                "status": map_fsrs_state_to_status(
                    state_val, stability_val, reps_val
                ),
                "difficulty": float(row["difficulty"] or 0.0),
                "stability": stability_val,
                "reps": reps_val,
                "lapses": int(row["lapses"] or 0),
                "due_timestamp": float(row["due_timestamp"] or 0.0),
            })

    return {"status": "success", "questions": questions, "count": len(questions)}


@router.get("/api/csv/fsrs_summary")
@router.post("/api/csv/fsrs_summary")
@router.get("/api/mcq/fsrs_summary")
@router.post("/api/mcq/fsrs_summary")
def get_fsrs_summary() -> Dict[str, Any]:
    """
    Returns real-time FSRS mastery distribution across the entire question bank.
    Computed directly in SQLite for <2ms latency without loading all rows into RAM.
    """
    counts = {"new": 0, "learning": 0, "review": 0, "mastered": 0}
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                SUM(CASE WHEN COALESCE(state, 0) = 0 OR COALESCE(reps, 0) <= 0 THEN 1 ELSE 0 END) AS new_cnt,
                SUM(CASE WHEN COALESCE(state, 0) IN (1, 3) AND COALESCE(reps, 0) > 0 THEN 1 ELSE 0 END) AS learning_cnt,
                SUM(CASE WHEN COALESCE(state, 0) NOT IN (0, 1, 3) AND COALESCE(reps, 0) >= 2 AND COALESCE(stability, 0.0) >= 21.0 THEN 1 ELSE 0 END) AS mastered_cnt,
                SUM(CASE WHEN COALESCE(state, 0) NOT IN (0, 1, 3) AND COALESCE(reps, 0) > 0 AND NOT (COALESCE(reps, 0) >= 2 AND COALESCE(stability, 0.0) >= 21.0) THEN 1 ELSE 0 END) AS review_cnt
            FROM csv_questions
            """
        )
        row = cursor.fetchone()
        if row:
            counts["new"] = int(row["new_cnt"] or 0)
            counts["learning"] = int(row["learning_cnt"] or 0)
            counts["review"] = int(row["review_cnt"] or 0)
            counts["mastered"] = int(row["mastered_cnt"] or 0)

    return {
        "status": "success",
        "counts": counts,
        "total": sum(counts.values()),
    }


@router.get("/api/csv/db_status")
@router.get("/api/mcq/db_status")
def get_db_status() -> Dict[str, Any]:
    """Returns whether csv_questions has been populated with question records."""
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM csv_questions")
            row = cursor.fetchone()
            count = (
                int(row["count"])
                if row and "count" in row.keys()
                else (int(row[0]) if row else 0)
            )
            return {"status": "success", "exists": count > 0, "count": count}
    except Exception as exc:
        logger.debug("get_db_status error: %s", exc)
        return {"status": "success", "exists": False, "count": 0}


@router.post("/api/csv/build_db")
@router.post("/api/mcq/build_db")
async def build_db(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Triggers background CSV scanning via worker thread and returns ingestion stats."""
    stats = await asyncio.to_thread(load_csvs_background)
    if stats.get("status") == "busy":
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM task_queue
                WHERE task_type = 'BATCH_PRECOMPUTE'
                  AND LOWER(status) IN ('pending', 'processing')
                LIMIT 1
                """
            )
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO task_queue (task_type, priority, topic_name, payload, status)
                    VALUES ('BATCH_PRECOMPUTE', 3, 'Load CSV Question Bank', ?, 'pending')
                    """,
                    (json.dumps({"action": "load_csvs"}),),
                )
    return {
        "status": "success",
        "message": (
            f"Loaded {stats.get('total_in_db', 0)} questions across "
            f"{stats.get('files_processed', 0)} CSV files."
        ),
        "stats": stats,
    }


@router.get("/api/csv/diagnostics/lm_studio")
@router.get("/api/mcq/diagnostics/lm_studio")
async def check_lm_studio() -> Dict[str, Any]:
    """Non-blocking health probe for LM Studio local server (Legacy compatibility alias)."""
    base = LM_STUDIO_URL.rstrip("/")
    url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"

    def _probe_sync() -> bool:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "MedisearchAI-Diagnostic/1.0"},
        )
        with urllib.request.urlopen(req, timeout=2.5) as response:
            return response.status == 200

    try:
        is_ok = await asyncio.to_thread(_probe_sync)
        if is_ok:
            return {
                "status": "success",
                "online": True,
                "message": "LM Studio is reachable.",
            }
        return {
            "status": "error",
            "online": False,
            "message": "LM Studio returned non-200 status.",
        }
    except Exception as exc:
        return {"status": "error", "online": False, "message": str(exc)}


@router.post("/api/mcq/topics_status")
@router.post("/api/mcq/topics/status")
@router.post("/api/csv/topics_status")
def topics_status(req: TopicsStatusRequest) -> Dict[str, Any]:
    """
    Returns which topics already have synthesized LLM notes, reference links (Invariant 13),
    and which are on the permanent ignore list.
    """
    existing_notes: List[str] = []
    referenced_topics: List[str] = []
    ignored_matches: List[str] = []
    ignored_set = get_ignored_topics_set()

    if not req.topics:
        return {
            "status": "success",
            "existing_topics": [],
            "referenced_topics": [],
            "ignored_topics": [],
        }

    key_to_topic: Dict[str, List[str]] = {}
    for t in req.topics:
        if not t or not str(t).strip():
            continue
        clean_t = str(t).strip()
        norm_key = normalize_topic_key(clean_t)
        slug_key = re.sub(r"[\s_]+", "-", norm_key or clean_t.lower())
        raw_key = clean_t.lower()

        if norm_key in ignored_set:
            ignored_matches.append(clean_t)

        for k in (slug_key, norm_key, raw_key):
            if k:
                key_to_topic.setdefault(k, []).append(clean_t)

    all_keys = list(key_to_topic.keys())
    if all_keys:
        chunk_size = 150
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            for i in range(0, len(all_keys), chunk_size):
                chunk = all_keys[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT topic_key, unified_article, top_references "
                    f"FROM cached_syntheses WHERE topic_key IN ({placeholders})",
                    chunk,
                )
                for row in cursor.fetchall():
                    t_k = row["topic_key"]
                    targets = key_to_topic.get(t_k, [])
                    has_refs = _has_valid_references(row["top_references"])
                    has_art = _has_valid_synthesis_article(row["unified_article"])

                    for orig_topic in targets:
                        if has_refs:
                            referenced_topics.append(orig_topic)
                        if has_art:
                            existing_notes.append(orig_topic)

    return {
        "status": "success",
        "existing_topics": list(dict.fromkeys(existing_notes)),
        "referenced_topics": list(dict.fromkeys(referenced_topics)),
        "ignored_topics": list(dict.fromkeys(ignored_matches)),
    }