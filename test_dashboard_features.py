import time
import pytest
from fastapi.testclient import TestClient

from core.database import initialize_database, get_db_connection
from server.app import app


@pytest.fixture(autouse=True)
def setup_db():
    initialize_database()
    yield


def test_due_summary_immediately_includes_wrong_and_due_questions():
    """Verify that wrongly answered MCQs are immediately included in the due review summary and consolidated session."""
    client = TestClient(app)
    now = time.time()

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM csv_questions WHERE id LIKE 'test_cooldown_%'")
        # Q1: recently wrong (state = 1)
        c.execute(
            """
            INSERT INTO csv_questions (
                id, question, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation,
                state, last_review, due_timestamp, topic_name
            ) VALUES ('test_cooldown_recent', 'Recent wrong Q', 'A', 'B', 'C', 'D', 'A', 'Exp',
                     1, ?, ?, 'Cardiology')
            """,
            (now - 10, now - 10),
        )

        # Q2: wrong 7 hours ago
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
    assert data["total_due"] >= 2
    assert data["wrong_count"] >= 2
    assert data["session_id"] == "sess_spaced_repetition_due"


def test_generate_set_unattempted_priority_and_fallback():
    """Verify that generate_set prioritizes unattempted questions and falls back to attempted when count requires."""
    client = TestClient(app)
    now = time.time()

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM csv_questions WHERE id LIKE 'test_gen_q_%'")
        c.execute("DELETE FROM session_questions WHERE question_id LIKE 'test_gen_q_%'")

        # Create dummy session for foreign key
        c.execute("DELETE FROM quiz_sessions WHERE id = 'prev_sess'")
        c.execute(
            """
            INSERT INTO quiz_sessions (
                id, subject_name, custom_title, date, timestamp, last_attempted,
                total_questions, correct_answers, wrong_answers, unanswered,
                score, max_score, accuracy, time_used_seconds, mode
            ) VALUES (
                'prev_sess', 'RareTopic', 'Rare Test', '2025-01-01', ?, ?,
                2, 2, 0, 0, 2, 2, 100.0, 60, 'learn'
            )
            """,
            (now - 86400, now - 86400),
        )

        # 2 attempted questions for topic "RareTopic"
        for i in range(2):
            c.execute(
                """
                INSERT INTO csv_questions (
                    id, question, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation,
                    state, reps, last_review, due_timestamp, topic_name, file_path
                ) VALUES (?, ?, 'A', 'B', 'C', 'D', 'A', 'Exp', 2, 2, ?, ?, 'RareTopic', 'RareTopic.csv')
                """,
                (f"test_gen_q_att_{i}", f"Attempted Q {i}", now - 86400 * (i + 1), now + 86400 * 10),
            )
            # Record an answer in session_questions
            c.execute(
                """
                INSERT INTO session_questions (
                    session_id, question_id, selected_option, is_correct, timestamp
                ) VALUES ('prev_sess', ?, 'A', 1, ?)
                """,
                (f"test_gen_q_att_{i}", now - 86400 * (i + 1)),
            )

        # 2 unattempted questions for topic "RareTopic"
        for i in range(2):
            c.execute(
                """
                INSERT INTO csv_questions (
                    id, question, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation,
                    state, reps, last_review, due_timestamp, topic_name, file_path
                ) VALUES (?, ?, 'A', 'B', 'C', 'D', 'A', 'Exp', 0, 0, NULL, 0, 'RareTopic', 'RareTopic.csv')
                """,
                (f"test_gen_q_unatt_{i}", f"Unattempted Q {i}"),
            )
        conn.commit()

    # Request 2 questions: should exclusively pick unattempted questions
    resp1 = client.post("/api/csv/generate_set", json={"topics": ["RareTopic"], "count": 2, "spaced_repetition": False})
    assert resp1.status_code == 200
    q_ids_1 = [q["id"] for q in resp1.json()["questions"]]
    assert len(q_ids_1) == 2
    assert all("unatt" in qid for qid in q_ids_1)

    # Request 4 questions: should include both unattempted and fallback to attempted questions
    resp2 = client.post("/api/csv/generate_set", json={"topics": ["RareTopic"], "count": 4, "spaced_repetition": False})
    assert resp2.status_code == 200
    q_ids_2 = [q["id"] for q in resp2.json()["questions"]]
    assert len(q_ids_2) == 4
    # Unattempted questions must appear before attempted questions in the list
    assert "unatt" in q_ids_2[0]
    assert "unatt" in q_ids_2[1]
    assert "att" in q_ids_2[2]
    assert "att" in q_ids_2[3]


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


def test_dashboard_prompts_due_review_banner_format():
    """Verify that dashboard_prompts generates 'n questions left to review' due banner."""
    client = TestClient(app)
    now = time.time()

    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM csv_questions WHERE id LIKE 'test_due_format_%'")
        for i in range(3):
            c.execute(
                """
                INSERT INTO csv_questions (
                    id, question, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation,
                    state, last_review, due_timestamp, topic_name
                ) VALUES (?, 'Due Q', 'A', 'B', 'C', 'D', 'A', 'Exp', 1, ?, ?, 'Cardiology')
                """,
                (f"test_due_format_{i}", now - 10, now - 10),
            )
        conn.commit()

    resp = client.get("/api/sessions/dashboard_prompts")
    assert resp.status_code == 200
    data = resp.json()
    banners = data["banners"]
    due_banners = [b for b in banners if b["type"] == "due_review"]
    assert len(due_banners) == 1
    assert "left to review" in due_banners[0]["title"]
    assert due_banners[0]["count"] >= 3
