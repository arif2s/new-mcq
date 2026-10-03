import sqlite3
import os
from config import DB_PATH

def get_db_connection():
    """
    Yields a connection configured for concurrent async access and optimized for Windows.
    Must use check_same_thread=False to support asyncio.to_thread worker pools.
    """
    conn = sqlite3.connect(DB_PATH, timeout=20.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # Execute all performance PRAGMAs natively in C via executescript
    conn.executescript("""
        PRAGMA busy_timeout = 20000;
        PRAGMA foreign_keys = ON;
    """)

    return conn

def initialize_database():
    """Executes schema creation and configures the database in a single optimized script."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with get_db_connection() as conn:
        conn.executescript("""
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = NORMAL;
            PRAGMA mmap_size = 2147483648;
            PRAGMA temp_store = MEMORY;
            PRAGMA cache_size = -64000;
        """)
        # Using executescript for bulk schema generation is significantly faster
        # as it avoids round-tripping through the Python SQLite wrapper for each table.
        conn.executescript("""
            -- MCQ Bank Table
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
            CREATE INDEX IF NOT EXISTS idx_mcq_due ON mcq_bank (due_timestamp, state);

            -- Persistent Priority Queue for Async LLM Tasks
            CREATE TABLE IF NOT EXISTS task_queue (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                priority INTEGER DEFAULT 3,
                mcq_id INTEGER,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'PENDING',
                error_message TEXT,
                created_at REAL DEFAULT (unixepoch('now')),
                updated_at REAL DEFAULT (unixepoch('now')),
                FOREIGN KEY(mcq_id) REFERENCES mcq_bank(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_task_priority ON task_queue (status, priority, task_id);

            -- Synthesis & Topic Dossier Cache
            CREATE TABLE IF NOT EXISTS cached_syntheses (
                topic_key TEXT PRIMARY KEY,
                display_title TEXT NOT NULL,
                enhanced_explanation TEXT,
                distractor_analysis TEXT,
                unified_article TEXT,
                top_references TEXT,
                has_obsidian_note INTEGER DEFAULT 0,
                has_pdf_match INTEGER DEFAULT 0,
                has_zim_match INTEGER DEFAULT 0,
                created_at REAL DEFAULT (unixepoch('now')),
                updated_at REAL DEFAULT (unixepoch('now'))
            );

            CREATE TABLE IF NOT EXISTS knowledge_sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                path TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                itemCount INTEGER DEFAULT 0,
                lastIndexed TEXT DEFAULT 'Never'
            );

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
                explanation TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_csv_topic ON csv_questions(topic_name);

            -- FTS5 Virtual Table for fast keyword searching
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

            CREATE TABLE IF NOT EXISTS quiz_sessions (
                id TEXT PRIMARY KEY,
                subject_name TEXT NOT NULL,
                date TEXT NOT NULL,
                timestamp REAL NOT NULL,
                total_questions INTEGER NOT NULL,
                correct_answers INTEGER NOT NULL,
                wrong_answers INTEGER NOT NULL,
                unanswered INTEGER NOT NULL,
                score INTEGER NOT NULL,
                max_score INTEGER NOT NULL,
                accuracy REAL NOT NULL,
                time_limit_seconds INTEGER,
                time_used_seconds INTEGER NOT NULL,
                mode TEXT NOT NULL,
                is_completed INTEGER DEFAULT 1,
                created_at REAL DEFAULT (unixepoch('now'))
            );

            CREATE TABLE IF NOT EXISTS session_questions (
                session_id TEXT NOT NULL,
                question_id TEXT NOT NULL,
                selected_option TEXT,
                is_correct INTEGER NOT NULL,
                timed_out INTEGER NOT NULL,
                time_spent_ms INTEGER NOT NULL,
                timestamp REAL NOT NULL,
                question_text TEXT,
                opt_a TEXT,
                opt_b TEXT,
                opt_c TEXT,
                opt_d TEXT,
                correct_answer TEXT,
                explanation TEXT,
                topic TEXT,
                FOREIGN KEY(session_id) REFERENCES quiz_sessions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_session_questions_sid ON session_questions (session_id);

            CREATE TABLE IF NOT EXISTS extracted_topics (
                question_id TEXT NOT NULL,
                topic_name TEXT NOT NULL,
                is_main_topic INTEGER DEFAULT 0,
                PRIMARY KEY (question_id, topic_name)
            );

            CREATE TABLE IF NOT EXISTS user_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL DEFAULT (unixepoch('now'))
            );

            -- Triggers to keep FTS table in sync with csv_questions
            CREATE TRIGGER IF NOT EXISTS csv_questions_ai AFTER INSERT ON csv_questions BEGIN
                INSERT INTO csv_questions_fts(rowid, id, question, opt_a, opt_b, opt_c, opt_d)
                VALUES (new.rowid, new.id, new.question, new.opt_a, new.opt_b, new.opt_c, new.opt_d);
            END;

            CREATE TRIGGER IF NOT EXISTS csv_questions_ad AFTER DELETE ON csv_questions BEGIN
                INSERT INTO csv_questions_fts(csv_questions_fts, rowid, id, question, opt_a, opt_b, opt_c, opt_d)
                VALUES('delete', old.rowid, old.id, old.question, old.opt_a, old.opt_b, old.opt_c, old.opt_d);
            END;

            CREATE TRIGGER IF NOT EXISTS csv_questions_au AFTER UPDATE ON csv_questions BEGIN
                INSERT INTO csv_questions_fts(csv_questions_fts, rowid, id, question, opt_a, opt_b, opt_c, opt_d)
                VALUES('delete', old.rowid, old.id, old.question, old.opt_a, old.opt_b, old.opt_c, old.opt_d);
                INSERT INTO csv_questions_fts(rowid, id, question, opt_a, opt_b, opt_c, opt_d)
                VALUES (new.rowid, new.id, new.question, new.opt_a, new.opt_b, new.opt_c, new.opt_d);
            END;
        """)
        conn.commit()
