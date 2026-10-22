"""
RAG task queue state management, topic ignoring, priority overrides, and queue clear/retry.
Enforces zero VRAM usage, canonical lowercase statuses, Invariant 14 deduplication, and WebSocket sync.
"""

from __future__ import annotations

from contextlib import closing
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel

from agents.tema_q.topic_extractor import (
    add_ignored_topic,
    extract_topics_local,
    get_ignored_topics_set,
    normalize_topic_key,
    remove_ignored_topic,
)
from core.database import get_db_connection
from core.queue_manager import (
    get_session_queue_info,
    is_llm_processing_enabled,
    prioritize_session_tasks,
)
from server.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/queue", tags=["queue"])


def _to_synthesis_key(text: str) -> str:
    norm = normalize_topic_key(text)
    if not norm:
        norm = (text or "").strip().lower()
    return re.sub(r"[\s_]+", "-", norm)


def _has_valid_references(raw: Any) -> bool:
    """Polymorphic reference validator satisfying Invariant 13."""
    if not raw:
        return False
    data = raw
    if isinstance(raw, str):
        raw_str = raw.strip()
        if not raw_str or raw_str in ("{}", "[]", "null"):
            return False
        try:
            data = json.loads(raw_str)
        except Exception:
            return False

    if isinstance(data, list):
        return len(data) > 0
    if isinstance(data, dict):
        if any(k in data for k in ("title", "file_path", "snippet")):
            return True
        return any(
            isinstance(v, (list, dict, set)) and len(v) > 0
            for v in data.values()
        )
    return False


def _has_valid_synthesis_article(raw_article: Any) -> bool:
    """Verifies whether unified_article contains substantive synthesized content."""
    if not raw_article:
        return False
    art_str = str(raw_article).strip()
    if (
        len(art_str) <= 20
        or "LM Studio processing skipped" in art_str
        or "No local reference files found" in art_str
        or "No relevant documents passed" in art_str
        or "Please add reference material" in art_str
    ):
        return False
    try:
        parsed = json.loads(art_str)
        if isinstance(parsed, dict):
            pts = parsed.get("clinical_points")
            raw_txt = str(parsed.get("raw_text") or "").strip()
            return bool((isinstance(pts, list) and len(pts) > 0) or raw_txt)
    except Exception:
        return len(art_str) > 50
    return False


# ============================================================================
# 1. REQUEST & PAYLOAD SCHEMAS
# ============================================================================

class QuestionPayload(BaseModel):
    id: Optional[Union[str, int]] = ""
    question: str = ""
    topic_name: Optional[str] = "General"
    option_a: Optional[str] = ""
    option_b: Optional[str] = ""
    option_c: Optional[str] = ""
    option_d: Optional[str] = ""
    options: Optional[Union[Dict[str, str], List[str], Any]] = None
    correct_answer: Optional[str] = ""
    explanation: Optional[str] = ""

    def get_options_dict(self) -> Dict[str, str]:
        """Resolves options dictionary across dictionary, list, and explicit field formats."""
        if isinstance(self.options, dict):
            return {
                "A": str(self.options.get("A", self.option_a or "")),
                "B": str(self.options.get("B", self.option_b or "")),
                "C": str(self.options.get("C", self.option_c or "")),
                "D": str(self.options.get("D", self.option_d or "")),
            }
        elif isinstance(self.options, list):
            letters = ["A", "B", "C", "D"]
            opts = {}
            for idx, letter in enumerate(letters):
                if idx < len(self.options):
                    opts[letter] = str(self.options[idx])
                else:
                    opts[letter] = getattr(self, f"option_{letter.lower()}", "") or ""
            return opts

        return {
            "A": self.option_a or "",
            "B": self.option_b or "",
            "C": self.option_c or "",
            "D": self.option_d or "",
        }


class PrepareRequest(BaseModel):
    questions: List[QuestionPayload] = []
    use_zim_refinement: bool = False
    use_tantivy_refinement: bool = False


class IgnoreTopicRequest(BaseModel):
    topic: Optional[str] = ""
    topic_name: Optional[str] = ""

    def get_topic(self) -> str:
        return (self.topic or self.topic_name or "").strip()


class PrioritizeRequest(BaseModel):
    session_id: Optional[str] = None
    task_id: Optional[int] = None


class PrepareTopicsRequest(BaseModel):
    class PreparedQuestion(BaseModel):
        id: Optional[Union[str, int]] = ""
        question: str = ""
        option_a: Optional[str] = ""
        option_b: Optional[str] = ""
        option_c: Optional[str] = ""
        option_d: Optional[str] = ""
        options: Optional[Union[Dict[str, str], List[str], Any]] = None
        correct_answer: Optional[str] = ""
        explanation: Optional[str] = ""
        topics: List[str] = []
        topic_name: Optional[str] = ""

        def get_options_dict(self) -> Dict[str, str]:
            if isinstance(self.options, dict):
                return {
                    "A": str(self.options.get("A", self.option_a or "")),
                    "B": str(self.options.get("B", self.option_b or "")),
                    "C": str(self.options.get("C", self.option_c or "")),
                    "D": str(self.options.get("D", self.option_d or "")),
                }
            return {
                "A": self.option_a or "",
                "B": self.option_b or "",
                "C": self.option_c or "",
                "D": self.option_d or "",
            }

    questions: List[PreparedQuestion] = []
    session_id: Optional[str] = None


# ============================================================================
# 2. TOPIC EXTRACTION & VERIFICATION ENDPOINTS
# ============================================================================

@router.post("/extract_topics")
def extract_topics(req: PrepareRequest) -> Dict[str, Any]:
    """
    Used by the 'Confirm & Launch' Topic Verification Popup.
    Extracts and canonicalizes medical topics across the question set, classifying each as:
    - verified=True (confirmed UMLS/MeSH concepts, pre-selected)
    - verified=False (unconfirmed candidate terms, unselected)
    Cross-references cached_syntheses for existing dossiers and reference hits.
    """
    extractor_strategy = "medspacy"
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT value FROM user_state WHERE key = 'app_persistence'"
        )
        row = cursor.fetchone()
        if row:
            try:
                raw_val = row[0] if isinstance(row, tuple) else row["value"]
                app_state = (
                    json.loads(raw_val) if isinstance(raw_val, str) else raw_val
                )
                if isinstance(app_state, dict):
                    extractor_strategy = str(
                        app_state.get(
                            "extractorStrategy",
                            app_state.get("extractor_strategy", "medspacy"),
                        )
                        or "medspacy"
                    )
            except Exception:
                pass

    ignored_keys = get_ignored_topics_set()
    results: List[Dict[str, Any]] = []
    consolidated_map: Dict[str, Dict[str, Any]] = {}

    for idx, q in enumerate(req.questions):
        q_id = str(q.id or f"q_{idx}")
        options = q.get_options_dict()

        try:
            extracted_data = extract_topics_local(
                q.question,
                options,
                q.explanation or "",
                extractor_strategy,
                req.use_zim_refinement,
                req.use_tantivy_refinement,
            )
        except Exception as exc:
            logger.warning("Topic extraction error for question %s: %s", q_id, exc)
            extracted_data = {
                "main_topic": q.topic_name or "General Medical Concept",
                "all_topics": [q.topic_name]
                if q.topic_name
                else ["General Medical Concept"],
                "topic_details": [
                    {
                        "name": q.topic_name or "General Medical Concept",
                        "verified": False,
                        "selected": True,
                        "confidence": "low",
                    }
                ]
                if q.topic_name
                else [],
            }

        details = [
            d
            for d in extracted_data.get("topic_details", [])
            if d.get("name")
            and d.get("name") != "General Medical Concept"
            and normalize_topic_key(d["name"]) not in ignored_keys
        ]

        # Prioritize explicit CSV topic name if not generic
        if (
            q.topic_name
            and q.topic_name.strip()
            and q.topic_name.strip().lower()
            not in ("general", "all", "mixed", "mixed topics", "general medical concept")
        ):
            csv_t = q.topic_name.strip()
            csv_norm = normalize_topic_key(csv_t)
            if (
                csv_norm
                and csv_norm not in ignored_keys
                and not any(
                    normalize_topic_key(d["name"]) == csv_norm for d in details
                )
            ):
                details.insert(
                    0,
                    {
                        "name": csv_t,
                        "topic_name": csv_t,
                        "verified": True,
                        "selected": True,
                        "confidence": "high",
                    },
                )

        all_topics = [d["name"] for d in details] or ["General Medical Concept"]

        for d in details:
            norm = normalize_topic_key(d["name"])
            if not norm or norm in ignored_keys:
                continue
            if norm not in consolidated_map:
                consolidated_map[norm] = {
                    "name": d["name"],
                    "topic_name": d["name"],
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
            "csv_topic_name": q.topic_name or "General",
            "topic_name": q.topic_name or "General",
            "option_a": options.get("A", ""),
            "option_b": options.get("B", ""),
            "option_c": options.get("C", ""),
            "option_d": options.get("D", ""),
            "correct_answer": (q.correct_answer or "A").strip().upper(),
            "explanation": q.explanation or "",
            "topics": all_topics,
            "topic_details": details,
        })

    # Chunked multi-key lookup against cached_syntheses
    session_topics = list(consolidated_map.values())
    if session_topics:
        key_to_item: Dict[str, List[Dict[str, Any]]] = {}
        for item in session_topics:
            t_name = item["name"]
            slug_key = _to_synthesis_key(t_name)
            norm_key = normalize_topic_key(t_name)
            raw_key = t_name.lower().strip()
            for k in (slug_key, norm_key, raw_key):
                if k:
                    key_to_item.setdefault(k, []).append(item)

        all_keys = list(key_to_item.keys())
        if all_keys:
            chunk_size = 150
            with closing(get_db_connection()) as conn:
                cursor = conn.cursor()
                for i in range(0, len(all_keys), chunk_size):
                    chunk = all_keys[i : i + chunk_size]
                    placeholders = ",".join("?" * len(chunk))
                    cursor.execute(
                        f"SELECT topic_key, unified_article, top_references "
                        f"FROM cached_syntheses WHERE topic_key IN ({placeholders})",
                        chunk,
                    )
                    for r in cursor.fetchall():
                        matched_key = r["topic_key"]
                        has_refs = _has_valid_references(r["top_references"])
                        has_note = _has_valid_synthesis_article(r["unified_article"])
                        for item in key_to_item.get(matched_key, []):
                            if has_refs:
                                item["has_refs"] = True
                            if has_note:
                                item["existing_note"] = True

    session_topics.sort(
        key=lambda x: (0 if x["verified"] else 1, -x["count"], x["name"].lower())
    )

    return {
        "status": "success",
        "questions": results,
        "session_topics": session_topics,
    }


# ============================================================================
# 3. TOPIC IGNORE & SUPPRESSION ENDPOINTS
# ============================================================================

@router.post("/ignore_topic")
def ignore_topic(req: IgnoreTopicRequest) -> Dict[str, Any]:
    """
    Permanently adds a topic to ignored_topics and suppresses pending tasks.
    Updates any active task_queue rows to canonical status='ignored'.
    """
    topic_val = req.get_topic()
    if not topic_val:
        return {"status": "error", "message": "No topic specified."}

    added = add_ignored_topic(topic_val)
    norm_val = normalize_topic_key(topic_val)
    if added:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "DELETE FROM extracted_topics WHERE LOWER(topic_name) IN (LOWER(?), LOWER(?))",
                (topic_val, norm_val),
            )
            conn.execute(
                """
                UPDATE task_queue
                SET status = 'ignored', updated_at = unixepoch('now')
                WHERE LOWER(topic_name) IN (LOWER(?), LOWER(?))
                  AND LOWER(status) = 'pending'
                """,
                (topic_val, norm_val),
            )

    return {"status": "success" if added else "error", "topic": topic_val}


@router.delete("/ignore_topic")
@router.post("/unignore_topic")
def unignore_topic(
    topic: Optional[str] = Query(default=None),
    req: Optional[IgnoreTopicRequest] = Body(default=None),
) -> Dict[str, Any]:
    """Removes a topic from the permanent ignore list."""
    t_val = (topic or (req.get_topic() if req else "")).strip()
    if not t_val:
        return {"status": "error", "message": "No topic specified."}

    removed = remove_ignored_topic(t_val)
    return {"status": "success" if removed else "error", "topic": t_val}


@router.get("/ignored_topics")
def list_ignored_topics() -> Dict[str, Any]:
    """Retrieves all permanently ignored topics."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT topic_norm, original_text, created_at FROM ignored_topics ORDER BY created_at DESC"
        )
        return {
            "status": "success",
            "ignored_topics": [dict(r) for r in cursor.fetchall()],
        }


# ============================================================================
# 4. TASK QUEUE EXECUTION & PRIORITIZATION
# ============================================================================

@router.post("/prioritize")
def prioritize_queue_item(req: PrioritizeRequest) -> Dict[str, Any]:
    """
    Raises queue priority of an active session or specific task to #1 (P1).
    Broadcasts live WebSocket notification to connected queue listeners (Invariant 18).
    """
    updated = 0
    if req.session_id:
        updated = prioritize_session_tasks(req.session_id)
        if updated > 0:
            ws_manager.broadcast_task_update_sync(
                task_id=0,
                status="pending",
                session_id=req.session_id,
                progress=0,
            )
    elif req.task_id is not None:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE task_queue
                SET priority = 1, updated_at = unixepoch('now')
                WHERE task_id = ? AND LOWER(status) = 'pending'
                """,
                (req.task_id,),
            )
            updated = int(cursor.rowcount)

        if updated > 0:
            ws_manager.broadcast_task_update_sync(
                task_id=req.task_id,
                status="pending",
                progress=0,
            )

    queue_info = get_session_queue_info(req.session_id) if req.session_id else {}
    return {
        "status": "success",
        "updated_count": updated,
        "queue_info": queue_info,
    }


@router.post("/prepare")
@router.post("/prepare_questions")
def prepare_questions(req: PrepareTopicsRequest) -> Dict[str, Any]:
    """
    Ingests quiz session questions into mcq_bank, records extracted topics,
    and enqueues canonical 'SYNTHESIZE_TOPIC' (P2) tasks adhering to Invariant 14 deduplication.
    """
    if not req.questions:
        return {"status": "success", "tasks_enqueued": 0, "total_topics": 0}

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        # Check existing questions by exact text in bounded chunks
        question_texts = [
            q.question.strip() for q in req.questions if q.question and q.question.strip()
        ]
        existing_mcqs: Dict[str, int] = {}

        if question_texts:
            chunk_size = 150
            for i in range(0, len(question_texts), chunk_size):
                chunk = question_texts[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT id, question FROM mcq_bank WHERE question IN ({placeholders})",
                    chunk,
                )
                for row in cursor.fetchall():
                    existing_mcqs[row["question"]] = int(row["id"])

        ignored_norms = get_ignored_topics_set()
        mcq_ids_for_tasks: List[tuple[int, str]] = []

        for q in req.questions:
            if not q.question or not q.question.strip():
                continue

            mcq_id = None
            if q.id and str(q.id).isdigit():
                mcq_id = int(q.id)
            else:
                mcq_id = existing_mcqs.get(q.question.strip())

            topics_to_use = q.topics or ([q.topic_name] if q.topic_name else [])
            main_topic = (
                topics_to_use[0] if topics_to_use else "General Medical Concept"
            )

            if not mcq_id:
                opts = q.get_options_dict()
                corr = (q.correct_answer or "A").strip().upper()
                if corr not in ("A", "B", "C", "D"):
                    corr = "A"

                cursor.execute(
                    """
                    INSERT INTO mcq_bank (
                        source_file, source_tag, question, opt_a, opt_b, opt_c, opt_d, correct_opt, csv_explanation
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "quiz_session",
                        main_topic,
                        q.question.strip(),
                        opts.get("A", ""),
                        opts.get("B", ""),
                        opts.get("C", ""),
                        opts.get("D", ""),
                        corr,
                        q.explanation or "",
                    ),
                )
                mcq_id = int(cursor.lastrowid)
                existing_mcqs[q.question.strip()] = mcq_id

            for t_idx, topic in enumerate(topics_to_use):
                clean_t = str(topic or "").strip()
                if not clean_t or clean_t == "General Medical Concept":
                    continue

                norm = normalize_topic_key(clean_t)
                if not norm or norm in ignored_norms:
                    continue

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO extracted_topics (
                        question_id, topic_name, is_main_topic, verified, selected
                    )
                    VALUES (?, ?, ?, 1, 1)
                    """,
                    (str(mcq_id), clean_t, 1 if t_idx == 0 else 0),
                )
                mcq_ids_for_tasks.append((mcq_id, clean_t))

        new_tasks: List[tuple] = []
        if mcq_ids_for_tasks:
            seen_topics_low: Set[str] = set()

            for mcq_id, topic in mcq_ids_for_tasks:
                topic_low = topic.lower().strip()
                if topic_low in seen_topics_low:
                    continue

                # Skip if already synthesized in cached_syntheses
                h_key = _to_synthesis_key(topic)
                s_key = normalize_topic_key(topic)
                cursor.execute(
                    "SELECT unified_article FROM cached_syntheses WHERE topic_key IN (?, ?)",
                    (h_key, s_key),
                )
                if any(
                    _has_valid_synthesis_article(r["unified_article"])
                    for r in cursor.fetchall()
                ):
                    seen_topics_low.add(topic_low)
                    continue

                # Invariant 14: Index-assisted deduplication on (task_type, topic_name, status)
                cursor.execute(
                    """
                    SELECT 1 FROM task_queue
                    WHERE task_type = 'SYNTHESIZE_TOPIC'
                      AND topic_name = ?
                      AND status IN ('pending', 'processing')
                    LIMIT 1
                    """,
                    (topic,),
                )
                if cursor.fetchone():
                    seen_topics_low.add(topic_low)
                    continue

                new_tasks.append((
                    "SYNTHESIZE_TOPIC",
                    2,  # P2: Active Session
                    str(mcq_id),
                    req.session_id,
                    topic,
                    json.dumps({
                        "topic": topic,
                        "session_id": req.session_id,
                        "mcq_id": str(mcq_id),
                    }),
                    "pending",
                ))
                seen_topics_low.add(topic_low)

            if new_tasks:
                cursor.executemany(
                    """
                    INSERT INTO task_queue (
                        task_type, priority, mcq_id, session_id, topic_name, payload, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    new_tasks,
                )

        conn.commit()

    if new_tasks:
        ws_manager.broadcast_task_update_sync(
            task_id=0,
            status="pending",
            topic=f"{len(new_tasks)} session topics queued",
            session_id=req.session_id,
            progress=0,
        )

    return {
        "status": "success",
        "tasks_enqueued": len(new_tasks),
        "total_topics": len(mcq_ids_for_tasks),
    }


# ============================================================================
# 5. QUEUE STATUS, RETRY & MAINTENANCE ENDPOINTS
# ============================================================================

@router.post("/clear_failed")
@router.delete("/failed")
def clear_failed_tasks() -> Dict[str, Any]:
    """Purges all failed tasks from the task queue."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM task_queue WHERE LOWER(status) = 'failed'")
        deleted = int(cursor.rowcount)
    return {"status": "success", "cleared_count": deleted}


@router.post("/clear_completed")
def clear_completed_tasks() -> Dict[str, Any]:
    """Purges finished tasks to reduce queue table size."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM task_queue WHERE LOWER(status) = 'completed'")
        deleted = int(cursor.rowcount)
    return {"status": "success", "cleared_count": deleted}


@router.post("/retry_failed")
def retry_failed_tasks() -> Dict[str, Any]:
    """Resets failed tasks back to canonical status='pending' and clears error logs."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE task_queue
            SET status = 'pending', error_message = NULL, updated_at = unixepoch('now')
            WHERE LOWER(status) = 'failed'
            """
        )
        updated = int(cursor.rowcount)

    if updated > 0:
        ws_manager.broadcast_task_update_sync(
            task_id=0,
            status="pending",
            topic=f"Retrying {updated} failed tasks",
            progress=0,
        )

    return {"status": "success", "retried_count": updated}


@router.get("/status")
def get_queue_status() -> Dict[str, Any]:
    """
    Returns live task queue metrics, worker state, and active queue rows
    with dual-key ('id' and 'task_id') compatibility.
    """
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT value FROM user_state WHERE key = 'app_persistence'"
        )
        row = cursor.fetchone()
        limit = 100
        if row:
            try:
                raw_val = row[0] if isinstance(row, tuple) else row["value"]
                app_state = (
                    json.loads(raw_val) if isinstance(raw_val, str) else raw_val
                )
                if isinstance(app_state, dict):
                    limit = max(
                        10,
                        min(
                            500,
                            int(app_state.get("processingQueueHardLimit", 100)),
                        ),
                    )
            except Exception:
                pass

        cursor.execute(
            "SELECT LOWER(status) as st, COUNT(*) as cnt FROM task_queue GROUP BY LOWER(status)"
        )
        status_counts = {str(r["st"]): int(r["cnt"]) for r in cursor.fetchall()}

        pending_count = status_counts.get("pending", 0)
        processing_count = status_counts.get("processing", 0)
        completed_count = status_counts.get("completed", 0)
        failed_count = status_counts.get("failed", 0)
        ignored_count = status_counts.get("ignored", 0)
        total_count = sum(status_counts.values())

        cursor.execute(
            """
            SELECT t.task_id, t.task_type, t.priority, t.session_id, t.topic_name,
                   t.status, t.error_message, t.payload, m.question, t.updated_at
            FROM task_queue t
            LEFT JOIN mcq_bank m ON t.mcq_id = CAST(m.id AS TEXT)
            ORDER BY
                CASE LOWER(t.status)
                    WHEN 'processing' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'failed' THEN 2
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
                    label = (
                        p_obj.get("topic")
                        or p_obj.get("action")
                        or p_obj.get("session_id")
                        or ""
                    )
                except Exception:
                    pass
            if not label:
                label = f"{r['task_type']} (#{r['task_id']})"

            task_status = str(r["status"]).lower()
            t_id = int(r["task_id"])

            tasks_list.append({
                "id": t_id,
                "task_id": t_id,
                "task_type": str(r["task_type"]),
                "priority": int(r["priority"]),
                "session_id": r["session_id"],
                "topic_name": r["topic_name"] or label,
                "status": task_status,
                "error_message": r["error_message"],
                "question": label,
                "updated_at": r["updated_at"],
            })

        return {
            "status": "success",
            "llm_enabled": is_llm_processing_enabled(),
            "total_tasks": total_count,
            "pending_tasks": pending_count,
            "processing_tasks": processing_count,
            "completed_tasks": completed_count,
            "failed_tasks": failed_count,
            "ignored_tasks": ignored_count,
            "tasks": tasks_list,
        }