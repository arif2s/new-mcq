"""
parsers/pdf_topic_indexer.py

Full-PDF Hierarchical Topic Database Indexer (`pdf_topic_index`), Sibling-Safe
Topic-to-Page Resolver (disambiguating e.g. Bronchopneumonia vs Lobar Pneumonia),
and pypdfium2 Page-to-Picture (Base64 PNG) Renderer for LM Studio VLM synthesis.
"""

from __future__ import annotations

import base64
from contextlib import closing
import io
import logging
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from agents.tema_q.topic_extractor import (
    SIBLING_MUTUAL_EXCLUSIONS,
    extract_all_clinical_modifiers,
    get_topic_aliases,
    normalize_topic_key,
)
from core.database import get_db_connection
from parsers.heading_toc_calibrator import build_document_toc_and_hierarchy

logger = logging.getLogger("medisearch.pdf_topic_indexer")

_TABLE_INITIALIZED = False


def ensure_pdf_topic_index_table() -> None:
    """Creates the `pdf_topic_index` SQLite table and lookup indices if not present."""
    global _TABLE_INITIALIZED
    if _TABLE_INITIALIZED:
        return
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pdf_topic_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pdf_path TEXT NOT NULL,
                pdf_title TEXT NOT NULL,
                engine_source TEXT NOT NULL DEFAULT 'hybrid_toc',
                heading_level INTEGER NOT NULL,
                topic_title TEXT NOT NULL,
                topic_norm TEXT NOT NULL,
                parent_topic TEXT NOT NULL,
                parent_norm TEXT NOT NULL,
                heading_path TEXT NOT NULL,
                heading_path_norm TEXT NOT NULL,
                start_page INTEGER NOT NULL,
                end_page INTEGER NOT NULL,
                start_y0 REAL NOT NULL DEFAULT 0.0,
                end_y1 REAL NOT NULL DEFAULT 1.0,
                updated_at REAL DEFAULT (unixepoch('now'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pti_topic_norm ON pdf_topic_index(topic_norm);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pti_parent_norm ON pdf_topic_index(parent_norm);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pti_pdf_path ON pdf_topic_index(pdf_path);")
        conn.commit()
    _TABLE_INITIALIZED = True


def compute_toc_page_ranges(
    document_toc: Sequence[Dict[str, Any]], total_pages: int
) -> List[Dict[str, Any]]:
    """
    Computes exact (start_page, end_page, start_y0, end_y1) intervals for every H1..H6
    heading in `document_toc` up to the next heading of level <= current level.
    """
    rows: List[Dict[str, Any]] = []
    n = len(document_toc)

    for i, node in enumerate(document_toc):
        lvl = int(node.get("level", 2))
        start_p = max(1, int(node.get("page", 1)))
        start_y0 = float(node.get("y0", 0.0))
        h_path = str(node.get("heading_path") or "").strip()
        parts = [p.strip() for p in h_path.split(">") if p.strip()]
        title = str(node.get("title") or (parts[-1] if parts else "")).strip()
        if not title:
            continue
        parent_topic = parts[-2] if len(parts) >= 2 else (parts[0] if parts else title)

        end_p = max(start_p, int(total_pages or start_p))
        end_y1 = 1.0
        for j in range(i + 1, n):
            nxt = document_toc[j]
            nxt_lvl = int(nxt.get("level", 2))
            if nxt_lvl <= lvl:
                nxt_p = max(start_p, int(nxt.get("page", start_p)))
                nxt_y0 = float(nxt.get("y0", 0.0))
                if nxt_p > start_p and nxt_y0 <= 0.12:
                    end_p = nxt_p - 1
                    end_y1 = 1.0
                else:
                    end_p = nxt_p
                    end_y1 = max(0.05, nxt_y0)
                break

        rows.append({
            "heading_level": lvl,
            "topic_title": title,
            "topic_norm": normalize_topic_key(title),
            "parent_topic": parent_topic,
            "parent_norm": normalize_topic_key(parent_topic),
            "heading_path": h_path,
            "heading_path_norm": normalize_topic_key(h_path.replace(">", " ")),
            "start_page": start_p,
            "end_page": max(start_p, end_p),
            "start_y0": round(start_y0, 4),
            "end_y1": round(end_y1, 4),
        })
    return rows


def index_pdf_topics_to_db(
    pdf_path: Union[str, Path],
    default_title: str = "",
    engine_source: str = "indra_base_heading_opt",
    force_rebuild: bool = False,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> int:
    """
    Runs the full-PDF Multi-Pass H1-H6 TOC Calibrator (Mode B forward streaming)
    and persists all hierarchical topic and subtopic page spans into `pdf_topic_index`.
    """
    ensure_pdf_topic_index_table()
    path = Path(pdf_path).resolve()
    pdf_title = default_title.strip() or path.stem

    toc_payload = build_document_toc_and_hierarchy(
        path,
        default_title=pdf_title,
        full_scan=True,
        force_rebuild=force_rebuild,
        progress_callback=progress_callback,
    )
    doc_toc = toc_payload.get("document_toc") or []
    total_pages = int(toc_payload.get("total_pages") or 1)
    if not doc_toc:
        return 0

    span_rows = compute_toc_page_ranges(doc_toc, total_pages=total_pages)
    with closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM pdf_topic_index WHERE pdf_path = ?", (str(path),))
        conn.executemany(
            """
            INSERT INTO pdf_topic_index (
                pdf_path, pdf_title, engine_source, heading_level,
                topic_title, topic_norm, parent_topic, parent_norm,
                heading_path, heading_path_norm, start_page, end_page, start_y0, end_y1
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(path),
                    pdf_title,
                    engine_source,
                    r["heading_level"],
                    r["topic_title"],
                    r["topic_norm"],
                    r["parent_topic"],
                    r["parent_norm"],
                    r["heading_path"],
                    r["heading_path_norm"],
                    r["start_page"],
                    r["end_page"],
                    r["start_y0"],
                    r["end_y1"],
                )
                for r in span_rows
            ],
        )
        conn.commit()
    return len(span_rows)


def _has_sibling_conflict(query_topic: str, candidate_path: str) -> bool:
    """
    Returns True if `candidate_path` belongs to a mutually exclusive sibling entity
    (e.g., 'Lobar Pneumonia' when searching for 'Bronchopneumonia', or 'Type 2' vs 'Type 1').
    """
    q_low = query_topic.lower().strip()
    c_low = candidate_path.lower().strip()

    # 1. Explicit pneumonia / clinical anatomical pattern exclusions
    pneumonia_siblings = ("bronchopneumonia", "lobar pneumonia", "interstitial pneumonia", "aspiration pneumonia")
    for sib_a in pneumonia_siblings:
        if sib_a in q_low:
            for sib_b in pneumonia_siblings:
                if sib_b != sib_a and sib_b in c_low and sib_a not in c_low:
                    return True

    # 2. Standard mutual exclusion pairs from topic_extractor.py
    for group_a, group_b in SIBLING_MUTUAL_EXCLUSIONS:
        in_a_q = any(re.search(rf"\b{re.escape(t)}\b", q_low) for t in group_a)
        in_b_q = any(re.search(rf"\b{re.escape(t)}\b", q_low) for t in group_b)
        if not (in_a_q ^ in_b_q):
            continue
        in_a_c = any(re.search(rf"\b{re.escape(t)}\b", c_low) for t in group_a)
        in_b_c = any(re.search(rf"\b{re.escape(t)}\b", c_low) for t in group_b)
        if (in_a_q and in_b_c and not in_a_c) or (in_b_q and in_a_c and not in_b_c):
            return True

    # 3. Subtype modifier conflict check (Acute vs Chronic, Type 1 vs Type 2, Stage I vs IV)
    q_mods = extract_all_clinical_modifiers(q_low)
    if q_mods:
        c_mods = extract_all_clinical_modifiers(c_low)
        for mod_cat, q_vals in q_mods.items():
            if mod_cat in c_mods and not (q_vals & c_mods[mod_cat]):
                return True

    return False


def lookup_pdf_topic_pages(
    topic: str,
    subtopic: Optional[str] = None,
    max_results: int = 4,
) -> List[Dict[str, Any]]:
    """
    Finds the exact PDF pages and vertical coordinate bounds for `topic` (and optional `subtopic`,
    e.g., topic='Bronchopneumonia', subtopic='Treatment') from `pdf_topic_index`.
    Strictly disambiguates sibling topics (such as Bronchopneumonia vs. Lobar Pneumonia).
    """
    ensure_pdf_topic_index_table()
    t_norm = normalize_topic_key(topic)
    if not t_norm:
        return []

    aliases = [normalize_topic_key(a) for a in get_topic_aliases(topic, max_aliases=8) if normalize_topic_key(a)]
    if t_norm not in aliases:
        aliases.insert(0, t_norm)
    sub_norm = normalize_topic_key(subtopic or "")

    candidates: List[Dict[str, Any]] = []
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        # Fetch rows matching any topic alias in topic_norm, parent_norm, or heading_path_norm
        like_clauses = " OR ".join(["heading_path_norm LIKE ?"] * len(aliases))
        params = [f"%{a}%" for a in aliases]
        cursor.execute(
            f"""
            SELECT id, pdf_path, pdf_title, engine_source, heading_level,
                   topic_title, topic_norm, parent_topic, parent_norm,
                   heading_path, heading_path_norm, start_page, end_page, start_y0, end_y1
            FROM pdf_topic_index
            WHERE {like_clauses}
            LIMIT 200
            """,
            params,
        )
        for row in cursor.fetchall():
            r_dict = dict(row) if isinstance(row, dict) else {
                "id": row[0], "pdf_path": row[1], "pdf_title": row[2], "engine_source": row[3],
                "heading_level": row[4], "topic_title": row[5], "topic_norm": row[6],
                "parent_topic": row[7], "parent_norm": row[8], "heading_path": row[9],
                "heading_path_norm": row[10], "start_page": row[11], "end_page": row[12],
                "start_y0": row[13], "end_y1": row[14],
            }
            if _has_sibling_conflict(topic, r_dict["heading_path"]):
                continue

            # Compute hierarchical relevance score
            score = 0.0
            if r_dict["topic_norm"] == t_norm:
                score += 0.65
            elif r_dict["topic_norm"] in aliases:
                score += 0.55
            elif r_dict["parent_norm"] == t_norm or r_dict["parent_norm"] in aliases:
                score += 0.50
            else:
                score += 0.30

            if sub_norm:
                if sub_norm in r_dict["topic_norm"]:
                    score += 0.45
                elif sub_norm in r_dict["heading_path_norm"]:
                    score += 0.25
                else:
                    score -= 0.15

            r_dict["match_score"] = round(score, 4)
            candidates.append(r_dict)

    candidates.sort(key=lambda x: (x["match_score"], -x["heading_level"]), reverse=True)
    return candidates[:max_results]


def render_topic_pages_to_base64(
    pdf_path: Union[str, Path],
    start_page: int,
    end_page: int,
    start_y0: float = 0.0,
    end_y1: float = 1.0,
    dpi: int = 175,
    max_pages: int = 3,
    crop_to_subtopic_bounds: bool = True,
) -> List[Tuple[int, str]]:
    """
    Renders the exact PDF page range [start_page..end_page] into Base64 PNG data URLs
    for direct LM Studio Vision-LM (page-as-picture) synthesis.
    When `crop_to_subtopic_bounds=True`, crops out sibling topics above `start_y0` on
    the first page and below `end_y1` on the last page.
    """
    import pypdfium2 as pdfium

    results: List[Tuple[int, str]] = []
    path = Path(pdf_path)
    if not path.is_file():
        return results

    doc = pdfium.PdfDocument(str(path))
    try:
        total = len(doc)
        s_p = max(1, min(total, int(start_page)))
        e_p = max(s_p, min(total, int(end_page), s_p + max_pages - 1))
        scale = float(dpi) / 72.0

        for p_num in range(s_p, e_p + 1):
            page = doc[p_num - 1]
            bitmap = page.render(scale=scale)
            pil_img = bitmap.to_pil()
            w, h = pil_img.size

            if crop_to_subtopic_bounds:
                y_top = int(max(0.0, start_y0 - 0.035) * h) if p_num == s_p and start_y0 > 0.08 else 0
                y_bot = int(min(1.0, end_y1 + 0.035) * h) if p_num == e_p and end_y1 < 0.92 else h
                if (y_bot - y_top) >= int(0.16 * h):
                    pil_img = pil_img.crop((0, y_top, w, y_bot))

            buf = io.BytesIO()
            pil_img.save(buf, format="PNG", optimize=True)
            b64_url = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
            results.append((p_num, b64_url))

            pil_img.close()
            bitmap.close()
            page.close()
    finally:
        doc.close()

    return results