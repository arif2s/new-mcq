"""
Adaptive Multi-Column Medical Layout Engine for PyMuPDF (fitz) and Docling Hybrid Recovery.
Implements per-page typography profiling, vector ruling barriers, shaded callout isolation,
topological DAG reading order, base64 PNG rendering, layout boxes, and heading path detection.
"""

from __future__ import annotations

import logging
import os

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from parsers.docling_v2_parser import (
    AdaptivePdfLayoutEngine,
    clean_pdf_text,
    extract_page_with_adaptive_layout,
)

logger = logging.getLogger(__name__)

__all__ = [
    "clean_pdf_text",
    "AdaptivePdfLayoutEngine",
    "extract_page_with_adaptive_layout",
]
