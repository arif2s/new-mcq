import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from contextlib import closing
from fastapi import APIRouter
from pydantic import BaseModel

from config import LM_STUDIO_URL
from core.database import get_db_connection
from core.scheduler import map_fsrs_state_to_status
from server.routes_csv_loader import load_csvs_background
from agents.tema_q.topic_extractor import normalize_topic_key, get_ignored_topics_set

router = APIRouter(prefix="/api/mcq", tags=["csv"])


# --- Models ---
class SubjectResponse(BaseModel):
    subject: str
    count: int


class TopicResponse(BaseModel):
    topic: str
    count: int


class GenerateSetRequest(BaseModel):
    subjects: Optional[List[str]] = None
    topics: Optional[List[str]] = None
    count: int = 10
    keyword: Optional[str] = None
    spaced_repetition: bool = True


class TopicsStatusRequest(BaseModel):
    topics: List[str]


# --- Query Builder Helper ---
def _build_mcq_query(req: GenerateSetRequest, is_count: bool = False) -> Tuple[str, list]:
    """Builds parameterized SQL queries for both count checks and question set curation."""
    base_select = "SELECT COUNT(*) as count" if is_count else "SELECT q.*"
    query = f"{base_select} FROM csv_questions q"
    params: list = []
    conditions: List[str] = []

    if req.keyword and req.keyword.strip():
        clean_kw = req.keyword.replace('"', "").strip()
        terms = [f'"{term}"*' for term in clean_kw.split() if term]
        if terms:
            query += " JOIN csv_questions_fts f ON q.rowid = f.rowid"
            conditions.append("f.csv_questions_fts MATCH ?")
            params.append(" AND ".join(terms))

    if req.subjects and "all" not in (s.lower() for s in req.subjects if s):
        subject_conditions = ["q.file_path LIKE ?" for _ in req.subjects]
        for s in req.subjects:
            params.append(f"%{s}%")
        conditions.append(f"({' OR '.join(subject_conditions)})")

    if req.topics and "all" not in (t.lower() for t in req.topics if t):
        placeholders = ",".join("?" * len(req.topics))
        conditions.append(f"q.topic_name IN ({placeholders})")
        params.extend(req.topics)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    return query, params


# --- Routes ---
@router.get("/subjects", response_model=List[SubjectResponse])
def get_subjects():
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path, COUNT(*) as count FROM csv_questions GROUP BY file_path")
        aggregated: Dict[str, int] = {}
        for r in cursor.fetchall():
            stem = Path(r["file_path"]).stem if r["file_path"] else "General"
            aggregated[stem] = aggregated.get(stem, 0) + int(r["count"])
        return [{"subject": k, "count": v} for k, v in sorted(aggregated.items())]


@router.get("/topics", response_model=List[TopicResponse])
def get_topics(subject: Optional[str] = None):
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        if subject and subject.lower() != "all":
            cursor.execute(
                "SELECT topic_name, COUNT(*) as count FROM csv_questions WHERE file_path LIKE ? GROUP BY topic_name ORDER BY topic_name ASC",
                (f"%{subject}%",),
            )
        else:
            cursor.execute(
                "SELECT topic_name, COUNT(*) as count FROM csv_questions GROUP BY topic_name ORDER BY topic_name ASC"
            )
        return [{"topic": r["topic_name"] or "General", "count": r["count"]} for r in cursor.fetchall()]


@router.post("/search_count")
def search_count(req: GenerateSetRequest):
    query, params = _build_mcq_query(req, is_count=True)
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            row = cursor.fetchone()
            return {"status": "success", "count": row["count"] if row else 0}
        except Exception:
            return {"status": "success", "count": 0}


@router.post("/generate_set")
def generate_set(req: GenerateSetRequest):
    query, params = _build_mcq_query(req, is_count=False)

    # Prioritize due and learning FSRS cards when spaced repetition is enabled
    if req.spaced_repetition:
        query += (
            " ORDER BY CASE "
            "WHEN q.due_timestamp > 0 AND q.due_timestamp <= unixepoch('now') THEN 0 "
            "WHEN q.state IN (1, 3) THEN 1 "
            "ELSE 2 END, RANDOM()"
        )
    else:
        query += " ORDER BY RANDOM()"

    if req.count > 0:
        query += " LIMIT ?"
        params.append(req.count)

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()

        questions = []
        for row in rows:
            file_stem = Path(row["file_path"]).stem if row["file_path"] else "General"
            state_val = int(row["state"] or 0)
            stability_val = float(row["stability"] or 0.0)
            reps_val = int(row["reps"] or 0)

            questions.append({
                "id": row["id"],
                "subject_name": file_stem,
                "topic_name": row["topic_name"] or file_stem,
                "question": row["question"],
                "option_a": row["opt_a"],
                "option_b": row["opt_b"],
                "option_c": row["opt_c"],
                "option_d": row["opt_d"],
                "correct_answer": row["correct_answer"],
                "explanation": row["explanation"] or "",
                "llm_explanation": row["llm_explanation"] or "",
                "state": state_val,
                "status": map_fsrs_state_to_status(state_val, stability_val, reps_val),
                "difficulty": float(row["difficulty"] or 0.0),
                "stability": stability_val,
                "reps": reps_val,
                "lapses": int(row["lapses"] or 0),
                "due_timestamp": float(row["due_timestamp"] or 0.0),
            })

    return {"status": "success", "questions": questions}


@router.get("/fsrs_summary")
def get_fsrs_summary():
    """Returns real-time FSRS mastery counts across the question bank for the Dashboard."""
    counts = {"new": 0, "learning": 0, "review": 0, "mastered": 0}
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT state, stability, reps FROM csv_questions")
        for row in cursor.fetchall():
            st = map_fsrs_state_to_status(
                int(row["state"] or 0),
                float(row["stability"] or 0.0),
                int(row["reps"] or 0),
            )
            counts[st] = counts.get(st, 0) + 1
    return {"status": "success", "counts": counts, "total": sum(counts.values())}


@router.get("/db_status")
def get_db_status():
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM csv_questions LIMIT 1")
            exists = cursor.fetchone() is not None

            count = 0
            if exists:
                cursor.execute("SELECT COUNT(*) as count FROM csv_questions")
                count = cursor.fetchone()["count"]

            return {"status": "success", "exists": exists, "count": count}
    except Exception:
        return {"status": "success", "exists": False, "count": 0}


@router.post("/build_db")
def build_db():
    stats = load_csvs_background()
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO task_queue (task_type, priority, payload) VALUES (?, ?, ?)",
            ("BATCH_PRECOMPUTE", 3, json.dumps({"action": "load_csvs"})),
        )
        conn.commit()
    return {
        "status": "success",
        "message": f"Loaded {stats['total_in_db']} questions across {stats['files_processed']} CSV files.",
        "stats": stats,
    }


@router.get("/diagnostics/lm_studio")
def check_lm_studio():
    try:
        url = (
            LM_STUDIO_URL.replace("/v1", "/v1/models")
            if LM_STUDIO_URL.endswith("/v1")
            else LM_STUDIO_URL.rstrip("/") + "/models"
        )
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.5) as response:
            if response.status == 200:
                return {"status": "success", "message": "LM Studio is reachable."}
            return {"status": "error", "message": f"LM Studio returned status {response.status}."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/topics_status")
def topics_status(req: TopicsStatusRequest):
    """
    Returns which topics already have synthesized LLM notes (existing_topics),
    which have reference links resolved (referenced_topics), and which are on the
    permanent ignore list (ignored_topics).
    """
    existing_notes = []
    referenced_topics = []
    ignored_matches = []
    ignored_set = get_ignored_topics_set()

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        if req.topics:
            topic_keys = [t.lower().strip().replace(" ", "-") for t in req.topics if t]
            if topic_keys:
                placeholders = ",".join("?" * len(topic_keys))
                cursor.execute(
                    f"SELECT topic_key, unified_article, top_references FROM cached_syntheses WHERE topic_key IN ({placeholders})",
                    topic_keys,
                )
                synth_map = {row["topic_key"]: row for row in cursor.fetchall()}

                for t in req.topics:
                    t_key = t.lower().strip().replace(" ", "-")
                    norm = normalize_topic_key(t)
                    if norm in ignored_set:
                        ignored_matches.append(t)
                    if t_key in synth_map:
                        row = synth_map[t_key]
                        if row["top_references"] and row["top_references"] != "{}":
                            referenced_topics.append(t)
                        if (
                            row["unified_article"]
                            and "LM Studio processing skipped" not in row["unified_article"]
                            and "No relevant documents passed" not in row["unified_article"]
                        ):
                            existing_notes.append(t)

    return {
        "status": "success",
        "existing_topics": list(dict.fromkeys(existing_notes)),
        "referenced_topics": list(dict.fromkeys(referenced_topics)),
        "ignored_topics": list(dict.fromkeys(ignored_matches)),
    }
