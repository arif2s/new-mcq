from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
from collections import defaultdict
import json
from core.database import get_db_connection

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

class AnswerRecord(BaseModel):
    questionId: str
    selected: str
    isCorrect: bool
    timedOut: bool
    timeSpentMs: int
    timestamp: float

class SessionCreate(BaseModel):
    id: str
    subjectName: str
    date: str
    timestamp: float
    totalQuestions: int
    correctAnswers: int
    wrongAnswers: int
    unanswered: int
    score: int
    maxScore: int
    accuracy: float
    timeLimitSeconds: Optional[int]
    timeUsedSeconds: int
    mode: str
    questions: List[Dict]
    answers: List[AnswerRecord]

@router.post("/save")
def save_session(session: SessionCreate):
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Save session metadata
        cursor.execute("""
            INSERT OR REPLACE INTO quiz_sessions (
                id, subject_name, date, timestamp, total_questions, correct_answers,
                wrong_answers, unanswered, score, max_score, accuracy, time_limit_seconds,
                time_used_seconds, mode, is_completed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            session.id, session.subjectName, session.date, session.timestamp,
            session.totalQuestions, session.correctAnswers, session.wrongAnswers,
            session.unanswered, session.score, session.maxScore, session.accuracy,
            session.timeLimitSeconds, session.timeUsedSeconds, session.mode
        ))

        # Batch prepare individual questions and their answers
        q_dict = {q["id"]: q for q in session.questions}
        question_params = []

        for ans in session.answers:
            q = q_dict.get(ans.questionId)
            if not q:
                continue

            question_params.append((
                session.id, ans.questionId, ans.selected, 1 if ans.isCorrect else 0,
                1 if ans.timedOut else 0, ans.timeSpentMs, ans.timestamp,
                q.get("question"), q.get("option_a"), q.get("option_b"),
                q.get("option_c"), q.get("option_d"), q.get("correct_answer"),
                q.get("explanation"), q.get("topic_name")
            ))

        # Execute all question inserts in a single C-level transaction
        if question_params:
            cursor.executemany("""
                INSERT OR REPLACE INTO session_questions (
                    session_id, question_id, selected_option, is_correct, timed_out,
                    time_spent_ms, timestamp, question_text, opt_a, opt_b, opt_c, opt_d,
                    correct_answer, explanation, topic
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, question_params)

        # Add to queue for background processing
        cursor.execute("SELECT 1 FROM task_queue WHERE task_type = 'SESSION_PROCESSING' AND payload LIKE ?", (f'%"{session.id}"%',))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO task_queue (task_type, payload)
                VALUES (?, ?)
            """, ("SESSION_PROCESSING", json.dumps({"session_id": session.id})))

        conn.commit()
    return {"status": "success", "session_id": session.id}

@router.get("/history")
def get_session_history():
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # 1. Fetch all base sessions
        cursor.execute("""
            SELECT id, subject_name, date, timestamp, total_questions, correct_answers,
                   wrong_answers, unanswered, score, max_score, accuracy, time_limit_seconds,
                   time_used_seconds, mode
            FROM quiz_sessions
            ORDER BY timestamp DESC
        """)
        sessions = [dict(row) for row in cursor.fetchall()]

        if sessions:
            # 2. Fetch all unique topics across all sessions in a single query
            cursor.execute("""
                SELECT DISTINCT session_id, topic
                FROM session_questions
                WHERE topic IS NOT NULL AND topic != ''
            """)

            # 3. Map topics to their respective sessions in O(N) memory time
            topics_by_session = defaultdict(list)
            for row in cursor.fetchall():
                topics_by_session[row["session_id"]].append(row["topic"])

            for s in sessions:
                s["topics"] = topics_by_session.get(s["id"], [])

        return sessions

@router.get("/{session_id}/questions")
def get_session_questions(session_id: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT question_id, selected_option, is_correct, timed_out, time_spent_ms,
                   timestamp, question_text, opt_a, opt_b, opt_c, opt_d, correct_answer,
                   explanation, topic
            FROM session_questions
            WHERE session_id = ?
            ORDER BY timestamp ASC
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

@router.get("/{session_id}/topics")
def get_session_topics_and_notes(session_id: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT e.topic_name, c.display_title, c.enhanced_explanation, c.unified_article
            FROM session_questions sq
            JOIN extracted_topics e ON sq.question_id = e.question_id
            LEFT JOIN cached_syntheses c ON LOWER(REPLACE(e.topic_name, ' ', '-')) = c.topic_key
            WHERE sq.session_id = ?
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]
