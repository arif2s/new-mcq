import json
import time
from datetime import datetime
from typing import List, Optional, Dict, Any
from collections import defaultdict
from contextlib import closing
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.database import get_db_connection
from core.queue_manager import (
    is_llm_processing_enabled,
    get_session_queue_info,
    queue_worker,
)
from agents.tema_q.topic_extractor import extract_topics_local, normalize_topic_key

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class AnswerRecord(BaseModel):
    questionId: str
    selected: Optional[str] = None
    selectedOption: Optional[str] = None
    isCorrect: bool = False
    timedOut: bool = False
    timeSpentMs: Optional[int] = 0
    timeSpentSeconds: Optional[float] = 0.0
    timestamp: Optional[float] = None


class SessionCreate(BaseModel):
    id: str
    subjectName: str = "Mixed Topics"
    customTitle: Optional[str] = ""
    date: Optional[str] = None
    timestamp: Optional[float] = None
    lastAttempted: Optional[float] = None
    totalQuestions: int = 0
    correctAnswers: int = 0
    wrongAnswers: int = 0
    unanswered: Optional[int] = None
    score: Optional[int] = None
    maxScore: Optional[int] = None
    accuracy: float = 0.0
    timeLimitSeconds: Optional[int] = 0
    timeUsedSeconds: int = 0
    mode: str = "learn"
    isCompleted: Optional[bool] = None
    topicsReviewed: bool = False
    topicList: Optional[List[Any]] = None
    questions: List[Dict[str, Any]] = []
    answers: List[AnswerRecord] = []


class UpdateSessionTopicsRequest(BaseModel):
    topic_list: List[Dict[str, Any]]
    confirm: bool = False
    topics_reviewed: Optional[bool] = None


class DeleteSessionsRequest(BaseModel):
    session_ids: List[str]


def _normalize_topic_list(raw_list: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """Normalizes incoming topic items into [{name, verified, selected, confidence}]."""
    if not raw_list:
        return []
    normalized: List[Dict[str, Any]] = []
    seen = set()
    for item in raw_list:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("topic") or "").strip()
            if not name or name == "General Medical Concept":
                continue
            key = normalize_topic_key(name)
            if not key or key in seen:
                continue
            seen.add(key)
            verified = bool(item.get("verified", True))
            selected = bool(item.get("selected", True))
            normalized.append({
                "name": name,
                "verified": verified,
                "selected": selected,
                "confidence": item.get("confidence", "high" if verified else "low"),
            })
        elif isinstance(item, str) and item.strip():
            name = item.strip()
            if name == "General Medical Concept":
                continue
            key = normalize_topic_key(name)
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append({
                "name": name,
                "verified": True,
                "selected": True,
                "confidence": "high",
            })
    return normalized


def _enqueue_session_processing(conn, session_id: str):
    """Enqueues SESSION_PROCESSING (priority 2) for a confirmed session if not already active."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM task_queue WHERE task_type = 'SESSION_PROCESSING' AND session_id = ? AND status IN ('PENDING', 'PROCESSING')",
        (session_id,),
    )
    if not cursor.fetchone():
        cursor.execute(
            """
            INSERT INTO task_queue (task_type, priority, session_id, topic_name, payload)
            VALUES ('SESSION_PROCESSING', 2, ?, ?, ?)
            """,
            (
                session_id,
                f"Session {session_id[-6:]}",
                json.dumps({"session_id": session_id}),
            ),
        )


@router.post("/save")
def save_session(session: SessionCreate):
    """
    Saves or updates a Knowledge Hub question set / quiz session:
    1. 'Start Quiz Now' or Click-Outside Popup Draft:
       topicsReviewed = False -> Circle 1 active, Circles 2 & 3 greyed out, saves draft topicList.
    2. 'Confirm & Start Quiz' or 'Confirm & Add to Hub':
       topicsReviewed = True -> Circle 1 active, Circle 2 blinking ('processing'), queues SESSION_PROCESSING.
    """
    now_ms = time.time() * 1000.0
    ts = session.timestamp or now_ms
    date_str = session.date or datetime.fromtimestamp(ts / 1000.0 if ts > 1e11 else ts).strftime("%Y-%m-%d")

    total_q = session.totalQuestions or len(session.questions)
    answered_count = sum(
        1 for a in session.answers
        if (a.selected or a.selectedOption) and not a.timedOut
    )
    unanswered = session.unanswered if session.unanswered is not None else max(0, total_q - answered_count)
    score = session.score if session.score is not None else session.correctAnswers
    max_score = session.maxScore if session.maxScore is not None else total_q
    is_completed = (
        int(session.isCompleted)
        if session.isCompleted is not None
        else (1 if answered_count > 0 and answered_count >= total_q else 0)
    )
    last_attempted = session.lastAttempted or (now_ms if answered_count > 0 else 0.0)
    custom_title = (session.customTitle or session.subjectName or "Study Set").strip()

    normalized_topics = _normalize_topic_list(session.topicList)

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        # Check existing session state so completing a quiz whose topics were already confirmed
        # preserves its Circle 1 / Circle 2 / Circle 3 status!
        cursor.execute(
            "SELECT topics_reviewed, refs_status, llm_status, topic_list_json, custom_title FROM quiz_sessions WHERE id = ?",
            (session.id,),
        )
        existing = cursor.fetchone()

        topics_reviewed = 1 if session.topicsReviewed else (int(existing["topics_reviewed"]) if existing else 0)
        if not normalized_topics and existing and existing["topic_list_json"]:
            try:
                normalized_topics = json.loads(existing["topic_list_json"])
            except Exception:
                normalized_topics = []

        if existing and existing["custom_title"] and not session.customTitle:
            custom_title = existing["custom_title"]

        if topics_reviewed:
            if existing and existing["refs_status"] in ("processing", "ready"):
                refs_status = existing["refs_status"]
                llm_status = existing["llm_status"]
            else:
                refs_status = "processing"
                llm_status = "processing" if is_llm_processing_enabled() else "none"
        else:
            refs_status = "none"
            llm_status = "none"

        cursor.execute(
            """
            INSERT OR REPLACE INTO quiz_sessions (
                id, subject_name, custom_title, date, timestamp, last_attempted,
                total_questions, correct_answers, wrong_answers, unanswered,
                score, max_score, accuracy, time_limit_seconds, time_used_seconds,
                mode, is_completed, topics_reviewed, refs_status, llm_status, topic_list_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.subjectName,
                custom_title,
                date_str,
                ts,
                last_attempted,
                total_q,
                session.correctAnswers,
                session.wrongAnswers,
                unanswered,
                score,
                max_score,
                session.accuracy,
                session.timeLimitSeconds,
                session.timeUsedSeconds,
                session.mode,
                is_completed,
                topics_reviewed,
                refs_status,
                llm_status,
                json.dumps(normalized_topics),
            ),
        )

        # Upsert session_questions while preserving any existing llm_explanation
        cursor.execute(
            "SELECT question_id, llm_explanation, topic, question_topics_json FROM session_questions WHERE session_id = ?",
            (session.id,),
        )
        existing_q_map = {r["question_id"]: dict(r) for r in cursor.fetchall()}

        ans_dict = {str(ans.questionId): ans for ans in session.answers}
        question_params = []

        for idx, q in enumerate(session.questions):
            q_id = str(q.get("id") or q.get("question_id") or f"q_{idx}")
            ans = ans_dict.get(q_id)
            prev_q = existing_q_map.get(q_id, {})

            selected = (ans.selected or ans.selectedOption) if ans else None
            is_correct = (1 if ans.isCorrect else 0) if ans else 0
            timed_out = (1 if ans.timedOut else 0) if ans else 0
            time_spent = 0
            if ans:
                if ans.timeSpentMs:
                    time_spent = int(ans.timeSpentMs)
                elif ans.timeSpentSeconds:
                    time_spent = int(ans.timeSpentSeconds * 1000) if ans.timeSpentSeconds < 1000 else int(ans.timeSpentSeconds)

            q_ts = (ans.timestamp if (ans and ans.timestamp) else None) or ts
            q_text = q.get("question") or q.get("question_text") or ""
            opt_a = q.get("option_a") or q.get("opt_a") or ""
            opt_b = q.get("option_b") or q.get("opt_b") or ""
            opt_c = q.get("option_c") or q.get("opt_c") or ""
            opt_d = q.get("option_d") or q.get("opt_d") or ""
            correct_ans = q.get("correct_answer") or ""
            explanation = q.get("explanation") or ""
            llm_exp = q.get("llm_explanation") or prev_q.get("llm_explanation") or ""
            topic = q.get("topic") or q.get("topic_name") or prev_q.get("topic") or "General"

            q_topics = q.get("topics") or q.get("topic_details")
            if q_topics:
                q_topics_json = json.dumps(q_topics)
            else:
                q_topics_json = prev_q.get("question_topics_json") or "[]"

            question_params.append((
                session.id,
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

        if question_params:
            cursor.executemany(
                """
                INSERT OR REPLACE INTO session_questions (
                    session_id, question_id, selected_option, is_correct, timed_out,
                    time_spent_ms, timestamp, question_text, opt_a, opt_b, opt_c, opt_d,
                    correct_answer, explanation, llm_explanation, topic, question_topics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                question_params,
            )

        if topics_reviewed and (not existing or existing["refs_status"] == "none"):
            _enqueue_session_processing(conn, session.id)

        conn.commit()

    queue_worker._notify_session_status(session.id)
    return {
        "status": "success",
        "session_id": session.id,
        "topics_reviewed": bool(topics_reviewed),
        "refs_status": refs_status,
        "llm_status": llm_status,
    }


@router.get("/{session_id}/extract_draft_topics")
def get_or_extract_session_topics(session_id: str):
    """
    Returns the saved draft/confirmed topic list for a Hub card, or extracts topics
    from its session_questions if the set was created via 'Start Quiz Now' without review.
    """
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
        consolidated: Dict[str, Dict[str, Any]] = {}
        for q in q_rows:
            opts = {"A": q["opt_a"] or "", "B": q["opt_b"] or "", "C": q["opt_c"] or "", "D": q["opt_d"] or ""}
            ext = extract_topics_local(q["question_text"] or "", opts, q["explanation"] or "")
            for d in ext.get("topic_details", []):
                if not d.get("name") or d["name"] == "General Medical Concept":
                    continue
                norm = normalize_topic_key(d["name"])
                if norm not in consolidated:
                    consolidated[norm] = {
                        "name": d["name"],
                        "verified": bool(d.get("verified", False)),
                        "selected": bool(d.get("selected", False)),
                        "confidence": d.get("confidence", "low"),
                    }
                elif d.get("verified"):
                    consolidated[norm]["verified"] = True
                    consolidated[norm]["selected"] = True
                    consolidated[norm]["confidence"] = "high"
        saved_list = list(consolidated.values())

    return {
        "status": "success",
        "session_id": session_id,
        "title": s_row["custom_title"] or s_row["subject_name"],
        "topics_reviewed": bool(s_row["topics_reviewed"]),
        "topic_list": saved_list,
    }


@router.post("/{session_id}/update_topics")
def update_session_topics(session_id: str, req: UpdateSessionTopicsRequest):
    """
    Saves either a partial draft topic review (confirm=False, e.g. clicking outside popup)
    or confirms the topic list (confirm=True -> triggers Circle 2 & Circle 3 processing).
    """
    normalized = _normalize_topic_list(req.topic_list)
    is_confirmed = req.confirm or bool(req.topics_reviewed)
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, refs_status, llm_status FROM quiz_sessions WHERE id = ?", (session_id,))
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

    queue_worker._notify_session_status(session_id)
    return {
        "status": "success",
        "session_id": session_id,
        "topics_reviewed": bool(req.confirm),
        "refs_status": refs_status,
        "llm_status": llm_status,
        "topic_list": normalized,
    }


@router.post("/{session_id}/retry")
def retry_session_llm_tasks(session_id: str):
    """
    Retries failed LLM tasks for a Knowledge Hub session card.
    Resets status of FAILED tasks for session_id to PENDING and sets llm_status = 'processing'.
    """
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM quiz_sessions WHERE id = ?", (session_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Session not found")

        cursor.execute(
            """
            UPDATE task_queue
            SET status = 'PENDING', priority = 1, error_message = NULL, updated_at = unixepoch('now')
            WHERE session_id = ? AND status = 'FAILED'
            """,
            (session_id,),
        )
        reset_cnt = cursor.rowcount

        if reset_cnt == 0:
            _enqueue_session_processing(conn, session_id)
            reset_cnt = 1

        cursor.execute(
            "UPDATE quiz_sessions SET llm_status = 'processing' WHERE id = ?",
            (session_id,),
        )
        conn.commit()

    queue_worker._notify_session_status(session_id)
    return {"status": "success", "session_id": session_id, "retried_tasks": reset_cnt}


@router.post("/delete")
def delete_sessions(req: DeleteSessionsRequest):
    if not req.session_ids:
        return {"status": "success"}
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(req.session_ids))
        cursor.execute(f"DELETE FROM task_queue WHERE session_id IN ({placeholders})", req.session_ids)
        cursor.execute(f"DELETE FROM session_questions WHERE session_id IN ({placeholders})", req.session_ids)
        cursor.execute(f"DELETE FROM quiz_sessions WHERE id IN ({placeholders})", req.session_ids)
        conn.commit()
    return {"status": "success"}


@router.get("/history")
def get_session_history():
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

            for s in sessions:
                sid = s["id"]
                c_info = counts_by_sid.get(sid)
                s["completed_questions"] = int(c_info["answered_q"] or 0) if c_info else (s["correct_answers"] + s["wrong_answers"])
                if not s["total_questions"] and c_info:
                    s["total_questions"] = int(c_info["total_q"] or 0)

                s["custom_title"] = s.get("custom_title") or s.get("subject_name") or "Study Set"
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
                s["topics"] = selected_names if selected_names else q_topics_by_sid.get(sid, [])
                s["queue_info"] = get_session_queue_info(sid)

        return sessions


@router.get("/{session_id}/questions")
def get_session_questions(session_id: str):
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
            LEFT JOIN cached_syntheses cs ON LOWER(REPLACE(sq.topic, ' ', '-')) = cs.topic_key
            WHERE sq.session_id = ?
            ORDER BY sq.rowid ASC
            """,
            (session_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]

        # Enrich reference_links if question_topics_json has multiple topics
        for r in rows:
            if not r.get("reference_links"):
                try:
                    q_topics = json.loads(r.get("question_topics_json") or "[]")
                    for qt in q_topics:
                        t_name = qt["name"] if isinstance(qt, dict) else str(qt)
                        tk = t_name.lower().strip().replace(" ", "-")
                        cursor.execute("SELECT top_references FROM cached_syntheses WHERE topic_key = ?", (tk,))
                        cs_row = cursor.fetchone()
                        if cs_row and cs_row["top_references"]:
                            r["reference_links"] = cs_row["top_references"]
                            break
                except Exception:
                    pass

        return rows


@router.get("/{session_id}/topics")
def get_session_topics_and_notes(session_id: str):
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT topic_list_json, refs_status, llm_status FROM quiz_sessions WHERE id = ?", (session_id,))
        s_row = cursor.fetchone()

        selected_topics: List[str] = []
        if s_row and s_row["topic_list_json"]:
            try:
                t_list = json.loads(s_row["topic_list_json"])
                for item in t_list:
                    if isinstance(item, dict) and item.get("selected", True) and item.get("name"):
                        selected_topics.append(item["name"])
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
                r["topic_name"] for r in cursor.fetchall()
                if r["topic_name"] and r["topic_name"] != "General Medical Concept"
            ]

        llm_on = is_llm_processing_enabled()
        output = []
        seen = set()
        for t_name in selected_topics:
            t_key = t_name.lower().strip().replace(" ", "-")
            if not t_key or t_key in seen:
                continue
            seen.add(t_key)

            cursor.execute(
                """
                SELECT display_title, enhanced_explanation, distractor_analysis, unified_article, top_references
                FROM cached_syntheses
                WHERE topic_key = ?
                """,
                (t_key,),
            )
            c_row = cursor.fetchone()
            unified = (c_row["unified_article"] or "") if c_row else ""
            top_refs = (c_row["top_references"] or "") if c_row else ""

            if not llm_on:
                status = "references_only" if top_refs else "pending"
            elif unified and "LM Studio processing skipped" not in unified:
                status = "completed"
            else:
                status = "pending"

            output.append({
                "topic_name": t_name,
                "display_title": (c_row["display_title"] if c_row else None) or t_name,
                "enhanced_explanation": (c_row["enhanced_explanation"] or "") if (c_row and llm_on) else "",
                "distractor_analysis": (c_row["distractor_analysis"] or "") if (c_row and llm_on) else "",
                "unified_article": unified if llm_on else "",
                "top_references": top_refs,
                "llm_status": status,
                "llm_enabled": llm_on,
            })

        return output


@router.get("/{session_id}/inspector")
def get_session_inspector_details(session_id: str):
    """
    Powers the Contextual 3rd-Column Right Sidebar when a Knowledge Hub card is selected:
    - Shows topics the user frequently gets wrong + option to create a question set on that topic
    - Shows when the question set was attempted & flags stale topics not revised in >7 days
    - Shows user proficiency across all topics discussed in the question set
    - Shows exact Processing Queue position & supports raising priority to #1
    """
    now_sec = time.time()
    stale_cutoff_sec = 7 * 86400  # 7 days

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

        # Fetch all questions in this session and global question history for topic proficiency
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
            1 for q in session_qs
            if q["selected_option"] is not None and str(q["selected_option"]).strip() != ""
        )

        # Collect topics covered in this card
        card_topics: List[str] = []
        if session_dict.get("topic_list_json"):
            try:
                for item in json.loads(session_dict["topic_list_json"]):
                    if isinstance(item, dict) and item.get("selected", True) and item.get("name"):
                        card_topics.append(item["name"])
                    elif isinstance(item, str) and item.strip():
                        card_topics.append(item.strip())
            except Exception:
                pass

        for q in session_qs:
            if q["topic"] and q["topic"] != "General Medical Concept" and q["topic"] not in card_topics:
                card_topics.append(q["topic"])

        # Compute global historical stats per topic across all session_questions
        cursor.execute(
            """
            SELECT topic, selected_option, is_correct, timestamp
            FROM session_questions
            WHERE topic IS NOT NULL AND topic != ''
            """
        )
        global_topic_stats = defaultdict(lambda: {"attempted": 0, "correct": 0, "wrong": 0, "last_ts": 0.0})
        for r in cursor.fetchall():
            t = r["topic"]
            ts_val = float(r["timestamp"] or 0.0)
            ts_sec = ts_val / 1000.0 if ts_val > 1e11 else ts_val
            st = global_topic_stats[t]
            if r["selected_option"]:
                st["attempted"] += 1
                if int(r["is_correct"] or 0) == 1:
                    st["correct"] += 1
                else:
                    st["wrong"] += 1
                if ts_sec > st["last_ts"]:
                    st["last_ts"] = ts_sec

        topic_proficiency = []
        weak_topics = []
        stale_topics = []

        for t_name in dict.fromkeys(card_topics):
            st = global_topic_stats.get(t_name, {"attempted": 0, "correct": 0, "wrong": 0, "last_ts": 0.0})
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

            days_ago = round((now_sec - last_ts) / 86400.0, 1) if last_ts > 0 else None
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
        last_att_sec = last_att_raw / 1000.0 if last_att_raw > 1e11 else last_att_raw
        session_days_ago = round((now_sec - last_att_sec) / 86400.0, 1) if last_att_sec > 0 else None

        queue_info = get_session_queue_info(session_id)

        return {
            "status": "success",
            "session_id": session_id,
            "title": session_dict.get("custom_title") or session_dict.get("subject_name") or "Study Set",
            "subject_name": session_dict.get("subject_name") or "General",
            "date": session_dict.get("date"),
            "timestamp": session_dict.get("timestamp"),
            "last_attempted": last_att_raw,
            "days_since_attempt": session_days_ago,
            "completed_questions": completed_q,
            "total_questions": session_dict.get("total_questions") or len(session_qs),
            "accuracy": round(float(session_dict.get("accuracy") or 0.0), 1),
            "topics_reviewed": bool(session_dict.get("topics_reviewed")),
            "refs_status": session_dict.get("refs_status") or "none",
            "llm_status": session_dict.get("llm_status") or "none",
            "weak_topics": weak_topics,
            "stale_topics": stale_topics,
            "topic_proficiency": topic_proficiency,
            "queue_info": queue_info,
        }
