from __future__ import annotations

from parsers.indra.document import (
    DocumentHeadingTracker,
    get_or_create_document_tracker,
)
from parsers.indra.engine import (
    DEFAULT_WIN_THREADS,
    INDRA_MODELS_DIR,
    INDRA_REPO_ID,
    REQUIRED_CORE_FILES,
    REQUIRED_MATH_FILES,
    FallbackChain,
    IndraModularEngine,
    download_indra_models,
    get_indra_converter,
    get_indra_models_dir,
    is_indra_models_downloaded,
    reconstruct_2d_table_grid,
)
from parsers.indra.profiles import (
    CoordinateZone,
    FurnitureSuppressionPolicy,
    HeadingWeightPolicy,
    LAYOUT_PROFILES,
    LayoutProfile,
    PageGeometryPolicy,
)
from parsers.indra.refiner import (
    IndraHybridLayoutRefiner,
    build_layout_svg_overlay,
)
from parsers.indra.rules import (
    RegexCatalog,
    deskew_bbox_norm,
    enrich_lines,
    overlap_ratio,
    rgb_distance,
    rgb_saturation,
    rgb_to_hsv,
    union_bbox,
)
from parsers.indra.strategies import (
    AcademicArticleStrategy,
    IllustratedBookletStrategy,
    LayoutStrategy,
    LayoutStrategyRegistry,
    NativeDigitalStrategy,
    ScannedTwoColumnTextbookStrategy,
)
from parsers.indra.types import (
    BBox,
    ElementRole,
    HeadingPrototype,
    PageElement,
    PageElements,
    RGB,
    TableGrid,
    ThreadSafeLRUCache,
)

__all__ = [
    "CoordinateZone",
    "FurnitureSuppressionPolicy",
    "HeadingWeightPolicy",
    "LayoutProfile",
    "PageGeometryPolicy",
    "LAYOUT_PROFILES",
    "ThreadSafeLRUCache",
    "TableGrid",
    "PageElement",
    "PageElements",
    "HeadingPrototype",
    "ElementRole",
    "BBox",
    "RGB",
    "RegexCatalog",
    "deskew_bbox_norm",
    "enrich_lines",
    "overlap_ratio",
    "rgb_distance",
    "rgb_saturation",
    "rgb_to_hsv",
    "union_bbox",
    "LayoutStrategy",
    "NativeDigitalStrategy",
    "ScannedTwoColumnTextbookStrategy",
    "IllustratedBookletStrategy",
    "AcademicArticleStrategy",
    "LayoutStrategyRegistry",
    "DocumentHeadingTracker",
    "get_or_create_document_tracker",
    "FallbackChain",
    "IndraModularEngine",
    "reconstruct_2d_table_grid",
    "get_indra_models_dir",
    "is_indra_models_downloaded",
    "download_indra_models",
    "get_indra_converter",
    "DEFAULT_WIN_THREADS",
    "INDRA_MODELS_DIR",
    "INDRA_REPO_ID",
    "REQUIRED_CORE_FILES",
    "REQUIRED_MATH_FILES",
    "IndraHybridLayoutRefiner",
    "build_layout_svg_overlay",
]

# Consolidated compatibility exports
from parsers.indra.engine import *  # noqa: F401,F403
from parsers.indra.profiles import *  # noqa: F401,F403
