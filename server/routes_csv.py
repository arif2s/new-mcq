from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any
from core.database import get_db_connection
import random

router = APIRouter(prefix="/api/mcq", tags=["csv"])

from typing import Optional

class SubjectResponse(BaseModel):
    subject: str
    count: int

@router.get("/subjects", response_model=List[SubjectResponse])
def get_subjects():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Grouping by file_path to represent subjects
        cursor.execute("SELECT file_path, COUNT(*) as count FROM csv_questions GROUP BY file_path")
        rows = cursor.fetchall()
        return [{"subject": r["file_path"].replace(".csv", "").replace("data/csv/", "").replace("data/csv\\", ""), "count": r["count"]} for r in rows]

class TopicResponse(BaseModel):
    topic: str
    count: int

@router.get("/topics", response_model=List[TopicResponse])
def get_topics():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT topic_name, COUNT(*) as count FROM csv_questions GROUP BY topic_name")
        rows = cursor.fetchall()
        return [{"topic": r["topic_name"], "count": r["count"]} for r in rows]

class GenerateSetRequest(BaseModel):
    subjects: Optional[List[str]] = None
    topics: Optional[List[str]] = None
    count: int
    keyword: Optional[str] = None

@router.post("/search_count")
def search_count(req: GenerateSetRequest):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        query = "SELECT COUNT(*) as count FROM csv_questions q"
        params = []
        conditions = []

        if req.keyword and req.keyword.strip():
            query += " JOIN csv_questions_fts f ON q.id = f.id"
            conditions.append("f.csv_questions_fts MATCH ?")
            # fts5 MATCH syntax requires quotes for exact word match or phrase
            # For simpler prefix search we can append * to each term
            terms = [f'"{term}"*' for term in req.keyword.replace('"', '').split() if term]
            if terms:
                params.append(" OR ".join(terms))
            else:
                params.append(req.keyword.strip())

        if req.subjects and len(req.subjects) > 0 and "all" not in [s.lower() for s in req.subjects]:
            placeholders = ",".join("?" * len(req.subjects))
            # Match the cleaned subject names
            subject_conditions = []
            for s in req.subjects:
                subject_conditions.append("q.file_path LIKE ?")
                params.append(f"%{s}%")
            conditions.append(f"({' OR '.join(subject_conditions)})")

        if req.topics and len(req.topics) > 0 and "all" not in [t.lower() for t in req.topics]:
            placeholders = ",".join("?" * len(req.topics))
            conditions.append(f"q.topic_name IN ({placeholders})")
            params.extend(req.topics)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        cursor.execute(query, params)
        row = cursor.fetchone()
        return {"status": "success", "count": row["count"]}

@router.post("/generate_set")
def generate_set(req: GenerateSetRequest):
    questions = []
    with get_db_connection() as conn:
        cursor = conn.cursor()

        query = "SELECT q.* FROM csv_questions q"
        params = []
        conditions = []

        if req.keyword and req.keyword.strip():
            query += " JOIN csv_questions_fts f ON q.id = f.id"
            conditions.append("f.csv_questions_fts MATCH ?")
            terms = [f'"{term}"*' for term in req.keyword.replace('"', '').split() if term]
            if terms:
                params.append(" OR ".join(terms))
            else:
                params.append(req.keyword.strip())

        if req.subjects and len(req.subjects) > 0 and "all" not in [s.lower() for s in req.subjects]:
            subject_conditions = []
            for s in req.subjects:
                subject_conditions.append("q.file_path LIKE ?")
                params.append(f"%{s}%")
            conditions.append(f"({' OR '.join(subject_conditions)})")

        if req.topics and len(req.topics) > 0 and "all" not in [t.lower() for t in req.topics]:
            placeholders = ",".join("?" * len(req.topics))
            conditions.append(f"q.topic_name IN ({placeholders})")
            params.extend(req.topics)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        for row in rows:
            questions.append({
                "id": row["id"],
                "topic_name": row["topic_name"],
                "question": row["question"],
                "option_a": row["opt_a"],
                "option_b": row["opt_b"],
                "option_c": row["opt_c"],
                "option_d": row["opt_d"],
                "correct_answer": row["correct_answer"],
                "explanation": row["explanation"]
            })

    if req.count > 0 and len(questions) > req.count:
        questions = random.sample(questions, req.count)
    return {"status": "success", "questions": questions}
