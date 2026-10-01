import os
import glob
import csv
import hashlib
from core.database import get_db_connection

CSV_DIR = "data/csv"

def load_csvs_background():
    os.makedirs(CSV_DIR, exist_ok=True)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for file_path in glob.glob(f"{CSV_DIR}/*.csv"):
            try:
                with open(file_path, "r", encoding="utf-8-sig") as file_handle:
                    reader = csv.DictReader(file_handle)
                    if not reader.fieldnames:
                        continue
                    for i, row in enumerate(reader):
                        topic = row.get("topic_name", "General").strip() or "General"
                        q = row.get("question", "").strip()
                        correct = row.get("correct_answer", "").strip().upper()
                        if q and correct in ['A', 'B', 'C', 'D']:
                            id_str = f"{file_path}_{i}_{q}"
                            stable_id = f"q_{i}_" + hashlib.md5(id_str.encode()).hexdigest()[:8]
                            cursor.execute("SELECT id FROM csv_questions WHERE id = ?", (stable_id,))
                            if not cursor.fetchone():
                                cursor.execute("""
                                    INSERT INTO csv_questions (id, file_path, topic_name, question, opt_a, opt_b, opt_c, opt_d, correct_answer, explanation)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    stable_id, file_path, topic, q,
                                    row.get("option_a", "").strip(),
                                    row.get("option_b", "").strip(),
                                    row.get("option_c", "").strip(),
                                    row.get("option_d", "").strip(),
                                    correct,
                                    row.get("explanation", "").strip() or "No explanation provided."
                                ))
            except Exception:
                pass
        conn.commit()
