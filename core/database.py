import sqlite3
import os
from config import DB_PATH

def get_db_connection():
    """Yields a connection configured for concurrent async access."""
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    """Executes schema creation and configures Write-Ahead Logging."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Performance PRAGMAs
        cursor.execute("PRAGMA journal_mode = WAL;")
        cursor.execute("PRAGMA synchronous = NORMAL;")
        cursor.execute("PRAGMA busy_timeout = 20000;")

        # MCQ Bank Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mcq_bank (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL, source_tag TEXT DEFAULT 'General',
                question TEXT NOT NULL, opt_a TEXT NOT NULL, opt_b TEXT NOT NULL,
                opt_c TEXT NOT NULL, opt_d TEXT NOT NULL,
                correct_opt TEXT NOT NULL CHECK(correct_opt IN ('A', 'B', 'C', 'D')),
                csv_explanation TEXT, image_path TEXT,
                state INTEGER DEFAULT 0, difficulty REAL DEFAULT 0.0,
                stability REAL DEFAULT 0.0, reps INTEGER DEFAULT 0,
                lapses INTEGER DEFAULT 0, last_review REAL,
                due_timestamp REAL DEFAULT 0.0, created_at REAL DEFAULT (unixepoch('now'))
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mcq_due ON mcq_bank (due_timestamp, state);")

        # Persistent Priority Queue for Async LLM Tasks
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_queue (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,          -- 'INTERACTIVE_CHAT', 'SESSION_EXPLAIN', 'BATCH_PRECOMPUTE'
                priority INTEGER DEFAULT 3,       -- 1=Immediate, 2=Active Session, 3=Batch Low
                mcq_id INTEGER,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'PENDING',    -- 'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'
                error_message TEXT,
                created_at REAL DEFAULT (unixepoch('now')),
                updated_at REAL DEFAULT (unixepoch('now')),
                FOREIGN KEY(mcq_id) REFERENCES mcq_bank(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_priority ON task_queue (status, priority, task_id);")

        # Synthesis & Topic Dossier Cache
        cursor.execute("""
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
            )
        """)

        conn.commit()
