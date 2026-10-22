from collections import defaultdict
from contextlib import closing
from datetime import datetime
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.tema_q.topic_extractor import (
    extract_topics_local,
    get_ignored_topics_set,
    normalize_topic_key,
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

        if topics_reviewed and (not existing or existing["refs_status"] == "none"):
            _enqueue_session_processing(conn, sid)

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


@router.get("/history")
def get_session_history() -> List[Dict[str, Any]]:
    """Fetches ordered session history with aggregated question counts and batched queue status."""
    with closing(get_db_connection()) as conn:
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

            for s in sessions:
                sid = s["id"]
                c_info = counts_by_sid.get(sid)
                s["completed_questions"] = (
                    int(c_info["answered_q"] or 0)
                    if c_info
                    else (s["correct_answers"] + s["wrong_answers"])
                )
                if not s["total_questions"] and c_info:
                    s["total_questions"] = int(c_info["total_q"] or 0)

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