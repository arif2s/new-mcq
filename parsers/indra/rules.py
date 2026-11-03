from __future__ import annotations

import colorsys
import html
import math
import re
from typing import Any, Optional, Sequence, Union

from parsers.indra.types import BBox, RGB


class RegexCatalog:
    """Catalog of pre-compiled regular expressions for PDF text and layout analysis."""

    CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
    MULTIPLE_SPACES = re.compile(r"[ \t]+")
    PAGE_NUMBER = re.compile(r"^(?:page\s+)?(\d{1,4})(?:\s+of\s+\d{1,4})?$", re.IGNORECASE)
    RUNNING_HEADER = re.compile(
        r"^(?:guidelines?\s+for|chapter\s+\d+|section\s+\d+|handbook|manual|who\b|mohfw\b)",
        re.IGNORECASE,
    )
    ROMAN_NUMERAL = re.compile(r"^(?=[MDCLXVI])M*(?:C[MD]|D?C{0,3})(?:X[CL]|L?X{0,3})(?:I[XV]|V?I{0,3})$", re.IGNORECASE)
    BULLET_MARKER = re.compile(r"^(?:[-*•–—►▪▫o\u2022\u25cf\u25aa\u25ab]|\(?\d{1,3}[\.\)\:-]|\(?[A-Za-z][\.\)\:-])(?:\s+|$)")
    RUN_IN_HEADING = re.compile(
        r"^(?P<title>(?:\d{1,2}\.|\([A-Za-z\d]\)|[A-Za-z\d][\.\):]|[A-Z][A-Za-z0-9\s,\/\-]{2,45}?))\s*:\s*(?P<body>[A-Z0-9].*)$"
    )
    RUN_IN_EMDASH = re.compile(
        r"^(?P<title>(?:[A-Z][A-Za-z0-9\s,\/\-]{2,40}?))\s*(?:—|--)\s*(?P<body>[A-Z0-9].*)$"
    )
    DESCRIPTION_SEPARATOR = re.compile(r"^(?P<term>[^:\n]{2,45})\s*:\s*(?P<desc>.+)$")
    EXPLICIT_TABLE_CAPTION = re.compile(r"^(?:Table|Tab\.)\s+\d+[:\.]?", re.IGNORECASE)
    EXPLICIT_FIGURE_CAPTION = re.compile(r"^(?:Figure|Fig\.)\s+\d+[:\.]?", re.IGNORECASE)
    CITATION_OR_DOI = re.compile(r"\b(?:doi:\s*10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+|10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+)\b", re.IGNORECASE)
    JOURNAL_HEADER = re.compile(
        r"(?i)\b(?:official\s+journal|peer\s+reviewed|vol\.\s*\d+|volume\s+\d+\s*,\s*(?:issue|no\.)|corresponding\s+author)\b"
    )

    @classmethod
    def clean_text(cls, raw: str) -> str:
        if not raw:
            return ""
        txt = cls.CONTROL_CHARS.sub("", raw)
        txt = txt.replace("\u00a0", " ")
        txt = cls.MULTIPLE_SPACES.sub(" ", txt)
        return txt.strip()

    @classmethod
    def classify_prefix(cls, text: str) -> tuple[Optional[str], Optional[int], str]:
        t = text.strip()
        m_dec = re.match(r"^(\d{1,3})[\.\)\:-]\s*(.*)$", t)
        if m_dec:
            return "DECIMAL", int(m_dec.group(1)), m_dec.group(2).strip()
        m_alpha = re.match(r"^([A-Za-z])[\.\)\:-]\s*(.*)$", t)
        if m_alpha:
            ch = m_alpha.group(1).lower()
            return "ALPHA", ord(ch) - ord("a") + 1, m_alpha.group(2).strip()
        m_rom = re.match(r"^([ivxlcdm]{1,6})[\.\)\:-]\s*(.*)$", t, re.IGNORECASE)
        if m_rom and cls.ROMAN_NUMERAL.match(m_rom.group(1)):
            rom_val = cls._roman_to_int(m_rom.group(1).upper())
            return "ROMAN", rom_val, m_rom.group(2).strip()
        m_bullet = re.match(r"^([-*•–—►▪▫o\u2022\u25cf\u25aa\u25ab])\s*(.*)$", t)
        if m_bullet:
            return "BULLET", 0, m_bullet.group(2).strip()
        return None, None, t

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
            c["size"] = round((bh_px / px_per_pt) * 0.80, 2)
        elif "bbox_norm" in c and len(c["bbox_norm"]) == 4:
            bh_norm = max(0.001, float(c["bbox_norm"][3]) - float(c["bbox_norm"][1]))
            bh_pt = bh_norm * h_ref
            c["size"] = round(bh_pt * 0.82, 2)
        else:
            c["size"] = 10.0
        c["size_pt"] = c["size"]
        res.append(c)
    return res


def is_description_list_rows(rows: list[list[str]]) -> bool:
    if not rows or len(rows) < 2:
        return False
    if any(len(r) != 2 for r in rows):
        return False
    avg_col0 = sum(len(r[0]) for r in rows) / len(rows)
    avg_col1 = sum(len(r[1]) for r in rows) / len(rows)
    return avg_col0 <= 35 and avg_col1 >= 25
