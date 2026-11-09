import asyncio
from datetime import datetime
import json
import sqlite3
import pytest
from core.database import get_db_connection, initialize_database
from server.routes_sessions import get_checklist_analytics

@pytest.fixture(autouse=True)
def setup_db():
    initialize_database()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        now_ts = datetime.now().timestamp()
        today_date = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            """
            INSERT OR REPLACE INTO quiz_sessions (
                id, subject_name, custom_title, date, timestamp, total_questions, correct_answers, wrong_answers
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("sess_test_checklist_1", "Anatomy", "Anatomy Brachial Set", today_date, now_ts, 10, 8, 2)
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO session_questions (
                session_id, question_id, selected_option, is_correct, time_spent_ms, timestamp, question_text, topic, question_topics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("sess_test_checklist_1", "q_1", "A", 1, 5000, now_ts, "What nerve innervates deltoid?", "Brachial Plexus", json.dumps(["Brachial Plexus", "Axillary Nerve"]))
        )
        conn.commit()

def test_checklist_analytics_all():
    res = asyncio.run(get_checklist_analytics(scope_type="all", scope_value="", date_str=""))
    assert "today" in res
    assert "history_series" in res
    assert res["today"]["attempted"] >= 1
    assert res["today"]["correct"] >= 1
    assert res["today"]["accuracy"] > 0

def test_checklist_analytics_subject():
    res = asyncio.run(get_checklist_analytics(scope_type="subject", scope_value="Anatomy", date_str=""))
    assert res["scope_type"] == "subject"
    assert res["scope_value"] == "Anatomy"
    assert len(res["history_series"]) >= 1

def test_checklist_analytics_topic():
    res = asyncio.run(get_checklist_analytics(scope_type="topic", scope_value="Brachial", date_str=""))
    assert res["scope_type"] == "topic"
    assert len(res["history_series"]) >= 1

def test_checklist_analytics_keyword():
    res = asyncio.run(get_checklist_analytics(scope_type="keyword", scope_value="deltoid", date_str=""))
    assert res["scope_type"] == "keyword"
    assert len(res["history_series"]) >= 1
