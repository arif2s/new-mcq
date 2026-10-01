from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any
from core.database import get_db_connection
import random

router = APIRouter(prefix="/api/mcq", tags=["csv"])

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
    topics: List[str]
    count: int

@router.post("/generate_set")
def generate_set(req: GenerateSetRequest):
    questions = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(req.topics))
        query = f"SELECT * FROM csv_questions WHERE topic_name IN ({placeholders})"
        cursor.execute(query, req.topics)
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
