from fastapi import APIRouter
from pydantic import BaseModel
from typing import List
import json
from core.database import get_db_connection

router = APIRouter(prefix="/api/queue", tags=["queue"])

class PrepareRequest(BaseModel):
    questions: List[dict]

@router.post("/prepare")
def prepare_questions(req: PrepareRequest):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for q in req.questions:
            cursor.execute("SELECT id FROM mcq_bank WHERE question = ?", (q.get("question"),))
            row = cursor.fetchone()
            if row:
                mcq_id = row["id"]
            else:
                cursor.execute("""
                    INSERT INTO mcq_bank (source_file, source_tag, question, opt_a, opt_b, opt_c, opt_d, correct_opt, csv_explanation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    "remote_csv",
                    q.get("topic_name", "General"),
                    q.get("question"),
                    q.get("option_a", ""),
                    q.get("option_b", ""),
                    q.get("option_c", ""),
                    q.get("option_d", ""),
                    q.get("correct_answer"),
                    q.get("explanation", "")
                ))
                mcq_id = cursor.lastrowid
            cursor.execute("SELECT task_id FROM task_queue WHERE mcq_id = ? AND status IN ('PENDING', 'PROCESSING')", (mcq_id,))
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO task_queue (task_type, mcq_id, payload)
                    VALUES (?, ?, ?)
                """, ("synthesis", mcq_id, json.dumps({"topic": q.get("topic_name", "General")})))
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
        rows = cursor.fetchall()
        return [{"id": r["task_id"], "status": r["status"], "question": r["question"], "updated_at": r["updated_at"]} for r in rows]
