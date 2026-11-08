from __future__ import annotations
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping

ColumnMode = Literal['auto', 'single', 'two_column', 'multi_column']
StrategyKey = Literal[
    'NativeDigitalStrategy',
    'ScannedTwoColumnTextbookStrategy',
    'IllustratedBookletStrategy',
    'AcademicArticleStrategy',
]

@dataclass(frozen=True)
class CoordinateZone:
    x0_min: float
    y0_min: float
    x1_max: float
    y1_max: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.x0_min < self.x1_max <= 1.0 and 0.0 <= self.y0_min < self.y1_max <= 1.0):
            raise ValueError(
                f"Invalid CoordinateZone bounds: ({self.x0_min}, {self.y0_min}, {self.x1_max}, {self.y1_max})"
            )

@dataclass(frozen=True)
class PageGeometryPolicy:
    column_mode: ColumnMode
    gutter_x_range: tuple[float, float]
    min_gutter_gap_px_200dpi: float
    min_column_width_ratio: float
    max_column_width_ratio: float
    spanning_width_ratio: float
    max_wrapped_heading_line_gap_ratio: float
    paragraph_line_gap_ratio: float
    list_continuation_indent_ratio: float
    callout_box_min_area_ratio: float
    callout_box_max_area_ratio: float
    default_toc_chapter_window: tuple[int, int]
    subline_word_split_gap_em: float = 1.65
    ocr_line_merge_y_tol_ratio: float = 0.48
    table_col_gap_min_ratio: float = 0.025

@dataclass(frozen=True)
class HeadingWeightPolicy:
    def values(self): return self.factor_weights.values()
    def keys(self): return self.factor_weights.keys()
    def items(self): return self.factor_weights.items()
    def get(self, k: str, default=None): return self.factor_weights.get(k, default)
    def __getitem__(self, k: str): return self.factor_weights[k]

    size_cluster_tol_pt: float
    rel_size_tier_tol: float
    h1_min_rel_size: float
    h2_min_rel_size: float
    h3_min_rel_size: float
    chromatic_rgb_dist_min: float
    chromatic_saturation_min: int
    shaded_banner_bg_dist_min: float
    shaded_banner_min_saturation: int
    min_heading_confidence: float
    allow_chapter_color_shift: bool
    strict_list_vs_heading_disambiguation: bool
    factor_weights: Mapping[str, float] = field(default_factory=dict)

@dataclass(frozen=True)
class FurnitureSuppressionPolicy:
    header_max_y_ratio: float
    footer_min_y_ratio: float
    filter_page_numbers: bool
    filter_running_headers: bool
    filter_decorative_rules: bool
    suppress_quoted_epigraphs_from_headings: bool = True
    suppress_citation_and_doi_lines: bool = True
    min_repeat_pages: int = 2
    exception_zones: tuple[CoordinateZone, ...] = ()

@dataclass(frozen=True)
class LayoutProfile:
    profile_id: str
    description: str
    strategy_key: StrategyKey
    geometry: PageGeometryPolicy
    heading_weights: HeadingWeightPolicy
    furniture_suppression: FurnitureSuppressionPolicy

LAYOUT_PROFILES: Mapping[str, LayoutProfile] = MappingProxyType({
    'native_digital': LayoutProfile(
        profile_id='native_digital',
        description='Standard single/multi-column born-digital clinical guideline PDF',
        strategy_key='NativeDigitalStrategy',
        geometry=PageGeometryPolicy(
            column_mode='auto',
            gutter_x_range=(0.20, 0.80),
            min_gutter_gap_px_200dpi=12.0,
            min_column_width_ratio=0.18,
            max_column_width_ratio=0.96,
            spanning_width_ratio=0.68,
            max_wrapped_heading_line_gap_ratio=0.55,
            paragraph_line_gap_ratio=1.45,
            list_continuation_indent_ratio=0.035,
            callout_box_min_area_ratio=0.03,
            callout_box_max_area_ratio=0.85,
            default_toc_chapter_window=(1, 15),
            table_col_gap_min_ratio=0.025,
        ),
        heading_weights=HeadingWeightPolicy(
            size_cluster_tol_pt=1.1,
            rel_size_tier_tol=0.08,
            h1_min_rel_size=1.45,
            h2_min_rel_size=1.20,
            h3_min_rel_size=1.05,
            chromatic_rgb_dist_min=24.0,
            chromatic_saturation_min=28,
            shaded_banner_bg_dist_min=32.0,
            shaded_banner_min_saturation=25,
            min_heading_confidence=0.58,
            allow_chapter_color_shift=True,
            strict_list_vs_heading_disambiguation=True,
            factor_weights=MappingProxyType({
                'size': 0.28,
                'weight': 0.20,
                'color': 0.16,
                'case': 0.10,
                'banner': 0.14,
                'prefix': 0.12,
            }),
        ),
        furniture_suppression=FurnitureSuppressionPolicy(
            header_max_y_ratio=0.075,
            footer_min_y_ratio=0.935,
            filter_page_numbers=True,
            filter_running_headers=True,
            filter_decorative_rules=True,
        ),
    ),
    'scanned_two_column_textbook': LayoutProfile(
        profile_id='scanned_two_column_textbook',
        description='Scanned two-column clinical textbook with OCR line fragmentation',
        strategy_key='ScannedTwoColumnTextbookStrategy',
        geometry=PageGeometryPolicy(
            column_mode='two_column',
            gutter_x_range=(0.35, 0.65),
            min_gutter_gap_px_200dpi=14.0,
            min_column_width_ratio=0.25,
            max_column_width_ratio=0.60,
            spanning_width_ratio=0.68,
            max_wrapped_heading_line_gap_ratio=0.48,
            paragraph_line_gap_ratio=1.65,
            list_continuation_indent_ratio=0.030,
            callout_box_min_area_ratio=0.04,
            callout_box_max_area_ratio=0.75,
            default_toc_chapter_window=(1, 30),
            table_col_gap_min_ratio=0.030,
        ),
        heading_weights=HeadingWeightPolicy(
            size_cluster_tol_pt=1.4,
            rel_size_tier_tol=0.10,
            h1_min_rel_size=1.50,
            h2_min_rel_size=1.25,
            h3_min_rel_size=1.08,
            chromatic_rgb_dist_min=30.0,
            chromatic_saturation_min=35,
            shaded_banner_bg_dist_min=38.0,
            shaded_banner_min_saturation=30,
            min_heading_confidence=0.62,
            allow_chapter_color_shift=False,
            strict_list_vs_heading_disambiguation=True,
            factor_weights=MappingProxyType({
                'size': 0.32,
                'weight': 0.22,
                'color': 0.12,
                'case': 0.12,
                'banner': 0.12,
                'prefix': 0.10,
            }),
        ),
        furniture_suppression=FurnitureSuppressionPolicy(
            header_max_y_ratio=0.085,
            footer_min_y_ratio=0.925,
            filter_page_numbers=True,
            filter_running_headers=True,
            filter_decorative_rules=True,
        ),
    ),
    'illustrated_booklet': LayoutProfile(
        profile_id='illustrated_booklet',
        description='Single-column public health booklet with graphical callouts & icons',
        strategy_key='IllustratedBookletStrategy',
        geometry=PageGeometryPolicy(
            column_mode='single',
            gutter_x_range=(0.0, 0.0),
            min_gutter_gap_px_200dpi=0.0,
            min_column_width_ratio=0.40,
            max_column_width_ratio=0.98,
            spanning_width_ratio=0.85,
            max_wrapped_heading_line_gap_ratio=0.65,
            paragraph_line_gap_ratio=1.80,
            list_continuation_indent_ratio=0.045,
            callout_box_min_area_ratio=0.02,
            callout_box_max_area_ratio=0.92,
            default_toc_chapter_window=(1, 10),
            table_col_gap_min_ratio=0.020,
        ),
        heading_weights=HeadingWeightPolicy(
            size_cluster_tol_pt=1.6,
            rel_size_tier_tol=0.12,
            h1_min_rel_size=1.40,
            h2_min_rel_size=1.18,
            h3_min_rel_size=1.02,
            chromatic_rgb_dist_min=20.0,
            chromatic_saturation_min=24,
            shaded_banner_bg_dist_min=26.0,
            shaded_banner_min_saturation=20,
            min_heading_confidence=0.52,
            allow_chapter_color_shift=True,
            strict_list_vs_heading_disambiguation=False,
            factor_weights=MappingProxyType({
                'size': 0.22,
                'weight': 0.18,
                'color': 0.24,
                'case': 0.08,
                'banner': 0.20,
                'prefix': 0.08,
            }),
        ),
        furniture_suppression=FurnitureSuppressionPolicy(
            header_max_y_ratio=0.105,
            footer_min_y_ratio=0.910,
            filter_page_numbers=True,
            filter_running_headers=True,
            filter_decorative_rules=True,
        ),
    ),
    'academic_article': LayoutProfile(
        profile_id='academic_article',
        description='Peer-reviewed journal paper with dense two-column layout and citations',
        strategy_key='AcademicArticleStrategy',
        geometry=PageGeometryPolicy(
            column_mode='two_column',
            gutter_x_range=(0.42, 0.58),
            min_gutter_gap_px_200dpi=12.0,
            min_column_width_ratio=0.38,
            max_column_width_ratio=0.52,
            spanning_width_ratio=0.80,
            max_wrapped_heading_line_gap_ratio=0.45,
            paragraph_line_gap_ratio=1.35,
            list_continuation_indent_ratio=0.025,
            callout_box_min_area_ratio=0.05,
            callout_box_max_area_ratio=0.65,
            default_toc_chapter_window=(1, 5),
            table_col_gap_min_ratio=0.022,
        ),
        heading_weights=HeadingWeightPolicy(
            size_cluster_tol_pt=0.9,
            rel_size_tier_tol=0.06,
            h1_min_rel_size=1.35,
            h2_min_rel_size=1.15,
            h3_min_rel_size=1.02,
            chromatic_rgb_dist_min=22.0,
            chromatic_saturation_min=25,
            shaded_banner_bg_dist_min=30.0,
            shaded_banner_min_saturation=22,
            min_heading_confidence=0.60,
            allow_chapter_color_shift=False,
            strict_list_vs_heading_disambiguation=True,
            factor_weights=MappingProxyType({
                'size': 0.30,
                'weight': 0.24,
                'color': 0.10,
                'case': 0.14,
                'banner': 0.08,
                'prefix': 0.14,
            }),
        ),
        furniture_suppression=FurnitureSuppressionPolicy(
            header_max_y_ratio=0.070,
            footer_min_y_ratio=0.940,
            filter_page_numbers=True,
            filter_running_headers=True,
            filter_decorative_rules=True,
        ),
    ),
})


# --- Auto-migrated from layout_profiles.py ---
from parsers.indra.profiles import (
    ColumnMode,
    CoordinateZone,
    FurnitureSuppressionPolicy,
    HeadingWeightPolicy,
    LAYOUT_PROFILES,
    LayoutProfile,
    PageGeometryPolicy,
    StrategyKey,
)

__all__ = [
    "ColumnMode",
    "CoordinateZone",
    "FurnitureSuppressionPolicy",
    "HeadingWeightPolicy",
    "LAYOUT_PROFILES",
    "LayoutProfile",
    "PageGeometryPolicy",
    "StrategyKey",
]
