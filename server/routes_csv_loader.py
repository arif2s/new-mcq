import csv
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, List
from contextlib import closing

from config import CSV_DIR, MCQS_DIR
from core.database import get_db_connection

logger = logging.getLogger(__name__)


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


def load_csvs_background() -> Dict[str, int]:
    """
    Scans both data/csv and data/mcqs for CSV question files and bulk-loads them
    into csv_questions (with automatic FTS5 trigger indexing).
    Uses INSERT OR IGNORE so existing FSRS progress and LLM explanations are preserved.
    """
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    MCQS_DIR.mkdir(parents=True, exist_ok=True)

    csv_files: List[Path] = []
    seen_names = set()
    for folder in (CSV_DIR, MCQS_DIR):
        for fp in sorted(folder.glob("*.csv")):
            resolved = str(fp.resolve())
            if resolved not in seen_names:
                seen_names.add(resolved)
                csv_files.append(fp)

    files_processed = 0
    rows_attempted = 0

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()

        for file_path in csv_files:
            try:
                with open(file_path, "r", encoding="utf-8-sig", errors="replace") as file_handle:
                    reader = csv.DictReader(file_handle)
                    if not reader.fieldnames:
                        continue

                    batch_data = []
                    default_subject = file_path.stem

                    for i, row in enumerate(reader):
                        topic = _pick_field(
                            row,
                            ["topic_name", "topic", "subtopic", "subject", "category"],
                            default=default_subject or "General",
                        )
                        q = _pick_field(row, ["question", "question_text", "stem", "mcq"])
                        opt_a = _pick_field(row, ["option_a", "opt_a", "a", "choice_a"])
                        opt_b = _pick_field(row, ["option_b", "opt_b", "b", "choice_b"])
                        opt_c = _pick_field(row, ["option_c", "opt_c", "c", "choice_c"])
                        opt_d = _pick_field(row, ["option_d", "opt_d", "d", "choice_d"])
                        correct = _pick_field(
                            row,
                            ["correct_answer", "correct_opt", "correct", "answer", "ans"],
                        ).upper()
                        explanation = _pick_field(
                            row,
                            ["explanation", "csv_explanation", "rationale", "solution"],
                            default="",
                        )

                        # Normalize answers like "Option A" or "(B)" -> "A", "B"
                        if len(correct) > 1:
                            for letter in ("A", "B", "C", "D"):
                                if correct.endswith(letter) or correct.startswith(letter):
                                    correct = letter
                                    break

                        if q and correct in {"A", "B", "C", "D"}:
                            id_str = f"{file_path.name}_{i}_{q}"
                            stable_id = f"q_{i}_" + hashlib.md5(id_str.encode("utf-8")).hexdigest()[:8]

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

                    if batch_data:
                        cursor.executemany(
                            """
                            INSERT OR IGNORE INTO csv_questions
                            (id, file_path, topic_name, question, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            batch_data,
                        )
                        files_processed += 1
                        rows_attempted += len(batch_data)

            except (IOError, csv.Error, UnicodeDecodeError) as e:
                logger.warning(f"Error processing CSV {file_path.name}: {e}")

        conn.commit()
        cursor.execute("SELECT COUNT(*) as total FROM csv_questions")
        total_in_db = cursor.fetchone()["total"]

    return {
        "files_processed": files_processed,
        "rows_processed": rows_attempted,
        "total_in_db": total_in_db,
    }
