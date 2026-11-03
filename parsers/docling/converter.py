"""
Docling DocumentConverter factory and AST serialization (`parsers/docling/converter.py`).

Provides thread-safe singleton converters with customizable OCR engines (RapidOCR / EasyOCR /
Tesseract / Mac OCR) and serializes Docling AST objects into structured Markdown.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

_DOCLING_AVAILABLE = False
DocumentStream = None
RapidOcrOptions = None
EasyOcrOptions = None
try:
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import (
        AcceleratorDevice,
        AcceleratorOptions,
        PdfPipelineOptions,
        TableStructureOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    _DOCLING_AVAILABLE = True
    try:
        from docling.datamodel.pipeline_options import RapidOcrOptions
    except Exception:
        RapidOcrOptions = None
    try:
        from docling.datamodel.pipeline_options import EasyOcrOptions
    except Exception:
        EasyOcrOptions = None
    try:
        from docling.datamodel.pipeline_options import TesseractOcrOptions
    except Exception:
        TesseractOcrOptions = None
    try:
        from docling.datamodel.pipeline_options import TesseractCliOcrOptions
    except Exception:
        TesseractCliOcrOptions = None
    try:
        from docling.datamodel.pipeline_options import OcrMacOptions
    except Exception:
        OcrMacOptions = None
except Exception:
    _DOCLING_AVAILABLE = False
    TesseractOcrOptions = None
    TesseractCliOcrOptions = None
    OcrMacOptions = None

from parsers.docling.cleaner import (
    _PROSE_FINITE_VERBS_RE,
    _TRAILING_FUNCTION_WORD_RE,
    clean_pdf_text,
)
from parsers.docling.models import is_docling_models_downloaded

logger = logging.getLogger(__name__)

_DOCLING_LOCK = threading.RLock()
_DOCLING_CONVERTERS: Dict[Any, Any] = {}


def get_docling_converter(
    do_ocr: bool = True,
    ocr_engine: Optional[str] = "auto",
    force_full_page_ocr: bool = False,
    **kwargs: Any,
) -> Any:
    """
    Initializes and caches a CPU-only Docling DocumentConverter instance (using Heron RT-DETRv2 layout model).
    Explicitly routes to RapidOcrOptions (PP-OCR ONNX Runtime) or EasyOcrOptions based on `ocr_engine`.
    """
    if not _DOCLING_AVAILABLE:
        return None

    raw_engine = ocr_engine if (ocr_engine and ocr_engine != "auto") else (kwargs.get("OCR_engine") or kwargs.get("ocrEngine"))
    engine_choice = str(raw_engine or "auto").strip().lower()

    cache_key = (bool(do_ocr), engine_choice, bool(force_full_page_ocr))
    if cache_key in _DOCLING_CONVERTERS:
        return _DOCLING_CONVERTERS[cache_key]

    with _DOCLING_LOCK:
        if cache_key in _DOCLING_CONVERTERS:
            return _DOCLING_CONVERTERS[cache_key]

        if is_docling_models_downloaded():
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = bool(do_ocr)
        pipeline_options.do_table_structure = True
        pipeline_options.generate_table_images = False
        pipeline_options.generate_picture_images = False
        pipeline_options.images_scale = 2.0

        if hasattr(pipeline_options, "accelerator_options"):
            pipeline_options.accelerator_options = AcceleratorOptions(
                num_threads=4,
                device=AcceleratorDevice.CPU,
            )

        if hasattr(pipeline_options, "table_structure_options"):
            pipeline_options.table_structure_options = TableStructureOptions(
                do_cell_matching=True,
                mode=TableStructureOptions.Mode.ACCURATE if hasattr(TableStructureOptions, "Mode") else None,
            )

        if do_ocr:
            if hasattr(pipeline_options, "force_full_page_ocr"):
                pipeline_options.force_full_page_ocr = bool(force_full_page_ocr)

            if engine_choice in ("paddle", "paddleocr", "rapid", "rapidocr") and RapidOcrOptions is not None:
                pipeline_options.ocr_options = RapidOcrOptions(force_full_page_ocr=bool(force_full_page_ocr))
            elif engine_choice in ("easyocr", "easy") and EasyOcrOptions is not None:
                pipeline_options.ocr_options = EasyOcrOptions(
                    lang=["en"],
                    use_gpu=False,
                    force_full_page_ocr=bool(force_full_page_ocr),
                )
            elif engine_choice in ("tesseract", "tess") and TesseractOcrOptions is not None:
                pipeline_options.ocr_options = TesseractOcrOptions(force_full_page_ocr=bool(force_full_page_ocr))
            elif engine_choice in ("tesseract_cli", "tess_cli") and TesseractCliOcrOptions is not None:
                pipeline_options.ocr_options = TesseractCliOcrOptions(force_full_page_ocr=bool(force_full_page_ocr))
            elif engine_choice in ("mac_ocr", "apple") and OcrMacOptions is not None:
                pipeline_options.ocr_options = OcrMacOptions(force_full_page_ocr=bool(force_full_page_ocr))

        try:
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
        except Exception as exc:
            logger.error("Docling DocumentConverter instantiation failed: %s", exc)
            raise exc

        _DOCLING_CONVERTERS[cache_key] = converter
        # Maintain backwards-compatible boolean key lookup
        _DOCLING_CONVERTERS[bool(do_ocr)] = converter
        return converter


def serialize_docling_ast(doc: Any, hierarchy_tree: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Serializes a DoclingDocument AST into Markdown while calibrating H1-H5 levels
    (and promoting colored/typographic headings) using AdaptivePdfLayoutEngine's hierarchy tree.
    """
    if doc is None:
        return ""

    level_lookup: Dict[str, int] = {}
    for h_node in (hierarchy_tree or []):
        norm_title = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", str(h_node.get("title", "")).lower())).strip()
        if norm_title:
            level_lookup[norm_title] = int(h_node.get("level", 2))

    blocks: List[Tuple[str, str]] = []
    try:
        from docling.datamodel.document import ContentLayer, DocItemLabel, PictureItem, TableItem
        iterator = doc.iterate_items(included_content_layers={ContentLayer.BODY})
    except Exception:
        iterator = []

    for item, level in iterator:
        label = getattr(item, "label", None)
        text_val = getattr(item, "text", "").strip() if hasattr(item, "text") else ""

        if label in {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}:
            continue

        if label == DocItemLabel.TITLE and text_val:
            blocks.append(("heading", f"# {text_val}"))
        elif label == DocItemLabel.SECTION_HEADER and text_val:
            norm_t = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text_val.lower())).strip()
            calibrated_lvl = level_lookup.get(norm_t)
            if calibrated_lvl is None:
                for k_norm, k_lvl in level_lookup.items():
                    if k_norm in norm_t or norm_t in k_norm:
                        calibrated_lvl = k_lvl
                        break
            if calibrated_lvl is None:
                if _PROSE_FINITE_VERBS_RE.search(text_val) or _TRAILING_FUNCTION_WORD_RE.search(text_val.rstrip(".,;:")):
                    blocks.append(("paragraph", clean_pdf_text(text_val).replace("\n", " ")))
                    continue
                if re.match(r"^(CHAPTER|PART|SECTION|UNIT)\s+", text_val, re.I):
                    calibrated_lvl = 1
                elif text_val.isupper() or re.match(r"^([IVXLCDM]+\.|\d{1,2}\.)\s+", text_val):
                    calibrated_lvl = 2
                elif re.match(r"^\d{1,2}\.\d{1,2}\.?\s+", text_val):
                    calibrated_lvl = 3
                else:
                    calibrated_lvl = min(max(int(level) + 1, 2), 5)

            clean_h = text_val.strip(" *")
            if calibrated_lvl == 5 or (calibrated_lvl == 4 and clean_h.isupper()):
                blocks.append(("heading", f"{'#' * calibrated_lvl} **{clean_h}**"))
            else:
                blocks.append(("heading", f"{'#' * calibrated_lvl} {clean_h}"))
        elif label == DocItemLabel.FORMULA and text_val:
            blocks.append(("formula", f"$$\n{text_val}\n$$"))
        elif label == DocItemLabel.LIST_ITEM and text_val:
            indent = "  " * max(0, int(level) - 1)
            marker = getattr(item, "marker", "-") or "-"
            md_marker = marker if re.match(r"^\d+[\.\)]$", str(marker)) else "-"
            blocks.append(("list", f"{indent}{md_marker} {clean_pdf_text(text_val).replace(chr(10), ' ')}"))
        elif label == DocItemLabel.TABLE:
            cap = item.caption_text(doc).strip() if hasattr(item, "caption_text") else ""
            if cap:
                blocks.append(("caption", f"> *{cap}*"))
            if hasattr(item, "export_to_markdown"):
                blocks.append(("table", item.export_to_markdown(doc=doc).strip()))
        elif label == DocItemLabel.PICTURE:
            cap = item.caption_text(doc).strip() if hasattr(item, "caption_text") else ""
            if cap:
                blocks.append(("caption", f"> **[Figure]** *{cap}*"))
        elif label == DocItemLabel.CAPTION and text_val:
            blocks.append(("caption", f"> *{clean_pdf_text(text_val).replace(chr(10), ' ')}*"))
        elif label == DocItemLabel.FOOTNOTE and text_val:
            blocks.append(("footnote", f"> ^[Footnote]: {clean_pdf_text(text_val).replace(chr(10), ' ')}"))
        elif text_val:
            norm_t = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text_val.lower())).strip()
            if len(text_val) <= 110 and norm_t in level_lookup:
                lvl = level_lookup[norm_t]
                clean_h = text_val.strip(" *")
                if lvl == 5 or (lvl == 4 and clean_h.isupper()):
                    blocks.append(("heading", f"{'#' * lvl} **{clean_h}**"))
                else:
                    blocks.append(("heading", f"{'#' * lvl} {clean_h}"))
            else:
                blocks.append(("paragraph", clean_pdf_text(text_val).replace("\n", " ")))

    out_chunks: List[str] = []
    for idx, (kind, val) in enumerate(blocks):
        if not val:
            continue
        if idx == 0:
            out_chunks.append(val)
        elif kind == "list" and blocks[idx - 1][0] == "list":
            out_chunks.append("\n" + val)
        elif kind in {"heading", "table", "formula", "caption", "footnote"}:
            out_chunks.append("\n\n" + val)
        else:
            out_chunks.append("\n\n" + val)
    return "".join(out_chunks)


# Backwards compatibility aliases with leading underscores
_get_docling_converter = get_docling_converter
_serialize_docling_ast = serialize_docling_ast
