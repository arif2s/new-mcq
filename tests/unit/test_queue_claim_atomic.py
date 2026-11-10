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
