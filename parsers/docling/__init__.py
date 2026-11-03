"""
Docling Modular Parser Package (`parsers/docling/`).

Modular architecture:
- `models`: Pre-trained weights, offline cache checks, download management.
- `converter`: DocumentConverter singleton factory, multi-engine OCR routing, AST serialization.
- `cleaner`: Medical text sanitization, dehyphenation, drop-cap joining, OCR token repair.
- `geometry`: Bounding box math, overlap/containment tests, color distance.
- `inspector`: Pixel-level visual analyzer, stroke-density profiling, line-crop re-OCR.
- `engine`: AdaptivePdfLayoutEngine 6-stage layout extraction pipeline.
"""

from __future__ import annotations

from parsers.docling.cleaner import (
    clean_pdf_text,
    is_ocr_garbled_text,
    repair_ocr_token_heuristics,
)
from parsers.docling.converter import (
    get_docling_converter,
    serialize_docling_ast,
)
from parsers.docling.engine import (
    AdaptivePdfLayoutEngine,
)
from parsers.docling.geometry import (
    point_in_rect,
    rect_area,
    rects_overlap,
    rgb_distance,
)
from parsers.docling.inspector import (
    VisualPageInspector,
    get_easyocr_line_engine,
    get_rapidocr_line_engine,
)
from parsers.docling.models import (
    DOCLING_REQUIRED_MODELS,
    download_docling_models,
    ensure_offline_mode_if_downloaded,
    is_docling_models_downloaded,
)

__all__ = [
    "AdaptivePdfLayoutEngine",
    "clean_pdf_text",
    "is_ocr_garbled_text",
    "repair_ocr_token_heuristics",
    "get_docling_converter",
    "serialize_docling_ast",
    "VisualPageInspector",
    "get_rapidocr_line_engine",
    "get_easyocr_line_engine",
    "DOCLING_REQUIRED_MODELS",
    "is_docling_models_downloaded",
    "download_docling_models",
    "ensure_offline_mode_if_downloaded",
    "rect_area",
    "rects_overlap",
    "point_in_rect",
    "rgb_distance",
]
