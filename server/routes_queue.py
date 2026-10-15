import json
from typing import List, Optional, Dict, Any
from contextlib import closing
from fastapi import APIRouter
from pydantic import BaseModel

from core.database import get_db_connection
from core.queue_manager import (
    is_llm_processing_enabled,
    get_session_queue_info,
    prioritize_session_tasks,
)
from agents.tema_q.topic_extractor import (
    extract_topics_local,
    add_ignored_topic,
    remove_ignored_topic,
    normalize_topic_key,
)

router = APIRouter(prefix="/api/queue", tags=["queue"])


class QuestionPayload(BaseModel):
    id: Optional[str] = ""
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


class IgnoreTopicRequest(BaseModel):
    topic: str


class PrioritizeRequest(BaseModel):
    session_id: Optional[str] = None
    task_id: Optional[int] = None


class PrepareTopicsRequest(BaseModel):
    class PreparedQuestion(BaseModel):
        id: Optional[str] = ""
        question: str
        option_a: str = ""
        option_b: str = ""
        option_c: str = ""
        option_d: str = ""
        correct_answer: str = ""
        explanation: str = ""
        topics: List[str]

    questions: List[PreparedQuestion]
    session_id: Optional[str] = None


@router.post("/extract_topics")
def extract_topics(req: PrepareRequest):
    """
    Used by the 'Confirm & Launch' Topic Verification Popup.
    Extracts and canonicalizes medical topics across the question set, classifying each as:
    - verified=True (black text, pre-selected in green)
    - verified=False (grey doubtful text, unselected by default)
    Also cross-references cached_syntheses to flag topics that already have notes.
    """
    extractor_strategy = "medspacy"
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
        row = cursor.fetchone()
        if row:
            try:
                app_state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                extractor_strategy = app_state.get("extractorStrategy", "medspacy")
            except Exception:
                pass

    results = []
    consolidated_map: Dict[str, Dict[str, Any]] = {}

    for idx, q in enumerate(req.questions):
        q_id = q.id or f"q_{idx}"
        options = {
            "A": q.option_a,
            "B": q.option_b,
            "C": q.option_c,
            "D": q.option_d,
        }
        extracted_data = extract_topics_local(
            q.question,
            options,
            q.explanation,
            extractor_strategy,
            req.use_zim_refinement,
            req.use_tantivy_refinement,
        )
        details = [
            d for d in extracted_data.get("topic_details", [])
            if d.get("name") and d.get("name") != "General Medical Concept"
        ]

        # Include CSV topic_name if valid and not already extracted
        if q.topic_name and q.topic_name.strip() and q.topic_name.lower() not in ("general", "all", "mixed", "mixed topics"):
            csv_t = q.topic_name.strip()
            csv_norm = normalize_topic_key(csv_t)
            if csv_norm and not any(normalize_topic_key(d["name"]) == csv_norm for d in details):
                details.insert(0, {
                    "name": csv_t,
                    "verified": True,
                    "selected": True,
                    "confidence": "high",
                })

        all_topics = [d["name"] for d in details] or ["General Medical Concept"]

        for d in details:
            norm = normalize_topic_key(d["name"])
            if not norm:
                continue
            if norm not in consolidated_map:
                consolidated_map[norm] = {
                    "name": d["name"],
                    "verified": bool(d.get("verified", False)),
                    "selected": bool(d.get("selected", False)),
                    "confidence": d.get("confidence", "low"),
                    "count": 1,
                    "question_ids": [q_id],
                    "existing_note": False,
                    "has_refs": False,
                }
            else:
                entry = consolidated_map[norm]
                entry["count"] += 1
                if q_id not in entry["question_ids"]:
                    entry["question_ids"].append(q_id)
                if d.get("verified"):
                    entry["verified"] = True
                    entry["selected"] = True
                    entry["confidence"] = "high"

        results.append({
            "id": q_id,
            "question": q.question,
            "topic_name": q.topic_name,
            "option_a": q.option_a,
            "option_b": q.option_b,
            "option_c": q.option_c,
            "option_d": q.option_d,
            "correct_answer": q.correct_answer,
            "explanation": q.explanation,
            "topics": all_topics,
            "topic_details": details,
        })

    # Check cached_syntheses for existing notes/references
    session_topics = list(consolidated_map.values())
    if session_topics:
        topic_keys = [t["name"].lower().strip().replace(" ", "-") for t in session_topics]
        placeholders = ",".join("?" * len(topic_keys))
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT topic_key, unified_article, top_references FROM cached_syntheses WHERE topic_key IN ({placeholders})",
                topic_keys,
            )
            synth_rows = {r["topic_key"]: r for r in cursor.fetchall()}

            for item in session_topics:
                tk = item["name"].lower().strip().replace(" ", "-")
                if tk in synth_rows:
                    sr = synth_rows[tk]
                    if sr["top_references"] and sr["top_references"] != "{}":
                        item["has_refs"] = True
                    if (
                        sr["unified_article"]
                        and "LM Studio processing skipped" not in sr["unified_article"]
                        and "No relevant documents passed" not in sr["unified_article"]
                    ):
                        item["existing_note"] = True

    session_topics.sort(key=lambda x: (0 if x["verified"] else 1, -x["count"], x["name"].lower()))

    return {
        "status": "success",
        "questions": results,
        "session_topics": session_topics,
    }


@router.post("/ignore_topic")
def ignore_topic(req: IgnoreTopicRequest):
    """
    Permanently adds a topic to ignored_topics (triggered via double-click in UI)
    so false-positive terms from medSpaCy/regex are never shown again.
    """
    added = add_ignored_topic(req.topic)
    if added:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "DELETE FROM extracted_topics WHERE LOWER(topic_name) = LOWER(?)",
                (req.topic.strip(),),
            )
            conn.commit()
    return {"status": "success" if added else "error", "topic": req.topic.strip()}


@router.delete("/ignore_topic")
def unignore_topic(topic: str):
    removed = remove_ignored_topic(topic)
    return {"status": "success" if removed else "error", "topic": topic}


@router.get("/ignored_topics")
def list_ignored_topics():
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT topic_norm, original_text, created_at FROM ignored_topics ORDER BY created_at DESC")
        return {
            "status": "success",
            "ignored_topics": [dict(r) for r in cursor.fetchall()],
        }


@router.post("/prioritize")
def prioritize_queue_item(req: PrioritizeRequest):
    """
    Raises the queue priority of a Knowledge Hub session card (or specific task) to #1.
    """
    updated = 0
    if req.session_id:
        updated = prioritize_session_tasks(req.session_id)
    elif req.task_id is not None:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE task_queue SET priority = 1, updated_at = unixepoch('now') WHERE task_id = ? AND status = 'PENDING'",
                (req.task_id,),
            )
            updated = cursor.rowcount
            conn.commit()

    queue_info = get_session_queue_info(req.session_id) if req.session_id else {}
    return {
        "status": "success",
        "updated_count": updated,
        "queue_info": queue_info,
    }


@router.post("/prepare")
def prepare_questions(req: PrepareTopicsRequest):
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        question_texts = [q.question for q in req.questions if q.question.strip()]
        existing_mcqs = {}

        if question_texts:
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
                if corr not in ("A", "B", "C", "D"):
                    corr = "A"
                cursor.execute(
                    """
                    INSERT INTO mcq_bank (source_file, source_tag, question, opt_a, opt_b, opt_c, opt_d, correct_opt, csv_explanation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "remote_csv",
                        main_topic,
                        q.question,
                        q.option_a,
                        q.option_b,
                        q.option_c,
                        q.option_d,
                        corr,
                        q.explanation,
                    ),
                )
                mcq_id = cursor.lastrowid

            for topic in q.topics:
                if topic and topic != "General Medical Concept":
                    mcq_ids_for_tasks.append((mcq_id, topic))

        if mcq_ids_for_tasks:
            cursor.execute(
                "SELECT topic_name FROM task_queue WHERE status IN ('PENDING', 'PROCESSING') AND topic_name IS NOT NULL"
            )
            active_topics = {r["topic_name"] for r in cursor.fetchall()}

            new_tasks = []
            for mcq_id, topic in mcq_ids_for_tasks:
                if topic not in active_topics:
                    new_tasks.append((
                        "synthesis",
                        2,
                        mcq_id,
                        req.session_id,
                        topic,
                        json.dumps({"topic": topic, "session_id": req.session_id}),
                    ))
                    active_topics.add(topic)

            if new_tasks:
                cursor.executemany(
                    """
                    INSERT INTO task_queue (task_type, priority, mcq_id, session_id, topic_name, payload)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    new_tasks,
                )

        conn.commit()
    return {"status": "success"}


@router.post("/clear_failed")
def clear_failed_tasks():
    with closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM task_queue WHERE status = 'FAILED'")
        conn.commit()
    return {"status": "success"}


@router.post("/retry_failed")
def retry_failed_tasks():
    with closing(get_db_connection()) as conn:
        conn.execute(
            "UPDATE task_queue SET status = 'PENDING', error_message = NULL, updated_at = unixepoch('now') WHERE status = 'FAILED'"
        )
        conn.commit()
    return {"status": "success"}


@router.get("/status")
def get_queue_status():
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
        row = cursor.fetchone()
        limit = 100
        if row:
            try:
                app_state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                limit = max(10, min(500, int(app_state.get("processingQueueHardLimit", 100))))
            except Exception:
                pass

        cursor.execute("SELECT status, COUNT(*) as cnt FROM task_queue GROUP BY status")
        status_counts = {r["status"]: int(r["cnt"]) for r in cursor.fetchall()}

        pending_count = status_counts.get("PENDING", 0)
        processing_count = status_counts.get("PROCESSING", 0)
        completed_count = status_counts.get("COMPLETED", 0)
        failed_count = status_counts.get("FAILED", 0)
        total_count = sum(status_counts.values())

        cursor.execute(
            """
            SELECT t.task_id, t.task_type, t.priority, t.session_id, t.topic_name,
                   t.status, t.error_message, t.payload, m.question, t.updated_at
            FROM task_queue t
            LEFT JOIN mcq_bank m ON t.mcq_id = m.id
            ORDER BY
                CASE t.status
                    WHEN 'PROCESSING' THEN 0
                    WHEN 'PENDING' THEN 1
                    WHEN 'FAILED' THEN 2
                    ELSE 3
                END ASC,
                t.priority ASC,
                t.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )

        tasks_list = []
        for r in cursor.fetchall():
            label = r["topic_name"] or r["question"] or ""
            if not label and r["payload"]:
                try:
                    p_obj = json.loads(r["payload"])
                    label = p_obj.get("topic") or p_obj.get("action") or p_obj.get("session_id") or ""
                except Exception:
                    pass
            if not label:
                label = f"{r['task_type']} (#{r['task_id']})"

            tasks_list.append({
                "id": r["task_id"],
                "task_type": r["task_type"],
                "priority": r["priority"],
                "session_id": r["session_id"],
                "topic_name": r["topic_name"] or label,
                "status": r["status"],
                "error_message": r["error_message"],
                "question": label,
                "updated_at": r["updated_at"],
            })

        return {
            "llm_enabled": is_llm_processing_enabled(),
            "total_tasks": total_count,
            "pending_tasks": pending_count,
            "processing_tasks": processing_count,
            "completed_tasks": completed_count,
            "failed_tasks": failed_count,
            "tasks": tasks_list,
        }
