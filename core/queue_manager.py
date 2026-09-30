import asyncio
import json
import logging
from typing import Optional, Tuple

from core.database import get_db_connection
from parsers.tantivy_engine import search_index
from agents.tema_q.pipeline import run_tema_q_synthesis
from core.reporter import generate_topic_report

# Optional integration with WebSocket manager if present in app context
try:
    from server.ws_manager import ws_manager
except ImportError:
    ws_manager = None

logger = logging.getLogger(__name__)

class AsyncPriorityQueueWorker:
    def __init__(self):
        self.running = False
        self.process_task: Optional[asyncio.Task] = None
        self.prune_task: Optional[asyncio.Task] = None

    def start(self):
        if not self.running:
            self.running = True
            self.process_task = asyncio.create_task(self._process_loop())
            self.prune_task = asyncio.create_task(self._prune_completed_tasks())
            logger.info("AsyncPriorityQueueWorker started.")

    async def stop(self):
        self.running = False
        tasks = [t for t in (self.process_task, self.prune_task) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("AsyncPriorityQueueWorker stopped.")

    async def _process_loop(self):
        while self.running:
            task_id = None
            try:
                task_data = await asyncio.to_thread(self._fetch_highest_priority_task)
                if not task_data:
                    await asyncio.sleep(1.0)
                    continue

                task_id, task_type, priority, mcq_id, payload_str = task_data
                payload = json.loads(payload_str)
                await asyncio.to_thread(self._update_task_status, task_id, "PROCESSING")

                if task_type in ("SESSION_EXPLAIN", "BATCH_PRECOMPUTE"):
                    topic = payload.get("topic", "")
                    mcq_context = payload.get("mcq_context", None)

                    # Sub-millisecond BM25 index query
                    search_results = await asyncio.to_thread(search_index, topic, limit_per_source=3)

                    # Multi-pass clinical synthesis
                    synthesis_output = await run_tema_q_synthesis(topic, search_results, mcq_context)

                    # Write reports & persistence in parallel worker threads
                    await asyncio.gather(
                        asyncio.to_thread(generate_topic_report, topic, search_results, synthesis_output),
                        asyncio.to_thread(self._persist_cached_synthesis, topic, synthesis_output, search_results),
                    )

                await asyncio.to_thread(self._update_task_status, task_id, "COMPLETED")

                if ws_manager:
                    await ws_manager.broadcast_task_update(task_id, "COMPLETED", payload.get("topic", ""))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Task {task_id} failed: {e}", exc_info=True)
                if task_id is not None:
                    await asyncio.to_thread(self._update_task_status, task_id, "FAILED", str(e))
                    if ws_manager:
                        await ws_manager.broadcast_task_update(task_id, "FAILED", "")

    async def _prune_completed_tasks(self):
        while self.running:
            try:
                await asyncio.sleep(3600)
                await asyncio.to_thread(self._execute_prune_query)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error pruning task queue: {e}")

    def _execute_prune_query(self):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM task_queue WHERE status IN ('COMPLETED', 'FAILED') AND updated_at < unixepoch('now', '-48 hours')"
            )
            conn.commit()

    def _fetch_highest_priority_task(self) -> Optional[Tuple]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_id, task_type, priority, mcq_id, payload FROM task_queue WHERE status = 'PENDING' ORDER BY priority ASC, task_id ASC LIMIT 1"
            )
            return cursor.fetchone()

    def _update_task_status(self, task_id: int, status: str, error_message: str = None):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE task_queue SET status = ?, error_message = ?, updated_at = unixepoch('now') WHERE task_id = ?",
                (status, error_message, task_id),
            )
            conn.commit()

    def _persist_cached_synthesis(self, topic: str, synthesis: dict, results: dict):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO cached_syntheses (
                    topic_key, display_title, enhanced_explanation, distractor_analysis,
                    unified_article, top_references, has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('now'))
                """,
                (
                    topic.lower().replace(" ", "-"),
                    topic,
                    synthesis.get("enhanced_explanation", ""),
                    synthesis.get("distractor_analysis", ""),
                    synthesis.get("unified_article", ""),
                    json.dumps(results),
                    1 if results.get("obsidian") else 0,
                    1 if results.get("pdf") else 0,
                    1 if results.get("zim") else 0,
                ),
            )
            conn.commit()

queue_worker = AsyncPriorityQueueWorker()
