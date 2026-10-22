"""
core/queue_manager.py - Asynchronous priority task queue worker (P1/P2/P3) and WebSocket state broadcaster.
Enforces Invariant 1 (Zero VRAM), Invariant 14 (Queue Synthesis Deduplication),
Invariant 16 (Non-blocking Shutdown & Subsystem Cleanup), and Invariant 18 (Live WebSocket Broadcasts).
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import json as json_lib
import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from core.database import get_db_connection
from core.reporter import generate_topic_report

try:
    from server.ws_manager import manager as ws_manager
except ImportError:
    try:
        from server.ws_manager import ws_manager
    except ImportError:
        ws_manager = None

logger = logging.getLogger("medisearch.queue")


def is_llm_processing_enabled() -> bool:
    """Checks user_state -> app_persistence for llmEnabled (default True) and legacy skipLMStudio."""
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            row = cursor.fetchone()
            if row:
                raw_val = row[0] if isinstance(row, tuple) else row["value"]
                app_state = json_lib.loads(raw_val)
                if isinstance(app_state, dict):
                    if "llmEnabled" in app_state:
                        return bool(app_state["llmEnabled"])
                    if "skipLMStudio" in app_state:
                        return not bool(app_state["skipLMStudio"])
    except Exception as exc:
        logger.warning("Failed to read LLM processing state: %s", exc)
    return True


class PriorityQueueWorker:
    """Asynchronous priority task queue worker (P1/P2/P3) and WebSocket state broadcaster."""

    def __init__(self):
        self.running = False
        self._stop_event = threading.Event()
        self.process_thread: Optional[threading.Thread] = None
        self.prune_thread: Optional[threading.Thread] = None
        self.main_loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self) -> None:
        if not self.running:
            self.running = True
            self._stop_event.clear()
            if self.main_loop is None:
                try:
                    self.main_loop = asyncio.get_running_loop()
                except RuntimeError:
                    try:
                        self.main_loop = asyncio.get_event_loop()
                    except RuntimeError:
                        self.main_loop = None

            self._recover_interrupted_tasks()
            self.process_thread = threading.Thread(
                target=self._process_loop, daemon=True, name="PriorityQueueWorker-Process"
            )
            self.prune_thread = threading.Thread(
                target=self._prune_completed_tasks, daemon=True, name="PriorityQueueWorker-Prune"
            )
            self.process_thread.start()
            self.prune_thread.start()
            logger.info("PriorityQueueWorker started.")

    def stop(self) -> None:
        """Stops background threads cleanly (Invariant 16)."""
        self.running = False
        self._stop_event.set()
        logger.info("PriorityQueueWorker stopped.")

    def _recover_interrupted_tasks(self) -> None:
        """Resets tasks left in processing state from an abrupt shutdown back to pending."""
        try:
            with closing(get_db_connection()) as conn:
                conn.execute(
                    "UPDATE task_queue SET status = 'pending', updated_at = unixepoch('now') "
                    "WHERE LOWER(status) = 'processing'"
                )
        except Exception as exc:
            logger.warning("Could not recover interrupted tasks: %s", exc)

    def _broadcast_ws(self, coro: Any) -> None:
        """Safely dispatches a coroutine to the FastAPI main event loop (Invariant 18)."""
        if ws_manager and self.main_loop and not self.main_loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(coro, self.main_loop)
                return
            except Exception as exc:
                logger.debug("WebSocket broadcast failed to dispatch: %s", exc)

        try:
            if hasattr(coro, "close"):
                coro.close()
        except Exception:
            pass

    def _notify_session_status(self, session_id: str) -> None:
        """Pushes live 1-2-3 circle status updates for a Knowledge Hub card over WebSocket."""
        if not session_id or not ws_manager:
            return
        try:
            with closing(get_db_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT topics_reviewed, refs_status, llm_status FROM quiz_sessions WHERE id = ?",
                    (session_id,),
                )
                row = cursor.fetchone()
                if row and hasattr(ws_manager, "broadcast_session_update"):
                    self._broadcast_ws(
                        ws_manager.broadcast_session_update(
                            session_id=session_id,
                            topics_reviewed=bool(row["topics_reviewed"]),
                            refs_status=row["refs_status"],
                            llm_status=row["llm_status"],
                        )
                    )
        except Exception as exc:
            logger.debug("Failed to notify session status for %s: %s", session_id, exc)

    def _process_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while self.running:
            task_id = None
            session_id = None
            topic = ""
            try:
                task_data = self._claim_highest_priority_task()
                if not task_data:
                    if self._stop_event.wait(0.8):
                        break
                    continue

                (
                    task_id,
                    task_type,
                    priority,
                    mcq_id,
                    session_id,
                    topic_col,
                    payload_str,
                ) = task_data
                payload = json_lib.loads(payload_str) if payload_str else {}
                session_id = session_id or payload.get("session_id")
                topic = topic_col or payload.get("topic", "")
                task_type_upper = str(task_type).upper().strip()

                if ws_manager and hasattr(ws_manager, "broadcast_task_update"):
                    self._broadcast_ws(
                        ws_manager.broadcast_task_update(
                            task_id=task_id,
                            status="processing",
                            topic=topic,
                            session_id=session_id,
                            progress=0,
                        )
                    )

                # 1. CSV Bank Precomputation
                if (
                    task_type_upper == "BATCH_PRECOMPUTE"
                    and payload.get("action") == "load_csvs"
                ):
                    from server.routes_csv_loader import load_csvs_background

                    load_csvs_background()

                # 2. Session Pipeline (Circle 2 References -> Optional Circle 3 LLM Notes & Explanations)
                elif task_type_upper in ("SESSION_PROCESSING", "PROCESS_SESSION"):
                    from agents.tema_q.session_processor import process_session_questions

                    if session_id:
                        loop.run_until_complete(
                            process_session_questions(session_id, worker=self)
                        )

                # 3. Standalone Topic Synthesis or Session Topic Synthesis (Invariant 14)
                elif task_type_upper in (
                    "SESSION_EXPLAIN",
                    "SYNTHESIZE_TOPIC",
                    "SYNTHESIS",
                    "BATCH_PRECOMPUTE",
                ):
                    from agents.tema_q.pipeline import run_tema_q_synthesis
                    from parsers.tantivy_engine import search_index

                    if session_id:
                        self._set_session_stage(session_id, llm_status="processing")

                    mcq_context = payload.get("mcq_context", None)
                    search_results = search_index(topic, limit_per_source=3)
                    self._persist_initial_references(topic, search_results)

                    llm_on = is_llm_processing_enabled()
                    if llm_on and not self._is_topic_fully_processed(topic):
                        try:
                            synthesis_output = loop.run_until_complete(
                                run_tema_q_synthesis(
                                    topic, search_results, mcq_context, skip_lm=False
                                )
                            )
                        except TypeError:
                            synthesis_output = loop.run_until_complete(
                                run_tema_q_synthesis(topic, search_results, mcq_context)
                            )

                        try:
                            generate_topic_report(topic, search_results, synthesis_output)
                        except Exception as report_err:
                            logger.warning(
                                "Report generation skipped for '%s': %s", topic, report_err
                            )

                        self._persist_cached_synthesis(topic, synthesis_output, search_results)

                # 4. Per-MCQ Detailed Explanation Generation
                elif task_type_upper in ("MCQ_EXPLAIN", "EXPLAIN_MCQ"):
                    from agents.tema_q.pipeline import generate_mcq_explanation

                    if is_llm_processing_enabled():
                        q_id = payload.get("question_id") or mcq_id
                        q_data = payload.get("question_data", {})
                        if q_id and q_data:
                            if session_id:
                                self._set_session_stage(session_id, llm_status="processing")
                            exp_text = loop.run_until_complete(
                                generate_mcq_explanation(q_data)
                            )
                            if exp_text:
                                self._persist_mcq_explanation(session_id, str(q_id), exp_text)

                self._update_task_status(task_id, "completed")
                if session_id:
                    self._refresh_session_completion_state(session_id)

                if ws_manager and hasattr(ws_manager, "broadcast_task_update"):
                    self._broadcast_ws(
                        ws_manager.broadcast_task_update(
                            task_id=task_id,
                            status="completed",
                            topic=topic,
                            session_id=session_id,
                            progress=100,
                        )
                    )

            except Exception as exc:
                logger.error("Task %s failed: %s", task_id, exc, exc_info=True)
                if task_id is not None:
                    self._update_task_status(task_id, "failed", str(exc))
                    if session_id:
                        self._refresh_session_completion_state(session_id)
                    if ws_manager and hasattr(ws_manager, "broadcast_task_update"):
                        self._broadcast_ws(
                            ws_manager.broadcast_task_update(
                                task_id=task_id,
                                status="failed",
                                topic=topic,
                                session_id=session_id,
                                progress=0,
                            )
                        )

        try:
            loop.close()
        except Exception:
            pass

    def _set_session_stage(
        self,
        session_id: str,
        refs_status: Optional[str] = None,
        llm_status: Optional[str] = None,
    ) -> None:
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
        self._notify_session_status(session_id)

    def _refresh_session_completion_state(self, session_id: str) -> None:
        """Updates Knowledge Hub card llm_status when all session queue tasks conclude."""
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ? AND LOWER(status) IN ('pending', 'processing')",
                (session_id,),
            )
            remaining = int(cursor.fetchone()["cnt"] or 0)
            if remaining == 0:
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ? AND LOWER(status) = 'failed'",
                    (session_id,),
                )
                failed_cnt = int(cursor.fetchone()["cnt"] or 0)

                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ? AND LOWER(status) = 'completed'",
                    (session_id,),
                )
                completed_cnt = int(cursor.fetchone()["cnt"] or 0)

                cursor.execute("SELECT llm_status FROM quiz_sessions WHERE id = ?", (session_id,))
                s_row = cursor.fetchone()
                if s_row:
                    curr_status = str(s_row["llm_status"] or "none").lower()
                    if curr_status in ("processing", "ready", "failed", "partial"):
                        if failed_cnt > 0:
                            new_status = "partial" if completed_cnt > 0 else "failed"
                        else:
                            new_status = "ready"
                        cursor.execute(
                            "UPDATE quiz_sessions SET llm_status = ? WHERE id = ?",
                            (new_status, session_id),
                        )
        self._notify_session_status(session_id)

    def _persist_mcq_explanation(
        self, session_id: Optional[str], question_id: str, llm_explanation: str
    ) -> None:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            if session_id:
                cursor.execute(
                    "UPDATE session_questions SET llm_explanation = ? WHERE session_id = ? AND question_id = ?",
                    (llm_explanation, session_id, str(question_id)),
                )
            cursor.execute(
                "UPDATE csv_questions SET llm_explanation = ? WHERE id = ?",
                (llm_explanation, str(question_id)),
            )

    def _prune_completed_tasks(self) -> None:
        while self.running:
            try:
                if self._stop_event.wait(3600):
                    break
                self._execute_prune_query()
            except Exception as exc:
                logger.error("Error pruning task queue: %s", exc)

    def _execute_prune_query(self) -> None:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "DELETE FROM task_queue WHERE LOWER(status) IN ('completed', 'failed') AND updated_at < unixepoch('now', '-48 hours')"
            )

    def _claim_highest_priority_task(self) -> Optional[Tuple]:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE task_queue
                    SET status = 'processing', updated_at = unixepoch('now')
                    WHERE task_id = (
                        SELECT task_id FROM task_queue
                        WHERE LOWER(status) = 'pending'
                        ORDER BY priority ASC, task_id ASC
                        LIMIT 1
                    )
                    RETURNING task_id, task_type, priority, mcq_id, session_id, topic_name, payload
                """)
                result = cursor.fetchone()
                return tuple(result) if result else None
            except Exception:
                # Fallback for SQLite versions without RETURNING clause support
                cursor.execute("""
                    SELECT task_id FROM task_queue
                    WHERE LOWER(status) = 'pending'
                    ORDER BY priority ASC, task_id ASC
                    LIMIT 1
                """)
                row = cursor.fetchone()
                if not row:
                    return None
                t_id = row["task_id"]
                cursor.execute(
                    "UPDATE task_queue SET status = 'processing', updated_at = unixepoch('now') WHERE task_id = ?",
                    (t_id,),
                )
                cursor.execute(
                    "SELECT task_id, task_type, priority, mcq_id, session_id, topic_name, payload FROM task_queue WHERE task_id = ?",
                    (t_id,),
                )
                result = cursor.fetchone()
                return tuple(result) if result else None

    def _update_task_status(
        self, task_id: int, status: str, error_message: Optional[str] = None
    ) -> None:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "UPDATE task_queue SET status = ?, error_message = ?, updated_at = unixepoch('now') WHERE task_id = ?",
                (status.lower(), error_message, task_id),
            )

    def _persist_initial_references(self, topic: str, results: dict) -> None:
        if not topic:
            return
        with closing(get_db_connection()) as conn:
            topic_key = re.sub(r"[\s_]+", "-", topic.lower().strip())
            conn.execute(
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
                    json_lib.dumps(results),
                    1 if results.get("obsidian") else 0,
                    1 if results.get("pdf") else 0,
                    1 if results.get("zim") else 0,
                ),
            )

    def _is_topic_fully_processed(self, topic: str) -> bool:
        if not topic:
            return False
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            topic_key = re.sub(r"[\s_]+", "-", topic.lower().strip())
            cursor.execute(
                "SELECT unified_article FROM cached_syntheses WHERE topic_key = ?",
                (topic_key,),
            )
            row = cursor.fetchone()
            if row:
                article = row[0] if isinstance(row, tuple) else row["unified_article"]
                art_str = str(article or "").strip()
                if (
                    art_str
                    and "LM Studio processing skipped" not in art_str
                    and "Skipped LM Studio processing" not in art_str
                    and "Please add reference material" not in art_str
                    and "No relevant documents passed" not in art_str
                ):
                    return True
            return False

    def _persist_cached_synthesis(
        self, topic: str, synthesis: dict, results: dict
    ) -> None:
        if not topic:
            return
        with closing(get_db_connection()) as conn:
            top_refs = synthesis.get("top_references") or results
            variance_text = str(
                synthesis.get("variance_audit") or synthesis.get("discrepancies") or ""
            ).strip()
            conn.execute(
                """
                INSERT OR REPLACE INTO cached_syntheses (
                    topic_key, display_title, enhanced_explanation, distractor_analysis,
                    unified_article, variance_audit, top_references,
                    has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('now'))
                """,
                (
                    re.sub(r"[\s_]+", "-", topic.lower().strip()),
                    topic,
                    str(synthesis.get("enhanced_explanation", "")),
                    str(synthesis.get("distractor_analysis", "")),
                    str(synthesis.get("unified_article", "")),
                    variance_text,
                    json_lib.dumps(top_refs),
                    1 if results.get("obsidian") else 0,
                    1 if results.get("pdf") else 0,
                    1 if results.get("zim") else 0,
                ),
            )


def get_session_queue_info(session_id: str) -> Dict[str, Any]:
    """Returns queue position and task counts for a specific Knowledge Hub session card."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ? AND LOWER(status) = 'failed'",
            (session_id,),
        )
        failed_cnt = int(cursor.fetchone()["cnt"] or 0)

        cursor.execute(
            "SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ? AND LOWER(status) = 'completed'",
            (session_id,),
        )
        completed_cnt = int(cursor.fetchone()["cnt"] or 0)

        cursor.execute(
            "SELECT task_id, priority, status FROM task_queue WHERE session_id = ? AND LOWER(status) IN ('pending', 'processing') ORDER BY priority ASC, task_id ASC",
            (session_id,),
        )
        session_tasks = cursor.fetchall()
        if not session_tasks:
            return {
                "in_queue": False,
                "position": 0,
                "total_pending": 0,
                "session_pending_count": 0,
                "failed_count": failed_cnt,
                "completed_count": completed_cnt,
                "top_priority": None,
            }

        first_task = session_tasks[0]
        first_id = int(first_task["task_id"])
        first_prio = int(first_task["priority"])

        cursor.execute(
            "SELECT COUNT(*) as cnt FROM task_queue WHERE LOWER(status) IN ('pending', 'processing')"
        )
        total_pending = int(cursor.fetchone()["cnt"] or 0)

        cursor.execute(
            """
            SELECT COUNT(*) + 1 as pos FROM task_queue
            WHERE LOWER(status) IN ('pending', 'processing')
              AND (priority < ? OR (priority = ? AND task_id < ?))
            """,
            (first_prio, first_prio, first_id),
        )
        pos = int(cursor.fetchone()["pos"] or 1)

        return {
            "in_queue": True,
            "position": pos,
            "total_pending": total_pending,
            "session_pending_count": len(session_tasks),
            "failed_count": failed_cnt,
            "completed_count": completed_cnt,
            "top_priority": first_prio,
        }


def prioritize_session_tasks(session_id: str) -> int:
    """Bumps all pending tasks for a session to priority = 1 (top of queue)."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE task_queue SET priority = 1, updated_at = unixepoch('now') WHERE session_id = ? AND LOWER(status) = 'pending'",
            (session_id,),
        )
        return int(cursor.rowcount)


queue_worker = PriorityQueueWorker()