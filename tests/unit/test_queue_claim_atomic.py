import sqlite3
from unittest.mock import patch
from core.queue_manager import PriorityQueueWorker, AsyncQueueWorker

class _FailingReturningCursor(sqlite3.Cursor):
    def execute(self, sql, *args, **kwargs):
        if "RETURNING" in sql:
            raise sqlite3.OperationalError("Simulated syntax error near RETURNING")
        return super().execute(sql, *args, **kwargs)

class _FailingReturningConnection(sqlite3.Connection):
    def cursor(self, *args, **kwargs):
        return super().cursor(factory=_FailingReturningCursor)

def _create_mock_conn(db_file, failing_returning=False):
    factory = _FailingReturningConnection if failing_returning else sqlite3.Connection
    conn = sqlite3.connect(str(db_file), isolation_level=None, check_same_thread=False, factory=factory)
    conn.row_factory = sqlite3.Row
    return conn

def test_atomic_task_claim_offline(tmp_path):
    db_file = tmp_path / "test_queue.db"
    conn = _create_mock_conn(db_file)
    conn.execute("""
        CREATE TABLE task_queue (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT, priority INTEGER, mcq_id TEXT,
            session_id TEXT, topic_name TEXT, payload TEXT,
            status TEXT DEFAULT 'pending', updated_at REAL
        )
    """)
    conn.execute(
        "INSERT INTO task_queue (task_type, priority, topic_name, payload, status) "
        "VALUES ('SYNTHESIZE_TOPIC', 1, 'Malaria', '{}', 'pending')"
    )
    conn.close()

    worker = PriorityQueueWorker()
    with patch("core.queue_manager.get_db_connection", side_effect=lambda: _create_mock_conn(db_file)):
        claimed_1 = worker._claim_highest_priority_task()
        claimed_2 = worker._claim_highest_priority_task()
    assert claimed_1 is not None
    assert claimed_2 is None

def test_async_worker_claim_offline(tmp_path):
    db_file = tmp_path / "test_async_queue.db"
    conn = _create_mock_conn(db_file)
    conn.execute("""
        CREATE TABLE task_queue (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT, priority INTEGER, mcq_id TEXT,
            session_id TEXT, topic_name TEXT, payload TEXT,
            status TEXT DEFAULT 'pending', updated_at REAL
        )
    """)
    conn.execute(
        "INSERT INTO task_queue (task_type, priority, topic_name, payload, status) "
        "VALUES ('PROCESS_SESSION', 1, 'Dengue', '{}', 'pending')"
    )
    conn.close()

    worker = AsyncQueueWorker()
    with patch("core.database.get_db_connection", side_effect=lambda: _create_mock_conn(db_file)):
        with patch("core.queue_manager.get_db_connection", side_effect=lambda: _create_mock_conn(db_file)):
            claimed_1 = worker._fetch_and_claim_task()
            claimed_2 = worker._fetch_and_claim_task()
    assert claimed_1 is not None
    assert claimed_1["task_id"] == 1
    assert claimed_2 is None

def test_claim_fallback_path_without_returning(tmp_path):
    db_file = tmp_path / "test_fallback_queue.db"
    conn = _create_mock_conn(db_file)
    conn.execute("""
        CREATE TABLE task_queue (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT, priority INTEGER, mcq_id TEXT,
            session_id TEXT, topic_name TEXT, payload TEXT,
            status TEXT DEFAULT 'pending', updated_at REAL
        )
    """)
    conn.execute(
        "INSERT INTO task_queue (task_type, priority, topic_name, payload, status) "
        "VALUES ('SYNTHESIZE_TOPIC', 1, 'Typhoid', '{}', 'pending')"
    )
    conn.close()

    worker = PriorityQueueWorker()
    with patch("core.queue_manager.get_db_connection", side_effect=lambda: _create_mock_conn(db_file, failing_returning=True)):
        claimed_1 = worker._claim_highest_priority_task()
        claimed_2 = worker._claim_highest_priority_task()
    assert claimed_1 is not None
    assert claimed_1[5] == "Typhoid"
    assert claimed_2 is None

def test_async_worker_fallback_path_without_returning(tmp_path):
    db_file = tmp_path / "test_async_fallback_queue.db"
    conn = _create_mock_conn(db_file)
    conn.execute("""
        CREATE TABLE task_queue (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT, priority INTEGER, mcq_id TEXT,
            session_id TEXT, topic_name TEXT, payload TEXT,
            status TEXT DEFAULT 'pending', updated_at REAL
        )
    """)
    conn.execute(
        "INSERT INTO task_queue (task_type, priority, topic_name, payload, status) "
        "VALUES ('PROCESS_SESSION', 1, 'Cholera', '{}', 'pending')"
    )
    conn.close()

    worker = AsyncQueueWorker()
    with patch("core.database.get_db_connection", side_effect=lambda: _create_mock_conn(db_file, failing_returning=True)):
        with patch("core.queue_manager.get_db_connection", side_effect=lambda: _create_mock_conn(db_file, failing_returning=True)):
            claimed_1 = worker._fetch_and_claim_task()
            claimed_2 = worker._fetch_and_claim_task()
    assert claimed_1 is not None
    assert claimed_1["task_id"] == 1
    assert claimed_2 is None

def test_quiz_sessions_schema_primary_key(tmp_path):
    from core.database import initialize_database, get_db_connection
    test_db = tmp_path / "test_init.db"
    with patch("core.database.DB_PATH", str(test_db)):
        initialize_database()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(quiz_sessions);")
            columns = cursor.fetchall()
            pk_cols = [col["name"] for col in columns if col["pk"] > 0]
            assert pk_cols == ["id"], f"Expected sole primary key ['id'], got {pk_cols}"

import json
import asyncio
from unittest.mock import AsyncMock, MagicMock
from core.queue_manager import ensure_queue_jobs_table, persist_job_state, queue_worker

def test_unified_crash_recovery_offline():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE task_queue (task_id INTEGER PRIMARY KEY, status TEXT, updated_at REAL)")
    conn.execute("INSERT INTO task_queue (task_id, status, updated_at) VALUES (1, 'processing', 100.0)")
    conn.execute("INSERT INTO task_queue (task_id, status, updated_at) VALUES (2, 'running', 100.0)")
    ensure_queue_jobs_table(conn)
    persist_job_state(conn, "job-1", "SYNTHESIZE_TOPIC", "RUNNING", progress=50)
    persist_job_state(conn, "job-2", "SYNTHESIZE_TOPIC", "PROCESSING", progress=30)

    worker = PriorityQueueWorker()
    with patch("core.queue_manager.get_db_connection", return_value=conn), \
         patch("core.queue_manager.closing", side_effect=lambda x: x):
        worker._recover_interrupted_tasks()

    tq_status_1 = conn.execute("SELECT status FROM task_queue WHERE task_id = 1").fetchone()["status"]
    tq_status_2 = conn.execute("SELECT status FROM task_queue WHERE task_id = 2").fetchone()["status"]
    qj_status_1 = conn.execute("SELECT status FROM queue_jobs WHERE job_id = 'job-1'").fetchone()["status"]
    qj_status_2 = conn.execute("SELECT status FROM queue_jobs WHERE job_id = 'job-2'").fetchone()["status"]
    assert tq_status_1 == "pending"
    assert tq_status_2 == "pending"
    assert qj_status_1 == "PENDING"
    assert qj_status_2 == "PENDING"

def test_async_worker_mcq_explain_dispatch_offline(tmp_path):
    db_file = tmp_path / "test_async_mcq_explain.db"
    conn = _create_mock_conn(db_file)
    conn.execute("CREATE TABLE task_queue (task_id INTEGER PRIMARY KEY, session_id TEXT, status TEXT, error_message TEXT, updated_at REAL)")
    conn.execute("INSERT INTO task_queue VALUES (10, 'sess-1', 'processing', NULL, 0.0)")
    conn.execute("CREATE TABLE session_questions (session_id TEXT, question_id TEXT, llm_explanation TEXT)")
    conn.execute("CREATE TABLE csv_questions (id TEXT PRIMARY KEY, llm_explanation TEXT)")
    conn.execute("CREATE TABLE quiz_sessions (id TEXT PRIMARY KEY, refs_status TEXT, llm_status TEXT, topics_reviewed INTEGER)")
    conn.execute("INSERT INTO session_questions VALUES ('sess-1', 'q-1', NULL)")
    conn.execute("INSERT INTO csv_questions VALUES ('q-1', NULL)")
    conn.execute("INSERT INTO quiz_sessions VALUES ('sess-1', 'ready', 'processing', 1)")
    conn.close()

    task = {
        "task_id": 10,
        "task_type": "MCQ_EXPLAIN",
        "session_id": "sess-1",
        "mcq_id": "q-1",
        "payload": json.dumps({"question_id": "q-1", "question_data": {"question": "What is X?"}})
    }

    mock_generate = AsyncMock(return_value="Detailed explanation for MCQ")
    worker = AsyncQueueWorker()

    import sys
    mock_pipeline = MagicMock()
    mock_pipeline.generate_mcq_explanation = mock_generate
    with patch.dict(sys.modules, {"agents.tema_q.pipeline": mock_pipeline}), \
         patch("core.queue_manager.get_db_connection", side_effect=lambda: _create_mock_conn(db_file)), \
         patch("core.database.get_db_connection", side_effect=lambda: _create_mock_conn(db_file)), \
         patch("core.queue_manager.is_llm_processing_enabled", return_value=True):
        asyncio.run(worker._dispatch_task(task))

    verify_conn = _create_mock_conn(db_file)
    task_row = verify_conn.execute("SELECT status, error_message FROM task_queue WHERE task_id = 10").fetchone()
    assert task_row["status"] == "completed"
    assert task_row["error_message"] is None

    s_exp = verify_conn.execute("SELECT llm_explanation FROM session_questions WHERE question_id = 'q-1'").fetchone()["llm_explanation"]
    c_exp = verify_conn.execute("SELECT llm_explanation FROM csv_questions WHERE id = 'q-1'").fetchone()["llm_explanation"]
    assert s_exp == "Detailed explanation for MCQ"
    assert c_exp == "Detailed explanation for MCQ"
    verify_conn.close()

def test_async_worker_synthesize_topic_dispatch_offline(tmp_path):
    db_file = tmp_path / "test_async_synthesis.db"
    conn = _create_mock_conn(db_file)
    conn.execute("CREATE TABLE task_queue (task_id INTEGER PRIMARY KEY, status TEXT, error_message TEXT, updated_at REAL)")
    conn.execute("INSERT INTO task_queue VALUES (20, 'processing', NULL, 0.0)")
    conn.execute("""
        CREATE TABLE cached_syntheses (
            topic_key TEXT PRIMARY KEY,
            display_title TEXT NOT NULL,
            enhanced_explanation TEXT,
            distractor_analysis TEXT,
            unified_article TEXT,
            variance_audit TEXT DEFAULT '',
            top_references TEXT,
            has_obsidian_note INTEGER DEFAULT 0,
            has_pdf_match INTEGER DEFAULT 0,
            has_zim_match INTEGER DEFAULT 0,
            updated_at REAL
        )
    """)
    conn.close()

    task = {
        "task_id": 20,
        "task_type": "SYNTHESIZE_TOPIC",
        "session_id": None,
        "topic_name": "Asthma",
        "payload": json.dumps({"topic": "Asthma"})
    }

    mock_synthesis = AsyncMock(return_value={
        "enhanced_explanation": "Asthma explanation",
        "distractor_analysis": "Distractor analysis",
        "unified_article": "Comprehensive asthma article",
        "variance_audit": "",
        "top_references": {"obsidian": ["asthma.md"]}
    })
    mock_search = MagicMock(return_value={"obsidian": ["asthma.md"]})
    worker = AsyncQueueWorker()

    import sys
    mock_pipeline = MagicMock()
    mock_pipeline.run_tema_q_synthesis = mock_synthesis
    mock_tantivy = MagicMock()
    mock_tantivy.search_index = mock_search

    with patch.dict(sys.modules, {
            "agents.tema_q.pipeline": mock_pipeline,
            "parsers.tantivy_engine": mock_tantivy,
         }), \
         patch("core.queue_manager.get_db_connection", side_effect=lambda: _create_mock_conn(db_file)), \
         patch("core.database.get_db_connection", side_effect=lambda: _create_mock_conn(db_file)), \
         patch("core.queue_manager.is_llm_processing_enabled", return_value=True), \
         patch("core.queue_manager.generate_topic_report", return_value=None):
        asyncio.run(worker._dispatch_task(task))

    verify_conn = _create_mock_conn(db_file)
    task_row = verify_conn.execute("SELECT status, error_message FROM task_queue WHERE task_id = 20").fetchone()
    assert task_row["status"] == "completed"
    assert task_row["error_message"] is None

    cache_row = verify_conn.execute("SELECT * FROM cached_syntheses WHERE topic_key = 'asthma'").fetchone()
    assert cache_row is not None
    assert cache_row["display_title"] == "Asthma"
    assert cache_row["unified_article"] == "Comprehensive asthma article"
    verify_conn.close()
