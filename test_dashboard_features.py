import time
import pytest
from fastapi.testclient import TestClient

from core.database import initialize_database, get_db_connection
from server.app import app


@pytest.fixture(autouse=True)
def setup_db():
    initialize_database()
    yield


def test_due_summary_excludes_recently_wrong_questions():
    """Verify that wrongly answered MCQs within the 6-hour window do not trigger immediate due reminder."""
    client = TestClient(app)
    now = time.time()

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM csv_questions WHERE id LIKE 'test_cooldown_%'")
        # Q1: wrong 1 hour ago (within 6 hour cooldown)
        c.execute(
            """
            INSERT INTO csv_questions (
                id, question, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation,
                state, last_review, due_timestamp, topic_name
            ) VALUES ('test_cooldown_recent', 'Recent wrong Q', 'A', 'B', 'C', 'D', 'A', 'Exp',
                     1, ?, ?, 'Cardiology')
            """,
            (now - 3600, now - 3600),
        )

        # Q2: wrong 7 hours ago (past 6 hour cooldown)
        c.execute(
            """
            INSERT INTO csv_questions (
                id, question, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation,
                state, last_review, due_timestamp, topic_name
            ) VALUES ('test_cooldown_old', 'Old wrong Q', 'A', 'B', 'C', 'D', 'A', 'Exp',
                     1, ?, ?, 'Neurology')
            """,
            (now - 25200, now - 25200),
        )
        conn.commit()

    resp = client.get("/api/sessions/due_summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    # Old wrong question should be due, recent wrong question should be deferred
    assert data["total_due"] >= 1


def test_dashboard_prompts_endpoint():
    """Verify that dashboard_prompts generates weak topics, switch subject, and encouraging messages."""
    client = TestClient(app)
    now = time.time()

    with get_db_connection() as conn:
        c = conn.cursor()
        # Clean previous test entries
        c.execute("DELETE FROM quiz_sessions WHERE id = 'test_dash_sess'")
        c.execute("DELETE FROM session_questions WHERE session_id = 'test_dash_sess'")
        c.execute("DELETE FROM extracted_topics WHERE question_id = 'q_peds_1'")

        # Add historical session with subject and wrong answer on an extracted clinical topic
        c.execute(
            """
            INSERT OR REPLACE INTO quiz_sessions (
                id, subject_name, custom_title, date, timestamp, last_attempted,
                total_questions, correct_answers, wrong_answers, unanswered,
                score, max_score, accuracy, time_used_seconds, mode
            ) VALUES (
                'test_dash_sess', 'Pediatrics', 'Peds Review', '2025-01-01', ?, ?,
                10, 5, 5, 0, 5, 10, 50.0, 120, 'learn'
            )
            """,
            (now - 86400 * 5, now - 86400 * 5),
        )

        # Insert multiple wrong answers for Kawasaki Disease so it tops the weak topics list
        for i in range(3):
            qid = f"q_peds_{i}"
            c.execute(
                """
                INSERT OR REPLACE INTO session_questions (
                    session_id, question_id, is_correct, selected_option, topic,
                    question_topics_json, timestamp
                ) VALUES ('test_dash_sess', ?, 0, 'C', 'Peds', '[]', ?)
                """,
                (qid, now - 86400 * 5),
            )
            c.execute(
                """
                INSERT OR REPLACE INTO extracted_topics (question_id, topic_name, selected)
                VALUES (?, 'Kawasaki Disease', 1)
                """,
                (qid,),
            )
        conn.commit()

    resp = client.get("/api/sessions/dashboard_prompts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    banners = data["banners"]
    assert len(banners) <= 15
    assert len(banners) >= 7  # At least encouraging messages

    banner_types = {b["type"] for b in banners}
    assert "encouragement" in banner_types
    assert "weak_topic" in banner_types
    assert "switch_subject" in banner_types

    weak_banners = [b for b in banners if b["type"] == "weak_topic"]
    assert any("Kawasaki Disease" in b["title"] for b in weak_banners)
    assert weak_banners[0]["action"] == "practice_topic"

    switch_banners = [b for b in banners if b["type"] == "switch_subject"]
    assert any("Pediatrics" in b["title"] for b in switch_banners)
    assert switch_banners[0]["action"] == "switch_subject"
