"""
server/routes_csv_loader.py - Background loader scanning data/csv and auto-ingesting questions into SQLite.
Enforces zero VRAM usage, chunked SQLite transactions (BATCH_SIZE=1000), recursive folder discovery, and blueprint contracts.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import csv
import hashlib
import logging
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Dict, List, Optional, Set

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

# Increase CSV field size limit to prevent crashes on large medical case vignettes
try:
    csv.field_size_limit(2147483647 if sys.platform == "win32" else sys.maxsize)
except (OverflowError, AttributeError):
    csv.field_size_limit(int(1e8))

from fastapi import APIRouter, BackgroundTasks

from config import CSV_DIR, MCQS_DIR
from core.database import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["csv_loader"])

# Concurrency re-entrancy lock
_LOADER_LOCK = threading.Lock()
_IS_LOADING = False

_OPTION_PREFIX_RE = re.compile(r"^(?:\(?[A-Da-d1-4]\)?[.):\-\s]+)")


def _pick_field(row: Dict[str, Any], candidates: List[str], default: str = "") -> str:
    """Case-insensitive and alias-tolerant CSV column lookup."""
    for key in candidates:
        if key in row and row[key] is not None:
            val = str(row[key]).strip()
            if val:
                return val

    lower_map = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for key in candidates:
        val = lower_map.get(key.lower())
        if val is not None and str(val).strip():
            return str(val).strip()

    return default


def _strip_opt_prefix(text: str) -> str:
    return _OPTION_PREFIX_RE.sub("", (text or "").strip()).strip().lower()


def _normalize_correct_answer(
    raw_ans: str,
    opt_a: str = "",
    opt_b: str = "",
    opt_c: str = "",
    opt_d: str = "",
) -> str:
    """
    Normalizes diverse clinical question answer formats (Section 3):
    - Standard letters: 'A', 'b', 'c', 'D'
    - Bracketed/punctuated: '(A)', '[B]', 'C.', 'D:'
    - Numeric choices: '1' -> 'A', '2' -> 'B', '3' -> 'C', '4' -> 'D'
    - Spreadsheet float exports: '1.0' -> 'A', '2.0' -> 'B'
    - Verbose choices: 'Option A', 'Choice B', 'B) Hyperkalemia'
    - Direct option text match: matches verbatim or prefix-stripped content of opt_a..opt_d
    """
    if not raw_ans:
        return ""

    a = raw_ans.strip().upper()
    num_map = {"1": "A", "2": "B", "3": "C", "4": "D"}

    if a in num_map:
        return num_map[a]

    if len(a) >= 3 and a[:2] in {"1.", "2.", "3.", "4."}:
        return num_map[a[0]]

    cleaned = a.strip("()[]{}.- :")
    if cleaned in {"A", "B", "C", "D"}:
        return cleaned
    if cleaned in num_map:
        return num_map[cleaned]

    for letter in ("A", "B", "C", "D"):
        if (
            f"OPTION {letter}" in a
            or f"CHOICE {letter}" in a
            or a.startswith(f"{letter})")
            or a.startswith(f"{letter}.")
            or a.startswith(f"({letter})")
        ):
            return letter

    raw_lower = raw_ans.strip().lower()
    raw_stripped = _strip_opt_prefix(raw_ans)
    if raw_lower:
        options_map = (("A", opt_a), ("B", opt_b), ("C", opt_c), ("D", opt_d))
        for letter, opt_val in options_map:
            if not opt_val:
                continue
            opt_low = opt_val.strip().lower()
            opt_clean = _strip_opt_prefix(opt_val)
            if raw_lower == opt_low or (raw_stripped and raw_stripped == opt_clean):
                return letter

    return ""


def _flush_question_batch(conn: Any, cursor: Any, batch_data: List[tuple]) -> None:
    """Commits a discrete batch (<= 1000 rows) in a bounded transaction (Invariant 20)."""
    if not batch_data:
        return
    conn.execute("BEGIN IMMEDIATE;")
    try:
        cursor.executemany(
            """
            INSERT OR IGNORE INTO csv_questions
            (id, file_path, topic_name, question, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch_data,
        )
        conn.execute("COMMIT;")
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise


def _get_current_question_count() -> int:
    try:
        with closing(get_db_connection()) as conn:
            row = conn.execute("SELECT COUNT(*) FROM csv_questions").fetchone()
            return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def scan_and_ingest_csvs() -> Dict[str, Any]:
    """
    Scans data/csv and data/mcqs recursively for CSV files and bulk-loads them
    into csv_questions using chunked transactions (BATCH_SIZE=1000, Invariant 20)
    to avoid SQLite lock contention.
    Maintains FTS5 triggers and preserves existing FSRS state via INSERT OR IGNORE.
    """
    global _IS_LOADING

    with _LOADER_LOCK:
        if _IS_LOADING:
            current_total = _get_current_question_count()
            return {
                "status": "busy",
                "message": "CSV ingestion is already in progress.",
                "files_processed": 0,
                "rows_processed": 0,
                "rows_inserted": 0,
                "total_in_db": current_total,
            }
        _IS_LOADING = True

    try:
        CSV_DIR.mkdir(parents=True, exist_ok=True)
        MCQS_DIR.mkdir(parents=True, exist_ok=True)

        csv_files: List[Path] = []
        seen_resolved: Set[str] = set()

        for folder in (CSV_DIR, MCQS_DIR):
            if not folder.is_dir():
                continue
            for fp in sorted(folder.rglob("*.csv")):
                if fp.is_file():
                    resolved = str(fp.resolve())
                    if resolved.lower() not in seen_resolved:
                        seen_resolved.add(resolved.lower())
                        csv_files.append(fp)

        files_processed = 0
        rows_attempted = 0
        BATCH_SIZE = 1000  # Invariant 20: Discrete batch commits <= 1000

        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            initial_row = cursor.execute("SELECT COUNT(*) FROM csv_questions").fetchone()
            initial_count = int(initial_row[0] or 0) if initial_row else 0

            for file_path in csv_files:
                try:
                    with open(
                        file_path, "r", encoding="utf-8-sig", errors="replace"
                    ) as file_handle:
                        sample = file_handle.read(4096)
                        file_handle.seek(0)
                        delimiter = ","
                        if sample:
                            try:
                                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                                delimiter = dialect.delimiter
                            except Exception:
                                delimiter = ","

                        reader = csv.DictReader(file_handle, delimiter=delimiter)
                        if not reader.fieldnames:
                            continue

                        batch_data: List[tuple] = []
                        default_subject = file_path.stem

                        for i, row in enumerate(reader):
                            q = _pick_field(
                                row,
                                [
                                    "question",
                                    "question_text",
                                    "stem",
                                    "mcq",
                                    "query",
                                    "problem",
                                    "vignette",
                                ],
                            )
                            if not q:
                                continue

                            topic = _pick_field(
                                row,
                                [
                                    "topic_name",
                                    "topic",
                                    "subtopic",
                                    "subject",
                                    "category",
                                    "system",
                                    "specialty",
                                    "domain",
                                    "discipline",
                                ],
                                default=default_subject or "General",
                            )

                            opt_a = _pick_field(
                                row,
                                [
                                    "option_a",
                                    "opt_a",
                                    "a",
                                    "choice_a",
                                    "option 1",
                                    "opt1",
                                    "choice 1",
                                    "choice1",
                                ],
                            )
                            opt_b = _pick_field(
                                row,
                                [
                                    "option_b",
                                    "opt_b",
                                    "b",
                                    "choice_b",
                                    "option 2",
                                    "opt2",
                                    "choice 2",
                                    "choice2",
                                ],
                            )
                            opt_c = _pick_field(
                                row,
                                [
                                    "option_c",
                                    "opt_c",
                                    "c",
                                    "choice_c",
                                    "option 3",
                                    "opt3",
                                    "choice 3",
                                    "choice3",
                                ],
                            )
                            opt_d = _pick_field(
                                row,
                                [
                                    "option_d",
                                    "opt_d",
                                    "d",
                                    "choice_d",
                                    "option 4",
                                    "opt4",
                                    "choice 4",
                                    "choice4",
                                ],
                            )

                            if not opt_a and not opt_b:
                                continue

                            raw_ans = _pick_field(
                                row,
                                [
                                    "correct_answer",
                                    "correct_opt",
                                    "correct",
                                    "answer",
                                    "ans",
                                    "correct_choice",
                                    "key",
                                ],
                            )
                            correct = _normalize_correct_answer(
                                raw_ans, opt_a, opt_b, opt_c, opt_d
                            )
                            if correct not in {"A", "B", "C", "D"}:
                                continue

                            explanation = _pick_field(
                                row,
                                [
                                    "explanation",
                                    "csv_explanation",
                                    "rationale",
                                    "solution",
                                    "discussion",
                                    "reasoning",
                                    "comments",
                                ],
                                default="",
                            )

                            explicit_id = _pick_field(
                                row,
                                [
                                    "id",
                                    "question_id",
                                    "qid",
                                    "mcq_id",
                                    "item_id",
                                    "number",
                                    "num",
                                ],
                            )
                            if explicit_id:
                                clean_exp = explicit_id.strip()
                                stable_id = (
                                    f"{file_path.stem}_{clean_exp}"
                                    if not clean_exp.startswith(f"{file_path.stem}_")
                                    else clean_exp
                                )
                            else:
                                id_str = f"{file_path.name}_{i}_{q}"
                                stable_id = (
                                    f"q_{i}_"
                                    + hashlib.md5(
                                        id_str.encode("utf-8"), usedforsecurity=False
                                    ).hexdigest()[:8]
                                )

                            batch_data.append((
                                stable_id,
                                str(file_path),
                                topic,
                                q,
                                opt_a,
                                opt_b,
                                opt_c,
                                opt_d,
                                correct,
                                explanation,
                            ))

                            if len(batch_data) >= BATCH_SIZE:
                                _flush_question_batch(conn, cursor, batch_data)
                                rows_attempted += len(batch_data)
                                batch_data.clear()

                        if batch_data:
                            _flush_question_batch(conn, cursor, batch_data)
                            rows_attempted += len(batch_data)
                            batch_data.clear()

                        files_processed += 1

                except (OSError, csv.Error, UnicodeDecodeError) as exc:
                    logger.warning("Error processing CSV %s: %s", file_path.name, exc)

            final_row = cursor.execute("SELECT COUNT(*) FROM csv_questions").fetchone()
            total_in_db = int(final_row[0] or 0) if final_row else 0
            net_rows_inserted = max(0, total_in_db - initial_count)

            try:
                fts_docsize_cnt = cursor.execute(
                    "SELECT COUNT(*) FROM csv_questions_fts_docsize"
                ).fetchone()[0]
                if total_in_db > 0 and fts_docsize_cnt == 0:
                    conn.execute(
                        "INSERT INTO csv_questions_fts(csv_questions_fts) VALUES('rebuild')"
                    )
            except Exception:
                pass

        return {
            "status": "success",
            "files_processed": files_processed,
            "rows_processed": rows_attempted,
            "rows_inserted": net_rows_inserted,
            "total_in_db": total_in_db,
        }

    finally:
        _IS_LOADING = False


# Backward-compatibility alias for routes_csv.py and background task callers (Section 3)
load_csvs_background = scan_and_ingest_csvs


# ============================================================================
# API ROUTER ENDPOINTS
# ============================================================================

@router.post("/api/csv/reload")
@router.post("/reload")
@router.get("/api/csv/reload")
@router.get("/reload")
async def reload_csvs(
    background_tasks: BackgroundTasks = None,
) -> Dict[str, Any]:
    """
    API endpoint triggering recursive CSV question ingestion.
    Runs non-blockingly via asyncio.to_thread and returns ingestion statistics.
    """
    stats = await asyncio.to_thread(scan_and_ingest_csvs)
    return {
        "status": stats.get("status", "success"),
        "message": (
            f"Loaded {stats.get('total_in_db', 0)} questions across "
            f"{stats.get('files_processed', 0)} CSV files."
        ),
        "stats": stats,
    }
