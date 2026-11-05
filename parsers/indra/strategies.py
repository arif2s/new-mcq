from __future__ import annotations

import logging
import re
from typing import Any, Optional, Sequence

from parsers.indra.profiles import LayoutProfile
from parsers.indra.rules import (
    RegexCatalog,
    _colors_visually_match,
    deskew_bbox_norm,
    overlap_ratio,
    rgb_distance,
    rgb_saturation,
    union_bbox,
)
from parsers.indra.types import PageElement, PageElements

logger = logging.getLogger(__name__)


class LayoutStrategy:
    """Base strategy for column splitting, line merging, and multi-factor heading classification."""

    def __init__(self, profile: LayoutProfile) -> None:
        self.profile = profile

    def filter_furniture(self, ir: PageElements) -> None:
        furn = self.profile.furniture_suppression
        h_max = furn.header_max_y_ratio
        f_min = furn.footer_min_y_ratio

        for el in ir.elements:
            y_mid = el.center_y
            t = el.text.strip()
            # Strip leading stray OCR logo numbers (e.g. "2. National Programme...")
            t_stripped = re.sub(r"^\d{1,2}[\.\)]\s+", "", t)

            # 1. Header zone suppression: only suppress if it looks like a running header or page number, not large titles
            if y_mid <= h_max and el.rel_size < 1.35 and not el.is_bold:
                if furn.filter_running_headers and (
                    RegexCatalog.RUNNING_HEADER.match(t)
                    or RegexCatalog.RUNNING_HEADER.match(t_stripped)
                    or len(t) <= 65
                ):
                    el.is_suppressed_furniture = True
                    el.role = "furniture"
                    continue
            # 2. Footer zone suppression
            if y_mid >= f_min:
                if furn.filter_page_numbers and (RegexCatalog.PAGE_NUMBER.match(t) or len(t) <= 15):
                    el.is_suppressed_furniture = True
                    el.role = "furniture"
                    continue

    def detect_column_gutters(self, ir: PageElements) -> list[float]:
        geom = self.profile.geometry
        if geom.column_mode == "single":
            return []

        active = [
            e for e in ir.elements
            if not e.is_suppressed_furniture and not e.in_table and not e.in_figure
        ]
        if len(active) < 4:
            return []

        g_min, g_max = geom.gutter_x_range
        # Collect candidate x intervals
        boxes = [e.bbox_norm for e in active if (e.bbox_norm[2] - e.bbox_norm[0]) <= 0.65]
        if not boxes:
            return []

        # Find potential gutters in the range
        step = 0.01
        cur_x = g_min
        best_gutter = None
        min_crossing = 999999

        while cur_x <= g_max:
            # Check how many boxes intersect cur_x
            crossings = sum(1 for b in boxes if b[0] < cur_x < b[2])
            # Left and right supports
            left_supp = sum(1 for b in boxes if b[2] <= cur_x)
            right_supp = sum(1 for b in boxes if b[0] >= cur_x)
            if left_supp >= 2 and right_supp >= 2:
                if crossings < min_crossing:
                    min_crossing = crossings
                    best_gutter = cur_x
            cur_x += step

        if best_gutter is not None and min_crossing <= max(1, int(0.12 * len(boxes))):
            return [round(best_gutter, 3)]
        return []

    def assign_columns_and_merge_lines(self, ir: PageElements) -> None:
        geom = self.profile.geometry
        gutters = ir.column_gutters or self.detect_column_gutters(ir)
        ir.column_gutters = gutters

        # 1. Assign columns with spanning preservation
        # First sort vertically
        ir.elements.sort(key=lambda x: (x.bbox_norm[1], x.bbox_norm[0]))
        prev_assigned_col = None
        prev_bb = None
        prev_el = None

        for el in ir.elements:
            if el.is_suppressed_furniture:
                continue
            bb = deskew_bbox_norm(el.bbox_norm, ir.skew) if abs(ir.skew) >= 0.25 else el.bbox_norm
            el.bbox_norm = bb
            w = bb[2] - bb[0]

            # Preserve "SPAN" band across multi-line *Situation 1 & 2 and centered titles
            if (
                prev_assigned_col == 0
                and prev_el is not None
                and prev_bb is not None
                and (0.0 <= bb[1] - prev_bb[3] <= 0.035)
                and (
                    _colors_visually_match(prev_el.fg_rgb, el.fg_rgb)
                    or el.is_accent_color
                    or el.is_all_caps
                    or RegexCatalog.SITUATION_HEADER.match(prev_el.text)
                )
            ):
                el.column_index = 0
            elif w >= geom.spanning_width_ratio or not gutters:
                el.column_index = 0
            else:
                assigned = 0
                for idx, g in enumerate(gutters):
                    if bb[0] >= g - 0.015:
                        assigned = idx + 1
                el.column_index = assigned

            prev_assigned_col = el.column_index
            prev_bb = bb
            prev_el = el

        # 2. Horizontal line segment merging
        # Sort elements by reading order within columns
        active_elements = [e for e in ir.elements if not e.is_suppressed_furniture]
        active_elements.sort(key=lambda x: (x.column_index, x.bbox_norm[1], x.bbox_norm[0]))

        h_merged: list[PageElement] = []
        for el in active_elements:
            if not h_merged:
                h_merged.append(el)
                continue
            prev = h_merged[-1]
            min_h = min(prev.height_norm, el.height_norm)
            max_h = max(prev.height_norm, el.height_norm)
            v_ov = max(0.0, min(prev.bbox_norm[3], el.bbox_norm[3]) - max(prev.bbox_norm[1], el.bbox_norm[1]))

            # Check if elements are horizontally adjacent on the same line
            if (
                prev.column_index == el.column_index
                and not prev.in_table
                and not prev.in_figure
                and prev.role != "figure_caption"
                and el.role != "figure_caption"
                and (v_ov >= 0.40 * min_h or abs(prev.center_y - el.center_y) <= 0.45 * max_h)
                and abs(prev.rel_size - el.rel_size) <= 0.35
            ):
                ordered = sorted((prev, el), key=lambda x: x.bbox_norm[0])
                x_gap = ordered[1].bbox_norm[0] - ordered[0].bbox_norm[2]

                o0_clean = ordered[0].text.strip()
                is_marker = bool(re.match(r"^(?:[IVXLCDMivxlcdm]{1,6}|[A-Za-h]|\d{1,2})[\.\)]?$", o0_clean))
                sep = " " if o0_clean.endswith((".", ")")) else (". " if is_marker else " ")

                max_allowed_gap = 0.48 if (prev.column_index % 10 == 0 and is_marker) else 0.14
                crosses_gutter = (
                    bool(gutters)
                    and any(ordered[0].bbox_norm[2] <= g_v + 0.015 and ordered[1].bbox_norm[0] >= g_v - 0.015 for g_v in gutters)
                    and x_gap >= 0.018
                    and not (ordered[0].is_all_caps and ordered[1].is_all_caps)
                    and not ordered[0].in_banner
                    and not is_marker
                )

                if -0.015 <= x_gap <= max_allowed_gap and not crosses_gutter:
                    comb_t = RegexCatalog.clean_text(f"{o0_clean}{sep}{ordered[1].text.strip()}")
                    prev.text = comb_t
                    prev.bbox_norm = union_bbox(prev.bbox_norm, el.bbox_norm)
                    prev.is_bold = prev.is_bold or el.is_bold
                    prev.is_accent_color = prev.is_accent_color or el.is_accent_color
                    alpha_c = [c for c in comb_t if c.isalpha()]
                    prev.is_all_caps = bool(alpha_c) and (sum(c.isupper() for c in alpha_c) / len(alpha_c) >= 0.85)
                    pf, p_idx, _ = RegexCatalog.classify_prefix(comb_t)
                    prev.prefix_family, prev.prefix_index = pf, p_idx
                    continue

            h_merged.append(el)

        # 3. Vertical multi-line wrapped elements merging (_merge_wrapped_elements)
        ir.elements = self._merge_wrapped_elements(h_merged)

    def _merge_wrapped_elements(self, elements: list[PageElement]) -> list[PageElement]:
        """Merges wrapped lines of the same heading or paragraph block."""
        if not elements:
            return []

        merged: list[PageElement] = []
        acronym_tail_re = re.compile(r"^\([A-Za-z0-9\s,\-/]{2,25}\)\s*[:.]?$")

        for el in elements:
            if not merged:
                merged.append(el)
                continue

            curr = merged[-1]
            nxt = el

            v_gap = nxt.bbox_norm[1] - curr.bbox_norm[3]
            max_h_gap = 0.038

            # Situation header multi-line continuation exemption
            is_situation = bool(RegexCatalog.SITUATION_HEADER.match(curr.text))
            colors_match = _colors_visually_match(curr.fg_rgb, nxt.fg_rgb)

            if (
                curr.column_index == nxt.column_index
                and not curr.in_table
                and not nxt.in_table
                and not curr.in_figure
                and not nxt.in_figure
                and 0.0 <= v_gap <= max_h_gap
            ):
                # Subtask 3.2.1: Parenthetical Acronym Tail Merge
                if acronym_tail_re.match(nxt.text.strip()) and (curr.is_bold or curr.is_all_caps or curr.rel_size >= 1.1):
                    comb_t = RegexCatalog.clean_text(f"{curr.text.strip()} {nxt.text.strip()}")
                    curr.text = comb_t
                    curr.bbox_norm = union_bbox(curr.bbox_norm, nxt.bbox_norm)
                    curr.is_bold = curr.is_bold or nxt.is_bold
                    curr.is_accent_color = curr.is_accent_color or nxt.is_accent_color
                    pf, p_idx, _ = RegexCatalog.classify_prefix(comb_t)
                    curr.prefix_family, curr.prefix_index = pf, p_idx
                    continue

                # Subtask 3.2.2: *Situation 1 & 2 Multi-Line Exemption
                if is_situation and colors_match and (curr.is_bold or curr.is_accent_color or curr.rel_size >= 1.0):
                    comb_t = RegexCatalog.clean_text(f"{curr.text.strip()} {nxt.text.strip()}")
                    curr.text = comb_t
                    curr.bbox_norm = union_bbox(curr.bbox_norm, nxt.bbox_norm)
                    curr.is_bold = curr.is_bold or nxt.is_bold
                    curr.is_accent_color = curr.is_accent_color or nxt.is_accent_color
                    pf, p_idx, _ = RegexCatalog.classify_prefix(comb_t)
                    curr.prefix_family, curr.prefix_index = pf, p_idx
                    continue

            merged.append(el)

        return merged

    def score_and_classify_elements(self, ir: PageElements, active_stack: Optional[dict[int, str]] = None) -> None:
        weights = self.profile.heading_weights
        act_stack = active_stack or {}

        for el in ir.elements:
            if el.is_suppressed_furniture or el.in_table:
                continue

            text = el.text.strip()
            if not text:
                continue

            # Subtask 3.3.1: Reject visual icon bullets, contributor rows, and TOC dot-leaders
            if RegexCatalog.ICON_BULLET_MARKER.match(text):
                el.role = "list_item"
                continue
            if RegexCatalog.TOC_DOT_LEADER.search(text) or RegexCatalog.CONTRIBUTOR_ROW.match(text):
                el.role = "paragraph"
                continue

            # Subtask 3.3.2: Exempt *Situation 1 & 2 from asterisk and verb disqualifiers
            is_situation_hd = bool(RegexCatalog.SITUATION_HEADER.match(text))
            if is_situation_hd and (el.is_bold or el.is_accent_color or el.rel_size >= 1.0):
                el.role = "heading"
                el.heading_confidence = 0.92
                el.heading_level = 3 if 2 in act_stack else 2
                continue

            if not is_situation_hd and text.lstrip().startswith(("•", "-", ":", ";")):
                el.role = "list_item"
                continue

            pf, p_idx, clean_body = RegexCatalog.classify_prefix(text)
            el.prefix_family, el.prefix_index = pf, p_idx

            words = len(text.split())

            # Subtask 3.3.3: Recognize clinical subheadings a)-d) consistently
            is_clinical_alpha_sub = (
                pf in ("ALPHA_LOWER", "ALPHA", "DECIMAL")
                and words <= 6
                and (el.is_bold or text.endswith(":"))
            )

            # Multi-factor score
            score = 0.0
            # 1. Size
            if el.rel_size >= weights.h1_min_rel_size:
                score += weights.get("size", 0.30) * 1.0
            elif el.rel_size >= weights.h2_min_rel_size:
                score += weights.get("size", 0.30) * 0.75
            elif el.rel_size >= weights.h3_min_rel_size:
                score += weights.get("size", 0.30) * 0.50

            # 2. Weight
            if el.is_bold:
                score += weights.get("weight", 0.22) * 1.0

            # 3. Banner
            if el.in_banner:
                score += weights.get("banner", 0.15) * 1.0

            # 4. Color / Accent
            if el.is_accent_color or rgb_saturation(el.fg_rgb) >= weights.chromatic_saturation_min:
                score += weights.get("color", 0.15) * 1.0

            # 5. Case
            if el.is_all_caps:
                score += weights.get("case", 0.10) * 1.0

            # 6. Prefix
            if pf in ("DECIMAL", "ROMAN", "ALPHA", "ALPHA_LOWER", "ALPHA_UPPER") and words <= 12:
                score += weights.get("prefix", 0.10) * 1.0

            el.heading_confidence = score
            is_prior_heading_label = (el.role == "heading")

            if is_clinical_alpha_sub:
                el.role = "heading"
                el.heading_confidence = max(score, 0.75)
                # Assign level H3 or H4
                el.heading_level = 4 if 3 in act_stack or 2 in act_stack else 3
            elif (
                is_prior_heading_label
                or score >= weights.min_heading_confidence
                or el.rel_size >= weights.h2_min_rel_size
                or (el.is_bold and el.rel_size >= 1.15)
            ) and words <= 15 and not text.lstrip().startswith(("-", "•")):
                el.role = "heading"
                if el.rel_size >= weights.h1_min_rel_size or el.in_banner or (is_prior_heading_label and el.rel_size >= 1.20):
                    el.heading_level = 1
                elif el.rel_size >= weights.h2_min_rel_size or is_prior_heading_label:
                    el.heading_level = 2
                elif el.rel_size >= weights.h3_min_rel_size:
                    el.heading_level = 3
                else:
                    el.heading_level = 4
            elif pf in ("DECIMAL", "ALPHA", "ALPHA_LOWER", "BULLET") and not (el.is_bold and words <= 5):
                el.role = "list_item"
            else:
                el.role = "paragraph"


class NativeDigitalStrategy(LayoutStrategy):
    pass


class ScannedTwoColumnTextbookStrategy(LayoutStrategy):
    pass


class IllustratedBookletStrategy(LayoutStrategy):
    pass


class AcademicArticleStrategy(LayoutStrategy):
    pass


class LayoutStrategyRegistry:
    _strategies: dict[str, LayoutStrategy] = {}

    @classmethod
    def get(cls, profile_or_id: Any) -> LayoutStrategy:
        from parsers.indra.profiles import LAYOUT_PROFILES
        pid = profile_or_id.profile_id if hasattr(profile_or_id, "profile_id") else str(profile_or_id)
        if pid not in cls._strategies:
            prof = LAYOUT_PROFILES.get(pid) or LAYOUT_PROFILES["native_digital"]
            strat_cls = {
                "NativeDigitalStrategy": NativeDigitalStrategy,
                "ScannedTwoColumnTextbookStrategy": ScannedTwoColumnTextbookStrategy,
                "IllustratedBookletStrategy": IllustratedBookletStrategy,
                "AcademicArticleStrategy": AcademicArticleStrategy,
            }.get(prof.strategy_key, NativeDigitalStrategy)
            cls._strategies[pid] = strat_cls(prof)
        return cls._strategies[pid]
