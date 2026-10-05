from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
import json
from core.database import get_db_connection
from agents.tema_q.topic_extractor import extract_topics_local

router = APIRouter(prefix="/api/queue", tags=["queue"])

# 1. Implement a structured payload model to replace dynamic dict lookups
class QuestionPayload(BaseModel):
    question: str
    topic_name: str = "General"
    option_a: str = ""
    option_b: str = ""
    option_c: str = ""
    option_d: str = ""
    correct_answer: str = ""
    explanation: str = ""

class PrepareRequest(BaseModel):
    questions: List[QuestionPayload]
    use_zim_refinement: bool = False
    use_tantivy_refinement: bool = False

@router.post("/extract_topics")
def extract_topics(req: PrepareRequest):
    # This endpoint extracts topics without saving anything, used by the Topic Editor UI.
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
        row = cursor.fetchone()
        extractor_strategy = 'regex'
        if row:
            try:
                app_state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                extractor_strategy = app_state.get('extractorStrategy', 'regex')
            except Exception:
                pass

    results = []
    for q in req.questions:
        options = {
            "A": q.option_a,
            "B": q.option_b,
            "C": q.option_c,
            "D": q.option_d
        }
        extracted_data = extract_topics_local(q.question, options, q.explanation, extractor_strategy, req.use_zim_refinement, req.use_tantivy_refinement)
        all_topics = extracted_data.get("all_topics", [])
        main_topic = extracted_data.get("main_topic", "General Medical Concept")

        if main_topic and main_topic not in all_topics:
            all_topics.insert(0, main_topic)

        if not all_topics:
            all_topics = ["General Medical Concept"]

        results.append({
            "question": q.question,
            "option_a": q.option_a,
            "option_b": q.option_b,
            "option_c": q.option_c,
            "option_d": q.option_d,
            "correct_answer": q.correct_answer,
            "explanation": q.explanation,
            "topics": all_topics
        })

    return {"status": "success", "questions": results}

class PrepareTopicsRequest(BaseModel):
    class PreparedQuestion(BaseModel):
        question: str
        option_a: str = ""
        option_b: str = ""
        option_c: str = ""
        option_d: str = ""
        correct_answer: str = ""
        explanation: str = ""
        topics: List[str]

    questions: List[PreparedQuestion]

@router.post("/prepare")
def prepare_questions(req: PrepareTopicsRequest):
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Bulk fetch existing questions to eliminate N+1 query loops
        question_texts = [q.question for q in req.questions if q.question.strip()]
        existing_mcqs = {}

        if question_texts:
            # SQLite supports a maximum of 999 variables by default; batching is safe for typical payload sizes
            placeholders = ",".join("?" * len(question_texts))
            cursor.execute(f"SELECT id, question FROM mcq_bank WHERE question IN ({placeholders})", question_texts)
            existing_mcqs = {row["question"]: row["id"] for row in cursor.fetchall()}

        mcq_ids_for_tasks = []

        for q in req.questions:
            if not q.question.strip() or not q.topics:
                continue

            mcq_id = existing_mcqs.get(q.question)
            main_topic = q.topics[0] if q.topics else "General Medical Concept"

            if not mcq_id:
                corr = q.correct_answer.strip().upper()
                if corr not in ('A', 'B', 'C', 'D'):
                    corr = 'A' # Fallback to avoid constraint error
                cursor.execute("""
                    INSERT INTO mcq_bank (source_file, source_tag, question, opt_a, opt_b, opt_c, opt_d, correct_opt, csv_explanation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "remote_csv", main_topic, q.question,
                    q.option_a, q.option_b, q.option_c, q.option_d,
                    corr, q.explanation
                ))
                mcq_id = cursor.lastrowid

            for topic in q.topics:
                mcq_ids_for_tasks.append((mcq_id, topic))

        # Bulk fetch existing tasks and batch insert the new ones
        if mcq_ids_for_tasks:
            ids_only = [item[0] for item in mcq_ids_for_tasks]
            placeholders = ",".join("?" * len(ids_only))

            cursor.execute(f"""
                SELECT mcq_id FROM task_queue
                WHERE status IN ('PENDING', 'PROCESSING') AND mcq_id IN ({placeholders})
            """, ids_only)

            existing_tasks = {row["mcq_id"] for row in cursor.fetchall()}

            new_tasks = [
                ("synthesis", mcq_id, json.dumps({"topic": topic}))
                for mcq_id, topic in mcq_ids_for_tasks
                if mcq_id not in existing_tasks
            ]

            if new_tasks:
                cursor.executemany("""
                    INSERT INTO task_queue (task_type, mcq_id, payload)
                    VALUES (?, ?, ?)
                """, new_tasks)

        conn.commit()
    return {"status": "success"}

@router.post("/clear_failed")
def clear_failed_tasks():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM task_queue WHERE status = 'FAILED'")
        conn.commit()
    return {"status": "success"}

@router.post("/retry_failed")
def retry_failed_tasks():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE task_queue SET status = 'PENDING', error_message = NULL, updated_at = unixepoch('now') WHERE status = 'FAILED'")
        conn.commit()
    return {"status": "success"}

@router.get("/status")
def get_queue_status():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
        row = cursor.fetchone()
        limit = 100
        if row:
            try:
                import json
                app_state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                limit = int(app_state.get('processingQueueHardLimit', 100))
            except Exception:
                pass

        cursor.execute("SELECT COUNT(*) as total FROM task_queue")
        total_count = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) as completed FROM task_queue WHERE status = 'COMPLETED'")
        completed_count = cursor.fetchone()["completed"]

        cursor.execute(f"""
            SELECT t.task_id, t.status, m.question, t.updated_at
            FROM task_queue t
            LEFT JOIN mcq_bank m ON t.mcq_id = m.id
            ORDER BY t.created_at DESC
            LIMIT {limit}
        """)

        return {
            "total_tasks": total_count,
            "completed_tasks": completed_count,
            "tasks": [
                {
                    "id": r["task_id"],
                    "status": r["status"],
                    "question": r["question"],
                    "updated_at": r["updated_at"]
                }
                for r in cursor.fetchall()
            ]
        }
