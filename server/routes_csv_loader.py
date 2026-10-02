import csv
import hashlib
from pathlib import Path
from core.database import get_db_connection

# Utilize pathlib for native Windows path normalization
CSV_DIR = Path("data/csv")

def load_csvs_background():
    CSV_DIR.mkdir(parents=True, exist_ok=True)

    with get_db_connection() as conn:
        cursor = conn.cursor()

        for file_path in CSV_DIR.glob("*.csv"):
            try:
                with open(file_path, "r", encoding="utf-8-sig") as file_handle:
                    reader = csv.DictReader(file_handle)
                    if not reader.fieldnames:
                        continue

                    # Batch rows to drastically reduce I/O and query overhead
                    batch_data = []

                    for i, row in enumerate(reader):
                        topic = row.get("topic_name", "General").strip() or "General"
                        q = row.get("question", "").strip()
                        correct = row.get("correct_answer", "").strip().upper()

                        # Use a Set {} for O(1) membership testing
                        if q and correct in {'A', 'B', 'C', 'D'}:
                            # Use file_path.name to prevent absolute path drift if the project folder is moved
                            id_str = f"{file_path.name}_{i}_{q}"
                            stable_id = f"q_{i}_" + hashlib.md5(id_str.encode()).hexdigest()[:8]

                            batch_data.append((
                                stable_id, str(file_path), topic, q,
                                row.get("option_a", "").strip(),
                                row.get("option_b", "").strip(),
                                row.get("option_c", "").strip(),
                                row.get("option_d", "").strip(),
                                correct,
                                row.get("explanation", "").strip() or "No explanation provided."
                            ))

                    if batch_data:
                        # Use INSERT OR IGNORE to bypass the N+1 SELECT check entirely
                        cursor.executemany("""
                            INSERT OR IGNORE INTO csv_questions
                            (id, file_path, topic_name, question, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, batch_data)

            except (IOError, csv.Error, UnicodeDecodeError) as e:
                # Scope exceptions to file/parsing errors instead of blanketing all exceptions
                print(f"Error processing {file_path.name}: {e}")

        conn.commit()
