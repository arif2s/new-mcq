import logging
import os
import sqlite3

# Enforce Invariant 1: Zero VRAM for Backend
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from config import DB_PATH

logger = logging.getLogger("medisearch.database")


class _ManagedSQLiteConnection(sqlite3.Connection):
    """SQLite connection subclass that ensures `with get_db_connection() as conn:`
    properly closes the underlying file descriptor upon context exit on Windows.
    """

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            try:
                self.close()
            except Exception:
                pass


def get_db_connection() -> sqlite3.Connection:
    """Yields a connection configured for concurrent async access and optimized for Windows.

    Enforces Invariant 3 per-connection PRAGMAs:
      - journal_mode = WAL
      - busy_timeout = 20000
      - foreign_keys = ON
      - synchronous = NORMAL
      - mmap_size = 536870912 (512 MB cap)
      - temp_store = MEMORY
      - cache_size = -64000 (64 MB cache)
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(
        DB_PATH,
        timeout=20.0,
        check_same_thread=False,
        isolation_level=None,  # Autocommit mode by default; explicit transactions can use 'BEGIN'
        factory=_ManagedSQLiteConnection,
    )
    conn.row_factory = sqlite3.Row

    # Fast connection-scoped PRAGMAs (Invariant 3)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 20000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA mmap_size = 536870912;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = -64000;")
    return conn


def _safe_add_column(conn: sqlite3.Connection, table: str, col_def: str) -> None:
    """Safely adds a column to an existing SQLite table if it does not already exist."""
    col_name = col_def.split()[0].strip()
    try:
        cursor = conn.execute(f"PRAGMA table_info({table})")
        existing_cols = {row["name"] for row in cursor.fetchall()}
        if col_name not in existing_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            logger.info("Migrated table '%s': added column '%s'", table, col_name)
    except sqlite3.OperationalError as exc:
        logger.warning(
            "Column addition skipped for table '%s', column '%s': %s", table, col_name, exc
        )


def _migrate_task_queue_schema(conn: sqlite3.Connection) -> None:
    """Removes legacy FOREIGN KEY(mcq_id) REFERENCES mcq_bank(id) from task_queue
    and normalizes mcq_id to TEXT so both mcq_bank (int) and csv_questions (TEXT) IDs succeed.
    """
    try:
        fk_rows = conn.execute("PRAGMA foreign_key_list(task_queue)").fetchall()
        col_rows = conn.execute("PRAGMA table_info(task_queue)").fetchall()
        mcq_col_type = next(
            (str(r["type"]).upper() for r in col_rows if r["name"] == "mcq_id"),
            "TEXT",
        )
        if not fk_rows and mcq_col_type == "TEXT":
            return

        logger.info("Migrating task_queue schema to remove legacy mcq_bank foreign key...")
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute("""
                CREATE TABLE task_queue_new (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL,
                    priority INTEGER DEFAULT 3,
                    mcq_id TEXT,
                    session_id TEXT,
                    topic_name TEXT,
                    payload TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    error_message TEXT,
                    created_at REAL DEFAULT (unixepoch('now')),
                    updated_at REAL DEFAULT (unixepoch('now'))
                )
            """)
            conn.execute("""
                INSERT INTO task_queue_new (
                    task_id, task_type, priority, mcq_id, session_id,
                    topic_name, payload, status, error_message, created_at, updated_at
                )
                SELECT
                    task_id, task_type, priority, CAST(mcq_id AS TEXT), session_id,
                    topic_name, payload, LOWER(COALESCE(status, 'pending')), error_message,
                    created_at, updated_at
                FROM task_queue
            """)
            conn.execute("DROP TABLE task_queue;")
            conn.execute("ALTER TABLE task_queue_new RENAME TO task_queue;")
            conn.execute("COMMIT;")
            logger.info("Successfully migrated task_queue schema.")
        except Exception:
            conn.execute("ROLLBACK;")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON;")
    except sqlite3.OperationalError as exc:
        logger.warning("task_queue schema migration check skipped: %s", exc)


def initialize_database() -> None:
    """Executes 4-stage initialization (Invariant 3):
    1. Table & FTS5 trigger creation
    2. Idempotent column & task_queue FK removal migrations
    3. Index creation
    4. csv_questions_fts_docsize external-content FTS5 rebuild check
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with get_db_connection() as conn:
        # STAGE 1: Create Core Tables & FTS5 Triggers (No indices on migratable columns yet)
        conn.executescript("""
            -- 1. Master Question Bank (Legacy & Custom Prepared Questions)
            CREATE TABLE IF NOT EXISTS mcq_bank (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL,
                source_tag TEXT DEFAULT 'General',
                question TEXT NOT NULL,
                opt_a TEXT NOT NULL,
                opt_b TEXT NOT NULL,
                opt_c TEXT NOT NULL,
                opt_d TEXT NOT NULL,
                correct_opt TEXT NOT NULL CHECK(correct_opt IN ('A', 'B', 'C', 'D')),
                csv_explanation TEXT,
                llm_explanation TEXT DEFAULT '',
                image_path TEXT,
                state INTEGER DEFAULT 0,
                difficulty REAL DEFAULT 0.0,
                stability REAL DEFAULT 0.0,
                reps INTEGER DEFAULT 0,
                lapses INTEGER DEFAULT 0,
                last_review REAL,
                due_timestamp REAL DEFAULT 0.0,
                created_at REAL DEFAULT (unixepoch('now'))
            );

            -- 2. CSV Questions Table (With FSRS v4.5 & LLM Explanation Columns)
            CREATE TABLE IF NOT EXISTS csv_questions (
                id TEXT PRIMARY KEY,
                file_path TEXT,
                topic_name TEXT,
                question TEXT,
                opt_a TEXT,
                opt_b TEXT,
                opt_c TEXT,
                opt_d TEXT,
                correct_answer TEXT,
                explanation TEXT,
                llm_explanation TEXT DEFAULT '',
                state INTEGER DEFAULT 0,
                difficulty REAL DEFAULT 0.0,
                stability REAL DEFAULT 0.0,
                reps INTEGER DEFAULT 0,
                lapses INTEGER DEFAULT 0,
                last_review REAL,
                due_timestamp REAL DEFAULT 0.0
            );

            -- 3. FTS5 Virtual Table for Sub-Millisecond Keyword Filtering
            CREATE VIRTUAL TABLE IF NOT EXISTS csv_questions_fts USING fts5(
                id UNINDEXED,
                question,
                opt_a,
                opt_b,
                opt_c,
                opt_d,
                content='csv_questions',
                content_rowid='rowid'
            );

            -- Triggers to keep FTS5 table synchronized with csv_questions
            CREATE TRIGGER IF NOT EXISTS csv_questions_ai AFTER INSERT ON csv_questions BEGIN
                INSERT INTO csv_questions_fts(rowid, id, question, opt_a, opt_b, opt_c, opt_d)
                VALUES (new.rowid, new.id, new.question, new.opt_a, new.opt_b, new.opt_c, new.opt_d);
            END;

            CREATE TRIGGER IF NOT EXISTS csv_questions_ad AFTER DELETE ON csv_questions BEGIN
                INSERT INTO csv_questions_fts(csv_questions_fts, rowid, id, question, opt_a, opt_b, opt_c, opt_d)
                VALUES('delete', old.rowid, old.id, old.question, old.opt_a, old.opt_b, old.opt_c, old.opt_d);
            END;

            CREATE TRIGGER IF NOT EXISTS csv_questions_au AFTER UPDATE OF question, opt_a, opt_b, opt_c, opt_d ON csv_questions BEGIN
                INSERT INTO csv_questions_fts(csv_questions_fts, rowid, id, question, opt_a, opt_b, opt_c, opt_d)
                VALUES('delete', old.rowid, old.id, old.question, old.opt_a, old.opt_b, old.opt_c, old.opt_d);
                INSERT INTO csv_questions_fts(rowid, id, question, opt_a, opt_b, opt_c, opt_d)
                VALUES (new.rowid, new.id, new.question, new.opt_a, new.opt_b, new.opt_c, new.opt_d);
            END;

            -- 4. Persistent Priority Queue for Async Reference & LLM Tasks
            -- Note: mcq_id is TEXT without a strict mcq_bank FK to support both mcq_bank (int) and csv_questions (TEXT) IDs
            CREATE TABLE IF NOT EXISTS task_queue (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                priority INTEGER DEFAULT 3,
                mcq_id TEXT,
                session_id TEXT,
                topic_name TEXT,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                error_message TEXT,
                created_at REAL DEFAULT (unixepoch('now')),
                updated_at REAL DEFAULT (unixepoch('now'))
            );

            -- 5. Synthesis & Topic Dossier Cache
            CREATE TABLE IF NOT EXISTS cached_syntheses (
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
                created_at REAL DEFAULT (unixepoch('now')),
                updated_at REAL DEFAULT (unixepoch('now'))
            );

            -- 6. Configured Offline Knowledge Sources
            CREATE TABLE IF NOT EXISTS knowledge_sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                path TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                itemCount INTEGER DEFAULT 0,
                lastIndexed TEXT DEFAULT 'Never',
                pdfEngine TEXT DEFAULT 'fitz'
            );

            -- 7. Quiz Sessions & Knowledge Hub Cards
            CREATE TABLE IF NOT EXISTS quiz_sessions (
                id TEXT PRIMARY KEY,
                subject_name TEXT NOT NULL,
                custom_title TEXT DEFAULT '',
                date TEXT NOT NULL,
                timestamp REAL NOT NULL,
                last_attempted REAL DEFAULT 0.0,
                total_questions INTEGER NOT NULL,
                correct_answers INTEGER NOT NULL DEFAULT 0,
                wrong_answers INTEGER NOT NULL DEFAULT 0,
                unanswered INTEGER NOT NULL DEFAULT 0,
                score INTEGER NOT NULL DEFAULT 0,
                max_score INTEGER NOT NULL DEFAULT 0,
                accuracy REAL NOT NULL DEFAULT 0.0,
                time_limit_seconds INTEGER,
                time_used_seconds INTEGER NOT NULL DEFAULT 0,
                mode TEXT NOT NULL DEFAULT 'learn',
                is_completed INTEGER DEFAULT 0,
                topics_reviewed INTEGER DEFAULT 0,
                refs_status TEXT DEFAULT 'none',
                llm_status TEXT DEFAULT 'none',
                topic_list_json TEXT DEFAULT '[]',
                created_at REAL DEFAULT (unixepoch('now'))
            );

            -- 8. Session Questions (Individual MCQ Records + LLM Detailed Explanations)
            CREATE TABLE IF NOT EXISTS session_questions (
                session_id TEXT NOT NULL,
                question_id TEXT NOT NULL,
                selected_option TEXT,
                is_correct INTEGER NOT NULL DEFAULT 0,
                timed_out INTEGER NOT NULL DEFAULT 0,
                time_spent_ms INTEGER NOT NULL DEFAULT 0,
                timestamp REAL NOT NULL,
                question_text TEXT,
                opt_a TEXT,
                opt_b TEXT,
                opt_c TEXT,
                opt_d TEXT,
                correct_answer TEXT,
                explanation TEXT,
                llm_explanation TEXT DEFAULT '',
                topic TEXT,
                question_topics_json TEXT DEFAULT '[]',
                PRIMARY KEY (session_id, question_id),
                FOREIGN KEY(session_id) REFERENCES quiz_sessions(id) ON DELETE CASCADE
            );

            -- 9. Extracted Topics per Question
            CREATE TABLE IF NOT EXISTS extracted_topics (
                question_id TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                is_main_topic INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                selected INTEGER DEFAULT 1,
                PRIMARY KEY (question_id, topic_name)
            );

            -- 10. Permanent Ignore List
            CREATE TABLE IF NOT EXISTS ignored_topics (
                topic_norm TEXT PRIMARY KEY,
                original_text TEXT NOT NULL,
                created_at REAL DEFAULT (unixepoch('now'))
            );

            -- 11. ZIM Title Cache (Fast lookup for Wikipedia / Medical articles)
            CREATE TABLE IF NOT EXISTS zim_titles (
                title TEXT PRIMARY KEY
            );

            -- 12. Persistent User & App State
            CREATE TABLE IF NOT EXISTS user_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL DEFAULT (unixepoch('now'))
            );
        """)

        # STAGE 2: Idempotent Column Additions & Schema Migrations (BEFORE Index Creation)
        _safe_add_column(conn, "knowledge_sources", "pdfEngine TEXT DEFAULT 'fitz'")
        _safe_add_column(conn, "mcq_bank", "llm_explanation TEXT DEFAULT ''")
        _safe_add_column(conn, "csv_questions", "llm_explanation TEXT DEFAULT ''")
        _safe_add_column(conn, "cached_syntheses", "variance_audit TEXT DEFAULT ''")

        for col_def in (
            "state INTEGER DEFAULT 0",
            "difficulty REAL DEFAULT 0.0",
            "stability REAL DEFAULT 0.0",
            "reps INTEGER DEFAULT 0",
            "lapses INTEGER DEFAULT 0",
            "last_review REAL",
            "due_timestamp REAL DEFAULT 0.0",
        ):
            _safe_add_column(conn, "mcq_bank", col_def)
            _safe_add_column(conn, "csv_questions", col_def)

        for col_def in (
            "custom_title TEXT DEFAULT ''",
            "last_attempted REAL DEFAULT 0.0",
            "topics_reviewed INTEGER DEFAULT 0",
            "refs_status TEXT DEFAULT 'none'",
            "llm_status TEXT DEFAULT 'none'",
            "topic_list_json TEXT DEFAULT '[]'",
        ):
            _safe_add_column(conn, "quiz_sessions", col_def)

        for col_def in (
            "llm_explanation TEXT DEFAULT ''",
            "question_topics_json TEXT DEFAULT '[]'",
        ):
            _safe_add_column(conn, "session_questions", col_def)

        for col_def in (
            "verified INTEGER DEFAULT 0",
            "selected INTEGER DEFAULT 1",
        ):
            _safe_add_column(conn, "extracted_topics", col_def)

        for col_def in (
            "session_id TEXT",
            "topic_name TEXT",
        ):
            _safe_add_column(conn, "task_queue", col_def)

        _migrate_task_queue_schema(conn)

        # STAGE 3: Create All Required Performance & Deduplication Indices
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_mcq_due ON mcq_bank (due_timestamp, state);
            CREATE INDEX IF NOT EXISTS idx_csv_topic ON csv_questions (topic_name);
            CREATE INDEX IF NOT EXISTS idx_csv_due ON csv_questions (due_timestamp, state);
            CREATE INDEX IF NOT EXISTS idx_task_priority ON task_queue (status, priority, task_id);
            CREATE INDEX IF NOT EXISTS idx_task_session ON task_queue (session_id, status);
            CREATE INDEX IF NOT EXISTS idx_task_dedup ON task_queue (task_type, topic_name, status);
            CREATE INDEX IF NOT EXISTS idx_session_questions_sid ON session_questions (session_id);
            CREATE INDEX IF NOT EXISTS idx_extracted_topics_qid ON extracted_topics (question_id);
        """)

        # STAGE 4: Rebuild FTS5 index if csv_questions has data but FTS5 docsize shadow table is empty
        try:
            csv_count = conn.execute("SELECT COUNT(*) FROM csv_questions").fetchone()[0]
            fts_docsize_count = conn.execute(
                "SELECT COUNT(*) FROM csv_questions_fts_docsize"
            ).fetchone()[0]
            if csv_count > 0 and fts_docsize_count == 0:
                conn.execute("INSERT INTO csv_questions_fts(csv_questions_fts) VALUES('rebuild')")
                logger.info(
                    "Synchronized and rebuilt csv_questions_fts (%d records)", csv_count
                )
        except sqlite3.OperationalError as exc:
            logger.warning("FTS5 synchronization check skipped: %s", exc)
