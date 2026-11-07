import json
import pytest
from fastapi.testclient import TestClient
from server.app import app
from core.database import initialize_database, get_db_connection, record_question_attempt, update_question_verified_topics
from core.scheduler import calculate_fsrs_next_interval, get_fsrs_parameters


@pytest.fixture(autouse=True)
def setup_db():
    initialize_database()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_question_metadata_recording():
    with get_db_connection() as conn:
        qid = "test_meta_q_100"
        conn.execute("DELETE FROM question_metadata WHERE question_id = ?", (qid,))
        conn.commit()

        # First attempt: wrong
        record_question_attempt(conn, qid, is_correct=False, verified_topics=["Endocrinology", "Diabetes"])
        row = conn.execute("SELECT * FROM question_metadata WHERE question_id = ?", (qid,)).fetchone()
        assert row is not None
        assert row["attempt_count"] == 1
        assert row["wrong_count"] == 1
        assert row["last_is_correct"] == 0
        assert "Diabetes" in row["verified_topics_json"]

        # Second attempt: correct
        record_question_attempt(conn, qid, is_correct=True)
        row = conn.execute("SELECT * FROM question_metadata WHERE question_id = ?", (qid,)).fetchone()
        assert row["attempt_count"] == 2
        assert row["wrong_count"] == 1
        assert row["last_is_correct"] == 1

        # Clean up
        conn.execute("DELETE FROM question_metadata WHERE question_id = ?", (qid,))
        conn.commit()


def test_spaced_repetition_config_api(client):
    # GET initial config
    res = client.get("/api/mcq/spaced_repetition_config")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "requested_retention" in data["config"]

    # POST new config
    update_res = client.post(
        "/api/mcq/spaced_repetition_config",
        json={"requested_retention": 0.88, "max_interval_days": 120},
    )
    assert update_res.status_code == 200
    cfg = update_res.json()["config"]
    assert cfg["requested_retention"] == 0.88
    assert cfg["max_interval_days"] == 120

    # Verify calculation adheres to max_interval_days and retention
    calc_res = calculate_fsrs_next_interval(
        rating=4,
        state=2,
        difficulty=3.0,
        stability=100.0,
        reps=5,
        lapses=0,
    )
    assert calc_res["interval_days"] <= 120


def test_review_question_set_sync_and_removal(client):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        test_qid = "test_sr_due_q_1"
        cursor.execute("DELETE FROM csv_questions WHERE id = ?", (test_qid,))
        cursor.execute("DELETE FROM session_questions WHERE question_id = ?", (test_qid,))
        cursor.execute(
            """
            INSERT INTO csv_questions (id, question, opt_a, opt_b, opt_c, opt_d, correct_answer, state, due_timestamp)
            VALUES (?, 'Test Cardiac Q', 'A', 'B', 'C', 'D', 'A', 1, unixepoch('now') - 100)
            """,
            (test_qid,),
        )
        conn.commit()

    # Call due summary to trigger sync
    res = client.get("/api/sessions/due_summary")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["total_due"] >= 1

    # Simulate answering the question correctly in review set
    ans_res = client.post(
        "/api/sessions/save",
        json={
            "id": "sess_spaced_repetition_due",
            "subjectName": "Review Set",
            "totalQuestions": 1,
            "questions": [{"id": test_qid, "question": "Test Cardiac Q"}],
            "answers": [{"questionId": test_qid, "selected": "A", "isCorrect": True}],
        },
    )
    assert ans_res.status_code == 200

    # Verify due_timestamp is pushed into the future
    with get_db_connection() as conn:
        row = conn.execute("SELECT due_timestamp, state FROM csv_questions WHERE id = ?", (test_qid,)).fetchone()
        assert row["state"] == 2
        import time
        assert row["due_timestamp"] > time.time()

        # Clean up
        conn.execute("DELETE FROM csv_questions WHERE id = ?", (test_qid,))
        conn.execute("DELETE FROM session_questions WHERE question_id = ?", (test_qid,))
        conn.commit()


def test_batch_update_verified_endpoints(client):
    res_refs = client.post("/api/mcq/batch_update_all_references")
    assert res_refs.status_code == 200
    assert "status" in res_refs.json()

    res_sums = client.post("/api/mcq/batch_update_all_summaries")
    assert res_sums.status_code == 200
    assert "status" in res_sums.json()
