from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
import json
from core.database import get_db_connection

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

@router.post("/prepare")
def prepare_questions(req: PrepareRequest):
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 2. Bulk fetch existing questions to eliminate N+1 query loops
        question_texts = [q.question for q in req.questions if q.question.strip()]
        existing_mcqs = {}

        if question_texts:
            # SQLite supports a maximum of 999 variables by default; batching is safe for typical payload sizes
            placeholders = ",".join("?" * len(question_texts))
            cursor.execute(f"SELECT id, question FROM mcq_bank WHERE question IN ({placeholders})", question_texts)
            existing_mcqs = {row["question"]: row["id"] for row in cursor.fetchall()}

        mcq_ids_for_tasks = []

        for q in req.questions:
            if not q.question.strip():
                continue

            mcq_id = existing_mcqs.get(q.question)

            if not mcq_id:
                corr = q.correct_answer.strip().upper()
                if corr not in ('A', 'B', 'C', 'D'):
                    corr = 'A' # Fallback to avoid constraint error
                cursor.execute("""
                    INSERT INTO mcq_bank (source_file, source_tag, question, opt_a, opt_b, opt_c, opt_d, correct_opt, csv_explanation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "remote_csv", q.topic_name, q.question,
                    q.option_a, q.option_b, q.option_c, q.option_d,
                    corr, q.explanation
                ))
                mcq_id = cursor.lastrowid

            mcq_ids_for_tasks.append((mcq_id, q.topic_name))

        # 3. Bulk fetch existing tasks and batch insert the new ones
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

@router.get("/status")
def get_queue_status():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.task_id, t.status, m.question, t.updated_at
            FROM task_queue t
            LEFT JOIN mcq_bank m ON t.mcq_id = m.id
            ORDER BY t.created_at DESC
            LIMIT 50
        """)

        # 4. Use dictionary comprehension formatting to stream rows directly to memory
        return [
            {
                "id": r["task_id"],
                "status": r["status"],
                "question": r["question"],
                "updated_at": r["updated_at"]
            }
            for r in cursor.fetchall()
        ]
