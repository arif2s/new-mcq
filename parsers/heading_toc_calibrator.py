"""
parsers/heading_toc_calibrator.py

Shared Multi-Step, Multi-Pass H1-H6 Heading Hierarchy & Lazy/Streaming TOC Engine.
Used by both `docling_v1_heading_opt` and `indra_base_heading_opt` to calibrate
heading levels (H1-H6) and build full-PDF hierarchical Tables of Contents
without altering base layout detection or body text reading order.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
import ctypes
import gc
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import numpy as np

from parsers.indra.profiles import LAYOUT_PROFILES, LayoutProfile
from parsers.indra.rules import RegexCatalog, overlap_ratio, rgb_distance, rgb_saturation, union_bbox
from parsers.indra.types import BBox, HeadingPrototype, PageElement, PageElements, RGB

logger = logging.getLogger("medisearch.heading_calibrator")

_TOC_MEM_CACHE: Dict[str, Dict[str, Any]] = {}
_TOC_PROGRESS_STORE: Dict[str, Dict[str, Any]] = {}


def rgb_hue(rgb: Sequence[int]) -> float:
    """Computes circular HSV hue in [0, 360), or -1.0 for achromatic grayscale."""
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d < 15:
        return -1.0
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = ((b - r) / d) + 2
    else:
        h = ((r - g) / d) + 4
    return round((h * 60.0) % 360.0, 2)


def hue_distance(h1: float, h2: float) -> float:
    if h1 < 0.0 or h2 < 0.0:
        return 180.0
    diff = abs(h1 - h2)
    return min(diff, 360.0 - diff)


def norm_font_family(font_name: str) -> str:
    s = str(font_name or "").lower()
    if any(k in s for k in ("mono", "cour", "consol", "fixed")):
        return "mono"
    if any(
        k in s
        for k in (
            "arial", "helv", "sans", "calibri", "futura", "gill",
            "optima", "tahoma", "verdana", "univers", "avenir", "gothic", "segoe",
        )
    ):
        return "sans"
    return "serif"


def colors_visually_match(
    c1: Any, c2: Any, color_tol: float = 38.0, hue_tol: float = 22.0
) -> bool:
    if not isinstance(c1, (tuple, list)) or not isinstance(c2, (tuple, list)):
        return True
    rgb1 = (int(c1[0]), int(c1[1]), int(c1[2]))
    rgb2 = (int(c2[0]), int(c2[1]), int(c2[2]))
    if rgb_distance(rgb1, rgb2) <= color_tol:
        return True
    h1, h2 = rgb_hue(rgb1), rgb_hue(rgb2)
    return (
        h1 >= 0.0
        and h2 >= 0.0
        and hue_distance(h1, h2) <= hue_tol
        and abs(rgb_saturation(rgb1) - rgb_saturation(rgb2)) <= 45
    )


def compare_heading_visual_styles(
    cand: Mapping[str, Any],
    ref: Mapping[str, Any],
    rel_tol: float = 0.14,
    color_tol: float = 38.0,
) -> str:
    """Compares two heading visual signatures and returns 'stronger', 'weaker', or 'same'."""
    c_pk = str(cand.get("prefix_family") or "")
    r_pk = str(ref.get("prefix_family") or "")
    c_acc = bool(cand.get("is_accent", cand.get("is_accent_color", False)))
    r_acc = bool(ref.get("is_accent", ref.get("is_accent_color", False)))
    c_caps = bool(cand.get("is_all_caps", False))
    r_caps = bool(ref.get("is_all_caps", False))
    c_ban = bool(cand.get("in_banner", False))
    r_ban = bool(ref.get("in_banner", False))
    c_fg, r_fg = cand.get("fg_rgb"), ref.get("fg_rgb")
    c_bg, r_bg = cand.get("bg_rgb"), ref.get("bg_rgb")

    fg_same = colors_visually_match(c_fg, r_fg, color_tol=color_tol)
    bg_same = colors_visually_match(c_bg, r_bg, color_tol=color_tol)

    if c_pk and c_pk == r_pk and c_acc == r_acc and fg_same and c_caps == r_caps and c_ban == r_ban and bg_same:
        return "same"
    if c_ban != r_ban:
        return "stronger" if c_ban else "weaker"
    sz_diff = float(cand.get("rel_size", 1.0)) - float(ref.get("rel_size", 1.0))
    if abs(sz_diff) >= rel_tol:
        return "stronger" if sz_diff > 0 else "weaker"
    if c_acc != r_acc:
        return "stronger" if c_acc else "weaker"
    if c_caps != r_caps:
        return "stronger" if c_caps else "weaker"
    if (r_pk and c_pk and c_pk != r_pk) or (
        r_pk in ("DECIMAL", "ROMAN_UPPER", "ALPHA_UPPER") and not c_pk and not c_caps
    ):
        return "weaker"
    return "same"


def compute_prototype_similarity(
    el: PageElement, prototypes: Sequence[HeadingPrototype], tol: float = 0.12
) -> float:
    if not prototypes:
        return 0.85
    best = 0.0
    for p in prototypes:
        sz_s = (
            1.0
            if abs(el.rel_size - p.rel_size) <= max(tol, 0.14)
            else max(0.0, 1.0 - abs(el.rel_size - p.rel_size) / 0.45)
        )
        col_s = 1.0 if colors_visually_match(el.fg_rgb, p.fg_rgb) else 0.25
        ban_s = 1.0 if el.in_banner == p.in_banner else 0.0
        fam_s = (
            1.0
            if (
                el.font_family == p.font_family
                and (el.is_bold == p.is_bold or abs(getattr(el, "thickness", 1.0) - getattr(p, "thickness", 1.0)) <= 0.25)
            )
            else 0.40
        )
        prf_s = 1.0 if (el.prefix_family or "") == (p.prefix_family or "") else 0.35
        sim = 0.50 + 0.50 * (0.30 * sz_s + 0.25 * col_s + 0.15 * ban_s + 0.15 * fam_s + 0.15 * prf_s)
        if sim > best:
            best = sim
    return round(min(1.0, best), 3)


_CHAPTER_RE = re.compile(
    r"^(?:CHAPTER|SECTION|MODULE|UNIT|PART|ANNEX|APPENDIX)\s+(?:[IVXLCDM\d]+|[A-Z])\b",
    re.IGNORECASE,
)
_SITUATION_RE = re.compile(
    r"^\*?\s*(?:Situation\s+[12]|Part\s+[IVX\d]+)\b", re.IGNORECASE
)
_FINITE_VERBS_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|had|can|could|will|would|should|may|might|must|includes?|contains?|causes?|requires?|presents?|shows?)\b",
    re.IGNORECASE,
)
_CONTRIBUTOR_RE = re.compile(
    r"(?i)^(?:\d{1,2}[\.\)\s]+)?(?:Dr\.?\s*\(?Prof\)?|Dr\.|Prof\.|Shri\b|Smt\.|Directorate\s+General\s+of\s+Health)"
)
_TOC_DOT_LEADER_RE = re.compile(r"(?:\.{3,}|(?:\.\s+){3,})\s*\d{1,4}\s*$")


def classify_outline_prefix(text: str) -> Tuple[str, Optional[int], str]:
    """Granular 12-family outline prefix classifier."""
    t = (text or "").strip()
    if re.match(r"^(?:\([XVxv✓✗]\)|[XV]Y\)|\[[XVxv]\])\s*", t):
        return "BULLET", 0, t
    if _CHAPTER_RE.match(t):
        m_n = re.search(r"\b(\d+)\b", t)
        return "CHAPTER", int(m_n.group(1)) if m_n else 1, t
    if m_step := re.match(r"^(?:STEP|STAGE|PHASE|LEVEL|GRADE)\s+(\d{1,2})\b[:.\-–—\s]*(.*)$", t, re.I):
        return "STEP", int(m_step.group(1)), m_step.group(2).strip()
    if m_dot := re.match(r"^(\d{1,2}(?:\.\d{1,2})+)\.?\s+(.*)$", t):
        parts = m_dot.group(1).split(".")
        return f"DOTTED_{min(4, max(2, len(parts)))}", int(parts[-1]), m_dot.group(2).strip()
    if m_dec := re.match(r"^(\d{1,3})[\.\)]\s*(.*)$", t):
        return "DECIMAL", int(m_dec.group(1)), m_dec.group(2).strip()
    if m_rom := re.match(r"^([IVXLCDMivxlcdm]{1,6})[\.\)]\s*(.*)$", t):
        tok = m_rom.group(1)
        if RegexCatalog.ROMAN_NUMERAL.match(tok):
            val = RegexCatalog._roman_to_int(tok.upper())
            if tok.upper() in ("I", "V", "X", "L", "C", "D", "M") and val >= 50:
                return ("ALPHA_UPPER" if tok.isupper() else "ALPHA_LOWER"), ord(tok.lower()) - ord("a") + 1, m_rom.group(2).strip()
            return ("ROMAN_UPPER" if tok.isupper() else "ROMAN_LOWER"), val, m_rom.group(2).strip()
    if m_alp := re.match(r"^([A-Za-h])[\.\)]\s*(.*)$", t):
        ch = m_alp.group(1)
        return ("ALPHA_UPPER" if ch.isupper() else "ALPHA_LOWER"), ord(ch.lower()) - ord("a") + 1, m_alp.group(2).strip()
    if m_par := re.match(r"^\(([a-h0-9ivx]{1,4})\)\s+(.*)$", t, re.I):
        tok = m_par.group(1)
        idx = int(tok) if tok.isdigit() else ord(tok[0].lower()) - ord("a") + 1
        return "PAREN", idx, m_par.group(2).strip()
    if re.match(r"^([-*•–—►▪▫\u2022\u25cf])\s*", t):
        return "BULLET", 0, t
    return "", None, t


def score_and_resolve_heading_candidate(
    el: PageElement,
    profile: LayoutProfile,
    active_stack: Dict[int, HeadingPrototype],
    chapter_prototypes: List[HeadingPrototype],
    has_following_content: bool = True,
) -> None:
    """
    7-Factor Consensus Heading Scorer & Level Resolver (H1..H6).
    Strictly preserves *Situation 1/2 purple headings and a)-d) clinical subheadings.
    """
    hw = profile.heading_weights
    text = el.text.strip()
    if not text or el.is_suppressed_furniture or el.in_table or el.in_figure:
        return

    if _TOC_DOT_LEADER_RE.search(text) or _CONTRIBUTOR_RE.match(text):
        el.role = "paragraph"
        return

    # Exempt *Situation 1 & *Situation 2 clinical protocol dividers
    if _SITUATION_RE.match(text) and (el.is_bold or el.is_accent_color or el.rel_size >= 1.0):
        el.role = "heading"
        el.heading_confidence = 0.92
        el.heading_level = 3 if 2 in active_stack else 2
        return

    if text.lstrip().startswith(("•", "*", "-", ":", ";")):
        el.role = "list_item"
        return

    pf, p_idx, _ = classify_outline_prefix(text)
    el.prefix_family, el.prefix_index = pf, p_idx
    if pf == "BULLET":
        el.role = "list_item"
        return

    words = len(text.split())
    alpha_c = [c for c in text if c.isalpha()]
    if len(alpha_c) < 3 or words > 18:
        if el.role == "heading":
            el.role = "paragraph"
        return

    has_verb = bool(_FINITE_VERBS_RE.search(text))
    ends_sent = text.endswith((".", ";")) and not el.is_all_caps
    if ends_sent and words > 8:
        el.role = "paragraph"
        return

    # Clinical subsection exemption (e.g. "a) Clinical Diagnosis :", "b) Investigations :", "c) Treatment:")
    is_clinical_sub = (
        pf in ("ALPHA_LOWER", "ALPHA_UPPER", "DECIMAL", "ROMAN_LOWER")
        and words <= 6
        and (el.is_bold or text.endswith(":") or el.is_accent_color)
        and not has_verb
    )

    if not is_clinical_sub and has_verb and not el.in_banner and el.rel_size < hw.h2_min_rel_size:
        el.role = "paragraph"
        return

    continues_seq = any(p.prefix_family == pf for p in active_stack.values() if pf)
    sz_sc = max(
        0.65 if (el.is_bold or is_clinical_sub) else 0.0,
        min(1.0, max(0.0, (el.rel_size - 0.92) / max(0.15, hw.h1_min_rel_size - 0.92))),
    )
    proto_sim = compute_prototype_similarity(el, chapter_prototypes, hw.rel_size_tier_tol)

    factors = {
        "font_size_tier": round(sz_sc, 3),
        "foreground_color": 1.0 if el.is_accent_color else (0.82 if getattr(el, "fg_hue", -1.0) >= 0.0 else (0.75 if el.is_bold else 0.25)),
        "background_banner": 1.0 if el.in_banner else 0.40,
        "font_family_weight": 1.0 if (el.is_bold or getattr(el, "thickness", 1.0) >= 1.22 or el.role == "heading") else (0.70 if el.is_all_caps else 0.20),
        "outline_prefix": 1.0 if pf in ("CHAPTER", "DOTTED_2", "DOTTED_3", "DOTTED_4", "DECIMAL", "ROMAN_UPPER", "ALPHA_UPPER", "ALPHA_LOWER", "ROMAN_LOWER") else 0.55,
        "chapter_prototype_similarity": proto_sim,
        "contextual_sequence": 1.0 if continues_seq else 0.75,
    }

    # Support both 7-factor and legacy 6-factor weight dictionaries seamlessly
    fw = hw.factor_weights
    if "font_size_tier" in fw:
        conf = sum(factors[k] * float(fw.get(k, 0.0)) for k in factors)
    else:
        conf = (
            factors["font_size_tier"] * float(fw.get("size", 0.28))
            + factors["font_family_weight"] * float(fw.get("weight", 0.20))
            + factors["foreground_color"] * float(fw.get("color", 0.16))
            + (1.0 if el.is_all_caps else 0.3) * float(fw.get("case", 0.10))
            + factors["background_banner"] * float(fw.get("banner", 0.14))
            + factors["outline_prefix"] * float(fw.get("prefix", 0.12))
        )

    if el.in_banner or pf == "CHAPTER" or (el.is_all_caps and (el.is_bold or el.is_accent_color)) or is_clinical_sub:
        conf = max(conf, hw.min_heading_confidence + 0.06)
    elif (el.is_bold or el.is_accent_color or el.role == "heading") and words <= 14 and not ends_sent:
        conf = max(conf, hw.min_heading_confidence + 0.02)

    if conf < hw.min_heading_confidence:
        el.role = "list_item" if RegexCatalog.BULLET_MARKER.match(text) else "paragraph"
        return

    el.role = "heading"
    el.heading_confidence = round(min(1.0, conf), 4)
    el.factor_scores = factors

    # Resolve Preliminary Level (H1..H6)
    if pf == "CHAPTER" or (el.rel_size >= hw.h1_min_rel_size and (el.in_banner or 1 not in active_stack)):
        el.heading_level = 1
    elif pf.startswith("DOTTED_"):
        el.heading_level = min(6, max(2, int(pf.split("_")[1])))
    elif el.in_banner or (el.is_all_caps and not pf and el.rel_size >= hw.h3_min_rel_size) or (el.is_bold and el.rel_size >= 1.40 and 1 in active_stack):
        el.heading_level = 2
    elif pf in ("ALPHA_LOWER", "ROMAN_LOWER", "PAREN"):
        el.heading_level = 4 if 3 in active_stack else 3
    elif pf in ("DECIMAL", "ROMAN_UPPER", "ALPHA_UPPER"):
        el.heading_level = 3 if 2 in active_stack else 2
    else:
        deepest = max(active_stack.keys(), default=1)
        el.heading_level = min(6, deepest + 1) if deepest >= 2 else (3 if 2 in active_stack else 2)


def _get_toc_disk_cache_path(pdf_path: Path) -> Path:
    stat = pdf_path.stat()
    cache_dir = Path(tempfile.gettempdir()) / "clinical_ai_vault_toc_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    sig = f"{pdf_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|v38_hybrid"
    return cache_dir / f"toc_{hashlib.sha256(sig.encode('utf-8')).hexdigest()[:24]}.json"


def iter_prescan_pdf_primitives(
    pdf_path: Path, page_numbers: Iterable[int]
) -> Iterator[Tuple[int, Dict[str, Any]]]:
    """
    Fast pypdfium2 C-API primitive extractor for Pass 1 TOC scanning.
    Samples multiple interior points per text rect for accurate font size, bold weight,
    and RGB fill color, and extracts colored vector banner rectangles.
    """
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        total_pages = len(doc)
        for p_num in page_numbers:
            if p_num < 1 or p_num > total_pages:
                continue
            page = doc[p_num - 1]
            w = float(page.get_width()) or 612.0
            h = float(page.get_height()) or 792.0
            tp = page.get_textpage()
            raw_runs: List[Dict[str, Any]] = []
            try:
                n_rects = tp.count_rects()
                for r_i in range(n_rects):
                    x0, y0, x1, y1 = tp.get_rect(r_i)
                    txt = RegexCatalog.clean_text(tp.get_text_bounded(x0, y0, x1, y1))
                    if not txt:
                        continue
                    c_idx = -1
                    for frac in (0.25, 0.50, 0.75, 0.10):
                        c_idx = pdfium_c.FPDFText_GetCharIndexAtPos(
                            tp, x0 + frac * max(2.0, x1 - x0), 0.5 * (y0 + y1), 5.0, 5.0
                        )
                        if c_idx >= 0:
                            break
                    fsz = float(pdfium_c.FPDFText_GetFontSize(tp, c_idx)) if c_idx >= 0 else max(8.0, abs(y1 - y0))
                    fw = int(pdfium_c.FPDFText_GetFontWeight(tp, c_idx)) if c_idx >= 0 else 400
                    f_buf = ctypes.create_string_buffer(128)
                    f_flags = ctypes.c_int()
                    if c_idx >= 0:
                        pdfium_c.FPDFText_GetFontInfo(tp, c_idx, f_buf, 128, ctypes.byref(f_flags))
                    fname = f_buf.value.decode("utf-8", errors="ignore")
                    is_b = fw >= 600 or any(k in fname.lower() for k in ("bold", "black", "heavy", "demi"))
                    cr, cg, cb, ca = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
                    fg: RGB = (0, 0, 0)
                    if c_idx >= 0 and pdfium_c.FPDFText_GetFillColor(
                        tp, c_idx, ctypes.byref(cr), ctypes.byref(cg), ctypes.byref(cb), ctypes.byref(ca)
                    ):
                        fg = (int(cr.value), int(cg.value), int(cb.value))
                    top_pt = max(0.0, h - y1)
                    bot_pt = min(h, h - y0)
                    raw_runs.append({
                        "text": txt,
                        "x0": float(x0),
                        "top": float(top_pt),
                        "x1": float(x1),
                        "bottom": float(bot_pt),
                        "size_pt": max(6.5, fsz),
                        "fg_rgb": fg,
                        "font_family": norm_font_family(fname),
                        "is_bold": is_b,
                    })
            finally:
                tp.close()

            # Extract vector background banners (e.g. colored section headers)
            banners_out: List[Tuple[BBox, RGB]] = []
            try:
                n_objs = min(int(pdfium_c.FPDFPage_CountObjects(page)), 400)
                for o_i in range(n_objs):
                    obj = pdfium_c.FPDFPage_GetObject(page, o_i)
                    if obj and pdfium_c.FPDFPageObj_GetType(obj) == 1:
                        l_c, b_c, r_c, t_c = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
                        if pdfium_c.FPDFPageObj_GetBounds(
                            obj, ctypes.byref(l_c), ctypes.byref(b_c), ctypes.byref(r_c), ctypes.byref(t_c)
                        ):
                            bw, bh = float(r_c.value - l_c.value), float(t_c.value - b_c.value)
                            if bw >= 0.20 * w and 6.0 <= bh <= 68.0:
                                pr, pg, pb, pa = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
                                if (
                                    pdfium_c.FPDFPageObj_GetFillColor(
                                        obj, ctypes.byref(pr), ctypes.byref(pg), ctypes.byref(pb), ctypes.byref(pa)
                                    )
                                    and pa.value > 0
                                    and rgb_distance((int(pr.value), int(pg.value), int(pb.value)), (255, 255, 255)) >= 14.0
                                ):
                                    ban_bb: BBox = (
                                        max(0.0, float(l_c.value) / w),
                                        max(0.0, (h - float(t_c.value)) / h),
                                        min(1.0, float(r_c.value) / w),
                                        min(1.0, (h - float(b_c.value)) / h),
                                    )
                                    banners_out.append((ban_bb, (int(pr.value), int(pg.value), int(pb.value))))
            except Exception:
                pass
            page.close()

            # Group horizontal runs on the same baseline
            raw_runs.sort(key=lambda r: (round(r["top"], 1), r["x0"]))
            merged_lines: List[Dict[str, Any]] = []
            for r in raw_runs:
                if (
                    merged_lines
                    and abs(r["top"] - merged_lines[-1]["top"]) <= 3.0
                    and (r["x0"] - merged_lines[-1]["x1"]) <= max(14.0, 1.8 * r["size_pt"])
                ):
                    prev = merged_lines[-1]
                    prev["text"] = RegexCatalog.clean_text(f"{prev['text']} {r['text']}")
                    prev["x1"] = max(prev["x1"], r["x1"])
                    prev["bottom"] = max(prev["bottom"], r["bottom"])
                    prev["size_pt"] = max(prev["size_pt"], r["size_pt"])
                    prev["is_bold"] = prev["is_bold"] or r["is_bold"]
                else:
                    merged_lines.append(dict(r, first_bold=r["is_bold"]))

            lines_out = [
                {
                    "text": m["text"],
                    "bbox_norm": (m["x0"] / w, m["top"] / h, m["x1"] / w, m["bottom"] / h),
                    "size_pt": m["size_pt"],
                    "fg_rgb": m["fg_rgb"],
                    "font_family": m["font_family"],
                    "is_bold": m["is_bold"],
                    "first_bold": m.get("first_bold", m["is_bold"]),
                }
                for m in merged_lines
            ]
            yield p_num, {"width": w, "height": h, "lines": lines_out, "banners": banners_out}
    finally:
        doc.close()


def build_document_toc_and_hierarchy(
    pdf_path: Union[str, Path],
    default_title: str = "",
    target_page: Optional[int] = None,
    full_scan: bool = True,
    force_rebuild: bool = False,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    """
    Dual-Mode Multi-Pass H1-H6 TOC & Hierarchy Builder:
    - Mode B (`full_scan=True`): Monotonic Forward Chapter-Horizon Streaming across 1..N_pages
      (flushes and resolves each chapter at H1 boundaries, keeping RAM < 25 MB across 5,000 pages).
    - Mode A (`full_scan=False`): Lazy Bidirectional H1 Lookup around `target_page`.
    """
    import pypdfium2 as pdfium

    path = Path(pdf_path).resolve()
    stat = path.stat()
    full_key = f"{path}|{stat.st_size}|{stat.st_mtime_ns}|full"
    req_key = full_key if full_scan else f"{path}|{stat.st_size}|{stat.st_mtime_ns}|win_{target_page or 1}"

    if not force_rebuild and (full_key in _TOC_MEM_CACHE or (not full_scan and req_key in _TOC_MEM_CACHE)):
        return _TOC_MEM_CACHE[full_key if full_key in _TOC_MEM_CACHE else req_key]

    cache_file = _get_toc_disk_cache_path(path)
    if not force_rebuild and cache_file.is_file():
        try:
            loaded = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and "document_toc" in loaded:
                _TOC_MEM_CACHE[full_key] = loaded
                return loaded
        except Exception:
            pass

    doc = pdfium.PdfDocument(str(path))
    total_pages = len(doc)
    doc.close()

    root_title = default_title.strip() or path.stem
    do_full = bool(full_scan or total_pages <= 12)
    profile = LAYOUT_PROFILES["native_digital"]
    hw = profile.heading_weights

    running_header_counts: Counter[str] = Counter()
    running_footer_counts: Counter[str] = Counter()
    page_candidates: List[Tuple[int, List[PageElement]]] = []
    scanned_set: Set[int] = set()

    active_stack: Dict[int, HeadingPrototype] = {}
    active_titles: Dict[int, str] = {}
    chapter_prototypes: Dict[str, List[HeadingPrototype]] = defaultdict(list)
    document_toc: List[Dict[str, Any]] = []
    page_toc_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    page_init_stacks: Dict[str, Dict[int, str]] = {}
    page_fin_stacks: Dict[str, Dict[int, str]] = {}
    heading_mismatches: List[Dict[str, Any]] = []
    seen_h1_sigs: Set[str] = set()

    def _furn_sig(s: str) -> str:
        return re.sub(r"\d+", "#", re.sub(r"[^a-z0-9#]+", " ", s.lower())).strip()

    def _extract_page_candidates(p_num: int, raw: Dict[str, Any]) -> Tuple[List[PageElement], bool]:
        scanned_set.add(p_num)
        raw_lines = raw.get("lines") or []
        banners = raw.get("banners") or []
        sizes = [float(ln.get("size_pt", 10.0)) for ln in raw_lines]
        med_sz = float(np.median(sizes)) if sizes else 10.0

        els: List[PageElement] = []
        for idx, ln in enumerate(raw_lines):
            t = str(ln.get("text", "")).strip()
            if not t:
                continue
            bb: BBox = tuple(ln["bbox_norm"])  # type: ignore
            if bb[3] <= 0.065 and len(t) <= 95:
                running_header_counts[_furn_sig(t)] += 1
            if bb[1] >= 0.915 and len(t) <= 95:
                running_footer_counts[_furn_sig(t)] += 1

            sz = float(ln.get("size_pt", 10.0))
            rel_sz = round(sz / max(6.0, med_sz), 3)
            fg: RGB = tuple(ln.get("fg_rgb", (0, 0, 0)))  # type: ignore
            bg: RGB = (255, 255, 255)
            in_ban = False
            for ban_bb, ban_rgb in banners:
                if overlap_ratio(bb, ban_bb) >= 0.45:
                    in_ban = True
                    bg = ban_rgb
                    break

            alpha = [c for c in t if c.isalpha()]
            is_caps = bool(alpha) and (sum(c.isupper() for c in alpha) / len(alpha) >= 0.85)
            is_acc = rgb_distance(fg, (0, 0, 0)) >= hw.chromatic_rgb_dist_min and rgb_saturation(fg) >= hw.chromatic_saturation_min
            pf, p_idx, _ = classify_outline_prefix(t)

            el = PageElement(
                element_id=idx + 1,
                text=t,
                bbox_norm=bb,
                size_pt=sz,
                rel_size=rel_sz,
                fg_rgb=fg,
                bg_rgb=bg,
                font_family=ln.get("font_family", "serif"),
                is_bold=bool(ln.get("is_bold", False)),
                first_bold=bool(ln.get("first_bold", False)),
                is_all_caps=is_caps,
                is_accent_color=is_acc,
                in_banner=in_ban,
                prefix_family=pf,
                prefix_index=p_idx,
                fg_hue=rgb_hue(fg),
                bg_hue=rgb_hue(bg),
                thickness=1.55 if ln.get("is_bold") else 1.0,
            )
            els.append(el)

        # Merge wrapped multi-line headings (including *Situation 1 & 2 and CHAPTER + Title pairs)
        merged_els: List[PageElement] = []
        i = 0
        while i < len(els):
            curr = els[i]
            if i + 1 < len(els):
                nxt = els[i + 1]
                v_gap = nxt.bbox_norm[1] - curr.bbox_norm[3]
                is_sit = bool(_SITUATION_RE.match(curr.text)) and colors_visually_match(curr.fg_rgb, nxt.fg_rgb)
                is_ch = bool(_CHAPTER_RE.match(curr.text)) and len(nxt.text.split()) <= 10
                is_acr = bool(re.match(r"^\([A-Za-z0-9\s,\-/]{2,25}\)\s*[:.]?$", nxt.text.strip()))
                style_match = (
                    abs(curr.rel_size - nxt.rel_size) <= 0.10
                    and curr.is_bold == nxt.is_bold
                    and colors_visually_match(curr.fg_rgb, nxt.fg_rgb)
                    and curr.in_banner == nxt.in_banner
                    and len(nxt.text.split()) <= 8
                    and not curr.text.endswith((".", ";"))
                    and not _FINITE_VERBS_RE.search(nxt.text)
                )
                if 0.0 <= v_gap <= 0.032 and (is_sit or is_ch or is_acr or (style_match and (curr.is_bold or curr.is_all_caps or curr.in_banner or curr.is_accent_color or curr.rel_size >= 1.15))):
                    sep = ": " if (is_ch and not curr.text.endswith((":", "."))) else " "
                    comb_t = RegexCatalog.clean_text(f"{curr.text}{sep}{nxt.text}")
                    curr.text = comb_t
                    curr.bbox_norm = union_bbox(curr.bbox_norm, nxt.bbox_norm)
                    curr.rel_size = max(curr.rel_size, nxt.rel_size)
                    pf_c, p_idx_c, _ = classify_outline_prefix(comb_t)
                    curr.prefix_family = "CHAPTER" if curr.prefix_family == "CHAPTER" else pf_c
                    curr.prefix_index = p_idx_c if pf_c else curr.prefix_index
                    els[i + 1] = curr
                    i += 1
                    continue
            merged_els.append(curr)
            i += 1

        cands = [
            e for e in merged_els
            if 1 <= len(e.text.split()) <= 18
            and (e.is_bold or e.is_all_caps or e.is_accent_color or e.in_banner or e.rel_size >= 1.05 or bool(e.prefix_family) or bool(_SITUATION_RE.match(e.text)))
        ]
        has_h1 = any(
            e.prefix_family == "CHAPTER"
            or (e.in_banner and e.rel_size >= hw.h2_min_rel_size and e.bbox_norm[1] <= 0.28)
            or (e.rel_size >= hw.h1_min_rel_size and (e.is_bold or e.is_all_caps or e.is_accent_color) and e.bbox_norm[1] <= 0.30)
            for e in cands
        )
        return cands, has_h1

    def _flush_chapter_batch(batch: List[Tuple[int, List[PageElement]]]) -> None:
        if not batch:
            return
        batch.sort(key=lambda x: x[0])
        chapter_has_banners = any(
            el.in_banner and el.prefix_family != "CHAPTER" and len(el.text.split()) >= 2
            for _, c_list in batch for el in c_list
        )
        for p_num, cands in batch:
            page_init_stacks[str(p_num)] = dict(active_titles)
            chapter_key = active_titles.get(1, root_title)
            page_nodes: List[Dict[str, Any]] = []
            order_idx = 0

            for idx_c, el in enumerate(cands):
                sig = _furn_sig(el.text)
                if (
                    (el.bbox_norm[3] <= 0.075 and running_header_counts[sig] >= 2)
                    or (el.bbox_norm[1] >= 0.915 and running_footer_counts[sig] >= 2)
                    or (p_num > 1 and el.bbox_norm[3] <= 0.14 and sig in seen_h1_sigs)
                    or RegexCatalog.PAGE_NUMBER.match(el.text)
                    or RegexCatalog.RUNNING_HEADER.match(re.sub(r"^\d+[\.\)]\s*", "", el.text))
                ):
                    continue

                score_and_resolve_heading_candidate(
                    el, profile, active_stack, chapter_prototypes[chapter_key], idx_c + 1 < len(cands)
                )
                if el.role != "heading" or el.heading_level is None:
                    continue

                raw_lvl = el.heading_level
                lvl = raw_lvl
                cand_sig = {
                    "prefix_family": el.prefix_family,
                    "is_accent": el.is_accent_color,
                    "is_all_caps": el.is_all_caps,
                    "in_banner": el.in_banner,
                    "rel_size": el.rel_size,
                    "fg_rgb": el.fg_rgb,
                    "bg_rgb": el.bg_rgb,
                    "is_bold": el.is_bold,
                }

                # Reconcile sibling & parent levels across chapter
                same_pref_lvl = next(
                    (
                        k for k in sorted(active_stack)
                        if k >= 2
                        and el.prefix_family
                        and active_stack[k].prefix_family == el.prefix_family
                        and abs(el.rel_size - active_stack[k].rel_size) <= hw.rel_size_tier_tol
                        and el.in_banner == active_stack[k].in_banner
                    ),
                    None,
                )
                if el.prefix_family == "CHAPTER" or (p_num == 1 and el.rel_size >= hw.h1_min_rel_size and 1 not in active_stack):
                    lvl = 1
                elif same_pref_lvl is not None:
                    lvl = same_pref_lvl
                elif el.in_banner and not el.prefix_family:
                    lvl = 2
                elif _SITUATION_RE.match(el.text):
                    lvl = 3 if 2 in active_stack else 2
                elif el.prefix_family in ("ALPHA_LOWER", "ROMAN_LOWER", "PAREN"):
                    lvl = 4 if 3 in active_stack else 3
                elif el.is_all_caps and not el.prefix_family:
                    lvl = 3 if (2 in active_stack and chapter_has_banners and not el.in_banner) else 2

                # Prevent hierarchy jumps > 1 level deeper than current parent
                if active_stack and lvl > 1:
                    parent_max = max((k for k in active_stack if k < lvl), default=max(active_stack))
                    lvl = min(lvl, parent_max + 1)

                el.heading_level = lvl
                if lvl == 1:
                    seen_h1_sigs.add(sig)
                if lvl != raw_lvl:
                    heading_mismatches.append({
                        "page": p_num,
                        "heading": el.text,
                        "local_level": f"H{raw_lvl}",
                        "preassigned_level": f"H{lvl}",
                        "reason": "Cross-page chapter hierarchy reconciliation",
                    })

                proto = HeadingPrototype(
                    level=lvl,
                    rel_size=el.rel_size,
                    fg_rgb=el.fg_rgb,
                    bg_rgb=el.bg_rgb,
                    in_banner=el.in_banner,
                    is_all_caps=el.is_all_caps,
                    is_bold=el.is_bold,
                    is_accent=el.is_accent_color,
                    prefix_family=el.prefix_family,
                    fg_hue=el.fg_hue,
                    bg_hue=el.bg_hue,
                    font_family=el.font_family,
                    thickness=el.thickness,
                )
                active_stack[lvl] = proto
                active_titles[lvl] = el.text
                for deeper in [k for k in list(active_stack) if k > lvl]:
                    del active_stack[deeper]
                    active_titles.pop(deeper, None)

                chapter_key = active_titles.get(1, root_title)
                c_protos = chapter_prototypes[chapter_key]
                if len(c_protos) >= 24:
                    c_protos.pop(0)
                c_protos.append(proto)

                order_idx += 1
                el.heading_path = " > ".join([root_title] + [active_titles[k] for k in sorted(active_titles)])
                node = {
                    "page": p_num,
                    "level": lvl,
                    "tag": f"H{lvl}",
                    "title": el.text,
                    "heading_path": el.heading_path,
                    "order": order_idx,
                    "confidence": el.heading_confidence,
                    "factor_scores": el.factor_scores,
                    "x0": round(el.bbox_norm[0], 4),
                    "y0": round(el.bbox_norm[1], 4),
                    "x1": round(el.bbox_norm[2], 4),
                    "y1": round(el.bbox_norm[3], 4),
                }
                page_nodes.append(node)
                document_toc.append(node)

            page_fin_stacks[str(p_num)] = dict(active_titles)
            if page_nodes:
                page_toc_map[str(p_num)] = page_nodes
            cands.clear()
        batch.clear()

    if do_full:
        # MODE B: Monotonic Forward Chapter-Horizon Streaming (5,000+ Page Safe)
        for step_idx, (p_num, raw_page) in enumerate(
            iter_prescan_pdf_primitives(path, range(1, total_pages + 1)), start=1
        ):
            cands, has_h1 = _extract_page_candidates(p_num, raw_page)
            if page_candidates and ((has_h1 and len(page_candidates) >= 2) or len(page_candidates) >= 80):
                _flush_chapter_batch(page_candidates)
            page_candidates.append((p_num, cands))
            if step_idx % 64 == 0:
                gc.collect()
            if callable(progress_callback):
                try:
                    progress_callback(p_num, total_pages)
                except Exception:
                    pass
        _flush_chapter_batch(page_candidates)
    else:
        # MODE A: Lazy Bidirectional H1 Lookup around target_page
        tp = max(1, min(total_pages, int(target_page or 1)))
        for p_b in range(tp, max(0, tp - 96), -1):
            for p_num, raw_page in iter_prescan_pdf_primitives(path, (p_b,)):
                cands, has_h1 = _extract_page_candidates(p_num, raw_page)
                page_candidates.append((p_num, cands))
                if has_h1:
                    break
            else:
                continue
            break
        if 1 not in scanned_set:
            for p_num, raw_page in iter_prescan_pdf_primitives(path, (1,)):
                cands, _ = _extract_page_candidates(p_num, raw_page)
                page_candidates.append((p_num, cands))
        for p_f in range(tp + 1, min(total_pages, tp + 64) + 1):
            for p_num, raw_page in iter_prescan_pdf_primitives(path, (p_f,)):
                cands, has_h1 = _extract_page_candidates(p_num, raw_page)
                page_candidates.append((p_num, cands))
                if has_h1:
                    break
            else:
                continue
            break
        _flush_chapter_batch(page_candidates)

    payload = {
        "is_full_scan": len(scanned_set) == total_pages,
        "total_pages": total_pages,
        "scanned_pages": sorted(scanned_set),
        "document_toc": document_toc,
        "page_toc_map": dict(page_toc_map),
        "page_initial_stacks": page_init_stacks,
        "page_final_stacks": page_fin_stacks,
        "heading_mismatches": heading_mismatches,
    }
    if payload["is_full_scan"] and document_toc:
        try:
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass

    store_key = full_key if payload["is_full_scan"] else req_key
    _TOC_MEM_CACHE[store_key] = payload
    return payload

# --- Phase 3 Fast Fuzzy TOC Matching & SHA-256 VLM Cache ---
from difflib import SequenceMatcher as _SequenceMatcher
from functools import lru_cache as _lru_cache
import hashlib as _hashlib
import json as _json
from pathlib import Path as _Path
import re as _re

try:
    from rapidfuzz import fuzz as _rapidfuzz
except ImportError:
    _rapidfuzz = None

_NORM_HEADING_RE = _re.compile(r"[^a-z0-9\s]+")
_TOC_CACHE_DIR = _Path("storage/cache/toc_vlm")


@_lru_cache(maxsize=4096)
def normalize_heading_key(text: str) -> str:
    if not text:
        return ""
    return " ".join(_NORM_HEADING_RE.sub(" ", text.lower()).strip().split())


def fast_heading_similarity(toc_title: str, body_heading: str) -> float:
    a, b = normalize_heading_key(toc_title), normalize_heading_key(body_heading)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) / max(len(a), len(b)) < 0.25 and a not in b and b not in a:
        return 0.0
    if _rapidfuzz is not None:
        return float(_rapidfuzz.token_set_ratio(a, b)) / 100.0
    return _SequenceMatcher(None, a, b).ratio()


def get_cached_vlm_toc(pdf_path: str | _Path, page_numbers: list[int] | None = None) -> list[dict] | None:
    p = _Path(pdf_path)
    if not p.exists():
        return None
    st = p.stat()
    digest = _hashlib.sha256(f"{p.resolve()}:{st.st_size}:{st.st_mtime_ns}:{page_numbers or []}".encode()).hexdigest()
    cf = _TOC_CACHE_DIR / f"{digest}.json"
    if cf.exists():
        try:
            return _json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def save_cached_vlm_toc(pdf_path: str | _Path, toc_entries: list[dict], page_numbers: list[int] | None = None) -> None:
    p = _Path(pdf_path)
    if not p.exists():
        return
    _TOC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    st = p.stat()
    digest = _hashlib.sha256(f"{p.resolve()}:{st.st_size}:{st.st_mtime_ns}:{page_numbers or []}".encode()).hexdigest()
    (_TOC_CACHE_DIR / f"{digest}.json").write_text(_json.dumps(toc_entries, ensure_ascii=False), encoding="utf-8")
