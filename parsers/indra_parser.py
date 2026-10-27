"""
Standalone Datalab Indra OCR ONNX Parser (`parsers/indra_parser.py`).

Adheres strictly to Architectural Invariants:
- Invariant 1 (Zero VRAM): Enforces `CUDA_VISIBLE_DEVICES=""` before any ONNX import.
- Invariant 8 (Lazy Search Singletons): Caches `indra_ocr` converter singletons per
  configuration tuple (`mode`, `math`, `furniture`, `dpi`, `threads`, `image_ocr`) so
  models are never re-instantiated per page/query.
- Invariant 22 (Offline Execution Safety): Sets `INDRA_OCR_MODEL_DIR`, `HF_HUB_OFFLINE=1`,
  and `TRANSFORMERS_OFFLINE=1` when local weights are present on disk.
- Strict No-Fallback Execution: Executes purely through `indra_ocr` ONNX Runtime
  without silently degrading to `fitz` or `docling` extractors.
"""

from __future__ import annotations

import os

# Invariant 1: Zero VRAM for Backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

import base64
import hashlib
import html
import logging
import re
import shutil
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np

import config
from parsers.embedding_engine import get_embedding_engine
from parsers.lancedb_engine import LanceDBEngine
from parsers.pdf_extractor import clean_pdf_text

logger = logging.getLogger(__name__)

# Directory and HuggingFace Repo Constants
INDRA_REPO_ID: str = "datalab-to/indra-ocr"
INDRA_MODELS_DIR: Path = getattr(
    config, "INDRA_MODELS_DIR", config.MODELS_DIR / "indra_ocr"
)

# Core ONNX and language model files required for standard offline execution (~76 MB)
REQUIRED_CORE_FILES: tuple[str, ...] = (
    "config.json",
    "meta.json",
    "det.onnx",
    "orient.onnx",
    "order.onnx",
    "script.onnx",
    "rec.onnx",
    "rec_cjk.onnx",
    "rec_rtl.onnx",
    "rec_world.onnx",
    "lm.npz",
    "lm_cjk.npz",
    "lm_world.npz",
)

# Additional ONNX files required when math=True (~20 MB)
REQUIRED_MATH_FILES: tuple[str, ...] = (
    "math_meta.json",
    "mathcls.onnx",
    "math_enc.onnx",
    "math_dec_init.onnx",
    "math_dec_step.onnx",
)

# Thread-safe singleton cache for loaded indra_ocr converters
_CONVERTER_LOCK = threading.Lock()
_CONVERTER_CACHE: dict[tuple[str, bool, bool, int, int, bool], Any] = {}


def get_indra_models_dir() -> Path:
    """Returns the canonical local directory for Indra OCR ONNX weights."""
    INDRA_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return INDRA_MODELS_DIR


def is_indra_models_downloaded(include_math: bool = False) -> bool:
    """
    Checks whether all required Indra OCR ONNX and NPZ files exist locally
    and are non-empty (>128 bytes).
    """
    model_dir = get_indra_models_dir()
    required = list(REQUIRED_CORE_FILES)
    if include_math:
        required.extend(REQUIRED_MATH_FILES)

    for filename in required:
        fpath = model_dir / filename
        if not fpath.is_file() or fpath.stat().st_size <= 128:
            return False
    return True


def download_indra_models(include_math: bool = True) -> dict[str, Any]:
    """
    Atomically downloads Datalab Indra OCR ONNX weights from Hugging Face
    (`datalab-to/indra-ocr`) into `INDRA_MODELS_DIR` without leaving orphaned
    partial files. Clears cached converters upon completion.
    """
    model_dir = get_indra_models_dir()
    required_files = list(REQUIRED_CORE_FILES)
    if include_math:
        required_files.extend(REQUIRED_MATH_FILES)

    # Temporarily allow online HuggingFace fetch during explicit download
    prev_hf_offline = os.environ.pop("HF_HUB_OFFLINE", None)
    prev_tf_offline = os.environ.pop("TRANSFORMERS_OFFLINE", None)

    try:
        from huggingface_hub import hf_hub_download

        downloaded: list[str] = []
        with tempfile.TemporaryDirectory(dir=str(model_dir.parent)) as tmp_dir:
            tmp_path = Path(tmp_dir)
            for filename in required_files:
                target_file = model_dir / filename
                if target_file.is_file() and target_file.stat().st_size > 128:
                    downloaded.append(filename)
                    continue

                fetched_path = hf_hub_download(
                    repo_id=INDRA_REPO_ID,
                    filename=filename,
                    local_dir=str(tmp_path),
                )
                tmp_target = model_dir / f".{filename}.tmp.{os.getpid()}"
                shutil.copy2(fetched_path, tmp_target)
                os.replace(tmp_target, target_file)
                downloaded.append(filename)

        os.environ["INDRA_OCR_MODEL_DIR"] = str(model_dir)
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

        with _CONVERTER_LOCK:
            _CONVERTER_CACHE.clear()
        extract_structured_layout.cache_clear()

        return {
            "status": "success",
            "model_dir": str(model_dir),
            "include_math": include_math,
            "files_ready": len(downloaded),
            "downloaded_files": downloaded,
        }
    except Exception as exc:
        logger.error("Failed to download Indra OCR models: %s", exc)
        raise RuntimeError(
            f"Indra OCR model download failed ({INDRA_REPO_ID}): {exc}"
        ) from exc
    finally:
        if prev_hf_offline is not None and not is_indra_models_downloaded(
            include_math=False
        ):
            os.environ["HF_HUB_OFFLINE"] = prev_hf_offline
        if prev_tf_offline is not None and not is_indra_models_downloaded(
            include_math=False
        ):
            os.environ["TRANSFORMERS_OFFLINE"] = prev_tf_offline


def _enforce_offline_env_if_available(require_math: bool = False) -> Path:
    """
    Invariant 22: Sets `INDRA_OCR_MODEL_DIR`, `HF_HUB_OFFLINE=1`, and
    `TRANSFORMERS_OFFLINE=1` when local weights exist on disk.
    Raises RuntimeError if weights are missing so no silent fallback occurs.
    """
    model_dir = get_indra_models_dir()
    os.environ["INDRA_OCR_MODEL_DIR"] = str(model_dir)

    if is_indra_models_downloaded(include_math=require_math):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return model_dir

    missing = [
        f
        for f in (
            REQUIRED_CORE_FILES + (REQUIRED_MATH_FILES if require_math else ())
        )
        if not (model_dir / f).is_file() or (model_dir / f).stat().st_size <= 128
    ]
    raise RuntimeError(
        f"Indra OCR ONNX models are not downloaded in '{model_dir}'. "
        f"Missing files: {', '.join(missing)}. "
        f"Please download Indra OCR models in Settings -> Models first."
    )


def get_indra_converter(
    mode: str = "auto",
    math: bool = False,
    furniture: bool = False,
    dpi: int = 200,
    threads: int = 4,
    image_ocr: bool = True,
) -> Any:
    """
    Invariant 8: Thread-safe cached singleton loader for `indra_ocr.load()`.
    Strictly runs on CPU via ONNX Runtime without fallback.
    """
    normalized_mode = mode if mode in ("auto", "ocr", "text") else "auto"
    cache_key = (
        normalized_mode,
        bool(math),
        bool(furniture),
        int(dpi),
        int(threads),
        bool(image_ocr),
    )

    with _CONVERTER_LOCK:
        if cache_key in _CONVERTER_CACHE:
            return _CONVERTER_CACHE[cache_key]

        model_dir = _enforce_offline_env_if_available(require_math=bool(math))
        try:
            import indra_ocr
        except ImportError as exc:
            raise RuntimeError(
                "The 'indra-ocr' package is not installed. Run: pip install indra-ocr"
            ) from exc

        converter = indra_ocr.load(
            path=str(model_dir),
            threads=int(threads),
            dpi=int(dpi),
            mode=normalized_mode,
            image_ocr=bool(image_ocr),
            furniture=bool(furniture),
            math=bool(math),
        )
        _CONVERTER_CACHE[cache_key] = converter
        return converter


def ocr_image_region(
    gray_or_bgr_image: np.ndarray,
    math: bool = False,
    threads: int = 4,
) -> dict[str, Any]:
    """
    Directly runs Indra OCR on an in-memory OpenCV image or cropped line/region
    (`conv.ocr(gray)`), returning structured markdown, plain text, lines, and blocks.
    Can be invoked directly by `AdaptivePdfLayoutEngine` when `ocr_engine="indra"`.
    """
    if gray_or_bgr_image is None or gray_or_bgr_image.size == 0:
        raise ValueError("Empty image array passed to ocr_image_region().")

    if gray_or_bgr_image.ndim == 3:
        try:
            import cv2

            if gray_or_bgr_image.shape[2] == 4:
                gray = cv2.cvtColor(gray_or_bgr_image, cv2.COLOR_BGRA2GRAY)
            else:
                gray = cv2.cvtColor(gray_or_bgr_image, cv2.COLOR_RGB2GRAY)
        except ImportError:
            gray = (
                0.299 * gray_or_bgr_image[:, :, 0]
                + 0.587 * gray_or_bgr_image[:, :, 1]
                + 0.114 * gray_or_bgr_image[:, :, 2]
            ).astype(np.uint8)
    elif gray_or_bgr_image.ndim == 2:
        gray = gray_or_bgr_image
    else:
        raise ValueError(
            f"Unsupported image array shape: {gray_or_bgr_image.shape}"
        )

    if gray.dtype != np.uint8:
        gray = np.clip(gray, 0, 255).astype(np.uint8)

    conv = get_indra_converter(mode="ocr", math=math, threads=threads)
    page = conv.ocr(gray)
    return _serialize_indra_page(page, page_number=1)


def _render_single_pdf_page_to_png_and_gray(
    file_path: Path, page_number: int, dpi: int = 200
) -> tuple[Path, np.ndarray, int, int]:
    """
    Renders a single 1-based PDF page using `pypdfium2` to a temporary 1-page PDF
    (so `conv.convert()` processes only the target page in `auto`/`text` mode)
    and also extracts a grayscale uint8 array for bounding-box SVG overlays.
    """
    import pypdfium2 as pdfium

    if page_number < 1:
        raise ValueError(f"page_number must be >= 1 (got {page_number})")

    src_doc = pdfium.PdfDocument(str(file_path))
    try:
        total_pages = len(src_doc)
        if page_number > total_pages:
            raise ValueError(
                f"Page {page_number} out of range for '{file_path.name}' ({total_pages} pages)."
            )

        # Render bitmap at target DPI for visualization / direct OCR
        page_obj = src_doc[page_number - 1]
        scale = float(dpi) / 72.0
        bitmap = page_obj.render(scale=scale)
        pil_img = bitmap.to_pil()
        width, height = pil_img.size
        gray_arr = np.asarray(pil_img.convert("L"), dtype=np.uint8)

        # Create a 1-page PDF in temp storage so `conv.convert` can inspect native text layer + OCR
        single_pdf = pdfium.PdfDocument.new()
        single_pdf.import_pages(src_doc, pages=[page_number - 1])
        tmp_fd, tmp_pdf_str = tempfile.mkstemp(suffix=".pdf", prefix="indra_p_")
        os.close(tmp_fd)
        tmp_pdf_path = Path(tmp_pdf_str)
        single_pdf.save(str(tmp_pdf_path))
        single_pdf.close()

        return tmp_pdf_path, gray_arr, width, height
    finally:
        src_doc.close()


def _serialize_indra_page(page: Any, page_number: int) -> dict[str, Any]:
    """Normalizes an `indra_ocr.Page` object into a plain Python dictionary."""
    raw_blocks = getattr(page, "blocks", []) or []
    blocks: list[dict[str, Any]] = []
    for blk in raw_blocks:
        if isinstance(blk, dict):
            blocks.append(
                {
                    "label": str(blk.get("label", "Text")),
                    "bbox": list(blk.get("bbox", [0, 0, 0, 0])),
                    "text": clean_pdf_text(str(blk.get("text", ""))),
                }
            )
        else:
            blocks.append(
                {
                    "label": str(getattr(blk, "label", "Text")),
                    "bbox": list(getattr(blk, "bbox", [0, 0, 0, 0])),
                    "text": clean_pdf_text(str(getattr(blk, "text", ""))),
                }
            )

    raw_lines = getattr(page, "lines", []) or []
    lines: list[dict[str, Any]] = []
    for ln in raw_lines:
        if isinstance(ln, dict):
            box = list(ln.get("box", ln.get("bbox", [0, 0, 0, 0])))
            txt = clean_pdf_text(str(ln.get("text", "")))
            conf = float(ln.get("conf", ln.get("confidence", 1.0)))
        else:
            box = list(getattr(ln, "box", getattr(ln, "bbox", [0, 0, 0, 0])))
            txt = clean_pdf_text(str(getattr(ln, "text", "")))
            conf = float(getattr(ln, "conf", getattr(ln, "confidence", 1.0)))
        lines.append({"box": box, "text": txt, "conf": round(conf, 4)})

    markdown_text = clean_pdf_text(str(getattr(page, "markdown", "") or ""))
    plain_text = clean_pdf_text(str(getattr(page, "text", "") or ""))
    if not markdown_text and plain_text:
        markdown_text = plain_text
    if not plain_text and markdown_text:
        plain_text = markdown_text

    timings = dict(getattr(page, "timings", {}) or {})
    return {
        "page": int(page_number),
        "page_number": int(page_number),
        "markdown": markdown_text,
        "text": plain_text,
        "blocks": blocks,
        "lines": lines,
        "source": str(getattr(page, "source", "ocr")),
        "rotation": int(getattr(page, "rotation", 0) or 0),
        "skew": float(getattr(page, "skew", 0.0) or 0.0),
        "vertical": bool(getattr(page, "vertical", False)),
        "timings": timings,
        "note": str(getattr(page, "note", "") or ""),
    }


def _build_layout_svg_overlay(
    blocks: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    width: int,
    height: int,
) -> str:
    """
    Constructs a scalable SVG bounding-box overlay displaying Indra OCR's
    layout blocks (`Section-Header`, `Table`, `List-Group`, `Text`, etc.)
    and individual detected OCR lines in reading order.
    """
    if width <= 0 or height <= 0:
        return ""

    label_Palette: dict[str, tuple[str, str]] = {
        "Section-Header": ("#f59e0b", "rgba(245, 158, 11, 0.14)"),
        "Table": ("#10b981", "rgba(16, 185, 129, 0.14)"),
        "List-Group": ("#8b5cf6", "rgba(139, 92, 246, 0.12)"),
        "Caption": ("#ec4899", "rgba(236, 72, 153, 0.12)"),
        "Footnote": ("#64748b", "rgba(100, 116, 139, 0.12)"),
        "Page-Header": ("#94a3b8", "rgba(148, 163, 184, 0.10)"),
        "Page-Footer": ("#94a3b8", "rgba(148, 163, 184, 0.10)"),
        "Form": ("#06b6d4", "rgba(6, 182, 212, 0.14)"),
        "Text": ("#3b82f6", "rgba(59, 130, 246, 0.08)"),
    }

    svg_parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" height="100%" style="max-width:100%;height:auto;">'
    ]

    # Faint line boxes
    for line in lines:
        box = line.get("box", [])
        if len(box) == 4:
            x0, y0, x1, y1 = [float(v) for v in box]
            w = max(1.0, x1 - x0)
            h = max(1.0, y1 - y0)
            svg_parts.append(
                f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" '
                f'fill="none" stroke="rgba(56, 189, 248, 0.35)" stroke-width="1" />'
            )

    # Layout blocks with reading-order index badges
    for idx, blk in enumerate(blocks, start=1):
        bbox = blk.get("bbox", [])
        if len(bbox) != 4:
            continue
        x0, y0, x1, y1 = [float(v) for v in bbox]
        w = max(2.0, x1 - x0)
        h = max(2.0, y1 - y0)
        label = str(blk.get("label", "Text"))
        stroke, fill = label_Palette.get(label, ("#3b82f6", "rgba(59, 130, 246, 0.08)"))
        safe_label = html.escape(f"#{idx} {label}")

        svg_parts.append(
            f'<g class="indra-layout-block" data-label="{html.escape(label)}">'
            f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="2" rx="3" />'
            f'<text x="{x0 + 4:.1f}" y="{max(12.0, y0 - 4):.1f}" '
            f'fill="{stroke}" font-family="monospace" font-size="11" font-weight="bold">'
            f"{safe_label}</text></g>"
        )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


def _map_indra_label_to_box_type(label: str) -> str:
    lbl = str(label or "").strip().lower()
    if "header" in lbl and "page" not in lbl:
        return "heading"
    if "title" in lbl:
        return "heading"
    if "table" in lbl:
        return "table"
    if "list" in lbl:
        return "list"
    if "caption" in lbl:
        return "caption"
    if "form" in lbl or "box" in lbl or "callout" in lbl:
        return "callout"
    return "paragraph"


def extract_page_with_indra(
    file_path: Union[str, Path],
    page_number: int,
    mode: str = "auto",
    math: bool = False,
    furniture: bool = False,
    dpi: int = 200,
    threads: int = 4,
    default_title: str = "",
    initial_heading_stack: Optional[dict[int, str]] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Extracts a single 1-based page from a PDF or image using Datalab Indra OCR
    without fallback. Used by the interactive test bench (`/api/sources/test_pdf_extract`),
    hybrid Docling v2/v3 delegation, and single-page inspection flows.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Source file not found: {path}")

    doc_title = str(default_title or path.stem).strip() or "Untitled"
    active_stack: dict[int, str] = dict(initial_heading_stack or {})

    t0 = time.perf_counter()
    conv = get_indra_converter(
        mode=mode,
        math=math,
        furniture=furniture,
        dpi=dpi,
        threads=threads,
    )

    tmp_pdf_path: Optional[Path] = None
    try:
        if path.suffix.lower() == ".pdf":
            tmp_pdf_path, gray_arr, width, height = (
                _render_single_pdf_page_to_png_and_gray(path, page_number, dpi=dpi)
            )
            pages_iter = conv.convert(str(tmp_pdf_path), max_pages=1)
            indra_page = None
            for _, p in pages_iter:
                indra_page = p
                break
            if indra_page is None:
                raise RuntimeError(
                    f"Indra OCR returned no output for {path.name} page {page_number}."
                )
        else:
            try:
                import cv2

                bgr = cv2.imread(str(path))
                if bgr is None:
                    raise RuntimeError(f"Unable to decode image file: {path}")
                height, width = bgr.shape[:2]
                gray_arr = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            except ImportError:
                from PIL import Image

                with Image.open(path) as pil_im:
                    width, height = pil_im.size
                    gray_arr = np.asarray(pil_im.convert("L"), dtype=np.uint8)
            indra_page = conv.ocr(gray_arr)

        serialized = _serialize_indra_page(indra_page, page_number=page_number)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        if "total_ms" not in serialized["timings"]:
            serialized["timings"]["total_ms"] = elapsed_ms

        safe_w = max(1.0, float(width))
        safe_h = max(1.0, float(height))
        layout_boxes: list[dict[str, Any]] = []
        headings_found: list[str] = []
        hierarchy_tree: list[dict[str, Any]] = []

        for idx, blk in enumerate(serialized["blocks"], start=1):
            raw_bbox = blk.get("bbox", [0, 0, 0, 0])
            if len(raw_bbox) == 4:
                norm_bbox = [
                    round(max(0.0, min(1.0, float(raw_bbox[0]) / safe_w)), 4),
                    round(max(0.0, min(1.0, float(raw_bbox[1]) / safe_h)), 4),
                    round(max(0.0, min(1.0, float(raw_bbox[2]) / safe_w)), 4),
                    round(max(0.0, min(1.0, float(raw_bbox[3]) / safe_h)), 4),
                ]
            else:
                norm_bbox = [0.0, 0.0, 1.0, 1.0]

            raw_label = str(blk.get("label", "Text"))
            b_type = _map_indra_label_to_box_type(raw_label)
            b_text = str(blk.get("text", "")).strip()

            if b_type == "heading" and b_text:
                clean_h = re.sub(r"^#+\s*", "", b_text).strip(" .:*")
                if clean_h:
                    lvl = 1 if not active_stack else 2
                    active_stack[lvl] = clean_h
                    for k in [k for k in list(active_stack.keys()) if k > lvl]:
                        del active_stack[k]
                    headings_found.append(clean_h)
                    crumbs = [doc_title] + [
                        active_stack[k]
                        for k in sorted(active_stack.keys())
                        if active_stack[k].lower() != doc_title.lower()
                    ]
                    hierarchy_tree.append({
                        "level": lvl,
                        "tag": f"H{lvl}",
                        "title": clean_h,
                        "heading_path": " > ".join(crumbs),
                        "order": idx,
                    })

            layout_boxes.append({
                "order": idx,
                "type": b_type,
                "bbox": norm_bbox,
                "markdown": b_text,
                "label": f"{raw_label} #{idx}" + (f": {b_text[:28]}" if b_text else ""),
            })

        crumbs = [doc_title]
        for lvl in sorted(active_stack.keys()):
            val = active_stack[lvl].strip()
            if val and val.lower() != crumbs[-1].lower():
                crumbs.append(val)
        heading_path = " > ".join(crumbs)
        deepest_lvl = max(active_stack.keys()) if active_stack else None
        subheading = active_stack[deepest_lvl] if deepest_lvl is not None else doc_title
        main_heading = active_stack.get(1) or active_stack.get(2) or doc_title

        svg_overlay = _build_layout_svg_overlay(
            serialized["blocks"], serialized["lines"], width, height
        )

        # Encode rendered grayscale image as PNG base64 for UI preview
        page_image_b64 = ""
        try:
            import cv2

            ok, png_buf = cv2.imencode(".png", gray_arr)
            if ok:
                page_image_b64 = f"data:image/png;base64,{base64.b64encode(png_buf.tobytes()).decode('ascii')}"
        except ImportError:
            import io
            from PIL import Image

            buf = io.BytesIO()
            Image.fromarray(gray_arr).save(buf, format="PNG")
            page_image_b64 = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"

        md_out = serialized["markdown"] or serialized["text"]
        return {
            "status": "success",
            "engine": "indra",
            "mode": mode,
            "math": math,
            "page": page_number,
            "page_number": page_number,
            "markdown": md_out,
            "text": serialized["text"],
            "extracted_text": md_out,
            "main_heading": main_heading,
            "heading_path": heading_path,
            "subheading": subheading,
            "headings": headings_found,
            "hierarchy_tree": hierarchy_tree,
            "active_heading_stack": active_stack,
            "blocks": serialized["blocks"],
            "layout_boxes": layout_boxes,
            "lines": serialized["lines"],
            "source": serialized["source"],
            "rotation": serialized["rotation"],
            "skew": serialized["skew"],
            "vertical": serialized["vertical"],
            "timings": serialized["timings"],
            "elapsed_ms": elapsed_ms,
            "svg_overlay": svg_overlay,
            "page_image_b64": page_image_b64,
            "page_image_base64": page_image_b64,
            "page_width": float(width),
            "page_height": float(height),
        }
    finally:
        if tmp_pdf_path is not None and tmp_pdf_path.exists():
            try:
                tmp_pdf_path.unlink()
            except OSError:
                pass


@lru_cache(maxsize=128)
def extract_structured_layout(
    file_path: Union[str, Path],
    page_number: int,
    mode: str = "auto",
    math: bool = False,
) -> str:
    """
    LRU-cached (maxsize=128) layout markdown extractor for a specific 1-based page.
    Strictly uses Indra OCR without fallback.
    """
    res = extract_page_with_indra(
        file_path=file_path,
        page_number=page_number,
        mode=mode,
        math=math,
    )
    return res["markdown"]


def extract_pdf_structure(
    file_path: Union[str, Path],
    default_title: str = "",
    mode: str = "auto",
    math: bool = False,
    furniture: bool = False,
    dpi: int = 200,
    threads: int = 4,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """
    Full PDF textbook & document structure extractor using Datalab Indra OCR.
    Preserves hierarchical headings (`Section-Header` blocks and `##` markdown
    headings), HTML/Markdown tables, lists, formulas, and reading order.

    Returns dictionaries strictly conforming to `ChunkDict`:
      `id`, `doc_id`, `page`, `page_number`, `title`, `heading`, `heading_path`,
      `subheading`, `text`, `snippet`, `source`, `file_path`, `length`,
      `article_length`, `doc_length`, `score`, `rerank_score`, `parent_id`.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF file not found: {path}")

    doc_title = default_title.strip() or path.stem
    conv = get_indra_converter(
        mode=mode,
        math=math,
        furniture=furniture,
        dpi=dpi,
        threads=threads,
    )

    raw_chunks: list[dict[str, Any]] = []
    current_h1: str = doc_title
    current_h2: str = ""
    total_doc_chars: int = 0

    for page_idx, page in conv.convert(str(path)):
        page_num = int(page_idx) + 1
        serialized = _serialize_indra_page(page, page_number=page_num)
        blocks = serialized["blocks"]
        page_md = serialized["markdown"]

        # Track section headings discovered on this page
        page_sections: list[tuple[str, str, list[str]]] = []
        active_lines: list[str] = []

        if blocks:
            for blk in blocks:
                label = blk["label"]
                blk_text = blk["text"].strip()
                if not blk_text:
                    continue

                if label == "Section-Header":
                    # Flush accumulated body text before updating heading state
                    if active_lines:
                        joined = "\n\n".join(active_lines).strip()
                        if joined:
                            heading_path = (
                                f"{current_h1} > {current_h2}"
                                if current_h2 and current_h2 != current_h1
                                else current_h1
                            )
                            subheading = current_h2 or current_h1
                            page_sections.append((heading_path, subheading, [joined]))
                        active_lines = []

                    cleaned_hdr = re.sub(r"^#+\s*", "", blk_text).strip()
                    if cleaned_hdr:
                        if current_h1 == doc_title:
                            current_h1 = cleaned_hdr
                            current_h2 = ""
                        else:
                            current_h2 = cleaned_hdr
                elif label in ("Page-Header", "Page-Footer") and not furniture:
                    continue
                else:
                    active_lines.append(blk_text)

            if active_lines:
                joined = "\n\n".join(active_lines).strip()
                if joined:
                    heading_path = (
                        f"{current_h1} > {current_h2}"
                        if current_h2 and current_h2 != current_h1
                        else current_h1
                    )
                    subheading = current_h2 or current_h1
                    page_sections.append((heading_path, subheading, [joined]))

        # If blocks produced no sections but markdown exists, use page markdown
        if not page_sections and page_md:
            heading_match = re.search(r"^#{1,3}\s+(.+)$", page_md, flags=re.MULTILINE)
            if heading_match:
                current_h2 = heading_match.group(1).strip()
            heading_path = (
                f"{current_h1} > {current_h2}"
                if current_h2 and current_h2 != current_h1
                else current_h1
            )
            subheading = current_h2 or current_h1
            page_sections.append((heading_path, subheading, [page_md]))

        for sec_idx, (heading_path, subheading, texts) in enumerate(page_sections):
            section_text = clean_pdf_text("\n\n".join(texts).strip())
            if not section_text:
                continue

            char_len = len(section_text)
            total_doc_chars += char_len
            digest = hashlib.md5(
                f"{path}::{page_num}::{sec_idx}::{heading_path}".encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()[:16]
            chunk_id = f"pdf_indra_{digest}"

            raw_chunks.append(
                {
                    "id": chunk_id,
                    "doc_id": chunk_id,
                    "page": page_num,
                    "page_number": page_num,
                    "title": doc_title,
                    "source_article": doc_title,
                    "heading": subheading,
                    "heading_path": heading_path,
                    "subheading": subheading,
                    "text": section_text,
                    "snippet": section_text[:600],
                    "source": "pdf",
                    "file_path": str(path),
                    "length": char_len,
                    "article_length": 0,  # Backfilled below once total_doc_chars is known
                    "doc_length": 0,
                    "score": 0.0,
                    "rerank_score": 0.0,
                    "parent_id": f"pdf_indra_page_{path.stem}_{page_num}",
                }
            )

    final_doc_len = max(total_doc_chars, 1)
    for chunk in raw_chunks:
        chunk["article_length"] = final_doc_len
        chunk["doc_length"] = final_doc_len

    return raw_chunks


# Drop-in alias matching `parsers.pdf_extractor.extract_pdf_pages`
extract_pdf_pages = extract_pdf_structure


def ingest_to_lancedb(
    file_path: Union[str, Path],
    source_article: str,
    main_topic: str = "",
    mode: str = "auto",
    math: bool = False,
) -> int:
    """
    Extracts sections from `file_path` via Datalab Indra OCR and ingests them into
    LanceDB using the cached `get_embedding_engine()` singleton and batched ONNX
    CPU embeddings, strictly enforcing the PyArrow `medical_knowledge` schema.
    """
    chunks = extract_pdf_structure(
        file_path=file_path,
        default_title=source_article,
        mode=mode,
        math=math,
    )
    if not chunks:
        return 0

    embedder = get_embedding_engine()
    dim = embedder.get_dimension()
    lancedb_engine = LanceDBEngine(vector_dim=dim)

    texts_to_embed = [
        f"{c['heading_path']}: {c['text']}" if c.get("heading_path") else c["text"]
        for c in chunks
    ]
    vectors = embedder.embed(texts_to_embed)

    records: list[dict[str, Any]] = []
    for idx, (chunk, vec) in enumerate(zip(chunks, vectors)):
        records.append(
            {
                "id": str(chunk["id"]),
                "vector": [float(v) for v in vec],
                "source_article": str(source_article or chunk["title"]),
                "file_path": str(chunk["file_path"]),
                "main_topic": str(main_topic or chunk["heading"] or source_article),
                "subheading": str(chunk["subheading"]),
                "heading_path": str(chunk["heading_path"]),
                "chunk_index": int(idx),
                "page_number": int(chunk["page_number"]),
                "article_length": int(chunk["article_length"]),
                "parent_content": str(chunk["text"]),
                "child_content": str(chunk["text"]),
            }
        )

    lancedb_engine.insert(records)
    return len(records)