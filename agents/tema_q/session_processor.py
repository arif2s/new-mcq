import asyncio
import json
import logging
from core.database import get_db_connection
from agents.tema_q.pipeline import extract_topics_from_mcq
from core.queue_manager import queue_worker

logger = logging.getLogger(__name__)

async def process_session_questions(session_id: str):
    """
    Extracts topics for all questions in a session and queues them for synthesis.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT question_id, question_text, opt_a, opt_b, opt_c, opt_d FROM session_questions WHERE session_id = ?", (session_id,))
        questions = cursor.fetchall()

    for q in questions:
        options = {"A": q["opt_a"], "B": q["opt_b"], "C": q["opt_c"], "D": q["opt_d"]}
        topics_data = await extract_topics_from_mcq(q["question_text"], options)

        main_topic = topics_data.get("main_topic", "")
        all_topics = topics_data.get("all_topics", [])

        if main_topic and main_topic not in all_topics:
            all_topics.append(main_topic)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            for topic in all_topics:
                is_main = 1 if topic == main_topic else 0
                cursor.execute("""
                    INSERT OR IGNORE INTO extracted_topics (question_id, topic_name, is_main_topic)
                    VALUES (?, ?, ?)
                """, (q["question_id"], topic, is_main))

                # Queue the topic for synthesis if not already cached/queued
                cursor.execute("SELECT 1 FROM cached_syntheses WHERE topic_key = ?", (topic.lower().replace(" ", "-"),))
                if not cursor.fetchone():
                    cursor.execute("SELECT 1 FROM task_queue WHERE task_type = 'synthesis' AND payload LIKE ?", (f'%"{topic}"%',))
                    if not cursor.fetchone():
                        cursor.execute("""
                            INSERT INTO task_queue (task_type, payload)
                            VALUES (?, ?)
                        """, ("synthesis", json.dumps({"topic": topic})))
            conn.commit()
