"""
parsers/docling_v1_heading_opt.py

`docling_v1_heading_opt`: Preserves 100% of original Docling v1 layout detection,
reading order, and TableFormer table extraction while calibrating H1-H6 heading
levels and hierarchical breadcrumbs via `heading_toc_calibrator.py`.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from parsers.heading_toc_calibrator import build_document_toc_and_hierarchy

logger = logging.getLogger("medisearch.docling_v1_opt")


def _norm_heading_str(s: str) -> str:
    s_clean = re.sub(r"^[#•*\-–—\s]+", "", (s or "").strip()).replace("**", "")
    return re.sub(r"[^a-z0-9]+", " ", s_clean.lower()).strip()


def extract_page_with_docling_v1_heading_opt(
    file_path: Union[str, Path],
    page_number: int = 1,
    default_title: str = "",
    full_toc_scan: bool = False,
) -> Dict[str, Any]:
    """
    Runs original Docling v1 on `page_number` for high-accuracy layout and table
    detection, then upgrades all Markdown headings and unheaded TOC lines using
    the Multi-Pass H1-H6 TOC Calibrator.
    """
    path = Path(file_path).resolve()
    root_title = default_title.strip() or path.stem

    # 1. Obtain calibrated H1-H6 TOC entries for this page from the Multi-Pass Calibrator
    toc_payload = build_document_toc_and_hierarchy(
        path, default_title=root_title, target_page=page_number, full_scan=full_toc_scan
    )
    page_toc: List[Dict[str, Any]] = list(
        (toc_payload.get("page_toc_map") or {}).get(str(page_number), [])
    )
    init_stack: Dict[int, str] = {
        int(k): str(v)
        for k, v in ((toc_payload.get("page_initial_stacks") or {}).get(str(page_number), {})).items()
    }

    # 2. Run Base Docling v1 (`parsers/docling_parser.py`)
    base_md = ""
    try:
        from parsers.docling_parser import extract_structured_layout as docling_v1_extract
        base_md = str(docling_v1_extract(str(path), page_number) or "")
    except Exception as exc:
        logger.warning("Docling v1 base extraction failed on %s p.%d: %s", path.name, page_number, exc)

    if not base_md.strip():
        try:
            from parsers.pdf_extractor import extract_page_slice
            base_md = str(extract_page_slice(str(path), page_number) or "")
        except Exception:
            base_md = ""

    # 3. Non-Destructively Re-Level Headings in Docling v1 Output Using Calibrated Page TOC
    lines = base_md.splitlines()
    calibrated_lines: List[str] = []
    hierarchy_tree: List[Dict[str, Any]] = []
    headings: List[str] = []
    sections_for_chunks: List[Tuple[str, str, List[str]]] = []

    active_titles: Dict[int, str] = dict(init_stack)
    cur_sec_path = " > ".join([root_title] + [active_titles[k] for k in sorted(active_titles)]) if active_titles else root_title
    cur_sec_sub = active_titles[max(active_titles)] if active_titles else root_title
    cur_sec_blocks: List[str] = []

    used_toc_idx: Set[int] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            calibrated_lines.append("")
            continue

        # Never touch Markdown table rows emitted by Docling v1 TableFormer
        if stripped.startswith("|") and stripped.endswith("|"):
            calibrated_lines.append(line)
            cur_sec_blocks.append(line)
            continue

        is_md_heading = stripped.startswith("#")
        clean_cand = stripped.lstrip("#").strip() if is_md_heading else stripped
        cand_norm = _norm_heading_str(clean_cand)

        matched_te: Optional[Dict[str, Any]] = None
        for idx_t, te in enumerate(page_toc):
            if idx_t in used_toc_idx:
                continue
            tk = _norm_heading_str(te.get("title", ""))
            if tk and (
                cand_norm == tk
                or (is_md_heading and (cand_norm.startswith(tk) or tk.startswith(cand_norm)))
                or (len(tk) >= 8 and len(cand_norm) <= len(tk) + 6 and cand_norm == tk)
            ):
                matched_te = te
                used_toc_idx.add(idx_t)
                break

        if matched_te is not None or is_md_heading:
            lvl = int(matched_te["level"]) if matched_te else min(6, max(1, len(stripped) - len(stripped.lstrip("#"))))
            title_txt = str(matched_te["title"]) if matched_te else clean_cand

            active_titles[lvl] = title_txt
            for deeper in [k for k in list(active_titles) if k > lvl]:
                active_titles.pop(deeper, None)

            h_path = (
                str(matched_te.get("heading_path"))
                if (matched_te and matched_te.get("heading_path"))
                else " > ".join([root_title] + [active_titles[k] for k in sorted(active_titles)])
            )
            headings.append(title_txt)
            node = {
                "page": page_number,
                "level": lvl,
                "tag": f"H{lvl}",
                "title": title_txt,
                "heading_path": h_path,
                "order": len(hierarchy_tree) + 1,
            }
            hierarchy_tree.append(node)

            if cur_sec_blocks:
                sections_for_chunks.append((cur_sec_path, cur_sec_sub, list(cur_sec_blocks)))
                cur_sec_blocks.clear()
            cur_sec_path = h_path
            cur_sec_sub = title_txt

            new_h_line = f"{'#' * lvl} {title_txt}"
            calibrated_lines.append(new_h_line)
            cur_sec_blocks.append(new_h_line)
        else:
            calibrated_lines.append(line)
            cur_sec_blocks.append(line)

    if cur_sec_blocks:
        sections_for_chunks.append((cur_sec_path, cur_sec_sub, list(cur_sec_blocks)))

    final_md = "\n".join(calibrated_lines).strip()
    return {
        "status": "success",
        "engine": "docling_v1_heading_opt",
        "page": page_number,
        "page_number": page_number,
        "markdown": final_md,
        "text": final_md,
        "headings": headings,
        "hierarchy_tree": hierarchy_tree,
        "active_heading_stack": active_titles,
        "document_toc": toc_payload.get("document_toc", []),
        "sections_for_chunks": sections_for_chunks,
    }


def extract_structured_layout_v1_heading_opt(
    file_path: Union[str, Path], page_number: int, **kwargs: Any
) -> str:
    """Drop-in string return wrapper for pipeline.py `_extract_pdf_sync`."""
    res = extract_page_with_docling_v1_heading_opt(file_path, page_number=page_number, **kwargs)
    return str(res.get("markdown") or "")