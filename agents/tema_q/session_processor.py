"""
agents/tema_q/session_processor.py - Async session processor for background RAG and LLM task execution.
Enforces Invariant 1 (Zero VRAM), Invariant 10/13 (Polymorphic Reference Inspection),
Invariant 14 (Queue Synthesis Deduplication), and Invariant 18 (Live WebSocket Broadcasts).
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from agents.tema_q.topic_extractor import (
    extract_topics_local,
    get_ignored_topics_set,
    normalize_topic_key,
)
from core.database import get_db_connection
from core.queue_manager import is_llm_processing_enabled
from core.reporter import generate_topic_report
from parsers.tantivy_engine import search_index

logger = logging.getLogger("medisearch.session_processor")


def _broadcast_session_stage(
    session_id: str,
    refs_status: Optional[str] = None,
    llm_status: Optional[str] = None,
    worker: Optional[Any] = None,
) -> None:
    """Broadcasts live session status updates via worker or ws_manager (Invariant 18)."""
    if worker and hasattr(worker, "_notify_session_status"):
        try:
            worker._notify_session_status(session_id)
            return
        except Exception:
            pass

    try:
        from server.ws_manager import manager as ws_manager

        ws_manager.broadcast_session_update_sync(
            session_id,
            refs_status=refs_status,
            llm_status=llm_status,
        )
    except Exception as exc:
        logger.debug("WebSocket session broadcast skipped for %s: %s", session_id, exc)


def _update_session_stage_direct(
    session_id: str,
    refs_status: Optional[str] = None,
    llm_status: Optional[str] = None,
    worker: Optional[Any] = None,
) -> None:
    """Updates quiz_sessions refs_status and llm_status via worker or direct SQLite write."""
    if worker and hasattr(worker, "_set_session_stage"):
        worker._set_session_stage(
            session_id, refs_status=refs_status, llm_status=llm_status
        )
        return

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        if refs_status is not None and llm_status is not None:
            cursor.execute(
                "UPDATE quiz_sessions SET refs_status = ?, llm_status = ? WHERE id = ?",
                (refs_status, llm_status, session_id),
            )
        elif refs_status is not None:
            cursor.execute(
                "UPDATE quiz_sessions SET refs_status = ? WHERE id = ?",
                (refs_status, session_id),
            )
        elif llm_status is not None:
            cursor.execute(
                "UPDATE quiz_sessions SET llm_status = ? WHERE id = ?",
                (llm_status, session_id),
            )

    _broadcast_session_stage(
        session_id, refs_status=refs_status, llm_status=llm_status, worker=worker
    )


def _has_non_empty_references(raw_refs: Any) -> bool:
    """Polymorphically checks if top_references contains at least one hit (Invariant 10 & 13)."""
    if not raw_refs:
        return False
    parsed = raw_refs
    if isinstance(raw_refs, str):
        raw_str = raw_refs.strip()
        if not raw_str or raw_str in ("{}", "[]", "null"):
            return False
        try:
            parsed = json.loads(raw_str)
        except Exception:
            return False

    if isinstance(parsed, list):
        return len(parsed) > 0
    if isinstance(parsed, dict):
        return any(
            isinstance(v, list) and len(v) > 0 for v in parsed.values()
        )
    return False


def _has_synthesized_article(raw_article: Any) -> bool:
    """Verifies whether unified_article contains substantive synthesized content."""
    if not raw_article:
        return False
    art_str = str(raw_article).strip()
    if (
        not art_str
        or art_str in ("{}", "[]", "null")
        or "LM Studio processing skipped" in art_str
        or "No local reference files found" in art_str
        or "No relevant documents passed" in art_str
    ):
        return False

    try:
        parsed = json.loads(art_str)
        if isinstance(parsed, dict):
            pts = parsed.get("clinical_points")
            raw_txt = str(parsed.get("raw_text") or "").strip()
            return bool((isinstance(pts, list) and len(pts) > 0) or raw_txt)
    except Exception:
        return len(art_str) > 20
    return False


async def process_session_questions(
    session_id: str, worker: Optional[Any] = None
) -> None:
    """Coordinates the 3-stage pipeline for a Knowledge Hub Session Card:

    - Circle 1: Set Created & Topics Reviewed
    - Circle 2: Reference Resolution (decoupled search -> solid ready)
    - Circle 3: LLM Detailed MCQ Explanations + Topic Synthesis Notes
    """
    if not session_id:
        return

    await asyncio.to_thread(
        _update_session_stage_direct,
        session_id,
        "processing",
        None,
        worker,
    )

    def _fetch_session_and_questions() -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], str]:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, topics_reviewed, topic_list_json FROM quiz_sessions WHERE id = ?",
                (session_id,),
            )
            s_row = cursor.fetchone()

            cursor.execute(
                """
                SELECT question_id, question_text, opt_a, opt_b, opt_c, opt_d,
                       correct_answer, explanation, llm_explanation, topic, question_topics_json
                FROM session_questions
                WHERE session_id = ?
                """,
                (session_id,),
            )
            q_rows = [dict(r) for r in cursor.fetchall()]

            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            u_row = cursor.fetchone()
            strategy = "medspacy"
            if u_row:
                try:
                    st = json.loads(u_row[0] if isinstance(u_row, tuple) else u_row["value"])
                    if isinstance(st, dict):
                        strategy = str(st.get("extractorStrategy") or "medspacy")
                except Exception:
                    pass

        return (dict(s_row) if s_row else None), q_rows, strategy

    session_meta, questions, strategy = await asyncio.to_thread(
        _fetch_session_and_questions
    )
    if not session_meta or not questions:
        await asyncio.to_thread(
            _update_session_stage_direct,
            session_id,
            "ready",
            "none",
            worker,
        )
        return

    ignored_keys = await asyncio.to_thread(get_ignored_topics_set)

    # Extract user-selected topics from session metadata
    selected_topics_from_session: List[str] = []
    try:
        raw_tlist = json.loads(session_meta.get("topic_list_json") or "[]")
        if isinstance(raw_tlist, list):
            for item in raw_tlist:
                if isinstance(item, dict):
                    t_name = str(item.get("name") or item.get("topic_name") or "").strip()
                    if item.get("selected", True) and t_name:
                        if normalize_topic_key(t_name) not in ignored_keys:
                            selected_topics_from_session.append(t_name)
                elif isinstance(item, str) and item.strip():
                    t_name = item.strip()
                    if normalize_topic_key(t_name) not in ignored_keys:
                        selected_topics_from_session.append(t_name)
    except Exception as exc:
        logger.debug("Failed to parse topic_list_json: %s", exc)

    selected_set_lower: Set[str] = {t.lower() for t in selected_topics_from_session}
    all_selected_topics: List[str] = list(dict.fromkeys(selected_topics_from_session))

    # Phase 1: Topic Extraction & Question Synchronization
    def _sync_question_topics_and_get_unresolved() -> Tuple[List[str], List[str]]:
        llm_enabled = is_llm_processing_enabled()
        mcq_tasks_to_queue: List[tuple] = []

        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()

            for q in questions:
                q_id = str(q["question_id"])
                q_topics: List[str] = []
                try:
                    parsed_qt = json.loads(q.get("question_topics_json") or "[]")
                    if isinstance(parsed_qt, list):
                        for t in parsed_qt:
                            t_str = (
                                str(t.get("name") or t.get("topic_name") or "").strip()
                                if isinstance(t, dict)
                                else str(t).strip()
                            )
                            is_sel = t.get("selected", True) if isinstance(t, dict) else True
                            if t_str and is_sel and normalize_topic_key(t_str) not in ignored_keys:
                                q_topics.append(t_str)
                except Exception:
                    pass

                if not q_topics:
                    opts = {
                        "A": q.get("opt_a") or q.get("option_a") or "",
                        "B": q.get("opt_b") or q.get("option_b") or "",
                        "C": q.get("opt_c") or q.get("option_c") or "",
                        "D": q.get("opt_d") or q.get("option_d") or "",
                    }
                    exp_text = q.get("explanation") or q.get("csv_explanation") or ""
                    ext = extract_topics_local(
                        q.get("question_text", ""), opts, exp_text, strategy=strategy
                    )

                    ext_details = ext.get("topic_details", [])
                    for d in ext_details:
                        name = (
                            str(d.get("name") or d.get("topic_name") or "")
                            if isinstance(d, dict)
                            else str(d)
                        ).strip()
                        if not name or normalize_topic_key(name) in ignored_keys:
                            continue
                        is_sel = bool(d.get("selected", True)) if isinstance(d, dict) else True
                        if selected_set_lower:
                            if name.lower() in selected_set_lower:
                                q_topics.append(name)
                        elif is_sel:
                            q_topics.append(name)

                    if not q_topics and ext.get("main_topic"):
                        fallback_main = str(ext["main_topic"]).strip()
                        if normalize_topic_key(fallback_main) not in ignored_keys:
                            q_topics = [fallback_main]

                main_t = (
                    q_topics[0]
                    if q_topics
                    else (q.get("topic") or "General Medical Concept")
                )

                cursor.execute(
                    "UPDATE session_questions SET topic = ?, question_topics_json = ? "
                    "WHERE session_id = ? AND question_id = ?",
                    (main_t, json.dumps(q_topics), session_id, q_id),
                )

                for t_name in q_topics:
                    if (
                        not t_name
                        or t_name == "General Medical Concept"
                        or normalize_topic_key(t_name) in ignored_keys
                    ):
                        continue
                    if selected_set_lower and t_name.lower() not in selected_set_lower:
                        continue
                    if t_name not in all_selected_topics:
                        all_selected_topics.append(t_name)

                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO extracted_topics
                        (question_id, topic_name, is_main_topic, verified, selected)
                        VALUES (?, ?, ?, 1, 1)
                        """,
                        (q_id, t_name, 1 if t_name == main_t else 0),
                    )

                # Queue MCQ Explanation if enabled and not already generated
                if llm_enabled and not str(q.get("llm_explanation") or "").strip():
                    cursor.execute(
                        """
                        SELECT 1 FROM task_queue
                        WHERE task_type IN ('EXPLAIN_MCQ', 'MCQ_EXPLAIN')
                          AND session_id = ? AND (mcq_id = ? OR topic_name = ?)
                          AND status IN ('pending', 'processing')
                        LIMIT 1
                        """,
                        (session_id, q_id, f"MCQ:{q_id}"),
                    )
                    if not cursor.fetchone():
                        mcq_tasks_to_queue.append((
                            "EXPLAIN_MCQ",
                            2,
                            q_id,
                            session_id,
                            f"MCQ:{q_id}",
                            json.dumps({
                                "session_id": session_id,
                                "question_id": q_id,
                                "mcq_id": q_id,
                                "question_data": q,
                            }),
                            "pending",
                        ))

            if mcq_tasks_to_queue:
                cursor.executemany(
                    """
                    INSERT INTO task_queue
                    (task_type, priority, mcq_id, session_id, topic_name, payload, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    mcq_tasks_to_queue,
                )

            # Determine which topics require Tantivy/ZIM/LanceDB searches or LLM synthesis
            topics_needing_search: List[str] = []
            topics_needing_synthesis: List[str] = []

            for topic in all_selected_topics:
                if (
                    not topic
                    or topic == "General Medical Concept"
                    or normalize_topic_key(topic) in ignored_keys
                ):
                    continue

                topic_key = re.sub(r"[\s_]+", "-", topic.lower().strip())
                cursor.execute(
                    "SELECT unified_article, top_references FROM cached_syntheses WHERE topic_key = ?",
                    (topic_key,),
                )
                row = cursor.fetchone()

                has_refs = False
                fully_synthesized = False
                if row:
                    has_refs = _has_non_empty_references(row["top_references"])
                    fully_synthesized = _has_synthesized_article(row["unified_article"])

                if not has_refs:
                    topics_needing_search.append(topic)

                if llm_enabled and not fully_synthesized:
                    # Invariant 14: Deduplicate against active SYNTHESIZE_TOPIC tasks via idx_task_dedup
                    cursor.execute(
                        """
                        SELECT 1 FROM task_queue
                        WHERE task_type IN ('SYNTHESIZE_TOPIC', 'synthesis', 'SESSION_EXPLAIN')
                          AND topic_name = ?
                          AND status IN ('pending', 'processing')
                        LIMIT 1
                        """,
                        (topic,),
                    )
                    if not cursor.fetchone():
                        topics_needing_synthesis.append(topic)

        return topics_needing_search, topics_needing_synthesis

    topics_to_search, topics_to_synthesize = await asyncio.to_thread(
        _sync_question_topics_and_get_unresolved
    )

    # Phase 2: Decoupled Multi-Stage Hybrid Search (Executes with zero SQLite locks)
    def _execute_searches() -> Dict[str, dict]:
        search_results_map: Dict[str, dict] = {}
        for topic in topics_to_search:
            try:
                search_results_map[topic] = search_index(topic, limit_per_source=3)
            except Exception as exc:
                logger.warning("Hybrid search failed for '%s': %s", topic, exc)
                search_results_map[topic] = {"obsidian": [], "pdf": [], "zim": []}
        return search_results_map

    resolved_searches = await asyncio.to_thread(_execute_searches)

    # Phase 3: Fast Atomic Persistence & Session Status Finalization
    def _persist_results_and_finalize() -> str:
        llm_enabled = is_llm_processing_enabled()

        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()

            for topic, search_results in resolved_searches.items():
                topic_key = re.sub(r"[\s_]+", "-", topic.lower().strip())
                has_obsidian = 1 if search_results.get("obsidian") else 0
                has_pdf = 1 if search_results.get("pdf") else 0
                has_zim = 1 if search_results.get("zim") else 0

                cursor.execute(
                    """
                    INSERT INTO cached_syntheses (
                        topic_key, display_title, enhanced_explanation, distractor_analysis,
                        unified_article, variance_audit, top_references,
                        has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                    ) VALUES (?, ?, '', '', '', '', ?, ?, ?, ?, unixepoch('now'))
                    ON CONFLICT(topic_key) DO UPDATE SET
                        top_references = excluded.top_references,
                        has_obsidian_note = excluded.has_obsidian_note,
                        has_pdf_match = excluded.has_pdf_match,
                        has_zim_match = excluded.has_zim_match,
                        updated_at = unixepoch('now')
                    """,
                    (
                        topic_key,
                        topic,
                        json.dumps(search_results),
                        has_obsidian,
                        has_pdf,
                        has_zim,
                    ),
                )

                # Compile initial reference dossier so Hub links work immediately
                try:
                    generate_topic_report(
                        topic=topic,
                        search_results=search_results,
                        synthesis_output={
                            "topic_key": topic_key,
                            "display_title": topic,
                            "top_references": search_results,
                        },
                    )
                except Exception as rep_exc:
                    logger.debug("Initial report generation skipped for '%s': %s", topic, rep_exc)

            # Invariant 14: Re-verify active queue deduplication immediately before insert
            synthesis_tasks_to_queue: List[tuple] = []
            for topic in topics_to_synthesize:
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
                if not cursor.fetchone():
                    synthesis_tasks_to_queue.append((
                        "SYNTHESIZE_TOPIC",
                        2,
                        session_id,
                        topic,
                        json.dumps({"topic": topic, "session_id": session_id}),
                        "pending",
                    ))

            if synthesis_tasks_to_queue:
                cursor.executemany(
                    """
                    INSERT INTO task_queue (task_type, priority, session_id, topic_name, payload, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    synthesis_tasks_to_queue,
                )

            # Determine final session llm_status accurately
            cursor.execute(
                """
                SELECT COUNT(*) as cnt FROM task_queue
                WHERE session_id = ? AND status IN ('pending', 'processing')
                """,
                (session_id,),
            )
            remaining_pending = int(cursor.fetchone()["cnt"] or 0)

            if not llm_enabled:
                new_llm_status = "none"
            elif remaining_pending > 0:
                new_llm_status = "processing"
            else:
                new_llm_status = "ready"

            cursor.execute(
                "UPDATE quiz_sessions SET refs_status = 'ready', llm_status = ? WHERE id = ?",
                (new_llm_status, session_id),
            )
            return new_llm_status

    final_llm_status = await asyncio.to_thread(_persist_results_and_finalize)
    _broadcast_session_stage(
        session_id,
        refs_status="ready",
        llm_status=final_llm_status,
        worker=worker,
    )


async def process_session_llm_tasks(
    session_id: str, worker: Optional[Any] = None
) -> None:
    """Compatibility alias for process_session_questions (Section 3)."""
    await process_session_questions(session_id, worker=worker)


__all__ = ["process_session_questions", "process_session_llm_tasks"]
