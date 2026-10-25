"""
Layout-aware PDF textbook section & table extractor using Docling ML / PyMuPDF.
Enforces zero VRAM usage, cached engine singletons, thread safety, and offline safety.
Includes single-page slicing fix, OCR auto-detection, TableFormer config, and hybrid callout recovery.
"""

from __future__ import annotations

import functools
import hashlib
import io
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# Align offline Hugging Face cache if config is available
try:
    from config import HF_CACHE_DIR

    if HF_CACHE_DIR:
        os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HF_CACHE_DIR))
except ImportError:
    pass

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

try:
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
except ImportError:
    DocumentConverter = None
    PdfFormatOption = None
    PdfPipelineOptions = None
    InputFormat = None
    DocumentStream = None

try:
    from docling_core.types.doc.document import (
        CodeItem,
        FormulaItem,
        ListItem,
        SectionHeaderItem,
        TableItem,
        TextItem,
        TitleItem,
    )
except ImportError:
    TextItem = None
    SectionHeaderItem = None
    TableItem = None
    TitleItem = None
    ListItem = None
    CodeItem = None
    FormulaItem = None

from parsers.pdf_parser import extract_page_with_adaptive_layout

logger = logging.getLogger(__name__)

_converters: Dict[bool, Any] = {}
_CONVERTER_LOCK = threading.RLock()
_CONVERT_LOCK = threading.RLock()


def get_converter(do_ocr: bool = False) -> Any:
    """
    Thread-safe singleton loader for Docling ML models cached by do_ocr flag (Section 3 & Invariant 8).
    Enforces CPU-only execution (device='cpu', num_threads=4) to adhere to the zero-VRAM budget.
    """
    global _converters
    if DocumentConverter is None:
        raise RuntimeError("Docling is not installed in the current environment.")

    try:
        from parsers.docling_v2_parser import is_docling_models_downloaded
        if is_docling_models_downloaded():
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
    except Exception:
        pass

    if do_ocr not in _converters:
        with _CONVERTER_LOCK:
            if do_ocr not in _converters:
                pipeline_options = PdfPipelineOptions()
                pipeline_options.do_table_structure = True
                pipeline_options.generate_page_images = False
                pipeline_options.images_scale = 2.0
                pipeline_options.do_ocr = do_ocr

                if hasattr(pipeline_options, "table_structure_options"):
                    try:
                        pipeline_options.table_structure_options.do_cell_matching = True
                    except Exception:
                        pass

                # Strictly limit threads and force CPU accelerator (Invariant 1 & Section 3)
                if hasattr(pipeline_options, "accelerator_options"):
                    try:
                        pipeline_options.accelerator_options.device = "cpu"
                        pipeline_options.accelerator_options.num_threads = 4
                    except Exception:
                        pass
                else:
                    try:
                        from docling.datamodel.pipeline_options import (
                            AcceleratorDevice,
                            AcceleratorOptions,
                        )

                        pipeline_options.accelerator_options = AcceleratorOptions(
                            num_threads=4,
                            device=AcceleratorDevice.CPU,
                        )
                    except Exception:
                        pass

                _converters[do_ocr] = DocumentConverter(
                    allowed_formats=[InputFormat.PDF],
                    format_options={
                        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                    },
                )
    return _converters[do_ocr]


def _export_item_markdown(item: Any, doc_obj: Any) -> str:
    """Safely exports a Docling item (e.g. TableItem) to Markdown across Docling versions."""
    if not hasattr(item, "export_to_markdown") or not callable(item.export_to_markdown):
        return ""
    try:
        return str(item.export_to_markdown(doc=doc_obj) or "").strip()
    except TypeError:
        try:
            return str(item.export_to_markdown() or "").strip()
        except Exception:
            return ""
    except Exception:
        return ""


def _recover_picture_callouts(doc_obj: Any, fitz_page: Any) -> List[str]:
    """
    Fix 2.4: Recovers text inside shaded clinical callout boxes misclassified as Picture/Figure.
    Inspects picture bounding boxes and fitz blocks to extract missing prose.
    """
    recovered_callouts: List[str] = []
    if fitz_page is None or doc_obj is None:
        return recovered_callouts

    try:
        picture_items = []
        for entry in doc_obj.iterate_items():
            item = entry[0] if isinstance(entry, (tuple, list)) else entry
            item_label = getattr(item, "label", None)
            label_str = (
                str(item_label.value if hasattr(item_label, "value") else item_label).lower()
                if item_label
                else ""
            )
            if label_str in {"picture", "figure", "image"} or "picture" in type(item).__name__.lower():
                picture_items.append(item)

        blocks = fitz_page.get_text("blocks")
        for pic in picture_items:
            prov = getattr(pic, "prov", None)
            clip_rect = None
            if prov and isinstance(prov, (list, tuple)) and len(prov) > 0:
                p_bbox = getattr(prov[0], "bbox", None)
                if p_bbox and hasattr(p_bbox, "l"):
                    # Convert Docling coords to Fitz rect if available
                    clip_rect = fitz.Rect(p_bbox.l, p_bbox.t, p_bbox.r, p_bbox.b)

            box_text = ""
            if clip_rect:
                box_text = fitz_page.get_text("text", clip=clip_rect).strip()

            # If no clip_rect or clip empty, inspect blocks inside drawings or shaded rects
            if len(box_text) < 35:
                for b in blocks:
                    b_text = str(b[4]).strip()
                    if len(b_text) >= 35 and "pearl" in b_text.lower() or "clinical" in b_text.lower():
                        box_text = b_text
                        break

            if len(box_text) >= 35:
                callout_lines = [f"> {line}" for line in box_text.split("\n") if line.strip()]
                recovered_callouts.append("> **[Clinical Box]**\n" + "\n".join(callout_lines))

    except Exception as exc:
        logger.debug("_recover_picture_callouts error: %s", exc)

    return recovered_callouts


@functools.lru_cache(maxsize=128)
def _cached_extract_page_layout(normalized_path: str, page_number: int) -> str:
    """
    Internal LRU-cached worker for single-page layout markdown extraction.
    Fix 2.1: Slices page into an in-memory 1-page PDF buffer before conversion.
    Fix 2.2: Auto-detects native text length to disable OCR on digital PDFs.
    Fix 2.3: Configures CPU accelerator & high-resolution TableFormer.
    Fix 2.4: Recovers text inside shaded clinical callout boxes.
    Fix 2.5: Merges/falls back to Adaptive Geometric Engine on truncated output.
    """
    if page_number < 1:
        return ""

    path_obj = Path(normalized_path)
    if not path_obj.is_file():
        return ""

    if fitz is None:
        logger.warning("PyMuPDF (fitz) is not installed; cannot slice PDF page.")
        return ""

    target_page_index = page_number - 1
    native_text = ""
    fitz_page = None

    try:
        with fitz.open(normalized_path) as doc:
            if doc.is_encrypted:
                try:
                    doc.authenticate("")
                except Exception:
                    pass

            if target_page_index < 0 or target_page_index >= doc.page_count:
                return ""

            fitz_page = doc[target_page_index]
            native_text = fitz_page.get_text("text").strip()

            # Fix 2.1: Slice target page into a single 1-page document in memory
            with fitz.open() as single_page_doc:
                single_page_doc.insert_pdf(
                    doc, from_page=target_page_index, to_page=target_page_index
                )
                pdf_bytes = single_page_doc.tobytes()

        # Fix 2.2: Disable OCR if native text count >= 40 chars
        do_ocr = len(native_text) < 40

        # Fix 2.3: Obtain singleton converter configured for CPU and TableFormer
        converter = get_converter(do_ocr=do_ocr)
        stream = DocumentStream(name=f"page_{page_number}.pdf", stream=io.BytesIO(pdf_bytes))

        # Thread-safe conversion lock to prevent native CPU thrashing/crashes
        with _CONVERT_LOCK:
            conversion_result = converter.convert(stream)

        if (
            conversion_result
            and hasattr(conversion_result, "document")
            and conversion_result.document
        ):
            doc_obj = conversion_result.document
            # Export without page_no filtering since the sliced document has only 1 page
            md_text = doc_obj.export_to_markdown().strip()

            # Fix 2.4: Recover shaded clinical callout boxes misclassified as pictures
            callouts = _recover_picture_callouts(doc_obj, fitz_page)
            if callouts:
                for c in callouts:
                    if c[:30] not in md_text:
                        md_text = md_text + "\n\n" + c

            # Fix 2.5: Check if output is non-empty and sufficient relative to fitz native text
            if md_text and (len(native_text) < 40 or len(md_text) >= 0.25 * len(native_text)):
                return md_text

    except Exception as exc:
        logger.warning(
            "Docling layout extraction failed for %s page %d: %s. Attempting Adaptive Engine fallback.",
            normalized_path,
            page_number,
            exc,
        )

    # Fix 2.5 Fallback: Execute 6-stage Adaptive Multi-Column Geometric Engine
    try:
        adaptive_res = extract_page_with_adaptive_layout(normalized_path, page_number)
        return adaptive_res.get("extracted_text", "") or native_text
    except Exception as fallback_err:
        logger.error(
            "Fallback extractor also failed for %s page %d: %s",
            normalized_path,
            page_number,
            fallback_err,
        )
        return native_text


def extract_structured_layout(file_path: Union[str, Path], page_number: int) -> str:
    """
    Extracts structured markdown (tables, columns, headings) from a specific PDF page using Docling.
    Falls back gracefully to Adaptive Multi-Column Geometric Engine if Docling is unavailable or encounters an error.
    """
    if not file_path:
        return ""
    try:
        p_num = int(page_number)
    except (ValueError, TypeError):
        return ""

    if p_num < 1:
        return ""

    try:
        norm_path = str(Path(file_path).resolve())
    except Exception:
        norm_path = str(file_path)

    if DocumentConverter is None:
        try:
            res = extract_page_with_adaptive_layout(norm_path, p_num)
            return res.get("extracted_text", "")
        except Exception:
            return ""

    return _cached_extract_page_layout(norm_path, p_num)


def extract_pdf_structure(file_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """
    Extracts structured sections from a PDF, preserving hierarchical headings,
    paragraphs, markdown tables, lists, formulas, and section character lengths.
    Adheres strictly to the Section 4 ChunkDict and LanceDB data contracts.
    """
    if not file_path:
        return []

    norm_path = str(Path(file_path).resolve())
    path_obj = Path(norm_path)
    if not path_obj.is_file():
        logger.warning("Target PDF file does not exist: %s", norm_path)
        return []

    default_title = path_obj.stem

    if DocumentConverter is None:
        from parsers.pdf_extractor import extract_pdf_pages

        return extract_pdf_pages(norm_path, default_title=default_title)

    try:
        converter = get_converter(do_ocr=False)
        with _CONVERT_LOCK:
            conversion_result = converter.convert(norm_path)

        doc_obj = getattr(conversion_result, "document", None) if conversion_result else None
        if not doc_obj:
            from parsers.pdf_extractor import extract_pdf_pages

            return extract_pdf_pages(norm_path, default_title=default_title)

        sections: List[Dict[str, Any]] = []
        heading_stack: Dict[int, str] = {}
        current_text: List[str] = []
        current_page: int = 1
        sec_idx: int = 0

        def get_current_heading() -> str:
            valid_parts = [
                heading_stack[k].strip()
                for k in sorted(heading_stack.keys())
                if heading_stack[k].strip()
            ]
            return " > ".join(valid_parts) if valid_parts else default_title or "Untitled"

        def save_section(page_num: int) -> None:
            nonlocal sec_idx
            if not current_text:
                return

            substantive_lines = [
                line
                for line in current_text
                if not (line.startswith("[") and line.endswith("]"))
            ]
            if not substantive_lines:
                return

            text_content = "\n\n".join(current_text).strip()
            if text_content:
                h_path = get_current_heading()
                subheading = h_path.split(" > ")[-1] if " > " in h_path else h_path
                page_int = max(1, int(page_num))
                art_len = len(text_content)
                snippet = text_content[:400] + ("..." if art_len > 400 else "")
                raw_key = f"{norm_path}_{page_int}_{sec_idx}_{text_content[:50]}"
                chunk_id = hashlib.md5(
                    raw_key.encode("utf-8"), usedforsecurity=False
                ).hexdigest()
                sec_idx += 1

                sections.append({
                    "id": chunk_id,
                    "doc_id": chunk_id,
                    "page": page_int,
                    "page_number": page_int,
                    "title": default_title or "Untitled",
                    "source_article": default_title or "Untitled",
                    "heading": h_path,
                    "heading_path": h_path,
                    "subheading": subheading,
                    "text": text_content,
                    "snippet": snippet,
                    "source": "pdf",
                    "file_path": norm_path,
                    "length": art_len,
                    "article_length": art_len,
                    "doc_length": art_len,
                    "score": 0.0,
                    "rerank_score": 0.0,
                    "parent_id": None,
                })

        for entry in doc_obj.iterate_items():
            if isinstance(entry, (tuple, list)):
                item = entry[0]
                item_level = entry[1] if len(entry) > 1 else getattr(item, "level", 1)
            else:
                item = entry
                item_level = getattr(item, "level", 1)

            try:
                item_level = int(item_level) if item_level is not None else 1
            except (ValueError, TypeError):
                item_level = 1

            item_page = current_page
            prov = getattr(item, "prov", None)
            if prov and isinstance(prov, (list, tuple)) and len(prov) > 0:
                p = prov[0]
                raw_page = getattr(
                    p, "page_no", getattr(p, "page", getattr(p, "page_number", None))
                )
                if raw_page is None and isinstance(p, dict):
                    raw_page = p.get("page_no", p.get("page", p.get("page_number")))
                if raw_page is not None:
                    try:
                        item_page = max(1, int(raw_page))
                    except (ValueError, TypeError):
                        pass

            if item_page != current_page and current_text:
                save_section(current_page)
                current_text = []
                current_page = item_page
                active_h = get_current_heading()
                if active_h != "Untitled":
                    current_text.append(f"[{active_h}]")

            item_label = getattr(item, "label", None)
            label_str = (
                str(item_label.value if hasattr(item_label, "value") else item_label).lower()
                if item_label
                else ""
            )

            is_header = False
            if (SectionHeaderItem and isinstance(item, SectionHeaderItem)) or (
                TitleItem and isinstance(item, TitleItem)
            ):
                is_header = True
            elif label_str in {"section_header", "title", "page_header", "header", "doc_title"}:
                is_header = True

            if is_header:
                save_section(current_page)
                for k in [k for k in list(heading_stack.keys()) if k >= item_level]:
                    del heading_stack[k]

                item_text = str(getattr(item, "text", "") or "").strip()
                if item_text:
                    heading_stack[item_level] = item_text
                    current_text = [f"[{get_current_heading()}]"]
                    current_page = item_page

            elif (TableItem and isinstance(item, TableItem)) or label_str == "table":
                md_table = _export_item_markdown(item, doc_obj)
                if md_table:
                    current_text.append(md_table)
                    current_page = item_page

            else:
                text_val = ""
                if hasattr(item, "text") and isinstance(item.text, str) and item.text.strip():
                    text_val = item.text.strip()
                else:
                    text_val = _export_item_markdown(item, doc_obj)

                if text_val:
                    current_text.append(text_val)
                    current_page = item_page

        save_section(current_page)
        if sections:
            return sections

    except FileNotFoundError:
        return []
    except Exception as exc:
        logger.warning(
            "Full PDF Docling extraction failed for %s (%s); falling back to PyMuPDF.",
            norm_path,
            exc,
        )

    try:
        from parsers.pdf_extractor import extract_pdf_pages

        return extract_pdf_pages(norm_path, default_title=default_title)
    except Exception:
        return []


def ingest_to_lancedb(
    file_path: Union[str, Path], source_article: str, main_topic: str = ""
) -> int:
    """
    Extracts PDF structure and ingests it into LanceDB with heading paths and article lengths.
    Utilizes cached singleton engines (Invariant 8) and batched CPU vector embeddings.
    """
    from parsers.embedding_engine import get_embedding_engine
    from parsers.lancedb_engine import get_lancedb_engine

    if not file_path:
        return 0

    norm_path = str(Path(file_path).resolve())
    if not Path(norm_path).is_file():
        logger.warning("File not found for LanceDB ingestion: %s", norm_path)
        return 0

    sections = extract_pdf_structure(norm_path)
    if not sections:
        return 0

    embedder = get_embedding_engine()
    vector_dim = embedder.get_dimension()
    engine = get_lancedb_engine(vector_dim=vector_dim)

    candidates: List[Dict[str, Any]] = []
    chunk_index = 0

    for section in sections:
        page_num = max(1, int(section.get("page_number", section.get("page", 1)) or 1))
        heading = str(section.get("heading_path", section.get("heading", "Untitled")))
        subheading = str(section.get("subheading", heading))
        parent_content = str(section.get("text", ""))
        art_len = int(section.get("article_length", len(parent_content)) or len(parent_content))

        child_chunks = [chunk.strip() for chunk in parent_content.split("\n\n") if chunk.strip()]

        for child_content in child_chunks:
            if (
                child_content.startswith("[")
                and child_content.endswith("]")
                and child_content[1:-1] == heading
            ):
                continue

            if len(child_content) < 10:
                continue

            candidates.append({
                "page_num": page_num,
                "heading": heading,
                "subheading": subheading,
                "parent_content": parent_content,
                "child_content": child_content,
                "art_len": art_len,
                "chunk_index": chunk_index,
            })
            chunk_index += 1

    if not candidates:
        return 0

    total_ingested = 0
    batch_size = 32
    flush_threshold = 128
    pending_records: List[Dict[str, Any]] = []
    article_name = str(source_article or Path(norm_path).name)

    for i in range(0, len(candidates), batch_size):
        batch = candidates[i : i + batch_size]
        texts = [c["child_content"] for c in batch]
        vectors = embedder.embed(texts)

        if not vectors or len(vectors) != len(batch):
            vectors = [[0.0] * vector_dim for _ in batch]

        for cand, vector in zip(batch, vectors):
            if not vector:
                continue

            c_text = cand["child_content"]
            p_num = cand["page_num"]
            c_idx = cand["chunk_index"]

            raw_key = f"{norm_path}_{p_num}_{c_idx}_{c_text[:50]}"
            chunk_id = hashlib.md5(raw_key.encode("utf-8"), usedforsecurity=False).hexdigest()

            pending_records.append({
                "id": str(chunk_id),
                "vector": [float(x) for x in vector],
                "source_article": article_name,
                "file_path": str(norm_path),
                "main_topic": str(main_topic or cand["heading"]),
                "subheading": str(cand["subheading"]),
                "heading_path": str(cand["heading"]),
                "chunk_index": int(c_idx),
                "page_number": int(p_num),
                "article_length": int(cand["art_len"]),
                "parent_content": str(cand["parent_content"]),
                "child_content": str(c_text),
            })

        if len(pending_records) >= flush_threshold:
            engine.insert(pending_records)
            total_ingested += len(pending_records)
            pending_records.clear()

    if pending_records:
        engine.insert(pending_records)
        total_ingested += len(pending_records)
        pending_records.clear()

    if total_ingested > 0:
        logger.info("Ingested %d chunks into LanceDB from %s", total_ingested, norm_path)

    return total_ingested
