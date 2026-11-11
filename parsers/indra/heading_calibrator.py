from __future__ import annotations
import colorsys
import re
from statistics import median
from typing import Any

# 1. Hard-excluded block labels from Indra order.onnx
HARD_EXCLUDED_LABELS: frozenset[str] = frozenset({
    "Page-Header",
    "Page-Footer",
    "Table",
    "Caption",
    "Footnote",
    "Table-Of-Contents",
    "Form",
    "Figure",
    "Picture",
    "Formula",
})

# 2. Pre-compiled structural and clinical outline regexes
BANNER_PREFIX_RE = re.compile(
    r"^\s*\*?\s*(?:Situation|Stage|Phase|Step|Part|Chapter|Section|Module|Protocol|Algorithm|Box|Guideline)\s+\d+\b",
    re.IGNORECASE,
)
ROMAN_PREFIX_RE = re.compile(r"^\s*[IVXLCDM]{1,6}[\.\)]\s+[A-Z]")
DECIMAL_OUTLINE_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)[\.\)]?\s+[A-Z]")
NUMBERED_OUTLINE_RE = re.compile(r"^\s*(\d{1,2})([\.\)])\s+[A-Z]")
LETTERED_OUTLINE_RE = re.compile(r"^\s*([a-zA-Z])([\.\)])\s+[A-Z]")
SUB_ROMAN_OUTLINE_RE = re.compile(r"^\s*\(?([ivx]{1,5}|\d{1,2})\)[\.\)]?\s+[A-Za-z]")
PLAIN_BULLET_RE = re.compile(r"^\s*[•●▪◦‣\-\*]\s+")
FINITE_PROSE_PUNCT_RE = re.compile(r"\.\s*$")


def parse_rgb_tuple(color_val: Any) -> tuple[int, int, int]:
    """Convert PyMuPDF int (0xRRGGBB), hex string ('#rrggbb'), or RGB sequence to (R, G, B) in 0..255."""
    if color_val is None:
        return (0, 0, 0)
    if isinstance(color_val, int):
        r = (color_val >> 16) & 255
        g = (color_val >> 8) & 255
        b = color_val & 255
        return (r, g, b)
    if isinstance(color_val, str):
        s = color_val.strip().lstrip("#")
        if len(s) == 6:
            try:
                return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
            except ValueError:
                return (0, 0, 0)
    if isinstance(color_val, (tuple, list)) and len(color_val) >= 3:
        vals = [float(c) for c in color_val[:3]]
        if all(0.0 <= v <= 1.0 for v in vals) and any(0.0 < v < 1.0 for v in vals):
            return (int(round(vals[0] * 255)), int(round(vals[1] * 255)), int(round(vals[2] * 255)))
        return (int(vals[0]) & 255, int(vals[1]) & 255, int(vals[2]) & 255)
    return (0, 0, 0)


def extract_hsv_color_profile(color_val: Any) -> dict[str, Any]:
    """
    Compute RGB and HSV (Hue 0..360 deg, Saturation 0..1, Value 0..1) metrics.
    Separates chromatic colors (blue, teal, maroon, green) from achromatic grayscale/black.
    """
    r, g, b = parse_rgb_tuple(color_val)
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    hue_deg = round(h * 360.0, 1)
    is_chromatic = (s >= 0.20 and v >= 0.15)
    is_light_or_white = (v >= 0.85 and s < 0.20)
    # Quantize hue into 15-degree buckets for heading tier grouping
    hue_bucket = int(round(hue_deg / 15.0) * 15) % 360 if is_chromatic else -1
    return {
        "rgb": (r, g, b),
        "hue_deg": hue_deg,
        "saturation": round(s, 3),
        "value": round(v, 3),
        "is_chromatic": is_chromatic,
        "is_light_or_white": is_light_or_white,
        "hue_bucket": hue_bucket,
    }


def is_distinct_heading_color(
    item_color: Any,
    body_mode_color: Any = (0, 0, 0),
    has_bg_fill: bool = False,
    bg_color: Any = None,
) -> tuple[bool, int]:
    """
    Determine whether a text block uses a distinct heading color hue or colored banner background.
    Returns (is_distinct_color: bool, hue_signature: int).
    """
    prof = extract_hsv_color_profile(item_color)
    body_prof = extract_hsv_color_profile(body_mode_color)

    # Case 1: Colored background banner behind the text (e.g., Situation 1 / Situation 2 boxes)
    if has_bg_fill:
        if bg_color is not None:
            bg_prof = extract_hsv_color_profile(bg_color)
            # Non-white shaded background box
            if bg_prof["is_chromatic"] or bg_prof["value"] < 0.94:
                return True, (bg_prof["hue_bucket"] if bg_prof["is_chromatic"] else 999)
        else:
            return True, 999

    # Case 2: White/light text (implies dark/colored banner fill behind it)
    if prof["is_light_or_white"]:
        return True, 998

    # Case 3: Chromatic text (e.g., Blue, Teal, Red, Maroon, Green heading) when body is black/gray
    if prof["is_chromatic"] and not body_prof["is_chromatic"]:
        return True, prof["hue_bucket"]

    # Case 4: Both item and body are chromatic, check circular hue angle distance >= 15 deg
    if prof["is_chromatic"] and body_prof["is_chromatic"]:
        diff = abs(prof["hue_deg"] - body_prof["hue_deg"])
        hue_dist = min(diff, 360.0 - diff)
        if hue_dist >= 15.0:
            return True, prof["hue_bucket"]

    return False, -1


def classify_outline_prefix(text: str) -> str | None:
    """Return the structural family of an outline prefix for sibling grouping and level assignment."""
    s = (text or "").strip()
    if not s:
        return None
    if BANNER_PREFIX_RE.match(s):
        return "banner_situation"
    if ROMAN_PREFIX_RE.match(s):
        return "roman_upper"
    if DECIMAL_OUTLINE_RE.match(s):
        depth = s.split()[0].rstrip(".)").count(".")
        return f"decimal_{min(depth, 3)}"
    m_num = NUMBERED_OUTLINE_RE.match(s)
    if m_num:
        return f"num_{m_num.group(2)}"  # 'num_.' or 'num_)'
    m_let = LETTERED_OUTLINE_RE.match(s)
    if m_let:
        case_tag = "upper" if m_let.group(1).isupper() else "lower"
        return f"alpha_{case_tag}_{m_let.group(2)}"  # e.g., 'alpha_lower_)' for a), b), c), d)
    if SUB_ROMAN_OUTLINE_RE.match(s):
        return "sub_roman"
    return None


def compute_body_typography_baseline(
    blocks: list[dict[str, Any]],
    page_height: float = 0.0,
) -> tuple[float, tuple[int, int, int]]:
    """
    Compute character-weighted median body font size and modal RGB color from 'Text' blocks,
    excluding hard-excluded labels and top/bottom 7% margin zones.
    """
    weighted_sizes: list[float] = []
    color_counts: dict[tuple[int, int, int], int] = {}

    for blk in blocks:
        raw_label = str(blk.get("raw_label") or blk.get("label") or blk.get("type") or "Text")
        if raw_label in HARD_EXCLUDED_LABELS:
            continue
        text = str(blk.get("text") or "").strip()
        if len(text) < 25:
            continue
        bbox = blk.get("bbox")
        if page_height > 0 and isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            y0, y1 = float(bbox[1]), float(bbox[3])
            if y1 <= 0.07 * page_height or y0 >= 0.93 * page_height:
                continue
        fsize = float(blk.get("font_size") or 0.0)
        if fsize > 0:
            weight = max(1, min(20, len(text) // 25))
            weighted_sizes.extend([fsize] * weight)
        rgb = parse_rgb_tuple(blk.get("text_color") or blk.get("color"))
        color_counts[rgb] = color_counts.get(rgb, 0) + len(text)

    body_median = float(median(weighted_sizes)) if weighted_sizes else 10.0
    body_color = max(color_counts.items(), key=lambda kv: kv[1])[0] if color_counts else (0, 0, 0)
    return body_median, body_color


def calibrate_page_blocks(
    blocks: list[dict[str, Any]],
    page_height: float = 0.0,
) -> list[dict[str, Any]]:
    """
    Multi-step Bidirectional Consensus Calibrator:
    1. Enforces Hard-Exclusion on Page-Header, Page-Footer, Table, Caption, Footnote, TOC, Form, Figure.
    2. Computes body font-size median and modal color baseline.
    3. Evaluates Sibling Sequence Consistency across outline families (e.g., a), b), c) Treatment:, d) Referral criteria).
    4. Applies Mandatory Prose Demotion Vetoes and Bidirectional Consensus Scoring (threshold >= 0.60).
    5. Assigns H1-H6 levels using font-size ratio, color hue bucket, and prefix tier, then clamps skipped levels.
    6. Synchronizes block['label'], block['type'], and block['heading_level'] in-place for UI layout_boxes.
    """
    if not blocks:
        return []

    body_median, body_color = compute_body_typography_baseline(blocks, page_height=page_height)

    # First pass: extract features and group outline prefix families for Sibling Sequence Consistency
    family_members: dict[str, list[int]] = {}
    family_styled_hits: dict[str, int] = {}
    enriched: list[dict[str, Any]] = []

    for idx, blk in enumerate(blocks):
        item = dict(blk)
        raw_label = str(item.get("raw_label") or item.get("label") or item.get("type") or "Text")
        text = " ".join(str(item.get("text") or "").split())
        words = text.split()
        word_count = len(words)

        fsize = float(item.get("font_size") or body_median or 10.0)
        size_ratio = fsize / max(1.0, body_median)
        font_name = str(item.get("font_name") or item.get("font_family") or "").lower()
        is_bold = bool(
            item.get("is_bold")
            or (float(item.get("bold_ratio") or 0.0) >= 0.75)
            or any(w in font_name for w in ("bold", "black", "heavy", "demi", "medi", "semibold"))
        )
        has_bg_fill = bool(item.get("has_bg_fill") or item.get("bg_color") is not None)
        distinct_color, hue_sig = is_distinct_heading_color(
            item.get("text_color") or item.get("color"),
            body_mode_color=body_color,
            has_bg_fill=has_bg_fill,
            bg_color=item.get("bg_color"),
        )
        prefix_family = classify_outline_prefix(text)

        item["_meta"] = {
            "raw_label": raw_label,
            "text": text,
            "word_count": word_count,
            "size_ratio": size_ratio,
            "is_bold": is_bold,
            "has_bg_fill": has_bg_fill,
            "distinct_color": distinct_color,
            "hue_sig": hue_sig,
            "prefix_family": prefix_family,
        }
        enriched.append(item)

        max_prefix_words = 35 if prefix_family == "banner_situation" else 16
        if prefix_family and raw_label not in HARD_EXCLUDED_LABELS and 1 <= word_count <= max_prefix_words:
            family_members.setdefault(prefix_family, []).append(idx)
            if is_bold or size_ratio >= 1.05 or distinct_color or has_bg_fill or text.endswith(":"):
                family_styled_hits[prefix_family] = family_styled_hits.get(prefix_family, 0) + 1

    # Determine which outline families qualify as a consistent sibling heading sequence (>= 50% styled & >= 2 items)
    promoted_families: set[str] = set()
    for fam, indices in family_members.items():
        hits = family_styled_hits.get(fam, 0)
        if (len(indices) >= 2 and hits / len(indices) >= 0.5) or (len(indices) >= 1 and hits >= 1 and fam == "banner_situation"):
            promoted_families.add(fam)

    # Second pass: Apply Hard Exclusions, Demotion Vetoes, and Consensus Scoring
    for idx, item in enumerate(enriched):
        m = item["_meta"]
        raw_label = m["raw_label"]
        text = m["text"]
        word_count = m["word_count"]
        size_ratio = m["size_ratio"]
        is_bold = m["is_bold"]
        has_bg_fill = m["has_bg_fill"]
        distinct_color = m["distinct_color"]
        prefix_family = m["prefix_family"]

        # Gate A: Hard-Excluded Labels & Margin Headers/Footers
        bbox = item.get("bbox")
        in_margin = False
        if page_height > 0 and isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            y0, y1 = float(bbox[1]), float(bbox[3])
            if (y1 <= 0.06 * page_height or y0 >= 0.94 * page_height) and word_count <= 8:
                if raw_label in ("Page-Header", "Page-Footer"):
                    in_margin = True

        if raw_label in HARD_EXCLUDED_LABELS or in_margin or not text:
            final_label = raw_label if raw_label in HARD_EXCLUDED_LABELS else "Text"
            item["label"] = final_label
            item["type"] = final_label.lower()
            item["heading_level"] = None
            item["is_heading"] = False
            continue

        # Gate B: Mandatory Demotion Vetoes (Prevents paragraphs/lists from becoming Section-Header)
        first_alpha = next((ch for ch in text if ch.isalpha()), None)
        starts_lowercase = bool(first_alpha and first_alpha.islower() and not prefix_family)
        is_plain_bullet = bool(PLAIN_BULLET_RE.match(text))
        is_banner_heading = (prefix_family == "banner_situation") or (has_bg_fill and is_bold)
        max_allowed_words = 35 if is_banner_heading else 18

        ends_with_sentence_period = bool(
            FINITE_PROSE_PUNCT_RE.search(text)
            and word_count > 10
            and not is_banner_heading
            and not text.isupper()
        )
        zero_visual_distinction = (
            size_ratio < 1.03
            and not is_bold
            and not distinct_color
            and not has_bg_fill
            and prefix_family not in promoted_families
            and not text.isupper()
        )
        unbolded_list_prose = (
            raw_label == "List-Group"
            and not is_bold
            and size_ratio < 1.05
            and not distinct_color
            and prefix_family not in promoted_families
        )

        vetoed = (
            starts_lowercase
            or is_plain_bullet
            or word_count > max_allowed_words
            or ends_with_sentence_period
            or zero_visual_distinction
            or unbolded_list_prose
        )

        if vetoed:
            demoted_label = "List-Group" if (raw_label == "List-Group" or is_plain_bullet) else "Text"
            item["label"] = demoted_label
            item["type"] = "list" if demoted_label == "List-Group" else "paragraph"
            item["heading_level"] = None
            item["is_heading"] = False
            item["heading_score"] = 0.0
            continue

        # Gate C: Bidirectional Consensus Score
        score = 0.0
        if raw_label in ("Section-Header", "Title", "Header"):
            score += 0.35
        elif raw_label == "List-Group" and prefix_family:
            score += 0.15

        if size_ratio >= 1.20:
            score += 0.40
        elif size_ratio >= 1.08:
            score += 0.25
        elif size_ratio >= 1.03:
            score += 0.12

        if is_bold:
            score += 0.30
        if distinct_color or has_bg_fill:
            score += 0.28
        alpha_words = [w for w in text.split() if any(c.isalpha() for c in w)]
        is_title_case = bool(
            alpha_words
            and not FINITE_PROSE_PUNCT_RE.search(text)
            and sum(1 for w in alpha_words if w[0].isupper() or w.lower() in {'of', 'and', 'in', 'on', 'at', 'to', 'for', 'the', 'a', 'an', 'by', 'with', 'from'}) / len(alpha_words) >= 0.8
        )
        if prefix_family or (text.isupper() and word_count <= 12):
            score += 0.15
        elif is_title_case and word_count <= 10 and size_ratio >= 1.30:
            score += 0.25
        if prefix_family in promoted_families:
            score += 0.25

        score = round(min(1.0, score), 3)
        item["heading_score"] = score

        if score >= 0.60:
            item["label"] = "Section-Header"
            item["type"] = "header"
            item["is_heading"] = True
        else:
            fallback_label = "List-Group" if raw_label == "List-Group" else "Text"
            item["label"] = fallback_label
            item["type"] = "list" if fallback_label == "List-Group" else "paragraph"
            item["heading_level"] = None
            item["is_heading"] = False

    # Third pass: Assign deterministic H1-H6 levels using Prefix Tier + Size Ratio + Color Hue Bucket
    last_level = 0
    family_assigned_level: dict[str, int] = {}

    for item in enriched:
        m = item.pop("_meta")
        if not item.get("is_heading"):
            continue

        size_ratio = m["size_ratio"]
        prefix_family = m["prefix_family"]
        distinct_color = m["distinct_color"]
        has_bg_fill = m["has_bg_fill"]
        text = m["text"]

        if prefix_family in family_assigned_level:
            item["heading_level"] = family_assigned_level[prefix_family]
            last_level = item["heading_level"]
            continue

        if size_ratio >= 1.60 or prefix_family == "roman_upper":
            raw_lvl = 1
        elif (
            size_ratio >= 1.25
            or prefix_family == "banner_situation"
            or has_bg_fill
            or (text.isupper() and (m["is_bold"] or distinct_color))
        ):
            raw_lvl = 2
        elif prefix_family in ("num_.", "num_)", "alpha_upper_.", "alpha_upper_)", "decimal_1") or size_ratio >= 1.12:
            raw_lvl = 3
        elif prefix_family in ("alpha_lower_)", "alpha_lower_.", "decimal_2"):
            raw_lvl = 4
        elif prefix_family in ("sub_roman", "decimal_3"):
            raw_lvl = 5
        elif size_ratio >= 1.05 or distinct_color:
            raw_lvl = 3
        else:
            raw_lvl = 4

        # Clamp skipped hierarchy levels (e.g. H1 -> H4 becomes H1 -> H2) unless an explicit outline prefix dictates nesting
        if last_level == 0:
            clamped_lvl = min(raw_lvl, 2)
        elif raw_lvl > last_level + 1:
            clamped_lvl = last_level + 1
        elif raw_lvl < last_level:
            clamped_lvl = max(1, raw_lvl)
        else:
            clamped_lvl = raw_lvl

        clamped_lvl = max(1, min(6, clamped_lvl))
        item["heading_level"] = clamped_lvl
        last_level = clamped_lvl

        if prefix_family and prefix_family in promoted_families:
            family_assigned_level[prefix_family] = clamped_lvl

    return enriched
