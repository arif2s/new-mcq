from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional, Tuple
from pathlib import Path
import urllib.request
import urllib.error

from core.database import get_db_connection
from config import LM_STUDIO_URL
from server.routes_csv_loader import load_csvs_background

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
    count: int
    keyword: Optional[str] = None


# --- Helper Methods ---
def _build_mcq_query(req: GenerateSetRequest, is_count: bool = False) -> Tuple[str, list]:
    """DRY helper to build dynamic SQL queries for both generation and counting."""
    base_select = "SELECT COUNT(*) as count" if is_count else "SELECT q.*"
    query = f"{base_select} FROM csv_questions q"
    params = []
    conditions = []

    if req.keyword and req.keyword.strip():
        query += " JOIN csv_questions_fts f ON q.id = f.id"
        conditions.append("f.csv_questions_fts MATCH ?")
        # fts5 MATCH syntax requires quotes for exact word match or phrase
        terms = [f'"{term}"*' for term in req.keyword.replace('"', '').split() if term]
        if terms:
            params.append(" OR ".join(terms))
        else:
            params.append(req.keyword.strip())

    # Use generator expressions `(...)` instead of list comprehensions `[...]` to short-circuit the "all" check
    if req.subjects and "all" not in (s.lower() for s in req.subjects):
        subject_conditions = ["q.file_path LIKE ?" for _ in req.subjects]
        for s in req.subjects:
            params.append(f"%{s}%")
        conditions.append(f"({' OR '.join(subject_conditions)})")

    if req.topics and "all" not in (t.lower() for t in req.topics):
        placeholders = ",".join("?" * len(req.topics))
        conditions.append(f"q.topic_name IN ({placeholders})")
        params.extend(req.topics)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    return query, params


# --- Routes ---
@router.get("/subjects", response_model=List[SubjectResponse])
def get_subjects():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path, COUNT(*) as count FROM csv_questions GROUP BY file_path")

        # pathlib safely extracts the filename without extensions or folder paths, resolving Windows '\' vs POSIX '/' bugs
        return [
            {"subject": Path(r["file_path"]).stem, "count": r["count"]}
            for r in cursor.fetchall()
        ]

@router.get("/topics", response_model=List[TopicResponse])
def get_topics():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT topic_name, COUNT(*) as count FROM csv_questions GROUP BY topic_name")
        return [{"topic": r["topic_name"], "count": r["count"]} for r in cursor.fetchall()]

@router.post("/search_count")
def search_count(req: GenerateSetRequest):
    query, params = _build_mcq_query(req, is_count=True)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return {"status": "success", "count": cursor.fetchone()["count"]}

@router.post("/generate_set")
def generate_set(req: GenerateSetRequest):
    query, params = _build_mcq_query(req, is_count=False)

    # Offload randomization and limiting to the database engine instead of Python memory
    if req.count > 0:
        query += " ORDER BY RANDOM() LIMIT ?"
        params.append(req.count)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)

        # Dictionary comprehension is significantly faster than iterative appending
        questions = [
            {
                "id": row["id"],
                "topic_name": row["topic_name"],
                "question": row["question"],
                "option_a": row["opt_a"],
                "option_b": row["opt_b"],
                "option_c": row["opt_c"],
                "option_d": row["opt_d"],
                "correct_answer": row["correct_answer"],
                "explanation": row["explanation"]
            }
            for row in cursor.fetchall()
        ]

    return {"status": "success", "questions": questions}


@router.get("/db_status")
def get_db_status():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # O(1) existence check; avoids a full table scan before counting
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
    load_csvs_background()
    return {"status": "success", "message": "Database generation started."}

@router.get("/diagnostics/lm_studio")
def check_lm_studio():
    try:
        # Utilize rstrip to prevent double-slashing in URL construction
        url = LM_STUDIO_URL.replace("/v1", "/v1/models") if LM_STUDIO_URL.endswith("/v1") else LM_STUDIO_URL.rstrip("/") + "/models"

        # Explicitly define GET and reduce the timeout to prevent blocking thread starvation in FastAPI
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.5) as response:
            if response.status == 200:
                return {"status": "success", "message": "LM Studio is reachable."}
            return {"status": "error", "message": f"LM Studio returned status {response.status}."}
    except Exception as e:
        return {"status": "error", "message": str(e)}
