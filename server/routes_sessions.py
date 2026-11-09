from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.tema_q.topic_extractor import (
    extract_topics_local,
    get_ignored_topics_set,
    normalize_topic_key,
    record_topic_note_selections,
)
from core.database import get_db_connection
from core.queue_manager import (
    get_session_queue_info,
    is_llm_processing_enabled,
    queue_worker,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _to_synthesis_key(text: str) -> str:
    """Converts a topic name into the canonical hyphenated cached_syntheses.topic_key."""
    norm = normalize_topic_key(text)
    if not norm:
        norm = (text or "").strip().lower()
    return re.sub(r"[\s_]+", "-", norm)


def _has_references(refs: Any) -> bool:
    """Safely checks if references exist across strings, dicts, or lists (Invariant 10 & 13)."""
    if not refs:
        return False
    if isinstance(refs, str):
        raw = refs.strip()
        if not raw or raw in ("{}", "[]", "null"):
            return False
        try:
            parsed = json.loads(raw)
            return _has_references(parsed)
        except Exception:
            return False
    if isinstance(refs, dict):
        return any(isinstance(v, list) and len(v) > 0 for v in refs.values()) or any(
            bool(v) for k, v in refs.items() if k in ("title", "file_path", "snippet")
        )
    if isinstance(refs, (list, tuple)):
        return len(refs) > 0
    return False


def _safe_notify_session(
    session_id: str,
    refs_status: str = "none",
    llm_status: str = "none",
    topics_reviewed: bool = False,
) -> None:
    """Safely broadcasts a single session status update over WebSocket (Invariant 18)."""
    try:
        if hasattr(queue_worker, "_notify_session_status") and queue_worker.main_loop:
            queue_worker._notify_session_status(session_id)
            return
    except Exception as exc:
        logger.debug("queue_worker._notify_session_status skipped: %s", exc)

    try:
        from server.ws_manager import manager as ws_manager

        ws_manager.broadcast_session_update_sync(
            session_id=session_id,
            refs_status=refs_status,
            llm_status=llm_status,
            topics_reviewed=topics_reviewed,
        )
    except Exception:
        pass


class AnswerRecord(BaseModel):
    questionId: Optional[str] = None
    question_id: Optional[str] = None
    selected: Optional[str] = None
    selectedOption: Optional[str] = None
    selected_option: Optional[str] = None
    isCorrect: Optional[bool] = False
    is_correct: Optional[bool] = False
    timedOut: Optional[bool] = False
    timed_out: Optional[bool] = False
    timeSpentMs: Optional[int] = 0
    time_spent_ms: Optional[int] = 0
    timeSpentSeconds: Optional[float] = 0.0
    time_spent_seconds: Optional[float] = 0.0
    timestamp: Optional[float] = None


def _extract_answer_id(ans: Any) -> str:
    if hasattr(ans, "questionId") and ans.questionId:
        return str(ans.questionId)
    if hasattr(ans, "question_id") and ans.question_id:
        return str(ans.question_id)
    if isinstance(ans, dict):
        return str(ans.get("questionId") or ans.get("question_id") or "")
    return ""


def _extract_answer_selected(ans: Any) -> Optional[str]:
    if not ans:
        return None
    for attr in ("selected", "selectedOption", "selected_option"):
        val = getattr(ans, attr, None) if not isinstance(ans, dict) else ans.get(attr)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return None


def _extract_answer_is_correct(ans: Any) -> int:
    if not ans:
        return 0
    for attr in ("isCorrect", "is_correct"):
        val = getattr(ans, attr, None) if not isinstance(ans, dict) else ans.get(attr)
        if val is not None:
            return 1 if val else 0
    return 0


def _extract_answer_timed_out(ans: Any) -> int:
    if not ans:
        return 0
    for attr in ("timedOut", "timed_out"):
        val = getattr(ans, attr, None) if not isinstance(ans, dict) else ans.get(attr)
        if val is not None:
            return 1 if val else 0
    return 0


def _extract_answer_time_spent(ans: Any) -> int:
    if not ans:
        return 0
    for attr in ("timeSpentMs", "time_spent_ms"):
        val = getattr(ans, attr, None) if not isinstance(ans, dict) else ans.get(attr)
        if val is not None and val > 0:
            return int(val)
    for attr in ("timeSpentSeconds", "time_spent_seconds"):
        val = getattr(ans, attr, None) if not isinstance(ans, dict) else ans.get(attr)
        if val is not None and val > 0:
            sec = float(val)
            return int(sec * 1000) if sec < 1000 else int(sec)
    return 0


class SessionCreate(BaseModel):
    id: str
    subjectName: Optional[str] = None
    subject_name: Optional[str] = None
    customTitle: Optional[str] = None
    custom_title: Optional[str] = None
    date: Optional[str] = None
    timestamp: Optional[float] = None
    lastAttempted: Optional[float] = None
    last_attempted: Optional[float] = None
    totalQuestions: Optional[int] = None
    total_questions: Optional[int] = None
    correctAnswers: Optional[int] = None
    correct_answers: Optional[int] = None
    wrongAnswers: Optional[int] = None
    wrong_answers: Optional[int] = None
    unanswered: Optional[int] = None
    score: Optional[int] = None
    maxScore: Optional[int] = None
    max_score: Optional[int] = None
    accuracy: float = 0.0
    timeLimitSeconds: Optional[int] = None
    time_limit_seconds: Optional[int] = None
    timeUsedSeconds: Optional[int] = None
    time_used_seconds: Optional[int] = None
    mode: str = "learn"
    isCompleted: Optional[bool] = None
    is_completed: Optional[bool] = None
    topicsReviewed: Optional[bool] = None
    topics_reviewed: Optional[bool] = None
    topicList: Optional[List[Any]] = None
    topic_list: Optional[List[Any]] = None
    questions: List[Dict[str, Any]] = []
    answers: List[AnswerRecord] = []


class UpdateSessionTopicsRequest(BaseModel):
    topic_list: List[Dict[str, Any]]
    confirm: bool = False
    topics_reviewed: Optional[bool] = None


class SelectTopicRequest(BaseModel):
    question_id: str
    topic_name: str
    selected: int = 1


class DeleteSessionsRequest(BaseModel):
    session_ids: List[str]


def _normalize_topic_list(raw_list: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Normalizes incoming topic items into [{name, topic_name, verified, selected, confidence}]."""
    if not raw_list:
        return []
    ignored_keys = get_ignored_topics_set()
    normalized: List[Dict[str, Any]] = []
    seen = set()
    for item in raw_list:
        if isinstance(item, dict):
            name = str(
                item.get("name") or item.get("topic_name") or item.get("topic") or ""
            ).strip()
            if not name or name == "General Medical Concept":
                continue
            key = normalize_topic_key(name)
            if not key or key in seen or key in ignored_keys:
                continue
            seen.add(key)
            verified = bool(item.get("verified", True))
            selected = bool(item.get("selected", True))
            confidence = str(
                item.get("confidence") or ("high" if verified else "low")
            )
            normalized.append({
                "name": name,
                "topic_name": name,
                "verified": verified,
                "selected": selected,
                "confidence": confidence,
            })
        elif isinstance(item, str) and item.strip():
            name = item.strip()
            if name == "General Medical Concept":
                continue
            key = normalize_topic_key(name)
            if not key or key in seen or key in ignored_keys:
                continue
            seen.add(key)
            normalized.append({
                "name": name,
                "topic_name": name,
                "verified": True,
                "selected": True,
                "confidence": "high",
            })
    return normalized


def _enqueue_session_processing(conn: Any, session_id: str) -> None:
    """Enqueues PROCESS_SESSION (priority 2) using canonical task_queue schema (Section 4)."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 1 FROM task_queue
        WHERE task_type IN ('PROCESS_SESSION', 'SESSION_PROCESSING')
          AND session_id = ?
          AND LOWER(status) IN ('pending', 'processing')
        LIMIT 1
        """,
        (session_id,),
    )
    if not cursor.fetchone():
        cursor.execute(
            """
            INSERT INTO task_queue (
                task_type, priority, session_id, topic_name, payload, status, created_at, updated_at
            )
            VALUES ('PROCESS_SESSION', 2, ?, ?, ?, 'pending', unixepoch('now'), unixepoch('now'))
            """,
            (
                session_id,
                f"Session {session_id[-6:] if len(session_id) >= 6 else session_id}",
                json.dumps({"session_id": session_id}),
            ),
        )


@router.post("/save")
def save_session(session: SessionCreate) -> Dict[str, Any]:
    """
    Saves or updates a Knowledge Hub question set / quiz session.
    Enforces Invariant 15: Uses atomic ON CONFLICT(id) DO UPDATE SET (never INSERT OR REPLACE)
    to prevent cascade-deleting child session_questions rows.
    """
    sid = str(session.id).strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session id is required")

    now_ms = time.time() * 1000.0
    ts = session.timestamp or now_ms
    date_str = session.date or datetime.fromtimestamp(
        ts / 1000.0 if ts > 1e11 else ts
    ).strftime("%Y-%m-%d")

    raw_topic_list = (
        session.topic_list if session.topic_list is not None else session.topicList
    )
    normalized_topics = _normalize_topic_list(raw_topic_list)

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT subject_name, custom_title, total_questions, topics_reviewed,
                   refs_status, llm_status, topic_list_json
            FROM quiz_sessions
            WHERE id = ?
            """,
            (sid,),
        )
        existing = cursor.fetchone()

        subj = (
            session.subject_name
            or session.subjectName
            or (existing["subject_name"] if existing else None)
            or "Mixed Topics"
        )
        custom_title = (
            session.custom_title
            or session.customTitle
            or (existing["custom_title"] if existing and existing["custom_title"] else None)
            or subj
            or "Study Set"
        ).strip()

        total_q_input = (
            session.total_questions
            if session.total_questions is not None
            else session.totalQuestions
        )
        if total_q_input is not None and total_q_input > 0:
            total_q = int(total_q_input)
        elif session.questions:
            total_q = len(session.questions)
        elif existing and existing["total_questions"]:
            total_q = int(existing["total_questions"])
        else:
            total_q = 0

        corr_ans = int(
            session.correct_answers
            if session.correct_answers is not None
            else (session.correctAnswers or 0)
        )
        wrong_ans = int(
            session.wrong_answers
            if session.wrong_answers is not None
            else (session.wrongAnswers or 0)
        )

        answered_count = sum(
            1
            for a in session.answers
            if _extract_answer_selected(a) and not _extract_answer_timed_out(a)
        )
        unanswered = (
            int(session.unanswered)
            if session.unanswered is not None
            else max(0, total_q - answered_count)
        )
        score = int(session.score if session.score is not None else corr_ans)
        max_score = int(
            session.max_score
            if session.max_score is not None
            else (session.maxScore if session.maxScore is not None else total_q)
        )

        is_comp_val = (
            session.is_completed
            if session.is_completed is not None
            else session.isCompleted
        )
        is_completed = (
            int(bool(is_comp_val))
            if is_comp_val is not None
            else (1 if answered_count > 0 and answered_count >= max(1, total_q) else 0)
        )

        last_att_val = (
            session.last_attempted
            if session.last_attempted is not None
            else session.lastAttempted
        )
        last_attempted = float(
            last_att_val or (now_ms if answered_count > 0 else 0.0)
        )

        time_limit = int(
            session.time_limit_seconds
            if session.time_limit_seconds is not None
            else (session.timeLimitSeconds or 0)
        )
        time_used = int(
            session.time_used_seconds
            if session.time_used_seconds is not None
            else (session.timeUsedSeconds or 0)
        )

        topics_rev_input = (
            session.topics_reviewed
            if session.topics_reviewed is not None
            else session.topicsReviewed
        )
        topics_reviewed = (
            1
            if topics_rev_input
            else (
                int(existing["topics_reviewed"])
                if existing and existing["topics_reviewed"] is not None
                else 0
            )
        )

        if not normalized_topics and existing and existing["topic_list_json"]:
            try:
                normalized_topics = json.loads(existing["topic_list_json"])
            except Exception:
                normalized_topics = []

        if topics_reviewed:
            if existing and existing["refs_status"] in ("processing", "ready"):
                refs_status = existing["refs_status"]
                llm_status = existing["llm_status"]
            else:
                refs_status = "processing"
                llm_status = "processing" if is_llm_processing_enabled() else "none"

            selected_names = [
                t["name"] for t in normalized_topics
                if t.get("selected") and t.get("name")
            ]
            if selected_names:
                record_topic_note_selections(selected_names)
        else:
            refs_status = existing["refs_status"] if existing else "none"
            llm_status = existing["llm_status"] if existing else "none"

        # Invariant 15: Strictly ON CONFLICT(id) DO UPDATE SET (never INSERT OR REPLACE)
        cursor.execute(
            """
            INSERT INTO quiz_sessions (
                id, subject_name, custom_title, date, timestamp, last_attempted,
                total_questions, correct_answers, wrong_answers, unanswered,
                score, max_score, accuracy, time_limit_seconds, time_used_seconds,
                mode, is_completed, topics_reviewed, refs_status, llm_status, topic_list_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                subject_name = excluded.subject_name,
                custom_title = excluded.custom_title,
                date = excluded.date,
                timestamp = excluded.timestamp,
                last_attempted = CASE WHEN excluded.last_attempted > 0 THEN excluded.last_attempted ELSE quiz_sessions.last_attempted END,
                total_questions = CASE WHEN excluded.total_questions > 0 THEN excluded.total_questions ELSE quiz_sessions.total_questions END,
                correct_answers = excluded.correct_answers,
                wrong_answers = excluded.wrong_answers,
                unanswered = excluded.unanswered,
                score = excluded.score,
                max_score = excluded.max_score,
                accuracy = excluded.accuracy,
                time_limit_seconds = excluded.time_limit_seconds,
                time_used_seconds = excluded.time_used_seconds,
                mode = excluded.mode,
                is_completed = excluded.is_completed,
                topics_reviewed = excluded.topics_reviewed,
                refs_status = excluded.refs_status,
                llm_status = excluded.llm_status,
                topic_list_json = excluded.topic_list_json
            """,
            (
                sid,
                subj,
                custom_title,
                date_str,
                ts,
                last_attempted,
                total_q,
                corr_ans,
                wrong_ans,
                unanswered,
                score,
                max_score,
                session.accuracy,
                time_limit,
                time_used,
                session.mode,
                is_completed,
                topics_reviewed,
                refs_status,
                llm_status,
                json.dumps(normalized_topics),
            ),
        )

        cursor.execute(
            """
            SELECT question_id, llm_explanation, topic, question_topics_json,
                   question_text, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation
            FROM session_questions
            WHERE session_id = ?
            """,
            (sid,),
        )
        existing_q_map = {r["question_id"]: dict(r) for r in cursor.fetchall()}

        ans_dict = {}
        for ans in session.answers:
            qid = _extract_answer_id(ans)
            if qid:
                ans_dict[qid] = ans

        question_params = []
        if session.questions:
            for idx, q in enumerate(session.questions):
                q_id = str(q.get("id") or q.get("question_id") or f"q_{idx}")
                ans = ans_dict.get(q_id)
                prev_q = existing_q_map.get(q_id, {})

                selected = _extract_answer_selected(ans)
                is_correct = _extract_answer_is_correct(ans)
                timed_out = _extract_answer_timed_out(ans)
                time_spent = _extract_answer_time_spent(ans)

                q_ts = (getattr(ans, "timestamp", None) if ans else None) or ts
                q_text = (
                    q.get("question")
                    or q.get("question_text")
                    or prev_q.get("question_text")
                    or ""
                )
                opt_a = (
                    q.get("option_a")
                    or q.get("opt_a")
                    or prev_q.get("opt_a")
                    or ""
                )
                opt_b = (
                    q.get("option_b")
                    or q.get("opt_b")
                    or prev_q.get("opt_b")
                    or ""
                )
                opt_c = (
                    q.get("option_c")
                    or q.get("opt_c")
                    or prev_q.get("opt_c")
                    or ""
                )
                opt_d = (
                    q.get("option_d")
                    or q.get("opt_d")
                    or prev_q.get("opt_d")
                    or ""
                )
                correct_ans = (
                    q.get("correct_answer")
                    or q.get("correct_opt")
                    or prev_q.get("correct_answer")
                    or ""
                )
                explanation = (
                    q.get("explanation")
                    or q.get("csv_explanation")
                    or prev_q.get("explanation")
                    or ""
                )
                llm_exp = (
                    q.get("llm_explanation") or prev_q.get("llm_explanation") or ""
                )
                topic = (
                    q.get("topic")
                    or q.get("topic_name")
                    or prev_q.get("topic")
                    or "General"
                )

                q_topics = q.get("topics") or q.get("topic_details")
                if q_topics:
                    q_topics_json = json.dumps(q_topics)
                else:
                    q_topics_json = prev_q.get("question_topics_json") or "[]"

                question_params.append((
                    sid,
                    q_id,
                    selected,
                    is_correct,
                    timed_out,
                    time_spent,
                    q_ts,
                    q_text,
                    opt_a,
                    opt_b,
                    opt_c,
                    opt_d,
                    correct_ans,
                    explanation,
                    llm_exp,
                    topic,
                    q_topics_json,
                ))
        elif ans_dict and existing_q_map:
            # Support answer-only updates when session.questions is omitted
            for q_id, ans in ans_dict.items():
                if q_id not in existing_q_map:
                    continue
                prev_q = existing_q_map[q_id]
                question_params.append((
                    sid,
                    q_id,
                    _extract_answer_selected(ans),
                    _extract_answer_is_correct(ans),
                    _extract_answer_timed_out(ans),
                    _extract_answer_time_spent(ans),
                    getattr(ans, "timestamp", None) or ts,
                    prev_q.get("question_text") or "",
                    prev_q.get("opt_a") or "",
                    prev_q.get("opt_b") or "",
                    prev_q.get("opt_c") or "",
                    prev_q.get("opt_d") or "",
                    prev_q.get("correct_answer") or "",
                    prev_q.get("explanation") or "",
                    prev_q.get("llm_explanation") or "",
                    prev_q.get("topic") or "General",
                    prev_q.get("question_topics_json") or "[]",
                ))

        if question_params:
            cursor.executemany(
                """
                INSERT INTO session_questions (
                    session_id, question_id, selected_option, is_correct, timed_out,
                    time_spent_ms, timestamp, question_text, opt_a, opt_b, opt_c, opt_d,
                    correct_answer, explanation, llm_explanation, topic, question_topics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, question_id) DO UPDATE SET
                    selected_option = excluded.selected_option,
                    is_correct = excluded.is_correct,
                    timed_out = excluded.timed_out,
                    time_spent_ms = excluded.time_spent_ms,
                    timestamp = excluded.timestamp,
                    question_text = CASE WHEN excluded.question_text != '' THEN excluded.question_text ELSE session_questions.question_text END,
                    opt_a = CASE WHEN excluded.opt_a != '' THEN excluded.opt_a ELSE session_questions.opt_a END,
                    opt_b = CASE WHEN excluded.opt_b != '' THEN excluded.opt_b ELSE session_questions.opt_b END,
                    opt_c = CASE WHEN excluded.opt_c != '' THEN excluded.opt_c ELSE session_questions.opt_c END,
                    opt_d = CASE WHEN excluded.opt_d != '' THEN excluded.opt_d ELSE session_questions.opt_d END,
                    correct_answer = CASE WHEN excluded.correct_answer != '' THEN excluded.correct_answer ELSE session_questions.correct_answer END,
                    explanation = CASE WHEN excluded.explanation != '' THEN excluded.explanation ELSE session_questions.explanation END,
                    llm_explanation = CASE WHEN excluded.llm_explanation != '' THEN excluded.llm_explanation ELSE session_questions.llm_explanation END,
                    topic = CASE WHEN excluded.topic != '' AND excluded.topic != 'General' THEN excluded.topic ELSE session_questions.topic END,
                    question_topics_json = CASE WHEN excluded.question_topics_json != '[]' THEN excluded.question_topics_json ELSE session_questions.question_topics_json END
                """,
                question_params,
            )

        # Record attempts and outcomes in question_metadata, and update spaced repetition states
        from core.database import record_question_attempt, update_question_verified_topics
        from core.scheduler import calculate_fsrs_next_interval
        now_epoch = time.time()

        if session.answers:
            for ans in session.answers:
                qid = _extract_answer_id(ans)
                sel = _extract_answer_selected(ans)
                if not qid or not sel:
                    continue
                is_corr = _extract_answer_is_correct(ans)
                ans_ts = getattr(ans, "timestamp", None) or now_epoch
                record_question_attempt(
                    conn=conn,
                    question_id=qid,
                    is_correct=is_corr,
                    attempt_ts=ans_ts,
                )

                # Fetch current question card to apply precise FSRS spaced repetition rules
                cursor.execute(
                    "SELECT state, difficulty, stability, reps, lapses FROM csv_questions WHERE id = ?",
                    (str(qid),),
                )
                card_row = cursor.fetchone()
                if card_row:
                    rating = 3 if is_corr else 1
                    new_fsrs = calculate_fsrs_next_interval(
                        rating=rating,
                        state=int(card_row["state"] or 0),
                        difficulty=float(card_row["difficulty"] or 0.0),
                        stability=float(card_row["stability"] or 0.0),
                        reps=int(card_row["reps"] or 0),
                        lapses=int(card_row["lapses"] or 0),
                        now=now_epoch,
                    )
                    cursor.execute(
                        """
                        UPDATE csv_questions
                        SET state = ?, difficulty = ?, stability = ?, reps = ?, lapses = ?,
                            last_review = ?, due_timestamp = ?
                        WHERE id = ?
                        """,
                        (
                            int(new_fsrs["state"]),
                            float(new_fsrs["difficulty"]),
                            float(new_fsrs["stability"]),
                            int(new_fsrs["reps"]),
                            int(new_fsrs["lapses"]),
                            now_epoch,
                            float(new_fsrs["due_timestamp"]),
                            str(qid),
                        ),
                    )
                else:
                    cursor.execute(
                        "SELECT state, difficulty, stability, reps, lapses FROM mcq_bank WHERE id = ?",
                        (str(qid),),
                    )
                    mcq_row = cursor.fetchone()
                    if mcq_row:
                        rating = 3 if is_corr else 1
                        new_fsrs = calculate_fsrs_next_interval(
                            rating=rating,
                            state=int(mcq_row["state"] or 0),
                            difficulty=float(mcq_row["difficulty"] or 0.0),
                            stability=float(mcq_row["stability"] or 0.0),
                            reps=int(mcq_row["reps"] or 0),
                            lapses=int(mcq_row["lapses"] or 0),
                            now=now_epoch,
                        )
                        cursor.execute(
                            """
                            UPDATE mcq_bank
                            SET state = ?, difficulty = ?, stability = ?, reps = ?, lapses = ?,
                                last_review = ?, due_timestamp = ?
                            WHERE id = ?
                            """,
                            (
                                int(new_fsrs["state"]),
                                float(new_fsrs["difficulty"]),
                                float(new_fsrs["stability"]),
                                int(new_fsrs["reps"]),
                                int(new_fsrs["lapses"]),
                                now_epoch,
                                float(new_fsrs["due_timestamp"]),
                                int(qid) if str(qid).isdigit() else str(qid),
                            ),
                        )

        # Update verified topics in question_metadata for each question if available
        if normalized_topics and session.questions:
            verified_names = [
                t["name"] for t in normalized_topics if t.get("verified") and t.get("name")
            ]
            if verified_names:
                for q in session.questions:
                    qid = str(q.get("id") or q.get("question_id") or "")
                    if qid:
                        update_question_verified_topics(
                            conn=conn,
                            question_id=qid,
                            verified_topics=verified_names,
                        )

        if topics_reviewed and (not existing or existing["refs_status"] == "none"):
            _enqueue_session_processing(conn, sid)

        sync_consolidated_due_session(conn)
        conn.commit()

    _safe_notify_session(sid, refs_status, llm_status, bool(topics_reviewed))
    return {
        "status": "success",
        "session_id": sid,
        "topics_reviewed": bool(topics_reviewed),
        "refs_status": refs_status,
        "llm_status": llm_status,
    }


@router.get("/{session_id}/extract_draft_topics")
def get_or_extract_session_topics(session_id: str) -> Dict[str, Any]:
    """Returns saved draft/confirmed topic list or extracts topics from session_questions."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, subject_name, custom_title, topics_reviewed, topic_list_json FROM quiz_sessions WHERE id = ?",
            (session_id,),
        )
        s_row = cursor.fetchone()
        if not s_row:
            raise HTTPException(status_code=404, detail="Session not found")

        saved_list = []
        if s_row["topic_list_json"]:
            try:
                saved_list = json.loads(s_row["topic_list_json"])
            except Exception:
                saved_list = []

        cursor.execute(
            "SELECT question_id, question_text, opt_a, opt_b, opt_c, opt_d, explanation, topic FROM session_questions WHERE session_id = ?",
            (session_id,),
        )
        q_rows = cursor.fetchall()

    if not saved_list:
        strategy = "medspacy"
        try:
            with closing(get_db_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT value FROM user_state WHERE key = 'app_persistence'"
                )
                row = cursor.fetchone()
                if row:
                    app_state = json.loads(
                        row[0] if isinstance(row, tuple) else row["value"]
                    )
                    if isinstance(app_state, dict):
                        strategy = str(app_state.get("extractorStrategy") or "medspacy")
        except Exception:
            strategy = "medspacy"

        ignored_keys = get_ignored_topics_set()
        consolidated: Dict[str, Dict[str, Any]] = {}
        for q in q_rows:
            opts = {
                "A": q["opt_a"] or "",
                "B": q["opt_b"] or "",
                "C": q["opt_c"] or "",
                "D": q["opt_d"] or "",
            }
            try:
                ext = extract_topics_local(
                    q["question_text"] or "",
                    opts,
                    q["explanation"] or "",
                    strategy=strategy,
                )
                for d in ext.get("topic_details", []):
                    t_name = str(d.get("name") or d.get("topic_name") or "").strip()
                    if not t_name or t_name == "General Medical Concept":
                        continue
                    norm = normalize_topic_key(t_name)
                    if not norm or norm in ignored_keys:
                        continue
                    if norm not in consolidated:
                        consolidated[norm] = {
                            "name": t_name,
                            "topic_name": t_name,
                            "verified": bool(d.get("verified", False)),
                            "selected": bool(d.get("selected", False)),
                            "confidence": d.get("confidence", "low"),
                        }
                    elif d.get("verified"):
                        consolidated[norm]["verified"] = True
                        consolidated[norm]["selected"] = True
                        consolidated[norm]["confidence"] = "high"
            except Exception as exc:
                logger.error(
                    "Failed extracting topics for session question %s: %s",
                    q["question_id"],
                    exc,
                )

        saved_list = list(consolidated.values())

    return {
        "status": "success",
        "session_id": session_id,
        "title": s_row["custom_title"] or s_row["subject_name"],
        "topics_reviewed": bool(s_row["topics_reviewed"]),
        "topic_list": saved_list,
    }


@router.post("/{session_id}/update_topics")
def update_session_topics(
    session_id: str, req: UpdateSessionTopicsRequest
) -> Dict[str, Any]:
    """Updates draft or confirmed topics and triggers RAG task queue processing."""
    normalized = _normalize_topic_list(req.topic_list)
    is_confirmed = bool(req.confirm or req.topics_reviewed)
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, refs_status, llm_status FROM quiz_sessions WHERE id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")

        if is_confirmed:
            refs_status = "processing"
            llm_status = "processing" if is_llm_processing_enabled() else "none"
            cursor.execute(
                """
                UPDATE quiz_sessions
                SET topics_reviewed = 1, refs_status = ?, llm_status = ?, topic_list_json = ?
                WHERE id = ?
                """,
                (refs_status, llm_status, json.dumps(normalized), session_id),
            )
            _enqueue_session_processing(conn, session_id)

            selected_names = [
                t["name"] for t in normalized
                if t.get("selected") and t.get("name")
            ]
            if selected_names:
                record_topic_note_selections(selected_names)
        else:
            cursor.execute(
                "UPDATE quiz_sessions SET topic_list_json = ? WHERE id = ?",
                (json.dumps(normalized), session_id),
            )
            refs_status = row["refs_status"]
            llm_status = row["llm_status"]

        conn.commit()

    _safe_notify_session(session_id, refs_status, llm_status, is_confirmed)
    return {
        "status": "success",
        "session_id": session_id,
        "topics_reviewed": is_confirmed,
        "refs_status": refs_status,
        "llm_status": llm_status,
        "topic_list": normalized,
    }


@router.patch("/topics/select")
def select_topic(req: SelectTopicRequest) -> Dict[str, Any]:
    """Optimistically updates the selected state of an extracted topic in SQLite."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO extracted_topics (question_id, topic_name, selected)
            VALUES (?, ?, ?)
            ON CONFLICT(question_id, topic_name) DO UPDATE SET selected = excluded.selected
            """,
            (req.question_id, req.topic_name, req.selected),
        )
        conn.commit()
    return {
        "status": "success",
        "question_id": req.question_id,
        "topic_name": req.topic_name,
        "selected": req.selected,
    }


@router.post("/{session_id}/retry")
def retry_session_llm_tasks(session_id: str) -> Dict[str, Any]:
    """Retries failed tasks for a Knowledge Hub session card under canonical lowercase status."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM quiz_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        cursor.execute(
            """
            UPDATE task_queue
            SET status = 'pending', priority = 1, error_message = NULL, updated_at = unixepoch('now')
            WHERE session_id = ? AND LOWER(status) = 'failed'
            """,
            (session_id,),
        )
        reset_cnt = int(cursor.rowcount)

        if reset_cnt == 0:
            _enqueue_session_processing(conn, session_id)
            reset_cnt = 1

        cursor.execute(
            "UPDATE quiz_sessions SET llm_status = 'processing' WHERE id = ?",
            (session_id,),
        )
        conn.commit()

    _safe_notify_session(session_id, "processing", "processing", True)
    return {"status": "success", "session_id": session_id, "retried_tasks": reset_cnt}


@router.post("/{session_id}/reprocess")
def reprocess_session(session_id: str) -> Dict[str, Any]:
    """Re-enqueues session questions and topic tasks for RAG synthesis and references."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM quiz_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        cursor.execute(
            """
            UPDATE task_queue
            SET status = 'pending', priority = 2, error_message = NULL, updated_at = unixepoch('now')
            WHERE session_id = ? AND LOWER(status) IN ('failed', 'completed')
            """,
            (session_id,),
        )
        # Always ensure PROCESS_SESSION is enqueued so references and topics are refreshed
        _enqueue_session_processing(conn, session_id)

        llm_status = "processing" if is_llm_processing_enabled() else "none"
        cursor.execute(
            """
            UPDATE quiz_sessions
            SET refs_status = 'processing', llm_status = ?
            WHERE id = ?
            """,
            (llm_status, session_id),
        )
        conn.commit()

    _safe_notify_session(session_id, "processing", llm_status, True)
    return {
        "status": "success",
        "session_id": session_id,
        "message": "Session reprocessing queued",
    }


@router.post("/delete")
def delete_sessions(req: DeleteSessionsRequest) -> Dict[str, Any]:
    """Deletes multiple sessions, session questions, and task queue entries in bounded chunks."""
    clean_ids = [s.strip() for s in req.session_ids if s and s.strip()]
    if not clean_ids:
        return {"status": "success", "deleted_count": 0}

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        chunk_size = 100
        total_deleted = 0
        for i in range(0, len(clean_ids), chunk_size):
            chunk = clean_ids[i : i + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(
                f"DELETE FROM task_queue WHERE session_id IN ({placeholders})", chunk
            )
            cursor.execute(
                f"DELETE FROM session_questions WHERE session_id IN ({placeholders})",
                chunk,
            )
            cursor.execute(
                f"DELETE FROM quiz_sessions WHERE id IN ({placeholders})", chunk
            )
            total_deleted += int(cursor.rowcount)
        conn.commit()

    return {"status": "success", "deleted_count": total_deleted}


@router.delete("/{session_id}")
def delete_single_session(session_id: str) -> Dict[str, Any]:
    """Deletes a single session and associated records."""
    return delete_sessions(DeleteSessionsRequest(session_ids=[session_id]))


def _batch_compute_session_queue_info(
    conn: Any, session_ids: List[str]
) -> Dict[str, Dict[str, Any]]:
    """Computes queue_info for all sessions in a single pass instead of N*5 queries."""
    default_info = {
        "in_queue": False,
        "position": 0,
        "total_pending": 0,
        "session_pending_count": 0,
        "failed_count": 0,
        "completed_count": 0,
        "top_priority": None,
    }
    if not session_ids:
        return {}

    result: Dict[str, Dict[str, Any]] = {
        sid: dict(default_info) for sid in session_ids
    }
    cursor = conn.cursor()

    # 1. Fetch global ordered active tasks for exact queue position calculation
    cursor.execute(
        """
        SELECT task_id, session_id, priority
        FROM task_queue
        WHERE LOWER(status) IN ('pending', 'processing')
        ORDER BY priority ASC, task_id ASC
        """
    )
    active_rows = cursor.fetchall()
    total_pending = len(active_rows)

    first_pos_by_sid: Dict[str, int] = {}
    first_prio_by_sid: Dict[str, int] = {}
    pending_cnt_by_sid: Dict[str, int] = defaultdict(int)

    for idx, r in enumerate(active_rows, start=1):
        sid = r["session_id"]
        if not sid:
            continue
        pending_cnt_by_sid[sid] += 1
        if sid not in first_pos_by_sid:
            first_pos_by_sid[sid] = idx
            first_prio_by_sid[sid] = int(r["priority"])

    # 2. Fetch completed & failed counts grouped by session_id
    cursor.execute(
        """
        SELECT session_id,
               SUM(CASE WHEN LOWER(status) = 'failed' THEN 1 ELSE 0 END) as failed_cnt,
               SUM(CASE WHEN LOWER(status) = 'completed' THEN 1 ELSE 0 END) as completed_cnt
        FROM task_queue
        WHERE session_id IS NOT NULL
        GROUP BY session_id
        """
    )
    for r in cursor.fetchall():
        sid = r["session_id"]
        if sid in result:
            result[sid]["failed_count"] = int(r["failed_cnt"] or 0)
            result[sid]["completed_count"] = int(r["completed_cnt"] or 0)

    for sid, cnt in pending_cnt_by_sid.items():
        if sid in result and cnt > 0:
            result[sid].update({
                "in_queue": True,
                "position": first_pos_by_sid.get(sid, 1),
                "total_pending": total_pending,
                "session_pending_count": cnt,
                "top_priority": first_prio_by_sid.get(sid),
            })

    return result


CONSOLIDATED_REVIEWS_SESSION_ID = "sess_spaced_repetition_due"

# Set to 0 so wrongly answered and FSRS-due questions immediately appear in the Knowledge Hub & Dashboard
WRONG_ANSWER_COOLDOWN_SECONDS = 0

_DUE_OR_WRONG_CSV_WHERE = """
(
    (q.due_timestamp > 0 AND q.due_timestamp <= unixepoch('now'))
    OR (q.last_review IS NOT NULL AND (q.due_timestamp IS NULL OR q.due_timestamp <= unixepoch('now')))
    OR (COALESCE(q.state, 0) IN (1, 3) AND (q.due_timestamp IS NULL OR q.due_timestamp <= unixepoch('now')))
    OR (
        COALESCE(q.due_timestamp, 0) <= unixepoch('now')
        AND EXISTS (
            SELECT 1 FROM session_questions sq
            WHERE sq.question_id = q.id
              AND sq.session_id != 'sess_spaced_repetition_due'
              AND sq.selected_option IS NOT NULL
              AND TRIM(sq.selected_option) != ''
              AND sq.is_correct = 0
              AND sq.rowid = (
                  SELECT MAX(sq2.rowid) FROM session_questions sq2
                  WHERE sq2.question_id = q.id
                    AND sq2.session_id != 'sess_spaced_repetition_due'
                    AND sq2.selected_option IS NOT NULL
                    AND TRIM(sq2.selected_option) != ''
              )
        )
    )
)
"""

_DUE_OR_WRONG_MCQ_WHERE = """
(
    (due_timestamp > 0 AND due_timestamp <= unixepoch('now'))
    OR (last_review IS NOT NULL AND (due_timestamp IS NULL OR due_timestamp <= unixepoch('now')))
    OR (COALESCE(state, 0) IN (1, 3) AND (due_timestamp IS NULL OR due_timestamp <= unixepoch('now')))
)
"""


def sync_consolidated_due_session(conn) -> None:
    """
    Consolidates all wrongly answered questions and questions due for spaced repetition review
    into the single unified Knowledge Hub question set ('sess_spaced_repetition_due').
    """
    cursor = conn.cursor()

    # 1. Gather all due or wrongly answered questions from csv_questions
    cursor.execute(
        f"""
        SELECT q.id, q.question, q.opt_a, q.opt_b, q.opt_c, q.opt_d, q.correct_answer, q.explanation,
               q.llm_explanation, q.topic_name, q.due_timestamp, q.state, q.lapses, q.last_review
        FROM csv_questions q
        WHERE {_DUE_OR_WRONG_CSV_WHERE}
        ORDER BY CASE WHEN COALESCE(q.state, 0) IN (1, 3) THEN 0 ELSE 1 END ASC,
                 COALESCE(q.due_timestamp, 0) ASC
        """
    )
    due_csv = [dict(r) for r in cursor.fetchall()]

    # 2. Gather all due or wrongly answered questions from mcq_bank
    cursor.execute(
        f"""
        SELECT id, question, opt_a, opt_b, opt_c, opt_d, correct_opt as correct_answer,
               csv_explanation as explanation, llm_explanation, source_tag as topic_name,
               due_timestamp, state, lapses, last_review
        FROM mcq_bank
        WHERE {_DUE_OR_WRONG_MCQ_WHERE}
        ORDER BY CASE WHEN COALESCE(state, 0) IN (1, 3) THEN 0 ELSE 1 END ASC,
                 COALESCE(due_timestamp, 0) ASC
        """
    )
    due_mcq = [dict(r) for r in cursor.fetchall()]

    seen_ids = set()
    all_due = []
    for item in due_csv + due_mcq:
        qid_str = str(item["id"])
        if qid_str not in seen_ids:
            seen_ids.add(qid_str)
            all_due.append(item)

    if not all_due:
        # If no questions are currently due, remove or clean up the consolidated review session
        cursor.execute("DELETE FROM session_questions WHERE session_id = ?", (CONSOLIDATED_REVIEWS_SESSION_ID,))
        cursor.execute("DELETE FROM quiz_sessions WHERE id = ?", (CONSOLIDATED_REVIEWS_SESSION_ID,))
        conn.commit()
        return

    # Requirement 2: The topic list inside due/review set is updated with verified topics
    # from the questions added to it. As well as removing questions from set removes associated topics.
    all_due_qids = [str(q["id"]) for q in all_due]
    qid_to_verified_topics: Dict[str, List[str]] = {qid: [] for qid in all_due_qids}

    # Query verified topics from question_metadata and extracted_topics in chunks
    chunk_size = 150
    for i in range(0, len(all_due_qids), chunk_size):
        chunk = all_due_qids[i : i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)

        # 1. Check question_metadata.verified_topics_json
        cursor.execute(
            f"SELECT question_id, verified_topics_json FROM question_metadata WHERE question_id IN ({placeholders})",
            chunk,
        )
        for r in cursor.fetchall():
            q_id_str = str(r["question_id"])
            if r["verified_topics_json"]:
                try:
                    vt_list = json.loads(r["verified_topics_json"])
                    if isinstance(vt_list, list):
                        for item in vt_list:
                            t_name = item.get("name") if isinstance(item, dict) else str(item)
                            if t_name and t_name != "General Medical Concept" and t_name not in qid_to_verified_topics[q_id_str]:
                                qid_to_verified_topics[q_id_str].append(t_name)
                except Exception:
                    pass

        # 2. Check extracted_topics where verified = 1 OR selected = 1
        cursor.execute(
            f"""
            SELECT question_id, topic_name FROM extracted_topics
            WHERE question_id IN ({placeholders}) AND (verified = 1 OR selected = 1)
            """,
            chunk,
        )
        for r in cursor.fetchall():
            q_id_str = str(r["question_id"])
            t_name = str(r["topic_name"] or "").strip()
            if t_name and t_name != "General Medical Concept" and t_name not in qid_to_verified_topics[q_id_str]:
                qid_to_verified_topics[q_id_str].append(t_name)

        # 3. Check existing session_questions.question_topics_json across prior sessions
        cursor.execute(
            f"""
            SELECT question_id, question_topics_json FROM session_questions
            WHERE question_id IN ({placeholders}) AND session_id != ? AND question_topics_json IS NOT NULL
            """,
            chunk + [CONSOLIDATED_REVIEWS_SESSION_ID],
        )
        for r in cursor.fetchall():
            q_id_str = str(r["question_id"])
            if r["question_topics_json"]:
                try:
                    sq_topics = json.loads(r["question_topics_json"])
                    if isinstance(sq_topics, list):
                        for item in sq_topics:
                            t_name = item.get("name") if isinstance(item, dict) else str(item)
                            if t_name and t_name != "General Medical Concept" and t_name not in qid_to_verified_topics[q_id_str]:
                                qid_to_verified_topics[q_id_str].append(t_name)
                except Exception:
                    pass

    # For any question that still doesn't have an explicit verified topic in metadata,
    # fall back to question's primary topic_name so it's not orphaned
    for q in all_due:
        q_id_str = str(q["id"])
        if not qid_to_verified_topics[q_id_str]:
            raw_top = q.get("topic_name")
            if raw_top and raw_top != "General Medical Concept":
                qid_to_verified_topics[q_id_str].append(raw_top)

    # Aggregate distinct verified topics strictly from the questions currently in all_due
    consolidated_topics_list = list(dict.fromkeys(
        t
        for qid in all_due_qids
        for t in qid_to_verified_topics[qid]
        if t and t != "General Medical Concept"
    ))
    topic_items = [{"name": t, "topic_name": t, "verified": True, "selected": True} for t in consolidated_topics_list]

    now_ts = float(time.time() * 1000)
    today_str = datetime.now().strftime("%Y-%m-%d")
    total_q = len(all_due)

    # Insert or update the single consolidated session card
    cursor.execute(
        """
        INSERT INTO quiz_sessions (
            id, subject_name, custom_title, date, timestamp, last_attempted,
            total_questions, correct_answers, wrong_answers, unanswered,
            score, max_score, accuracy, time_limit_seconds, time_used_seconds,
            mode, is_completed, topics_reviewed, refs_status, llm_status, topic_list_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            custom_title = excluded.custom_title,
            subject_name = excluded.subject_name,
            total_questions = excluded.total_questions,
            unanswered = excluded.total_questions,
            correct_answers = 0,
            wrong_answers = 0,
            score = 0,
            max_score = excluded.total_questions,
            accuracy = 0.0,
            is_completed = 0,
            date = excluded.date,
            timestamp = excluded.timestamp,
            topics_reviewed = excluded.topics_reviewed,
            refs_status = excluded.refs_status,
            llm_status = excluded.llm_status,
            topic_list_json = excluded.topic_list_json
        """,
        (
            CONSOLIDATED_REVIEWS_SESSION_ID,
            "Spaced Repetition & Wrong Qs",
            f"🧠 Due & Wrong Review Set ({total_q} Questions)",
            today_str,
            now_ts,
            0.0,
            total_q,
            0,
            0,
            total_q,
            0,
            total_q,
            0.0,
            0,
            0,
            "learn",
            0,
            1,
            "ready",
            "ready" if is_llm_processing_enabled() else "none",
            json.dumps(topic_items),
        ),
    )

    # Re-populate session_questions for this consolidated session
    cursor.execute("DELETE FROM session_questions WHERE session_id = ?", (CONSOLIDATED_REVIEWS_SESSION_ID,))
    q_params = []
    for q in all_due:
        q_id = str(q["id"])
        q_verified = qid_to_verified_topics.get(q_id) or []
        primary_topic = q_verified[0] if q_verified else (q.get("topic_name") or "General")
        q_params.append((
            CONSOLIDATED_REVIEWS_SESSION_ID,
            q_id,
            None,
            0,
            0,
            0,
            now_ts,
            q.get("question") or "",
            q.get("opt_a") or "",
            q.get("opt_b") or "",
            q.get("opt_c") or "",
            q.get("opt_d") or "",
            (q.get("correct_answer") or "A").strip().upper(),
            q.get("explanation") or "",
            q.get("llm_explanation") or "",
            primary_topic,
            json.dumps(q_verified if q_verified else ([q.get("topic_name")] if q.get("topic_name") else [])),
        ))

    cursor.executemany(
        """
        INSERT INTO session_questions (
            session_id, question_id, selected_option, is_correct, timed_out, time_spent_ms,
            timestamp, question_text, opt_a, opt_b, opt_c, opt_d, correct_answer,
            explanation, llm_explanation, topic, question_topics_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        q_params,
    )
    conn.commit()


@router.get("/due_summary")
def get_due_summary() -> Dict[str, Any]:
    """
    Returns count and details of questions currently due for repetition review or answered wrong.
    """
    with closing(get_db_connection()) as conn:
        sync_consolidated_due_session(conn)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT COUNT(*) as cnt, SUM(CASE WHEN COALESCE(q.state, 0) IN (1, 3) THEN 1 ELSE 0 END) as wrong_cnt FROM csv_questions q WHERE {_DUE_OR_WRONG_CSV_WHERE}"
        )
        csv_row = cursor.fetchone()
        csv_count = int(csv_row["cnt"] or 0) if csv_row else 0
        wrong_count = int(csv_row["wrong_cnt"] or 0) if csv_row else 0

        cursor.execute(
            f"SELECT COUNT(*) as cnt FROM mcq_bank WHERE {_DUE_OR_WRONG_MCQ_WHERE}"
        )
        mcq_row = cursor.fetchone()
        mcq_count = int(mcq_row["cnt"] or 0) if mcq_row else 0

        total_due = int(csv_count + mcq_count)

    return {
        "status": "success",
        "total_due": total_due,
        "wrong_count": wrong_count,
        "due_review_count": max(0, total_due - wrong_count),
        "session_id": CONSOLIDATED_REVIEWS_SESSION_ID if total_due > 0 else None,
    }


ENCOURAGING_MESSAGES = [
    {
        "id": "enc_1",
        "type": "encouragement",
        "title": "Stay Persistent! 🌟",
        "message": "Every question you review strengthens your clinical intuition. Consistent daily practice is the secret to mastery.",
        "icon": "🌟"
    },
    {
        "id": "enc_2",
        "type": "encouragement",
        "title": "Mistakes Are Stepping Stones 🧠",
        "message": "Getting questions wrong now is how neural pathways form. You are turning weak areas into your future strengths!",
        "icon": "💡"
    },
    {
        "id": "enc_3",
        "type": "encouragement",
        "title": "Keep the Momentum Going 🔥",
        "message": "Small, focused study bursts beat marathon cramming every time. Trust your preparation and stay on course.",
        "icon": "🔥"
    },
    {
        "id": "enc_4",
        "type": "encouragement",
        "title": "You're Making Tangible Progress 📈",
        "message": "With each spaced repetition interval, your long-term memory consolidates deeper medical knowledge.",
        "icon": "🎯"
    },
    {
        "id": "enc_5",
        "type": "encouragement",
        "title": "Breathe and Stay Curious 🌱",
        "message": "Medicine is a marathon of understanding, not rote memory. Take pride in your dedication today.",
        "icon": "🌱"
    },
    {
        "id": "enc_6",
        "type": "encouragement",
        "title": "Excellence Through Practice 🏆",
        "message": "Top exam performers aren't born with the answers—they simply reviewed their errors more intentionally.",
        "icon": "🏆"
    },
    {
        "id": "enc_7",
        "type": "encouragement",
        "title": "Focus On One Question At A Time ⚡",
        "message": "Don't worry about the entire syllabus today. Win the current question, understand the physiology, and keep moving.",
        "icon": "⚡"
    }
]


@router.get("/dashboard_prompts")
def get_dashboard_prompts() -> Dict[str, Any]:
    """
    Computes intelligent, rotating dashboard prompts and recommendations:
    1. Due spaced repetition reminder (only for questions not answered wrongly in the last 6 hours).
    2. Suggesting subjects not tried in a while.
    3. Suggesting questions on clinical topics frequently missed (derived from NLP extracted topics).
    4. Encouraging and motivational clinical study messages.
    """
    banners: List[Dict[str, Any]] = []
    now_ts = time.time()

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        # 1. Due & Wrong set summary (synced immediately)
        sync_consolidated_due_session(conn)
        cursor.execute(
            f"SELECT COUNT(*) FROM csv_questions q WHERE {_DUE_OR_WRONG_CSV_WHERE}"
        )
        csv_due = cursor.fetchone()[0] or 0

        cursor.execute(
            f"SELECT COUNT(*) FROM mcq_bank WHERE {_DUE_OR_WRONG_MCQ_WHERE}"
        )
        mcq_due = cursor.fetchone()[0] or 0
        total_due = int(csv_due + mcq_due)

        if total_due > 0:
            banners.append({
                "id": "due_review_banner",
                "type": "due_review",
                "title": f"{total_due} question{'s' if total_due != 1 else ''} left to review 🧠",
                "message": f"You have {total_due} question{'s' if total_due != 1 else ''} left to review in your consolidated review set.",
                "icon": "🔔",
                "badge": f"{total_due} Left to Review",
                "action": "review_due",
                "action_label": f"Review Set ({total_due}) →",
                "session_id": CONSOLIDATED_REVIEWS_SESSION_ID,
                "count": total_due
            })

        # 2. Topics frequently missed (from NLP-extracted topics of wrong questions)
        # Note: Must use extracted topics rather than raw CSV names
        cursor.execute(
            """
            SELECT sq.question_id, sq.topic, sq.question_topics_json, et.topic_name
            FROM session_questions sq
            LEFT JOIN extracted_topics et ON sq.question_id = et.question_id AND et.selected = 1
            WHERE sq.is_correct = 0
            """
        )
        wrong_rows = cursor.fetchall()
        wrong_topic_counts: Dict[str, int] = defaultdict(int)

        for r in wrong_rows:
            extracted_name = r["topic_name"]
            if extracted_name and extracted_name != "General Medical Concept" and extracted_name.strip():
                wrong_topic_counts[extracted_name.strip()] += 1
                continue

            # Fallback to question_topics_json if extracted_topics table didn't have rows
            q_json = r["question_topics_json"]
            found_in_json = False
            if q_json and q_json != "[]":
                try:
                    parsed = json.loads(q_json)
                    for item in parsed:
                        name = (item.get("name") or item.get("topic_name") or "") if isinstance(item, dict) else str(item)
                        name = name.strip()
                        if name and name != "General Medical Concept":
                            wrong_topic_counts[name] += 1
                            found_in_json = True
                except Exception:
                    pass

            if not found_in_json:
                q_top = (r["topic"] or "").strip()
                if q_top and q_top not in ("General", "General Medical Concept"):
                    wrong_topic_counts[q_top] += 1

        # Sort topics by frequency of wrong answers
        sorted_weak_topics = sorted(
            [t for t in wrong_topic_counts.items() if t[1] >= 1],
            key=lambda x: -x[1]
        )

        for top_name, wrong_count in sorted_weak_topics[:6]:
            banners.append({
                "id": f"weak_topic_{_to_synthesis_key(top_name)}",
                "type": "weak_topic",
                "title": f"Target Weak Topic: {top_name} 🎯",
                "message": f"You've missed {wrong_count} question{'s' if wrong_count != 1 else ''} related to {top_name}. A focused review will solidify this concept!",
                "icon": "🎯",
                "badge": "High Yield Weak Area",
                "topic": top_name,
                "action": "practice_topic",
                "action_label": f"Practice {top_name} →"
            })

        # 3. Subject Switch-Up: Find subjects not tried in a while
        cursor.execute(
            """
            SELECT DISTINCT subject_name,
                   MAX(CASE WHEN timestamp > 1e11 THEN timestamp / 1000.0 ELSE timestamp END) as last_ts
            FROM quiz_sessions
            WHERE subject_name IS NOT NULL
              AND subject_name != ''
              AND subject_name NOT IN ('Spaced Repetition', 'Due Reviews', 'Mixed Topics', 'Mixed Session', 'General')
            GROUP BY subject_name
            ORDER BY last_ts ASC
            """
        )
        sub_rows = cursor.fetchall()
        switch_count = 0
        for s in sub_rows:
            s_name = s["subject_name"]
            last_ts = float(s["last_ts"] or 0)
            days_ago = round((now_ts - last_ts) / 86400.0, 1) if last_ts > 0 else 999
            if days_ago >= 3:
                time_desc = f"{int(days_ago)} days" if days_ago < 999 else "a while"
                banners.append({
                    "id": f"switch_subject_{_to_synthesis_key(s_name)}",
                    "type": "switch_subject",
                    "title": f"Switch It Up: Practice {s_name} 📚",
                    "message": f"You haven't practiced {s_name} in {time_desc}. Rotating subjects improves retention and cognitive flexibility.",
                    "icon": "🔄",
                    "badge": "Subject Rotation",
                    "subject": s_name,
                    "action": "switch_subject",
                    "action_label": f"Practice {s_name} →"
                })
                switch_count += 1
                if switch_count >= 2:
                    break

        # 4. Rich Context-Aware Motivation & Habit Progression
        # Time-of-day contextual checks
        current_dt = datetime.fromtimestamp(now_ts)
        current_hour = current_dt.hour
        today_date_str = current_dt.strftime("%Y-%m-%d")
        yesterday_date_str = (current_dt - timedelta(days=1)).strftime("%Y-%m-%d")

        # Query user activity today vs yesterday
        cursor.execute(
            """
            SELECT date,
                   COUNT(DISTINCT qs.id) as session_count,
                   COALESCE(SUM(qs.total_questions), 0) as total_qs,
                   COALESCE(SUM(qs.correct_answers), 0) as correct_qs,
                   COALESCE(SUM(qs.time_used_seconds), 0) as total_time
            FROM quiz_sessions qs
            WHERE date IN (?, ?)
            GROUP BY date
            """,
            (today_date_str, yesterday_date_str)
        )
        perf_by_date = {r["date"]: r for r in cursor.fetchall()}
        today_perf = perf_by_date.get(today_date_str)
        yest_perf = perf_by_date.get(yesterday_date_str)

        today_qs = int(today_perf["total_qs"] or 0) if today_perf else 0
        yest_qs = int(yest_perf["total_qs"] or 0) if yest_perf else 0
        today_time_sec = int(today_perf["total_time"] or 0) if today_perf else 0

        # Hourly pace calculation for today
        # Check questions answered in the last hour vs prior 2 hours
        one_hour_ago = now_ts - 3600
        two_hours_ago = now_ts - 7200
        cursor.execute(
            """
            SELECT
                SUM(CASE WHEN (CASE WHEN timestamp > 1e11 THEN timestamp/1000.0 ELSE timestamp END) >= ? THEN total_questions ELSE 0 END) as last_1h_qs,
                SUM(CASE WHEN (CASE WHEN timestamp > 1e11 THEN timestamp/1000.0 ELSE timestamp END) >= ? AND (CASE WHEN timestamp > 1e11 THEN timestamp/1000.0 ELSE timestamp END) < ? THEN total_questions ELSE 0 END) as prev_1h_qs
            FROM quiz_sessions
            WHERE (CASE WHEN timestamp > 1e11 THEN timestamp/1000.0 ELSE timestamp END) >= ?
            """,
            (one_hour_ago, two_hours_ago, one_hour_ago, two_hours_ago)
        )
        hourly_row = cursor.fetchone()
        last_1h_qs = int(hourly_row["last_1h_qs"] or 0) if hourly_row else 0
        prev_1h_qs = int(hourly_row["prev_1h_qs"] or 0) if hourly_row else 0

        # 4a. Time of day greeting or sleep banner
        if 5 <= current_hour <= 11:
            banners.append({
                "id": "motivation_morning_greeting",
                "type": "motivation",
                "title": "Good Morning, Doctor! 🌅",
                "message": f"A brand new day of clinical learning begins. You've solved {today_qs} MCQ{'s' if today_qs != 1 else ''} today. Let's build strong diagnostic habits!",
                "icon": "🌅",
                "badge": "Morning Fuel",
                "anim_style": "sparkle"
            })
        elif current_hour >= 23 or current_hour <= 4:
            banners.append({
                "id": "motivation_sleep_reminder",
                "type": "warning",
                "title": "Time for Rest & Sleep 🌙",
                "message": "It's late in the night. Long-term synaptic memory consolidation happens during deep sleep. Wrap up this question, hydrate, and get some good rest!",
                "icon": "🌙",
                "badge": "Sleep & Memory",
                "anim_style": "pulse"
            })

        # 4b. Push for more MCQs than yesterday or hourly pace motivation
        if yest_qs > 0 and today_qs > 0:
            if today_qs > yest_qs:
                banners.append({
                    "id": "motivation_beat_yesterday_record",
                    "type": "encouragement",
                    "title": "New Daily Record Pace! 🔥",
                    "message": f"You've completed {today_qs} MCQs today, surpassing yesterday's {yest_qs} questions! Outstanding dedication to your goal.",
                    "icon": "🔥",
                    "badge": "Pace Record",
                    "anim_style": "sparkle"
                })
            else:
                diff = yest_qs - today_qs
                banners.append({
                    "id": "motivation_push_yesterday",
                    "type": "motivation",
                    "title": f"Push to Beat Yesterday ({today_qs}/{yest_qs} Qs) 🚀",
                    "message": f"You completed {yest_qs} MCQs yesterday. Just {diff} more question{'s' if diff != 1 else ''} to beat yesterday's high-yield score!",
                    "icon": "🚀",
                    "badge": "Push Pace",
                    "anim_style": "sparkle"
                })
        elif today_qs >= 30:
            banners.append({
                "id": "motivation_high_daily_volume",
                "type": "encouragement",
                "title": f"Strong Progress: {today_qs} MCQs Completed ⚡",
                "message": "Keep your study momentum rolling! Steady clinical repetition is the proven path to residency exam mastery.",
                "icon": "⚡",
                "badge": "High Momentum",
                "anim_style": "sparkle"
            })

        # Hourly pace tracker
        if prev_1h_qs >= 10 and last_1h_qs < (prev_1h_qs * 0.5):
            banners.append({
                "id": "motivation_hourly_pace_slow",
                "type": "warning",
                "title": "Keep the Previous Pace Going ⏱️",
                "message": f"Your speed slowed down from {prev_1h_qs} Qs/hr to {last_1h_qs} Qs/hr. Refocus on one vignette at a time to stay on track for your goal!",
                "icon": "⏱️",
                "badge": "Hourly Pace Alert",
                "anim_style": "pulse"
            })
        elif last_1h_qs >= 15:
            banners.append({
                "id": "motivation_hourly_pace_fast",
                "type": "encouragement",
                "title": f"High Velocity: {last_1h_qs} Qs in the Last Hour 🏎️",
                "message": "You're in the zone! Maintain this pace to easily exceed your target study goals today.",
                "icon": "🏎️",
                "badge": "Velocity",
                "anim_style": "sparkle"
            })

        # 4c. Wellness, Eye Rest (20-20-20 rule), and Water Break
        if today_time_sec >= 2400 or today_qs >= 30:
            banners.append({
                "id": "motivation_wellness_break",
                "type": "warning",
                "title": "Quick Break: Rest Your Eyes & Hydrate 💧",
                "message": "You've been studying intensively! Look away at an object 20 feet away for 20 seconds, grab a glass of water, and stretch before your next question block.",
                "icon": "💧",
                "badge": "Wellness Check",
                "anim_style": "pulse"
            })

        # 4d. Poor MCQ performance notes recommendation
        if sorted_weak_topics:
            top_missed_name, top_missed_count = sorted_weak_topics[0]
            if top_missed_count >= 2:
                banners.append({
                    "id": f"motivation_study_notes_{_to_synthesis_key(top_missed_name)}",
                    "type": "warning",
                    "title": f"Review Notes: {top_missed_name} 📖",
                    "message": f"You've struggled with {top_missed_count} questions on {top_missed_name}. Deepen your conceptual foundation by studying clinical synthesis notes before testing again!",
                    "icon": "📖",
                    "badge": "Study Notes",
                    "topic": top_missed_name,
                    "action": "search_topic_notes",
                    "action_label": f"Study {top_missed_name} Notes 📖",
                    "anim_style": "pulse"
                })

        # 5. Fill with encouraging and motivational messages
        for enc in ENCOURAGING_MESSAGES:
            enc_copy = dict(enc)
            enc_copy["anim_style"] = "sparkle"
            banners.append(enc_copy)

    # Return full list so frontend can maintain a window of up to 10 active banners
    return {
        "status": "success",
        "banners": banners,
        "total_available": len(banners)
    }


@router.get("/history")
def get_session_history() -> List[Dict[str, Any]]:
    """Fetches ordered session history with aggregated question counts and batched queue status."""
    with closing(get_db_connection()) as conn:
        sync_consolidated_due_session(conn)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, subject_name, custom_title, date, timestamp, last_attempted,
                   total_questions, correct_answers, wrong_answers, unanswered,
                   score, max_score, accuracy, time_limit_seconds, time_used_seconds,
                   mode, is_completed, topics_reviewed, refs_status, llm_status, topic_list_json
            FROM quiz_sessions
            ORDER BY timestamp DESC
            """
        )
        sessions = [dict(row) for row in cursor.fetchall()]

        if sessions:
            cursor.execute(
                """
                SELECT session_id,
                       COUNT(*) as total_q,
                       SUM(CASE WHEN selected_option IS NOT NULL AND selected_option != '' THEN 1 ELSE 0 END) as answered_q
                FROM session_questions
                GROUP BY session_id
                """
            )
            counts_by_sid = {r["session_id"]: r for r in cursor.fetchall()}

            cursor.execute(
                """
                SELECT DISTINCT session_id, topic
                FROM session_questions
                WHERE topic IS NOT NULL AND topic != '' AND topic != 'General Medical Concept'
                """
            )
            q_topics_by_sid = defaultdict(list)
            for r in cursor.fetchall():
                q_topics_by_sid[r["session_id"]].append(r["topic"])

            all_sids = [s["id"] for s in sessions]
            queue_info_map = _batch_compute_session_queue_info(conn, all_sids)

            # Check if user enabled auto-removal of fully completed sets
            auto_remove_completed = False
            try:
                cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
                row_pers = cursor.fetchone()
                if row_pers and row_pers["value"]:
                    pers = json.loads(row_pers["value"]) if isinstance(row_pers["value"], str) else row_pers["value"]
                    if isinstance(pers, dict):
                        auto_remove_completed = bool(pers.get("auto_remove_completed_sets", False))
            except Exception:
                pass

            completed_sids_to_clean: List[str] = []
            filtered_sessions: List[Dict[str, Any]] = []

            for s in sessions:
                sid = s["id"]
                c_info = counts_by_sid.get(sid)
                answered_count = (
                    int(c_info["answered_q"] or 0)
                    if c_info
                    else (s["correct_answers"] + s["wrong_answers"])
                )
                s["completed_questions"] = answered_count
                if not s["total_questions"] and c_info:
                    s["total_questions"] = int(c_info["total_q"] or 0)

                total_q = int(s["total_questions"] or 0)
                is_comp = bool(s.get("is_completed")) or (total_q > 0 and answered_count >= total_q)

                # If auto_remove_completed is true and full questions in set are revised/completed, clean up
                if auto_remove_completed and is_comp and sid != CONSOLIDATED_REVIEWS_SESSION_ID:
                    completed_sids_to_clean.append(sid)
                    continue

                s["custom_title"] = (
                    s.get("custom_title") or s.get("subject_name") or "Study Set"
                )
                s["topics_reviewed"] = bool(s.get("topics_reviewed"))
                s["refs_status"] = s.get("refs_status") or "none"
                s["llm_status"] = s.get("llm_status") or "none"

                parsed_tlist = []
                if s.get("topic_list_json"):
                    try:
                        parsed_tlist = json.loads(s["topic_list_json"])
                    except Exception:
                        parsed_tlist = []
                s["topic_list"] = parsed_tlist

                selected_names = [
                    (t["name"] if isinstance(t, dict) else str(t))
                    for t in parsed_tlist
                    if (t.get("selected", True) if isinstance(t, dict) else True)
                ]
                s["topics"] = (
                    selected_names if selected_names else q_topics_by_sid.get(sid, [])
                )
                s["queue_info"] = queue_info_map.get(sid, {})
                filtered_sessions.append(s)

            if completed_sids_to_clean:
                placeholders = ",".join("?" for _ in completed_sids_to_clean)
                cursor.execute(f"DELETE FROM task_queue WHERE session_id IN ({placeholders})", completed_sids_to_clean)
                cursor.execute(f"DELETE FROM session_questions WHERE session_id IN ({placeholders})", completed_sids_to_clean)
                cursor.execute(f"DELETE FROM quiz_sessions WHERE id IN ({placeholders})", completed_sids_to_clean)
                conn.commit()

            return filtered_sessions

        return sessions


@router.get("/{session_id}/questions")
@router.get("/questions/{session_id}")
def get_session_questions(session_id: str) -> List[Dict[str, Any]]:
    """Fetches session questions with batch-resolved cached reference links."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT sq.question_id, sq.selected_option, sq.is_correct, sq.timed_out, sq.time_spent_ms,
                   sq.timestamp, sq.question_text, sq.opt_a, sq.opt_b, sq.opt_c, sq.opt_d, sq.correct_answer,
                   sq.explanation, COALESCE(NULLIF(sq.llm_explanation, ''), cq.llm_explanation, '') as llm_explanation,
                   sq.topic, sq.question_topics_json, cs.top_references as reference_links
            FROM session_questions sq
            LEFT JOIN csv_questions cq ON sq.question_id = cq.id
            LEFT JOIN cached_syntheses cs ON LOWER(REPLACE(TRIM(sq.topic), ' ', '-')) = cs.topic_key
            WHERE sq.session_id = ?
            ORDER BY sq.rowid ASC
            """,
            (session_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]

        needed_keys: Set[str] = set()
        for r in rows:
            if not _has_references(r.get("reference_links")):
                if r.get("topic") and r["topic"] != "General Medical Concept":
                    needed_keys.add(_to_synthesis_key(r["topic"]))
                    needed_keys.add(normalize_topic_key(r["topic"]))
                try:
                    q_topics = json.loads(r.get("question_topics_json") or "[]")
                    for qt in q_topics:
                        t_name = qt["name"] if isinstance(qt, dict) else str(qt)
                        if t_name and t_name != "General Medical Concept":
                            needed_keys.add(_to_synthesis_key(t_name))
                            needed_keys.add(normalize_topic_key(t_name))
                except Exception:
                    pass

        needed_keys.discard("")
        if needed_keys:
            key_list = list(needed_keys)
            ref_map: Dict[str, str] = {}
            chunk_size = 100
            for i in range(0, len(key_list), chunk_size):
                chunk = key_list[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"SELECT topic_key, top_references FROM cached_syntheses WHERE topic_key IN ({placeholders})",
                    chunk,
                )
                for cr in cursor.fetchall():
                    if _has_references(cr["top_references"]):
                        ref_map[cr["topic_key"]] = cr["top_references"]

            for r in rows:
                if not _has_references(r.get("reference_links")):
                    raw_t = r.get("topic") or ""
                    for candidate_k in (
                        _to_synthesis_key(raw_t),
                        normalize_topic_key(raw_t),
                    ):
                        if candidate_k in ref_map:
                            r["reference_links"] = ref_map[candidate_k]
                            break
                    if _has_references(r.get("reference_links")):
                        continue
                    try:
                        q_topics = json.loads(r.get("question_topics_json") or "[]")
                        for qt in q_topics:
                            t_name = qt["name"] if isinstance(qt, dict) else str(qt)
                            for sub_k in (
                                _to_synthesis_key(t_name),
                                normalize_topic_key(t_name),
                            ):
                                if sub_k in ref_map:
                                    r["reference_links"] = ref_map[sub_k]
                                    break
                            if _has_references(r.get("reference_links")):
                                break
                    except Exception:
                        pass

        return rows


@router.get("/{session_id}/topics")
def get_session_topics_and_notes(session_id: str) -> List[Dict[str, Any]]:
    """Fetches topic cards and LLM syntheses for a session using chunked SQL batching."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT topic_list_json, refs_status, llm_status FROM quiz_sessions WHERE id = ?",
            (session_id,),
        )
        s_row = cursor.fetchone()

        selected_topics: List[str] = []
        if s_row and s_row["topic_list_json"]:
            try:
                t_list = json.loads(s_row["topic_list_json"])
                for item in t_list:
                    if (
                        isinstance(item, dict)
                        and item.get("selected", True)
                        and (item.get("name") or item.get("topic_name"))
                    ):
                        selected_topics.append(
                            str(item.get("name") or item.get("topic_name")).strip()
                        )
                    elif isinstance(item, str) and item.strip():
                        selected_topics.append(item.strip())
            except Exception:
                pass

        if not selected_topics:
            cursor.execute(
                """
                SELECT DISTINCT COALESCE(e.topic_name, sq.topic) as topic_name
                FROM session_questions sq
                LEFT JOIN extracted_topics e ON sq.question_id = e.question_id AND e.selected = 1
                WHERE sq.session_id = ?
                """,
                (session_id,),
            )
            selected_topics = [
                r["topic_name"]
                for r in cursor.fetchall()
                if r["topic_name"] and r["topic_name"] != "General Medical Concept"
            ]

        llm_on = is_llm_processing_enabled()
        output: List[Dict[str, Any]] = []

        if selected_topics:
            # Map both hyphenated (canonical) and space-separated keys to support all rows
            t_key_map: Dict[str, str] = {}
            query_keys_set: Set[str] = set()
            for t_name in selected_topics:
                h_key = _to_synthesis_key(t_name)
                s_key = normalize_topic_key(t_name)
                if h_key and h_key not in t_key_map:
                    t_key_map[h_key] = t_name
                    query_keys_set.add(h_key)
                    if s_key:
                        query_keys_set.add(s_key)

            query_keys = list(query_keys_set)
            cached_data: Dict[str, Dict[str, Any]] = {}
            chunk_size = 100
            for i in range(0, len(query_keys), chunk_size):
                chunk = query_keys[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"""
                    SELECT topic_key, display_title, enhanced_explanation, distractor_analysis,
                           unified_article, variance_audit, top_references
                    FROM cached_syntheses
                    WHERE topic_key IN ({placeholders})
                    """,
                    chunk,
                )
                for r in cursor.fetchall():
                    row_dict = dict(r)
                    cached_data[r["topic_key"]] = row_dict
                    cached_data[_to_synthesis_key(r["topic_key"])] = row_dict

            for h_key, t_name in t_key_map.items():
                c_row = cached_data.get(h_key) or cached_data.get(
                    normalize_topic_key(t_name)
                )
                unified = (c_row["unified_article"] or "") if c_row else ""
                top_refs = (c_row["top_references"] or "") if c_row else ""
                variance_audit = (c_row.get("variance_audit") or "") if c_row else ""
                has_valid_refs = _has_references(top_refs)

                if not llm_on:
                    status = "references_only" if has_valid_refs else "pending"
                elif (
                    unified
                    and "LM Studio processing skipped" not in unified
                    and "Please add reference material" not in unified
                    and "No local reference files found" not in unified
                ):
                    status = "completed"
                elif has_valid_refs:
                    status = "references_only"
                else:
                    status = "pending"

                output.append({
                    "topic_key": h_key,
                    "topic_name": t_name,
                    "display_title": (c_row.get("display_title") if c_row else None)
                    or t_name,
                    "enhanced_explanation": (c_row.get("enhanced_explanation") or "")
                    if (c_row and llm_on)
                    else "",
                    "distractor_analysis": (c_row.get("distractor_analysis") or "")
                    if (c_row and llm_on)
                    else "",
                    "unified_article": unified if llm_on else "",
                    "variance_audit": variance_audit if llm_on else "",
                    "top_references": top_refs,
                    "llm_status": status,
                    "llm_enabled": llm_on,
                })

        return output


@router.get("/{session_id}/inspector")
def get_session_inspector_details(session_id: str) -> Dict[str, Any]:
    """Powers the right sidebar inspector with SQL-aggregated topic proficiencies."""
    now_sec = time.time()
    stale_cutoff_sec = 7 * 86400

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, subject_name, custom_title, date, timestamp, last_attempted,
                   total_questions, correct_answers, wrong_answers, accuracy,
                   topics_reviewed, refs_status, llm_status, topic_list_json
            FROM quiz_sessions
            WHERE id = ?
            """,
            (session_id,),
        )
        s_row = cursor.fetchone()
        if not s_row:
            raise HTTPException(status_code=404, detail="Session not found")

        session_dict = dict(s_row)

        cursor.execute(
            """
            SELECT question_id, selected_option, is_correct, timed_out, timestamp, topic, question_topics_json
            FROM session_questions
            WHERE session_id = ?
            """,
            (session_id,),
        )
        session_qs = cursor.fetchall()

        completed_q = sum(
            1
            for q in session_qs
            if q["selected_option"] is not None
            and str(q["selected_option"]).strip() != ""
        )

        card_topics: List[str] = []
        if session_dict.get("topic_list_json"):
            try:
                for item in json.loads(session_dict["topic_list_json"]):
                    if (
                        isinstance(item, dict)
                        and item.get("selected", True)
                        and item.get("name")
                    ):
                        card_topics.append(item["name"])
                    elif isinstance(item, str) and item.strip():
                        card_topics.append(item.strip())
            except Exception:
                pass

        for q in session_qs:
            q_topic = q["topic"] if hasattr(q, "keys") else q[5]
            if (
                q_topic
                and q_topic != "General Medical Concept"
                and q_topic not in card_topics
            ):
                card_topics.append(q_topic)

        unique_card_topics = list(dict.fromkeys(card_topics))

        global_topic_stats: Dict[str, Dict[str, Any]] = {}
        if unique_card_topics:
            chunk_size = 100
            for i in range(0, len(unique_card_topics), chunk_size):
                chunk = unique_card_topics[i : i + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                cursor.execute(
                    f"""
                    SELECT topic,
                           COUNT(*) as attempted,
                           SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) as correct,
                           SUM(CASE WHEN is_correct = 0 THEN 1 ELSE 0 END) as wrong,
                           MAX(CASE WHEN timestamp > 1e11 THEN timestamp / 1000.0 ELSE timestamp END) as last_ts
                    FROM session_questions
                    WHERE topic IN ({placeholders})
                      AND selected_option IS NOT NULL
                      AND TRIM(selected_option) != ''
                    GROUP BY topic
                    """,
                    chunk,
                )
                for r in cursor.fetchall():
                    global_topic_stats[r["topic"]] = {
                        "attempted": int(r["attempted"] or 0),
                        "correct": int(r["correct"] or 0),
                        "wrong": int(r["wrong"] or 0),
                        "last_ts": float(r["last_ts"] or 0.0),
                    }

        topic_proficiency = []
        weak_topics = []
        stale_topics = []

        for t_name in unique_card_topics:
            st = global_topic_stats.get(
                t_name, {"attempted": 0, "correct": 0, "wrong": 0, "last_ts": 0.0}
            )
            att = st["attempted"]
            corr = st["correct"]
            wrong = st["wrong"]
            acc = round((corr / att) * 100.0, 1) if att > 0 else 0.0
            last_ts = st["last_ts"]

            if att == 0:
                level = "untested"
            elif acc >= 85 and att >= 3:
                level = "expert"
            elif acc >= 70:
                level = "advanced"
            elif acc >= 50:
                level = "intermediate"
            else:
                level = "beginner"

            days_ago = (
                round((now_sec - last_ts) / 86400.0, 1) if last_ts > 0 else None
            )
            is_stale = (last_ts == 0) or ((now_sec - last_ts) >= stale_cutoff_sec)

            prof_entry = {
                "topic": t_name,
                "attempted": att,
                "correct": corr,
                "wrong": wrong,
                "accuracy": acc,
                "level": level,
                "last_practiced_ts": last_ts,
                "days_since_revision": days_ago,
                "is_stale": is_stale,
            }
            topic_proficiency.append(prof_entry)

            if att > 0 and (acc < 65.0 or wrong > corr):
                weak_topics.append(prof_entry)
            if is_stale:
                stale_topics.append(prof_entry)

        weak_topics.sort(key=lambda x: (x["accuracy"], -x["wrong"]))

        last_att_raw = float(session_dict.get("last_attempted") or 0.0)
        if last_att_raw == 0.0 and completed_q > 0:
            last_att_raw = float(session_dict.get("timestamp") or 0.0)
        last_att_sec = (
            last_att_raw / 1000.0 if last_att_raw > 1e11 else last_att_raw
        )
        session_days_ago = (
            round((now_sec - last_att_sec) / 86400.0, 1)
            if last_att_sec > 0
            else None
        )

        queue_info = get_session_queue_info(session_id)

        return {
            "status": "success",
            "session_id": session_id,
            "title": session_dict.get("custom_title")
            or session_dict.get("subject_name")
            or "Study Set",
            "subject_name": session_dict.get("subject_name") or "General",
            "date": session_dict.get("date"),
            "timestamp": session_dict.get("timestamp"),
            "last_attempted": last_att_raw,
            "days_since_attempt": session_days_ago,
            "completed_questions": completed_q,
            "total_questions": session_dict.get("total_questions")
            or len(session_qs),
            "accuracy": round(float(session_dict.get("accuracy") or 0.0), 1),
            "topics_reviewed": bool(session_dict.get("topics_reviewed")),
            "refs_status": session_dict.get("refs_status") or "none",
            "llm_status": session_dict.get("llm_status") or "none",
            "weak_topics": weak_topics,
            "stale_topics": stale_topics,
            "topic_proficiency": topic_proficiency,
            "queue_info": queue_info,
        }


@router.get("/{session_id}")
def get_session_by_id(session_id: str) -> Dict[str, Any]:
    """Fetches single quiz session summary by ID."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, subject_name, custom_title, date, timestamp, last_attempted,
                   total_questions, correct_answers, wrong_answers, unanswered,
                   score, max_score, accuracy, time_limit_seconds, time_used_seconds,
                   mode, is_completed, topics_reviewed, refs_status, llm_status, topic_list_json
            FROM quiz_sessions
            WHERE id = ?
            """,
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        sess = dict(row)
        sess["topics_reviewed"] = bool(sess.get("topics_reviewed"))
        parsed_tlist = []
        if sess.get("topic_list_json"):
            try:
                parsed_tlist = json.loads(sess["topic_list_json"])
            except Exception:
                parsed_tlist = []
        sess["topic_list"] = parsed_tlist
        try:
            sess["queue_info"] = get_session_queue_info(session_id)
        except Exception:
            sess["queue_info"] = {
                "status": "none",
                "pending": 0,
                "processing": 0,
                "completed": 0,
                "failed": 0,
            }
        return sess