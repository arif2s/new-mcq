from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
from collections import defaultdict
import json
from contextlib import closing

from core.database import get_db_connection
from parsers.tantivy_engine import search_index
from agents.tema_q.topic_extractor import extract_topics_local

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
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        # Save session metadata
        cursor.execute("""
            INSERT OR REPLACE INTO quiz_sessions (
                id, subject_name, date, timestamp, total_questions, correct_answers,
                wrong_answers, unanswered, score, max_score, accuracy, time_limit_seconds,
                time_used_seconds, mode, is_completed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session.id, session.subjectName, session.date, session.timestamp,
            session.totalQuestions, session.correctAnswers, session.wrongAnswers,
            session.unanswered, session.score, session.maxScore, session.accuracy,
            session.timeLimitSeconds, session.timeUsedSeconds, session.mode,
            1 if len(session.answers) == session.totalQuestions and session.totalQuestions > 0 else 0
        ))

        # Batch prepare individual questions and their answers
        q_dict = {q["id"]: q for q in session.questions}
        ans_dict = {ans.questionId: ans for ans in session.answers}

        question_params = []

        for q_id, q in q_dict.items():
            ans = ans_dict.get(q_id)

            selected = ans.selected if ans else None
            is_correct = (1 if ans.isCorrect else 0) if ans else 0
            timed_out = (1 if ans.timedOut else 0) if ans else 0
            time_spent = ans.timeSpentMs if ans else 0
            ts = ans.timestamp if ans else session.timestamp

            question_params.append((
                session.id, q_id, selected, is_correct,
                timed_out, time_spent, ts,
                q.get("question"), q.get("option_a"), q.get("option_b"),
                q.get("option_c"), q.get("option_d"), q.get("correct_answer"),
                q.get("explanation"), q.get("topic_name")
            ))

        if question_params:
            cursor.executemany("""
                INSERT OR REPLACE INTO session_questions (
                    session_id, question_id, selected_option, is_correct, timed_out,
                    time_spent_ms, timestamp, question_text, opt_a, opt_b, opt_c, opt_d,
                    correct_answer, explanation, topic
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, question_params)

        # Fetch extractor strategy
        cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
        row = cursor.fetchone()
        extractor_strategy = 'regex'
        if row:
            try:
                app_state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                extractor_strategy = app_state.get('extractorStrategy', 'regex')
            except Exception:
                pass

        # Immediate Topic Derivation and Local Reference Fetching
        topics_to_process = set()
        for q in session.questions:
            options = {
                "A": q.get("option_a", ""),
                "B": q.get("option_b", ""),
                "C": q.get("option_c", ""),
                "D": q.get("option_d", "")
            }
            extracted_data = extract_topics_local(
                q.get("question", ""),
                options,
                q.get("explanation", ""),
                extractor_strategy
            )

            main_topic = extracted_data.get("main_topic", "General Medical Concept")
            all_topics = extracted_data.get("all_topics", [])

            if main_topic and main_topic not in all_topics:
                all_topics.append(main_topic)

            if not all_topics:
                all_topics = ["General Medical Concept"]
                main_topic = "General Medical Concept"

            # Update the session_questions topic to the dynamically extracted main topic
            cursor.execute("UPDATE session_questions SET topic = ? WHERE session_id = ? AND question_id = ?", (main_topic, session.id, q["id"]))

            for topic in all_topics:
                topics_to_process.add(topic)
                cursor.execute("""
                    INSERT OR IGNORE INTO extracted_topics (question_id, topic_name, is_main_topic)
                    VALUES (?, ?, ?)
                """, (q["id"], topic, 1 if topic == main_topic else 0))

        for topic in topics_to_process:
            topic_key = topic.lower().replace(' ', '-')

            cursor.execute("SELECT 1 FROM cached_syntheses WHERE topic_key = ?", (topic_key,))
            if not cursor.fetchone():
                search_results = search_index(topic, limit_per_source=3)

                # Properly parse the Tantivy dictionary response format
                has_obsidian = 1 if search_results.get('obsidian') else 0
                has_pdf = 1 if search_results.get('pdf') else 0
                has_zim = 1 if search_results.get('zim') else 0

                cursor.execute("""
                    INSERT OR IGNORE INTO cached_syntheses (
                        topic_key, display_title, top_references,
                        has_obsidian_note, has_pdf_match, has_zim_match
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    topic_key, topic, json.dumps(search_results),
                    has_obsidian, has_pdf, has_zim
                ))

                cursor.execute("SELECT 1 FROM task_queue WHERE task_type = 'synthesis' AND payload LIKE ?", (f'%"{topic}"%',))
                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO task_queue (task_type, payload)
                        VALUES (?, ?)
                    """, ("synthesis", json.dumps({"topic": topic})))

        cursor.execute("SELECT 1 FROM task_queue WHERE task_type = 'SESSION_PROCESSING' AND payload LIKE ?", (f'%"{session.id}"%',))
        if not cursor.fetchone():
            cursor.execute("""
                INSERT INTO task_queue (task_type, payload)
                VALUES (?, ?)
            """, ("SESSION_PROCESSING", json.dumps({"session_id": session.id})))

        conn.commit()
    return {"status": "success", "session_id": session.id}

class DeleteSessionsRequest(BaseModel):
    session_ids: List[str]

@router.post("/delete")
def delete_sessions(req: DeleteSessionsRequest):
    if not req.session_ids:
        return {"status": "success"}
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(req.session_ids))

        cursor.execute(f"DELETE FROM session_questions WHERE session_id IN ({placeholders})", req.session_ids)
        cursor.execute(f"DELETE FROM quiz_sessions WHERE id IN ({placeholders})", req.session_ids)

        conn.commit()
    return {"status": "success"}

@router.get("/history")
def get_session_history():
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, subject_name, date, timestamp, total_questions, correct_answers,
                   wrong_answers, unanswered, score, max_score, accuracy, time_limit_seconds,
                   time_used_seconds, mode
            FROM quiz_sessions
            ORDER BY timestamp DESC
        """)
        sessions = [dict(row) for row in cursor.fetchall()]

        if sessions:
            cursor.execute("""
                SELECT DISTINCT session_id, topic
                FROM session_questions
                WHERE topic IS NOT NULL AND topic != ''
            """)

            topics_by_session = defaultdict(list)
            for row in cursor.fetchall():
                topics_by_session[row["session_id"]].append(row["topic"])

            for s in sessions:
                s["topics"] = topics_by_session.get(s["id"], [])

        return sessions

@router.get("/{session_id}/questions")
def get_session_questions(session_id: str):
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT sq.question_id, sq.selected_option, sq.is_correct, sq.timed_out, sq.time_spent_ms,
                   sq.timestamp, sq.question_text, sq.opt_a, sq.opt_b, sq.opt_c, sq.opt_d, sq.correct_answer,
                   sq.explanation, sq.topic, cs.top_references as reference_links
            FROM session_questions sq
            LEFT JOIN cached_syntheses cs ON LOWER(REPLACE(sq.topic, ' ', '-')) = cs.topic_key
            WHERE sq.session_id = ?
            ORDER BY sq.timestamp ASC
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]

@router.get("/{session_id}/topics")
def get_session_topics_and_notes(session_id: str):
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT e.topic_name, c.display_title, c.enhanced_explanation, c.unified_article, c.top_references,
            CASE
                WHEN c.unified_article IS NOT NULL AND c.unified_article != '' THEN 'completed'
                WHEN c.top_references IS NOT NULL AND c.top_references != '' THEN 'pending'
                ELSE 'pending'
            END as llm_status
            FROM session_questions sq
            JOIN extracted_topics e ON sq.question_id = e.question_id
            LEFT JOIN cached_syntheses c ON LOWER(REPLACE(e.topic_name, ' ', '-')) = c.topic_key
            WHERE sq.session_id = ?
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]
