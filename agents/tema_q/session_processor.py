import asyncio
import json
import logging
from contextlib import closing
from core.database import get_db_connection
from agents.tema_q.pipeline import extract_topics_from_mcq

logger = logging.getLogger(__name__)

async def process_session_questions(session_id: str):
    """
    Extracts topics for all questions in a session and queues them for synthesis.
    """

    def fetch_initial_state():
        """Hoisted state fetch to avoid N+1 queries during the question loop."""
        qs = []
        skip_lm_state = False
        strategy = 'regex'

        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()

            # Fetch all questions at once
            cursor.execute("SELECT question_id, question_text, opt_a, opt_b, opt_c, opt_d FROM session_questions WHERE session_id = ?", (session_id,))
            qs = cursor.fetchall()

            # Fetch persistence state exactly once
            cursor.execute("SELECT value FROM user_state WHERE key = 'app_persistence'")
            row = cursor.fetchone()
            if row:
                try:
                    app_state = json.loads(row[0] if isinstance(row, tuple) else row["value"])
                    skip_lm_state = app_state.get('skipLMStudio', False)
                    strategy = app_state.get('extractorStrategy', 'regex')
                except Exception as e:
                    logger.warning(f"Failed to parse app_persistence: {e}")

        return qs, skip_lm_state, strategy

    # 1. Unblock the event loop by fetching initial state in a thread
    questions, skip_lm, extractor_strategy = await asyncio.to_thread(fetch_initial_state)

    if not questions:
        return

    # Deferred imports to avoid circular dependencies at startup
    from parsers.tantivy_engine import search_index

    for q in questions:
        # Gracefully handle both raw tuples and sqlite3.Row objects
        q_id = q[0] if isinstance(q, tuple) else q["question_id"]
        q_text = q[1] if isinstance(q, tuple) else q["question_text"]
        options = {
            "A": q[2] if isinstance(q, tuple) else q["opt_a"],
            "B": q[3] if isinstance(q, tuple) else q["opt_b"],
            "C": q[4] if isinstance(q, tuple) else q["opt_c"],
            "D": q[5] if isinstance(q, tuple) else q["opt_d"]
        }

        topics_data = await extract_topics_from_mcq(q_text, options, skip_lm, extractor_strategy)

        main_topic = topics_data.get("main_topic", "")
        all_topics = topics_data.get("all_topics", [])

        if main_topic and main_topic not in all_topics:
            all_topics.append(main_topic)

        if not all_topics:
            continue

        def process_and_queue_topics(topics, question_id, main_t):
            """Processes topics synchronously but is designed to be pushed to a worker thread."""
            with closing(get_db_connection()) as conn:
                cursor = conn.cursor()

                for topic in topics:
                    is_main = 1 if topic == main_t else 0
                    cursor.execute("""
                        INSERT OR IGNORE INTO extracted_topics (question_id, topic_name, is_main_topic)
                        VALUES (?, ?, ?)
                    """, (question_id, topic, is_main))

                    topic_key = topic.lower().replace(" ", "-")
                    cursor.execute("SELECT unified_article FROM cached_syntheses WHERE topic_key = ?", (topic_key,))
                    row = cursor.fetchone()

                    fully_processed = False
                    if row:
                        article = row[0] if isinstance(row, tuple) else row["unified_article"]
                        if article and "Skipped LM Studio processing" not in article and "Please add reference material" not in article:
                            fully_processed = True

                    if not fully_processed:
                        # Sub-millisecond BM25 Tantivy query
                        search_results = search_index(topic, limit_per_source=3)

                        has_obsidian = 1 if search_results.get("obsidian") else 0
                        has_pdf = 1 if search_results.get("pdf") else 0
                        has_zim = 1 if search_results.get("zim") else 0

                        # Consolidate reference persistence here to prevent connection overlap/lock contention
                        cursor.execute("""
                            INSERT INTO cached_syntheses (
                                topic_key, display_title, enhanced_explanation, distractor_analysis,
                                unified_article, top_references, has_obsidian_note, has_pdf_match, has_zim_match, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('now'))
                            ON CONFLICT(topic_key) DO UPDATE SET
                                top_references = excluded.top_references,
                                has_obsidian_note = excluded.has_obsidian_note,
                                has_pdf_match = excluded.has_pdf_match,
                                has_zim_match = excluded.has_zim_match,
                                updated_at = unixepoch('now')
                        """, (
                            topic_key, topic, "", "", "",
                            json.dumps(search_results),
                            has_obsidian, has_pdf, has_zim
                        ))

                        # Safely enqueue task
                        cursor.execute("SELECT 1 FROM task_queue WHERE task_type = 'synthesis' AND payload LIKE ?", (f'%"{topic}"%',))
                        if not cursor.fetchone():
                            cursor.execute("""
                                INSERT INTO task_queue (task_type, payload)
                                VALUES (?, ?)
                            """, ("synthesis", json.dumps({"topic": topic})))

                conn.commit()

        # 2. Offload the synchronous DB writes and Tantivy searches to a background thread
        await asyncio.to_thread(process_and_queue_topics, all_topics, q_id, main_topic)
