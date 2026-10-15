import asyncio
import json
import logging
from typing import List
from contextlib import closing

from core.database import get_db_connection
from core.queue_manager import is_llm_processing_enabled
from agents.tema_q.topic_extractor import extract_topics_local
from parsers.tantivy_engine import search_index

logger = logging.getLogger(__name__)


async def process_session_questions(session_id: str, worker=None):
    """
    Coordinates the 3-stage pipeline for a Knowledge Hub Session Card:
    - Circle 1: Set Created & Topics Reviewed
    - Circle 2: Reference Resolution (blinking -> solid ready)
    - Circle 3: LLM Detailed MCQ Explanations + Topic Synthesis Notes (blinking -> solid ready when LLM enabled)
    """
    if worker:
        worker._set_session_stage(session_id, refs_status="processing")

    def _fetch_session_and_questions():
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, topics_reviewed, topic_list_json FROM quiz_sessions WHERE id = ?",
                (session_id,)
            )
            s_row = cursor.fetchone()

            cursor.execute(
                """
                SELECT question_id, question_text, opt_a, opt_b, opt_c, opt_d,
                       correct_answer, explanation, llm_explanation, topic, question_topics_json
                FROM session_questions
                WHERE session_id = ?
                """,
                (session_id,)
            )
            q_rows = [dict(r) for r in cursor.fetchall()]

            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            u_row = cursor.fetchone()
            strategy = "medspacy"
            if u_row:
                try:
                    st = json.loads(u_row[0] if isinstance(u_row, tuple) else u_row["value"])
                    strategy = st.get("extractorStrategy", "medspacy")
                except Exception:
                    pass

        return (dict(s_row) if s_row else None), q_rows, strategy

    session_meta, questions, strategy = await asyncio.to_thread(_fetch_session_and_questions)
    if not session_meta or not questions:
        if worker:
            worker._set_session_stage(session_id, refs_status="ready", llm_status="none")
        return

    # Determine selected topics from the user-reviewed topic list (if present)
    selected_topics_from_session: List[str] = []
    try:
        raw_tlist = json.loads(session_meta.get("topic_list_json") or "[]")
        for item in raw_tlist:
            if isinstance(item, dict):
                if item.get("selected", True) and item.get("name"):
                    selected_topics_from_session.append(item["name"])
            elif isinstance(item, str) and item.strip():
                selected_topics_from_session.append(item.strip())
    except Exception:
        pass

    selected_set_lower = {t.lower() for t in selected_topics_from_session}
    all_selected_topics: List[str] = list(dict.fromkeys(selected_topics_from_session))

    def _resolve_references_and_enqueue():
        llm_enabled = is_llm_processing_enabled()
        enqueued_llm_tasks = 0

        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()

            for q in questions:
                q_id = q["question_id"]
                q_topics: List[str] = []
                try:
                    parsed_qt = json.loads(q.get("question_topics_json") or "[]")
                    if isinstance(parsed_qt, list):
                        q_topics = [
                            (t["name"] if isinstance(t, dict) else str(t))
                            for t in parsed_qt
                            if (t.get("selected", True) if isinstance(t, dict) else True)
                        ]
                except Exception:
                    pass

                if not q_topics:
                    opts = {
                        "A": q.get("opt_a", ""),
                        "B": q.get("opt_b", ""),
                        "C": q.get("opt_c", ""),
                        "D": q.get("opt_d", ""),
                    }
                    ext = extract_topics_local(q.get("question_text", ""), opts, q.get("explanation", ""), strategy=strategy)
                    q_topics = [
                        d["name"] for d in ext.get("topic_details", [])
                        if (d["name"].lower() in selected_set_lower if selected_set_lower else d.get("selected", True))
                    ]
                    if not q_topics and ext.get("main_topic"):
                        q_topics = [ext["main_topic"]]

                main_t = q_topics[0] if q_topics else (q.get("topic") or "General Medical Concept")

                cursor.execute(
                    "UPDATE session_questions SET topic = ?, question_topics_json = ? WHERE session_id = ? AND question_id = ?",
                    (main_t, json.dumps(q_topics), session_id, q_id)
                )

                for t_name in q_topics:
                    if not t_name or t_name == "General Medical Concept":
                        continue
                    if selected_set_lower and t_name.lower() not in selected_set_lower:
                        continue
                    if t_name not in all_selected_topics:
                        all_selected_topics.append(t_name)

                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO extracted_topics (question_id, topic_name, is_main_topic, verified, selected)
                        VALUES (?, ?, ?, 1, 1)
                        """,
                        (q_id, t_name, 1 if t_name == main_t else 0)
                    )

                # Queue per-MCQ LLM detailed explanation if LLM processing is enabled
                if llm_enabled and not (q.get("llm_explanation") or "").strip():
                    cursor.execute(
                        "SELECT 1 FROM task_queue WHERE task_type = 'MCQ_EXPLAIN' AND session_id = ? AND topic_name = ? AND status IN ('PENDING', 'PROCESSING')",
                        (session_id, f"MCQ:{q_id}")
                    )
                    if not cursor.fetchone():
                        cursor.execute(
                            """
                            INSERT INTO task_queue (task_type, priority, session_id, topic_name, payload)
                            VALUES ('MCQ_EXPLAIN', 2, ?, ?, ?)
                            """,
                            (
                                session_id,
                                f"MCQ:{q_id}",
                                json.dumps({
                                    "session_id": session_id,
                                    "question_id": q_id,
                                    "question_data": q,
                                }),
                            )
                        )
                        enqueued_llm_tasks += 1

            # Resolve Tantivy/ZIM references for every selected topic (completes Circle 2)
            for topic in all_selected_topics:
                if not topic or topic == "General Medical Concept":
                    continue

                topic_key = topic.lower().strip().replace(" ", "-")
                cursor.execute(
                    "SELECT unified_article, top_references FROM cached_syntheses WHERE topic_key = ?",
                    (topic_key,)
                )
                row = cursor.fetchone()

                has_refs = False
                fully_synthesized = False
                if row:
                    existing_refs = row["top_references"]
                    existing_art = row["unified_article"]
                    if existing_refs and existing_refs != "{}":
                        has_refs = True
                    if (
                        existing_art
                        and "LM Studio processing skipped" not in existing_art
                        and "No relevant documents passed" not in existing_art
                    ):
                        fully_synthesized = True

                if not has_refs:
                    search_results = search_index(topic, limit_per_source=3)
                    has_obsidian = 1 if search_results.get("obsidian") else 0
                    has_pdf = 1 if search_results.get("pdf") else 0
                    has_zim = 1 if search_results.get("zim") else 0

                    cursor.execute(
                        """
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
                        """,
                        (topic_key, topic, json.dumps(search_results), has_obsidian, has_pdf, has_zim)
                    )

                if llm_enabled and not fully_synthesized:
                    cursor.execute(
                        "SELECT 1 FROM task_queue WHERE task_type = 'synthesis' AND topic_name = ? AND status IN ('PENDING', 'PROCESSING')",
                        (topic,)
                    )
                    if not cursor.fetchone():
                        cursor.execute(
                            """
                            INSERT INTO task_queue (task_type, priority, session_id, topic_name, payload)
                            VALUES ('synthesis', 2, ?, ?, ?)
                            """,
                            (session_id, topic, json.dumps({"topic": topic, "session_id": session_id}))
                        )
                        enqueued_llm_tasks += 1

            # Update Circle 2 (refs_status = 'ready') and Circle 3 (llm_status)
            new_llm_status = "none" if not llm_enabled else ("processing" if enqueued_llm_tasks > 0 else "ready")
            cursor.execute(
                "UPDATE quiz_sessions SET refs_status = 'ready', llm_status = ? WHERE id = ?",
                (new_llm_status, session_id)
            )
            conn.commit()

    await asyncio.to_thread(_resolve_references_and_enqueue)
    if worker:
        worker._notify_session_status(session_id)
