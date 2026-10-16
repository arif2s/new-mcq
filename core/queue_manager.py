import asyncio
import json as json_lib
import logging
import threading
import time
from typing import Optional, Tuple, Dict, Any
from contextlib import closing

from core.database import get_db_connection
from core.reporter import generate_topic_report

try:
    from server.ws_manager import ws_manager
except ImportError:
    ws_manager = None

logger = logging.getLogger(__name__)


def is_llm_processing_enabled() -> bool:
    """
    Checks user_state -> app_persistence for llmEnabled (default True)
    and legacy skipLMStudio (default False).
    """
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            row = cursor.fetchone()
            if row:
                raw_val = row[0] if isinstance(row, tuple) else row["value"]
                app_state = json_lib.loads(raw_val)
                if "llmEnabled" in app_state:
                    return bool(app_state["llmEnabled"])
                if "skipLMStudio" in app_state:
                    return not bool(app_state["skipLMStudio"])
    except Exception as e:
        logger.warning(f"Failed to read LLM processing state: {e}")
    return True


class PriorityQueueWorker:
    def __init__(self):
        self.running = False
        self.process_thread: Optional[threading.Thread] = None
        self.prune_thread: Optional[threading.Thread] = None
        self.main_loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self):
        if not self.running:
            self.running = True
            if self.main_loop is None:
                try:
                    self.main_loop = asyncio.get_running_loop()
                except RuntimeError:
                    self.main_loop = asyncio.get_event_loop()

            self._recover_interrupted_tasks()
            self.process_thread = threading.Thread(target=self._process_loop, daemon=True)
            self.prune_thread = threading.Thread(target=self._prune_completed_tasks, daemon=True)
            self.process_thread.start()
            self.prune_thread.start()
            logger.info("PriorityQueueWorker started.")

    def stop(self):
        self.running = False
        logger.info("PriorityQueueWorker stopped.")

    def _recover_interrupted_tasks(self):
        """Resets any tasks left in PROCESSING state from a prior abrupt shutdown back to PENDING."""
        try:
            with closing(get_db_connection()) as conn:
                conn.execute(
                    "UPDATE task_queue SET status = 'PENDING', updated_at = unixepoch('now') WHERE status = 'PROCESSING'"
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Could not recover interrupted tasks: {e}")

    def _broadcast_ws(self, coro):
        if ws_manager and self.main_loop and not self.main_loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(coro, self.main_loop)
            except Exception as e:
                logger.debug(f"WebSocket broadcast skipped: {e}")

    def _notify_session_status(self, session_id: str):
        """Pushes live 1-2-3 circle status updates for a Knowledge Hub card over WebSocket."""
        if not session_id or not ws_manager:
            return
        try:
            with closing(get_db_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT topics_reviewed, refs_status, llm_status FROM quiz_sessions WHERE id = ?",
                    (session_id,)
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
        except Exception as e:
            logger.debug(f"Failed to notify session status for {session_id}: {e}")

    def _process_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while self.running:
            task_id = None
            session_id = None
            topic = ""
            try:
                task_data = self._claim_highest_priority_task()
                if not task_data:
                    time.sleep(0.8)
                    continue

                task_id, task_type, priority, mcq_id, session_id, topic_col, payload_str = task_data
                payload = json_lib.loads(payload_str) if payload_str else {}
                session_id = session_id or payload.get("session_id")
                topic = topic_col or payload.get("topic", "")

                if ws_manager:
                    self._broadcast_ws(ws_manager.broadcast_task_update(task_id, "PROCESSING", topic))

                # 1. CSV Bank Precomputation
                if task_type == "BATCH_PRECOMPUTE" and payload.get("action") == "load_csvs":
                    from server.routes_csv_loader import load_csvs_background
                    load_csvs_background()

                # 2. Session Pipeline (Circle 2 References -> Optional Circle 3 LLM Notes & MCQ Explanations)
                elif task_type == "SESSION_PROCESSING":
                    from agents.tema_q.session_processor import process_session_questions
                    if session_id:
                        loop.run_until_complete(process_session_questions(session_id, worker=self))

                # 3. Standalone Topic Synthesis or Session Topic Synthesis
                elif task_type in ("SESSION_EXPLAIN", "BATCH_PRECOMPUTE", "synthesis"):
                    from parsers.tantivy_engine import search_index
                    from agents.tema_q.pipeline import run_tema_q_synthesis

                    if session_id:
                        self._set_session_stage(session_id, llm_status="processing")

                    mcq_context = payload.get("mcq_context", None)
                    search_results = search_index(topic, limit_per_source=3)
                    self._persist_initial_references(topic, search_results)

                    llm_on = is_llm_processing_enabled()
                    if llm_on and not self._is_topic_fully_processed(topic):
                        synthesis_output = loop.run_until_complete(
                            run_tema_q_synthesis(topic, search_results, mcq_context, skip_lm=False)
                        )
                        generate_topic_report(topic, search_results, synthesis_output)
                        self._persist_cached_synthesis(topic, synthesis_output, search_results)

                # 4. Per-MCQ Detailed Explanation Generation
                elif task_type == "MCQ_EXPLAIN":
                    from agents.tema_q.pipeline import generate_mcq_explanation
                    if is_llm_processing_enabled():
                        q_id = payload.get("question_id")
                        q_data = payload.get("question_data", {})
                        if q_id and q_data:
                            if session_id:
                                self._set_session_stage(session_id, llm_status="processing")
                            exp_text = loop.run_until_complete(generate_mcq_explanation(q_data))
                            if exp_text:
                                self._persist_mcq_explanation(session_id, q_id, exp_text)

                self._update_task_status(task_id, "COMPLETED")
                if session_id:
                    self._refresh_session_completion_state(session_id)

                if ws_manager:
                    self._broadcast_ws(ws_manager.broadcast_task_update(task_id, "COMPLETED", topic))

            except Exception as e:
                logger.error(f"Task {task_id} failed: {e}", exc_info=True)
                if task_id is not None:
                    self._update_task_status(task_id, "FAILED", str(e))
                    if session_id:
                        self._refresh_session_completion_state(session_id)
                    if ws_manager:
                        self._broadcast_ws(ws_manager.broadcast_task_update(task_id, "FAILED", topic))

        loop.close()

    def _set_session_stage(self, session_id: str, refs_status: Optional[str] = None, llm_status: Optional[str] = None):
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            if refs_status is not None and llm_status is not None:
                cursor.execute(
                    "UPDATE quiz_sessions SET refs_status = ?, llm_status = ? WHERE id = ?",
                    (refs_status, llm_status, session_id)
                )
            elif refs_status is not None:
                cursor.execute(
                    "UPDATE quiz_sessions SET refs_status = ? WHERE id = ?",
                    (refs_status, session_id)
                )
            elif llm_status is not None:
                cursor.execute(
                    "UPDATE quiz_sessions SET llm_status = ? WHERE id = ?",
                    (llm_status, session_id)
                )
            conn.commit()
        self._notify_session_status(session_id)

    def _refresh_session_completion_state(self, session_id: str):
        """Checks if any pending/processing tasks remain for session_id; if none, updates Circle 3 llm_status."""
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ? AND status IN ('PENDING', 'PROCESSING')",
                (session_id,)
            )
            remaining = cursor.fetchone()["cnt"]
            if remaining == 0:
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ? AND status = 'FAILED'",
                    (session_id,)
                )
                failed_cnt = cursor.fetchone()["cnt"]

                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ? AND status = 'COMPLETED'",
                    (session_id,)
                )
                completed_cnt = cursor.fetchone()["cnt"]

                cursor.execute("SELECT llm_status FROM quiz_sessions WHERE id = ?", (session_id,))
                s_row = cursor.fetchone()
                if s_row:
                    curr_status = (s_row["llm_status"] or "none").lower()
                    if curr_status in ("processing", "ready", "failed", "partial"):
                        if failed_cnt > 0:
                            new_status = "partial" if completed_cnt > 0 else "failed"
                        else:
                            new_status = "ready"
                        cursor.execute(
                            "UPDATE quiz_sessions SET llm_status = ? WHERE id = ?",
                            (new_status, session_id)
                        )
                        conn.commit()
        self._notify_session_status(session_id)

    def _persist_mcq_explanation(self, session_id: Optional[str], question_id: str, llm_explanation: str):
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            if session_id:
                cursor.execute(
                    "UPDATE session_questions SET llm_explanation = ? WHERE session_id = ? AND question_id = ?",
                    (llm_explanation, session_id, str(question_id))
                )
            cursor.execute(
                "UPDATE csv_questions SET llm_explanation = ? WHERE id = ?",
                (llm_explanation, str(question_id))
            )
            conn.commit()

    def _prune_completed_tasks(self):
        while self.running:
            try:
                time.sleep(3600)
                self._execute_prune_query()
            except Exception as e:
                logger.error(f"Error pruning task queue: {e}")

    def _execute_prune_query(self):
        with closing(get_db_connection()) as conn:
            conn.execute(
                "DELETE FROM task_queue WHERE status IN ('COMPLETED', 'FAILED') AND updated_at < unixepoch('now', '-48 hours')"
            )
            conn.commit()

    def _claim_highest_priority_task(self) -> Optional[Tuple]:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE task_queue
                SET status = 'PROCESSING', updated_at = unixepoch('now')
                WHERE task_id = (
                    SELECT task_id FROM task_queue
                    WHERE status = 'PENDING'
                    ORDER BY priority ASC, task_id ASC
                    LIMIT 1
                )
                RETURNING task_id, task_type, priority, mcq_id, session_id, topic_name, payload
            """)
            result = cursor.fetchone()
            conn.commit()
            return tuple(result) if result else None

    def _update_task_status(self, task_id: int, status: str, error_message: str = None):
        with closing(get_db_connection()) as conn:
            conn.execute(
                "UPDATE task_queue SET status = ?, error_message = ?, updated_at = unixepoch('now') WHERE task_id = ?",
                (status, error_message, task_id),
            )
            conn.commit()

    def _persist_initial_references(self, topic: str, results: dict):
        with closing(get_db_connection()) as conn:
            topic_key = topic.lower().strip().replace(" ", "-")
            conn.execute("""
                INSERT INTO cached_syntheses (
                    topic_key, display_title, enhanced_explanation, distractor_analysis,
                    unified_article, top_references, has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                ) VALUES (?, ?, '', '', '', ?, ?, ?, ?, unixepoch('now'))
                ON CONFLICT(topic_key) DO UPDATE SET
                    top_references = excluded.top_references,
                    has_obsidian_note = excluded.has_obsidian_note,
                    has_pdf_match = excluded.has_pdf_match,
                    has_zim_match = excluded.has_zim_match,
                    updated_at = unixepoch('now')
            """, (
                topic_key,
                topic,
                json_lib.dumps(results),
                1 if results.get("obsidian") else 0,
                1 if results.get("pdf") else 0,
                1 if results.get("zim") else 0,
            ))
            conn.commit()

    def _is_topic_fully_processed(self, topic: str) -> bool:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            topic_key = topic.lower().strip().replace(" ", "-")
            cursor.execute("SELECT unified_article FROM cached_syntheses WHERE topic_key = ?", (topic_key,))
            row = cursor.fetchone()
            if row:
                article = row[0] if isinstance(row, tuple) else row["unified_article"]
                if (
                    article
                    and "LM Studio processing skipped" not in article
                    and "Skipped LM Studio processing" not in article
                    and "Please add reference material" not in article
                    and "No relevant documents passed" not in article
                ):
                    return True
            return False

    def _persist_cached_synthesis(self, topic: str, synthesis: dict, results: dict):
        with closing(get_db_connection()) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cached_syntheses (
                    topic_key, display_title, enhanced_explanation, distractor_analysis,
                    unified_article, top_references, has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('now'))
                """,
                (
                    topic.lower().strip().replace(" ", "-"),
                    topic,
                    synthesis.get("enhanced_explanation", ""),
                    synthesis.get("distractor_analysis", ""),
                    synthesis.get("unified_article", ""),
                    json_lib.dumps(results),
                    1 if results.get("obsidian") else 0,
                    1 if results.get("pdf") else 0,
                    1 if results.get("zim") else 0,
                ),
            )
            conn.commit()


def get_session_queue_info(session_id: str) -> Dict[str, Any]:
    """
    Returns queue position and task counts for a specific Knowledge Hub session card.
    Used by the Right Sidebar Inspector.
    """
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ? AND status = 'FAILED'",
            (session_id,)
        )
        failed_cnt = cursor.fetchone()["cnt"]

        cursor.execute(
            "SELECT COUNT(*) as cnt FROM task_queue WHERE session_id = ? AND status = 'COMPLETED'",
            (session_id,)
        )
        completed_cnt = cursor.fetchone()["cnt"]

        cursor.execute(
            "SELECT task_id, priority, status FROM task_queue WHERE session_id = ? AND status IN ('PENDING', 'PROCESSING') ORDER BY priority ASC, task_id ASC",
            (session_id,)
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
                "top_priority": None
            }

        first_task = session_tasks[0]
        first_id = first_task["task_id"]
        first_prio = first_task["priority"]

        cursor.execute("SELECT COUNT(*) as cnt FROM task_queue WHERE status IN ('PENDING', 'PROCESSING')")
        total_pending = cursor.fetchone()["cnt"]

        cursor.execute(
            """
            SELECT COUNT(*) + 1 as pos FROM task_queue
            WHERE status IN ('PENDING', 'PROCESSING')
              AND (priority < ? OR (priority = ? AND task_id < ?))
            """,
            (first_prio, first_prio, first_id)
        )
        pos = cursor.fetchone()["pos"]

        return {
            "in_queue": True,
            "position": pos,
            "total_pending": total_pending,
            "session_pending_count": len(session_tasks),
            "top_priority": first_prio,
        }


def prioritize_session_tasks(session_id: str) -> int:
    """Bumps all PENDING tasks for a session to priority = 1 (Top of Queue)."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE task_queue SET priority = 1, updated_at = unixepoch('now') WHERE session_id = ? AND status = 'PENDING'",
            (session_id,)
        )
        updated = cursor.rowcount
        conn.commit()
        return updated


queue_worker = PriorityQueueWorker()
