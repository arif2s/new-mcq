from __future__ import annotations

import colorsys
import html
import math
import re
from typing import Any, Optional, Sequence, Union

from parsers.indra.types import BBox, PageElement, RGB


class RegexCatalog:
    """Catalog of pre-compiled regular expressions for PDF text and layout analysis."""

    CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
    MULTIPLE_SPACES = re.compile(r"[ \t]+")
    PAGE_NUMBER = re.compile(r"^(?:page\s+)?(\d{1,4})(?:\s+of\s+\d{1,4})?$", re.IGNORECASE)
    RUNNING_HEADER = re.compile(
        r"^(?:guidelines?\s+for|chapter\s+\d+|section\s+\d+|handbook|manual|who\b|mohfw\b|national\s+programme\s+for\s+health\s+care|nphce\b)",
        re.IGNORECASE,
    )
    ROMAN_NUMERAL = re.compile(
        r"^(?=[MDCLXVI])M*(?:C[MD]|D?C{0,3})(?:X[CL]|L?X{0,3})(?:I[XV]|V?I{0,3})$",
        re.IGNORECASE,
    )
    ICON_BULLET_MARKER = re.compile(r"^(?:\([XVxv✓✗]\)|[XV]Y\)|\[[XVxv]\])\s*(.*)$")
    BULLET_MARKER = re.compile(
        r"^(?:[-*•–—►▪▫o\u2022\u25cf\u25aa\u25ab]|\([XVxv✓✗]\)|[XV]Y\)|\(?\d{1,3}[\.\)\:-]|\(?[A-Za-z][\.\)\:-])(?:\s+|$)"
    )
    TOC_DOT_LEADER = re.compile(r"(?:\.{3,}|(?:\.\s+){3,})\s*\d{1,4}\s*$")
    CONTRIBUTOR_ROW = re.compile(
        r"(?i)^(?:\d{1,2}[\.\)\s]+)?(?:Dr\.?\s*\(?Prof\)?|Dr\.|Prof\.|Shri\b|Smt\.|Mr\.|Ms\.|Directorate\s+General\s+of\s+Health)"
    )
    CHAPTER_HEADER = re.compile(
        r"^(?:CHAPTER|SECTION|MODULE|UNIT|PART|ANNEX|APPENDIX)\s+(?:[IVXLCDM\d]+|[A-Z])\b",
        re.IGNORECASE,
    )
    SITUATION_HEADER = re.compile(
        r"^\*?\s*(?:Situation\s+[12]|Part\s+[IVX\d]+)\b",
        re.IGNORECASE,
    )
    TRAILING_CONNECTOR = re.compile(
        r"\b(?:and|or|of|in|to|for|with|on|at|from|by|as|into|during|including|versus|vs\.?|&|—|–|-)\s*$",
        re.IGNORECASE,
    )
    FINITE_PROSE_VERBS = re.compile(
        r"\b(?:is|are|was|were|has|have|had|can|could|will|would|shall|should|may|might|must|includes?|contains?|consists?|occurs?|causes?|results?|produces?|requires?|provides?|indicates?|demonstrates?|presents?|shows?|appears?|remains?|becomes?)\b",
        re.IGNORECASE,
    )
    AUTHOR_OR_BYLINE = re.compile(
        r"(?i)^(?:by\s+[A-Z]|.*[,\s]+(?:M\.?D\.?|Ph\.?D\.?|M\.?B\.?B\.?S\.?|F\.?R\.?C\.?P\.?|M\.?S\.?|D\.?N\.?B\.?|H\.?o\.?D\.?|AIIMS)\b)"
    )
    RUNIN_HEADING_COLON = re.compile(
        r"^(?P<head>(?:(?:\d{1,2}(?:\.\d{1,2})*[\.\)]|[A-Za-h][\.\)]|[IVXLCDMivxlcdm]{1,6}[\.\)])\s+)?[A-Z][A-Za-z0-9\s,\-/()]{2,56}?)\s*(?:[:—–]|\s-\s)\s+(?P<body>[A-Za-z0-9].{6,})$"
    )
    RUNIN_ALLCAPS_PERIOD = re.compile(
        r"^(?P<head>(?:(?:\d{1,2}(?:\.\d{1,2})*|[A-Z]|[IVXLCDM]{1,6})\.\s+)?[A-Z][A-Z0-9\s,\-/()]{3,55})\.\s+(?P<body>[A-Z][a-z].{4,})$"
    )
    RUNIN_BOLD_PERIOD = re.compile(
        r"^\*\*(?P<head>(?:(?:\d{1,2}(?:\.\d{1,2})*|[A-Za-h]|[IVXLCDMivxlcdm]{1,6})[\.\)]\s+)?[A-Z][A-Za-z0-9\s,\-/()]{2,52}?)\.?\*\*\s*\.?\s+(?P<body>[A-Z0-9].{6,})$"
    )
    WRAPPED_ALLCAPS_TAIL = re.compile(
        r"^(?P<tail>[A-Z]{2,}[A-Za-z]*(?:\s+[A-Z]{2,}[A-Za-z]*){0,3})\.\s+(?P<body>[A-Z][a-z].*)$"
    )
    GLUED_SUBSECTION = re.compile(
        r"^(?P<Before>.+?[\.\!\?\:\;\)])\s+(?P<Sub>(?:[A-Za-h]\.|[IVXLCDMivxlcdm]{1,6}[\.\)]|\d{1,2}(?:\.\d{1,2})*\.)\s+[A-Z][A-Za-z\s,\-/()]{2,60})$"
    )
    GLUED_CLINICAL_SUB = re.compile(
        r"^(?P<Before>.{12,}?[\.\!\?\;\)])\s+(?P<Sub>(?:Clinical\s+Features|Etiology|Pathogenesis|Diagnosis|Treatment|Management|Prognosis|Complications|Prevention|Epidemiology|Signs\s+and\s+Symptoms)\s*[:.]?\s*)$",
        re.IGNORECASE,
    )
    RUN_IN_HEADING = RUNIN_HEADING_COLON
    RUN_IN_EMDASH = re.compile(
        r"^(?P<title>(?:[A-Z][A-Za-z0-9\s,\/\-]{2,40}?))\s*(?:—|--)\s*(?P<body>[A-Z0-9].*)$"
    )
    DESCRIPTION_SEPARATOR = re.compile(r"^(?P<term>[^:\n]{2,45})\s*:\s*(?P<desc>.+)$")
    EXPLICIT_TABLE_CAPTION = re.compile(r"^(?:Table|Tab\.)\s+\d+[:\.]?", re.IGNORECASE)
    EXPLICIT_FIGURE_CAPTION = re.compile(r"^(?:Figure|Fig\.)\s+\d+[:\.]?", re.IGNORECASE)
    CITATION_OR_DOI = re.compile(
        r"\b(?:doi:\s*10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+|10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+)\b",
        re.IGNORECASE,
    )
    JOURNAL_HEADER = re.compile(
        r"(?i)\b(?:official\s+journal|peer\s+reviewed|vol\.\s*\d+|volume\s+\d+\s*,\s*(?:issue|no\.)|corresponding\s+author)\b"
    )

    @classmethod
    def clean_text(cls, raw: str) -> str:
        if not raw:
            return ""
        txt = cls.CONTROL_CHARS.sub("", raw).replace("\u00a0", " ")
        # Fix glued "Treatmentas in Situation 1" -> "Treatment: as in Situation 1" (issues.md §3.5)
        txt = re.sub(r"\b(Treatment|Diagnosis|Investigations)(as\s+in\s+Situation\s+[12])\b", r"\1: \2", txt, flags=re.I)
        return cls.MULTIPLE_SPACES.sub(" ", txt).strip()

    @classmethod
    def classify_prefix(cls, text: str) -> tuple[str, Optional[int], str]:
        t = text.strip()
        if m_ico := cls.ICON_BULLET_MARKER.match(t):
            return "BULLET", 0, m_ico.group(1).strip()
        if cls.CHAPTER_HEADER.match(t):
            m_num = re.search(r"\b(\d+)\b", t)
            return "CHAPTER", int(m_num.group(1)) if m_num else 1, t
        if m_step := re.match(r"^(?:STEP|STAGE|PHASE|LEVEL|GRADE)\s+(\d{1,2})\b[:.\-–—\s]*(.*)$", t, re.I):
            return "STEP", int(m_step.group(1)), m_step.group(2).strip()
        if m_dot := re.match(r"^(\d{1,2}(?:\.\d{1,2})+)\.?\s+(.*)$", t):
            parts = m_dot.group(1).split(".")
            depth = min(4, max(2, len(parts)))
            return f"DOTTED_{depth}", int(parts[-1]), m_dot.group(2).strip()
        if m_dec := re.match(r"^(\d{1,3})[\.\)]\s*(.*)$", t):
            return "DECIMAL", int(m_dec.group(1)), m_dec.group(2).strip()
        if m_rom := re.match(r"^([IVXLCDMivxlcdm]{1,6})[\.\)]\s*(.*)$", t):
            tok = m_rom.group(1)
            if cls.ROMAN_NUMERAL.match(tok):
                val = cls._roman_to_int(tok.upper())
                if tok.upper() in ("I", "V", "X", "L", "C", "D", "M") and val >= 50:
                    return ("ALPHA_UPPER" if tok.isupper() else "ALPHA_LOWER"), ord(tok.lower()) - ord("a") + 1, m_rom.group(2).strip()
                return ("ROMAN_UPPER" if tok.isupper() else "ROMAN_LOWER"), val, m_rom.group(2).strip()
        if m_alp := re.match(r"^([A-Za-h])[\.\)]\s*(.*)$", t):
            ch = m_alp.group(1)
            return ("ALPHA_UPPER" if ch.isupper() else "ALPHA_LOWER"), ord(ch.lower()) - ord("a") + 1, m_alp.group(2).strip()
        if m_par := re.match(r"^\(([a-h0-9ivx]{1,4})\)\s+(.*)$", t, re.I):
            tok = m_par.group(1)
            idx = int(tok) if tok.isdigit() else (cls._roman_to_int(tok.upper()) if cls.ROMAN_NUMERAL.match(tok) else ord(tok[0].lower()) - ord("a") + 1)
            return "PAREN", idx, m_par.group(2).strip()
        if m_bul := re.match(r"^([-*•–—►▪▫o\u2022\u25cf\u25aa\u25ab])\s*(.*)$", t):
            return "BULLET", 0, m_bul.group(2).strip()
        return "", None, t

    @classmethod
    def _roman_to_int(cls, s: str) -> int:
        roman_map = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
        total = 0
        prev = 0
        for char in reversed(s.upper()):
            curr = roman_map.get(char, 0)
            if curr >= prev:
                total += curr
            else:
                total -= curr
            prev = curr
        return total


def rgb_distance(c1: RGB, c2: RGB) -> float:
    return math.sqrt((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2)


def rgb_saturation(rgb: RGB) -> int:
    r, g, b = rgb
    max_c, min_c = max(r, g, b), min(r, g, b)
    if max_c == 0:
        return 0
    return int(((max_c - min_c) / max_c) * 100)


def rgb_to_hsv(rgb: RGB) -> tuple[float, float, float]:
    r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
    return colorsys.rgb_to_hsv(r, g, b)


def _rgb_hue(rgb: RGB) -> float:
    """Calculates circular HSV hue in degrees [0.0, 360.0]. Returns -1.0 if achromatic."""
    r, g, b = rgb
    max_c, min_c = max(r, g, b), min(r, g, b)
    if max_c == min_c or (max_c - min_c) < 18:
        return -1.0
    h, _, _ = rgb_to_hsv(rgb)
    return round(h * 360.0, 1)


def _hue_distance(h1: float, h2: float) -> float:
    """Calculates minimal circular distance between two hues [0.0, 180.0]."""
    if h1 < 0.0 or h2 < 0.0:
        return 180.0
    diff = abs(h1 - h2) % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


def _norm_font_family(family: str) -> str:
    """Normalizes raw font family names to 'serif', 'sans', or 'mono'."""
    f = (family or "").lower()
    if any(k in f for k in ("courier", "mono", "typewriter", "consolas")):
        return "mono"
    if any(k in f for k in ("times", "serif", "georgia", "roman", "palatino", "garamond")):
        return "serif"
    return "sans"


def _colors_visually_match(c1: RGB, c2: RGB, tol: float = 38.0) -> bool:
    """Checks if two RGB colors match visually via Euclidean distance and HSV chromatic hue."""
    if rgb_distance(c1, c2) <= tol:
        return True
    h1, h2 = _rgb_hue(c1), _rgb_hue(c2)
    if h1 >= 0.0 and h2 >= 0.0 and _hue_distance(h1, h2) <= 22.0:
        s1, s2 = rgb_saturation(c1), rgb_saturation(c2)
        if abs(s1 - s2) <= 35:
            return True
    return False


def _compare_heading_visual_styles(e1: PageElement, e2: PageElement) -> float:
    """Calculates visual style consensus similarity between two heading elements in [0.0, 1.0]."""
    score = 0.0
    if abs(e1.rel_size - e2.rel_size) <= 0.08:
        score += 0.35
    elif abs(e1.rel_size - e2.rel_size) <= 0.16:
        score += 0.20

    if e1.is_bold == e2.is_bold:
        score += 0.25
    if _colors_visually_match(e1.fg_rgb, e2.fg_rgb):
        score += 0.20
    if e1.in_banner == e2.in_banner:
        score += 0.10
    if _norm_font_family(e1.font_family) == _norm_font_family(e2.font_family):
        score += 0.10
    return min(1.0, score)


def deskew_bbox_norm(bbox: BBox, skew_deg: float) -> BBox:
    if abs(skew_deg) < 0.25 or abs(skew_deg) > 15.0:
        return bbox
    rad = math.radians(-skew_deg)
    cos_t, sin_t = math.cos(rad), math.sin(rad)
    x0, y0, x1, y1 = bbox
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    rx = [0.5 + (x - 0.5) * cos_t - (y - 0.5) * sin_t for x, y in corners]
    ry = [0.5 + (x - 0.5) * sin_t + (y - 0.5) * cos_t for x, y in corners]
    return (
        max(0.0, min(rx)),
        max(0.0, min(ry)),
        min(1.0, max(rx)),
        min(1.0, max(ry)),
    )


def union_bbox(b1: BBox, b2: BBox) -> BBox:
    return (
        min(b1[0], b2[0]),
        min(b1[1], b2[1]),
        max(b1[2], b2[2]),
        max(b1[3], b2[3]),
    )


def overlap_ratio(b1: BBox, b2: BBox) -> float:
    ix0 = max(b1[0], b2[0])
    iy0 = max(b1[1], b2[1])
    ix1 = min(b1[2], b2[2])
    iy1 = min(b1[3], b2[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter_area = (ix1 - ix0) * (iy1 - iy0)
    area1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
    area2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
    min_area = min(area1, area2)
    return inter_area / min_area if min_area > 0 else 0.0


def enrich_lines(
    raw_lines: Sequence[dict[str, Any]],
    px_per_pt: float = 2.08,
    w_ref: float = 612.0,
    h_ref: float = 792.0,
) -> list[dict[str, Any]]:
    """Normalizes line font size, ink density, and geometry scaling consistently in points."""
    res = []
    for it in raw_lines:
        c = dict(it)
        bb = c.get("bbox") or c.get("box") or c.get("bbox_norm")
        if not bb:
            continue
        if "size_pt" in c:
            c["size"] = float(c["size_pt"])
        elif "size" in c:
            c["size"] = float(c["size"])
        elif "box" in c and len(c["box"]) == 4:
            bh_px = max(1.0, float(c["box"][3]) - float(c["box"][1]))
            c["size"] = round((bh_px / max(0.5, px_per_pt)) * 0.80, 2)
        elif "bbox_norm" in c and len(c["bbox_norm"]) == 4:
            bh_norm = max(0.001, float(c["bbox_norm"][3]) - float(c["bbox_norm"][1]))
            bh_pt = bh_norm * h_ref
            c["size"] = round(bh_pt * 0.82, 2)
        else:
            c["size"] = 10.0
        c["size_pt"] = c["size"]
        if "fg_rgb" in c and isinstance(c["fg_rgb"], (list, tuple)) and len(c["fg_rgb"]) == 3:
            c["fg_hue"] = _rgb_hue(tuple(c["fg_rgb"]))
        if "bg_rgb" in c and isinstance(c["bg_rgb"], (list, tuple)) and len(c["bg_rgb"]) == 3:
            c["bg_hue"] = _rgb_hue(tuple(c["bg_rgb"]))
        res.append(c)
    return res


class SharedLineProcessor:
    """Static pipeline methods for shared text line normalization and classification."""

    @staticmethod
    def clean(text: str) -> str:
        return RegexCatalog.clean_text(text)

    @staticmethod
    def classify(text: str) -> tuple[str, Optional[int], str]:
        return RegexCatalog.classify_prefix(text)

    @staticmethod
    def enrich(lines: Sequence[dict[str, Any]], px_per_pt: float = 2.08) -> list[dict[str, Any]]:
        return enrich_lines(lines, px_per_pt=px_per_pt)


def is_description_list_rows(rows: list[list[str]]) -> bool:
    if not rows or len(rows) < 2:
        return False
    if any(len(r) != 2 for r in rows):
        return False
    avg_col0 = sum(len(r[0]) for r in rows) / len(rows)
    avg_col1 = sum(len(r[1]) for r in rows) / len(rows)
    return avg_col0 <= 35 and avg_col1 >= 25
