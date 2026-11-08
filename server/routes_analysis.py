"""
PDF Layout Analysis API Router (`server/routes_analysis.py`).

Provides endpoints for tracking, reviewing, annotating, and persisting
fine-grained document layout elements across PDF knowledge sources.
Strictly conforms to Invariants 1, 2, 3, 15, and 20.
"""

from __future__ import annotations

import os

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

# Enforce Invariant 2: DLL Pre-warming at top of module
try:
    import onnxruntime
except ImportError:
    pass
try:
    import lancedb
except ImportError:
    pass
try:
    import tantivy
except ImportError:
    pass

import asyncio
from contextlib import closing
import json
import logging
from pathlib import Path
from typing import Any, List, Optional, Union
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from config import DATA_DIR, PDFS_DIR, VAULTS_DIR
from core.database import (
    get_db_connection,
    upsert_layout_analysis_progress,
    upsert_user_annotated_bbox,
)
import parsers.indra.profiles as layout_profiles
import parsers.pdf_extractor as pdf_extractor
import parsers.indra as indra_parser
import parsers.docling as docling_parser
from parsers.indra.document import DocumentHeadingTracker, get_or_create_document_tracker

logger = logging.getLogger("medisearch.routes_analysis")

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


# ---------------------------------------------------------------------------
# Request & Response Models
# ---------------------------------------------------------------------------

class InitializeAnalysisRequest(BaseModel):
    file_path: str = Field(..., description="Absolute or relative path to the PDF file")
    profile_id: Optional[str] = Field("native_digital", description="Profile ID from parsers.indra.profiles")


class BBoxItem(BaseModel):
    id: Optional[str] = Field(None, description="Unique ID for bounding box")
    x0: float = Field(..., description="Left coordinate in points or normalized")
    y0: float = Field(..., description="Top coordinate in points or normalized")
    x1: float = Field(..., description="Right coordinate in points or normalized")
    y1: float = Field(..., description="Bottom coordinate in points or normalized")
    element_type: str = Field(..., description="Element type (e.g., text, header, table, figure)")
    heading_level: Optional[int] = Field(0, description="Heading level if header")
    display_order: int = Field(..., description="Display / reading order index")
    raw_text: Optional[str] = Field("", description="Raw extracted text inside bbox")
    is_custom_drawn: Optional[int] = Field(0, description="1 if drawn manually by user, 0 otherwise")


class SavePageRequest(BaseModel):
    file_path: str = Field(..., description="Path to the PDF file")
    page_number: int = Field(..., ge=1, description="1-based page number")
    bboxes: List[BBoxItem] = Field(..., description="List of bounding boxes for this page")


class SaveMarkdownRequest(BaseModel):
    file_path: str = Field(..., description="Path to the PDF file")
    markdown_text: str = Field(..., description="Final markdown override content")


def _resolve_pdf_path(path_str: str) -> Optional[Path]:
    """Resolves relative or absolute path against common source roots."""
    if not path_str or not path_str.strip():
        return None
    p = Path(path_str.strip())
    if p.is_file():
        return p.resolve()
    for root in [PDFS_DIR, VAULTS_DIR, DATA_DIR]:
        candidate = (root / p).resolve()
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/files")
async def get_analysis_files():
    """Returns a list of all unindexed or selectively checked PDFs from knowledge_sources
    along with their analysis status from layout_analysis_progress.
    """
    files_result = []
    seen_paths = set()

    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()

            # 1. Fetch sources from knowledge_sources
            cursor.execute(
                """
                SELECT id, name, type, path, enabled, lastIndexed, pdfEngine
                FROM knowledge_sources
                WHERE LOWER(type) = 'pdf'
                ORDER BY name ASC
                """
            )
            source_rows = cursor.fetchall()

            pdf_candidates: list[dict[str, Any]] = []
            for r in source_rows:
                source_id = str(r["id"])
                source_name = str(r["name"] or "")
                raw_path = str(r["path"] or "")
                enabled = bool(r["enabled"])
                last_indexed = str(r["lastIndexed"] or "Never")
                pdf_engine = str(r["pdfEngine"] or "fitz")

                p = Path(raw_path)
                if p.is_file() and p.suffix.lower() == ".pdf":
                    norm_path = str(p.resolve())
                    if norm_path.lower() not in seen_paths:
                        seen_paths.add(norm_path.lower())
                        pdf_candidates.append({
                            "source_id": source_id,
                            "name": source_name or p.name,
                            "file_path": norm_path,
                            "enabled": enabled,
                            "last_indexed": last_indexed,
                            "pdf_engine": pdf_engine,
                        })
                elif p.is_dir():
                    for sub_pdf in sorted(p.rglob("*.pdf")):
                        if sub_pdf.is_file():
                            norm_path = str(sub_pdf.resolve())
                            if norm_path.lower() not in seen_paths:
                                seen_paths.add(norm_path.lower())
                                pdf_candidates.append({
                                    "source_id": source_id,
                                    "name": sub_pdf.name,
                                    "file_path": norm_path,
                                    "enabled": enabled,
                                    "last_indexed": last_indexed,
                                    "pdf_engine": pdf_engine,
                                })

            # Also check PDFS_DIR fallback if empty
            if not pdf_candidates and PDFS_DIR.exists():
                for sub_pdf in sorted(PDFS_DIR.rglob("*.pdf")):
                    if sub_pdf.is_file():
                        norm_path = str(sub_pdf.resolve())
                        if norm_path.lower() not in seen_paths:
                            seen_paths.add(norm_path.lower())
                            pdf_candidates.append({
                                "source_id": "fallback_pdfs_dir",
                                "name": sub_pdf.name,
                                "file_path": norm_path,
                                "enabled": True,
                                "last_indexed": "Never",
                                "pdf_engine": "indra",
                            })

            # 2. Query layout_analysis_progress for existing records
            cursor.execute(
                """
                SELECT file_path, status, total_pages, reviewed_pages_json,
                       active_profile, final_markdown_override, updated_at
                FROM layout_analysis_progress
                """
            )
            progress_map: dict[str, dict[str, Any]] = {}
            for row in cursor.fetchall():
                fp = str(row["file_path"])
                try:
                    norm_fp = str(Path(fp).resolve()).lower()
                except Exception:
                    norm_fp = fp.lower()

                reviewed_pages = []
                if row["reviewed_pages_json"]:
                    try:
                        reviewed_pages = json.loads(row["reviewed_pages_json"])
                    except Exception:
                        reviewed_pages = []

                progress_map[norm_fp] = {
                    "file_path": fp,
                    "status": row["status"] or "pending",
                    "total_pages": int(row["total_pages"] or 0),
                    "reviewed_pages": reviewed_pages,
                    "reviewed_pages_count": len(reviewed_pages),
                    "active_profile": row["active_profile"] or "native_digital",
                    "has_markdown_override": bool(row["final_markdown_override"]),
                    "updated_at": row["updated_at"],
                }

            # 3. Combine candidates with progress status
            for cand in pdf_candidates:
                fp_lower = cand["file_path"].lower()
                prog = progress_map.get(fp_lower)
                files_result.append({
                    "source_id": cand["source_id"],
                    "name": cand["name"],
                    "file_path": cand["file_path"],
                    "enabled": cand["enabled"],
                    "last_indexed": cand["last_indexed"],
                    "pdf_engine": cand["pdf_engine"],
                    "analysis_status": prog["status"] if prog else "unindexed",
                    "total_pages": prog["total_pages"] if prog else 0,
                    "reviewed_pages": prog["reviewed_pages"] if prog else [],
                    "reviewed_pages_count": prog["reviewed_pages_count"] if prog else 0,
                    "active_profile": prog["active_profile"] if prog else "native_digital",
                    "has_markdown_override": prog["has_markdown_override"] if prog else False,
                    "updated_at": prog["updated_at"] if prog else None,
                })

            # Also include any files already tracked in layout_analysis_progress that weren't in candidates
            for norm_fp, prog in progress_map.items():
                if norm_fp not in seen_paths:
                    files_result.append({
                        "source_id": "tracked",
                        "name": Path(prog["file_path"]).name,
                        "file_path": prog["file_path"],
                        "enabled": True,
                        "last_indexed": "Never",
                        "pdf_engine": "indra",
                        "analysis_status": prog["status"],
                        "total_pages": prog["total_pages"],
                        "reviewed_pages": prog["reviewed_pages"],
                        "reviewed_pages_count": prog["reviewed_pages_count"],
                        "active_profile": prog["active_profile"],
                        "has_markdown_override": prog["has_markdown_override"],
                        "updated_at": prog["updated_at"],
                    })

        return {
            "status": "success",
            "files": files_result,
            "count": len(files_result),
        }

    except Exception as exc:
        logger.exception("Error listing analysis files: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch analysis files: {exc}",
        )


@router.post("/initialize")
async def initialize_analysis(req: InitializeAnalysisRequest):
    """Takes file_path and profile_id, resolves page count using
    parsers.pdf_extractor.get_pdf_page_count(), and initializes layout_analysis_progress.
    """
    clean_path = req.file_path.strip()
    resolved_path = _resolve_pdf_path(clean_path)
    if not resolved_path:
        resolved_path = Path(clean_path)
        if not resolved_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PDF file not found at: {clean_path}",
            )

    normalized_file_path = str(resolved_path.resolve())

    # Validate profile_id against layout_profiles
    profile_id = req.profile_id or "native_digital"
    if profile_id not in layout_profiles.LAYOUT_PROFILES:
        valid_profiles = list(layout_profiles.LAYOUT_PROFILES.keys())
        logger.warning(
            "Profile ID '%s' not recognized; defaulting to 'native_digital'. Valid: %s",
            profile_id,
            valid_profiles,
        )
        profile_id = "native_digital"

    # Compute page count using parsers.pdf_extractor.get_pdf_page_count()
    try:
        total_pages = await asyncio.to_thread(
            pdf_extractor.get_pdf_page_count, normalized_file_path
        )
    except Exception as exc:
        logger.error("Error reading page count from %s: %s", normalized_file_path, exc)
        total_pages = 0

    if total_pages <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unable to read pages from PDF or file is empty: {clean_path}",
        )

    # Check if record already exists to preserve existing reviewed pages
    reviewed_pages_json = "[]"
    status_str = "pending"
    final_md = None

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT status, reviewed_pages_json, final_markdown_override
            FROM layout_analysis_progress
            WHERE file_path = ?
            """,
            (normalized_file_path,),
        )
        row = cursor.fetchone()
        if row:
            status_str = row["status"] or "pending"
            reviewed_pages_json = row["reviewed_pages_json"] or "[]"
            final_md = row["final_markdown_override"]

    # Upsert adhering to Invariant 15
    upsert_layout_analysis_progress(
        file_path=normalized_file_path,
        status=status_str,
        total_pages=total_pages,
        reviewed_pages_json=reviewed_pages_json,
        active_profile=profile_id,
        final_markdown_override=final_md,
    )

    return {
        "status": "success",
        "file_path": normalized_file_path,
        "profile_id": profile_id,
        "total_pages": total_pages,
        "analysis_status": status_str,
    }


@router.get("/page-data")
async def get_page_data(
    file_path: str = Query(..., description="PDF file path"),
    page_number: int = Query(..., ge=1, description="1-based page number"),
):
    """Takes file_path and page_number. Returns visual page dimensions and an ordered array
    of layout elements. If entries exist in user_annotated_bboxes, return those overrides.
    Otherwise, invoke parsers.indra.extract_page_with_indra() dynamically to get
    layout regions, caching text and visual boxes.
    """
    clean_path = file_path.strip()
    resolved_path = _resolve_pdf_path(clean_path)
    if not resolved_path:
        resolved_path = Path(clean_path)
        if not resolved_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PDF file not found at: {clean_path}",
            )

    normalized_file_path = str(resolved_path.resolve())

    # Check active_profile from layout_analysis_progress
    active_profile = "native_digital"
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT active_profile FROM layout_analysis_progress WHERE file_path = ?",
            (normalized_file_path,),
        )
        row = cursor.fetchone()
        if row and row["active_profile"]:
            active_profile = row["active_profile"]

    # 1. Check if user_annotated_bboxes exists for this file_path and page_number
    overrides: list[dict[str, Any]] = []
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, file_path, page_number, x0, y0, x1, y1,
                   element_type, heading_level, display_order, raw_text,
                   is_custom_drawn, updated_at
            FROM user_annotated_bboxes
            WHERE file_path = ? AND page_number = ?
            ORDER BY display_order ASC, id ASC
            """,
            (normalized_file_path, page_number),
        )
        for r in cursor.fetchall():
            overrides.append({
                "id": r["id"],
                "file_path": r["file_path"],
                "page_number": r["page_number"],
                "x0": r["x0"],
                "y0": r["y0"],
                "x1": r["x1"],
                "y1": r["y1"],
                "element_type": r["element_type"],
                "heading_level": r["heading_level"],
                "display_order": r["display_order"],
                "raw_text": r["raw_text"],
                "is_custom_drawn": r["is_custom_drawn"],
                "updated_at": r["updated_at"],
            })

    if overrides:
        # Measure page dimensions and render background page image
        import io
        import base64
        import pypdfium2 as pdfium
        page_image_base64 = ""
        try:
            doc = pdfium.PdfDocument(normalized_file_path)
            if 1 <= page_number <= len(doc):
                p_obj = doc[page_number - 1]
                w_pt, h_pt = p_obj.get_size()
                bitmap = p_obj.render(scale=150.0 / 72.0)
                pil_img = bitmap.to_pil()
                bitmap.close()
                p_obj.close()
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                page_image_base64 = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
                pil_img.close()
            else:
                w_pt, h_pt = 612.0, 792.0
            doc.close()
        except Exception as e:
            logger.debug("Failed to render page image in overrides: %s", e)
            w_pt, h_pt = 612.0, 792.0

        return {
            "status": "success",
            "file_path": normalized_file_path,
            "page_number": page_number,
            "source": "user_annotated_bboxes",
            "dimensions": {
                "width": float(w_pt),
                "height": float(h_pt),
            },
            "elements": overrides,
            "page_image_base64": page_image_base64,
        }

    # 2. No user overrides found -> Invoke extract_page_with_indra dynamically
    try:
        indra_res = await asyncio.to_thread(
            indra_parser.extract_page_with_indra,
            normalized_file_path,
            page_number=page_number,
            profile_id=active_profile,
        )
    except Exception as exc:
        logger.exception("extract_page_with_indra failed for %s (page %d): %s", normalized_file_path, page_number, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Indra page layout extraction failed: {exc}",
        )

    width = float(indra_res.get("page_width") or 612.0)
    height = float(indra_res.get("page_height") or 792.0)

    # Build layout elements from blocks/lines
    # If lines or layout_boxes are present, synthesize bounding boxes
    elements: list[dict[str, Any]] = []
    lines = indra_res.get("lines") or []
    layout_boxes = indra_res.get("layout_boxes") or indra_res.get("blocks") or []

    # Ensure layout_analysis_progress parent record exists prior to caching child bboxes
    total_pages = await asyncio.to_thread(
        pdf_extractor.get_pdf_page_count, normalized_file_path
    )
    upsert_layout_analysis_progress(
        file_path=normalized_file_path,
        status="in_progress",
        total_pages=max(1, total_pages),
        active_profile=active_profile,
    )

    if lines:
        for idx, line in enumerate(lines):
            # Prefer point coordinates box_pt or convert normalized bbox_norm
            box = line.get("box_pt") or line.get("box") or [0.0, 0.0, width, height]
            if line.get("bbox_norm"):
                bn = line["bbox_norm"]
                box = [bn[0] * width, bn[1] * height, bn[2] * width, bn[3] * height]
            elif box[0] <= 1.05 and box[1] <= 1.05 and box[2] <= 1.05 and box[3] <= 1.05:
                box = [box[0] * width, box[1] * height, box[2] * width, box[3] * height]
            elif box[2] > width * 1.5:
                # If coordinates are in raster pixels, convert to points
                scale_x = width / max(1.0, float(indra_res.get("page_image_width") or width * (200.0 / 72.0)))
                scale_y = height / max(1.0, float(indra_res.get("page_image_height") or height * (200.0 / 72.0)))
                box = [box[0] * scale_x, box[1] * scale_y, box[2] * scale_x, box[3] * scale_y]

            role = line.get("role") or "paragraph"
            raw_text = line.get("text") or ""
            heading_lvl = 0
            el_type = "text"
            if role == "heading":
                el_type = "header"
                heading_lvl = 1
            elif role == "table":
                el_type = "table"
            elif role == "figure":
                el_type = "figure"
            elif role == "caption":
                el_type = "caption"
            elif role == "list_item" or role == "list":
                el_type = "list"
            elif role == "footer":
                el_type = "footer"

            line_lvl = line.get("heading_level")
            if line_lvl is not None and line_lvl > 0:
                heading_lvl = int(line_lvl)
                el_type = "header"

            elem_id = f"auto_{page_number}_{idx}_{uuid.uuid4().hex[:8]}"
            elements.append({
                "id": elem_id,
                "file_path": normalized_file_path,
                "page_number": page_number,
                "x0": round(float(box[0]), 2),
                "y0": round(float(box[1]), 2),
                "x1": round(float(box[2]), 2),
                "y1": round(float(box[3]), 2),
                "element_type": el_type,
                "heading_level": heading_lvl,
                "display_order": idx,
                "raw_text": raw_text,
                "is_custom_drawn": 0,
            })
    elif layout_boxes:
        for idx, lb in enumerate(layout_boxes):
            bbox = lb.get("bbox_pt") or lb.get("bbox") or [0.0, 0.0, width, height]
            if lb.get("bbox_norm"):
                bn = lb["bbox_norm"]
                bbox = [bn[0] * width, bn[1] * height, bn[2] * width, bn[3] * height]
            elif bbox[0] <= 1.05 and bbox[1] <= 1.05 and bbox[2] <= 1.05 and bbox[3] <= 1.05:
                bbox = [bbox[0] * width, bbox[1] * height, bbox[2] * width, bbox[3] * height]
            elif bbox[2] > width * 1.5:
                scale_x = width / max(1.0, float(indra_res.get("page_image_width") or width * (200.0 / 72.0)))
                scale_y = height / max(1.0, float(indra_res.get("page_image_height") or height * (200.0 / 72.0)))
                bbox = [bbox[0] * scale_x, bbox[1] * scale_y, bbox[2] * scale_x, bbox[3] * scale_y]

            lbl = lb.get("label") or "Text"
            el_type = indra_parser._map_indra_label_to_box_type(lbl)
            heading_lvl = 1 if el_type == "header" else 0
            elem_id = f"auto_{page_number}_{idx}_{uuid.uuid4().hex[:8]}"
            elements.append({
                "id": elem_id,
                "file_path": normalized_file_path,
                "page_number": page_number,
                "x0": round(float(bbox[0]), 2),
                "y0": round(float(bbox[1]), 2),
                "x1": round(float(bbox[2]), 2),
                "y1": round(float(bbox[3]), 2),
                "element_type": el_type,
                "heading_level": heading_lvl,
                "display_order": idx,
                "raw_text": lb.get("text") or "",
                "is_custom_drawn": 0,
            })

    # Cache text and visual boxes into user_annotated_bboxes
    if elements:
        with closing(get_db_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                for el in elements:
                    upsert_user_annotated_bbox(
                        bbox_id=el["id"],
                        file_path=normalized_file_path,
                        page_number=page_number,
                        x0=el["x0"],
                        y0=el["y0"],
                        x1=el["x1"],
                        y1=el["y1"],
                        element_type=el["element_type"],
                        heading_level=el["heading_level"],
                        display_order=el["display_order"],
                        raw_text=el["raw_text"],
                        is_custom_drawn=el["is_custom_drawn"],
                        conn=conn,
                    )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    return {
        "status": "success",
        "file_path": normalized_file_path,
        "page_number": page_number,
        "source": "indra_parser",
        "dimensions": {
            "width": width,
            "height": height,
        },
        "elements": elements,
        "svg_overlay": indra_res.get("svg_overlay", ""),
        "page_image_base64": indra_res.get("page_image_base64") or indra_res.get("page_image_b64") or "",
    }


@router.post("/save-page")
async def save_page_annotations(req: SavePageRequest):
    """Takes file_path, page_number, and an array of bounding boxes.
    Uses a batched transaction (BATCH_SIZE <= 1000 per Invariant 20) to wipe existing
    page records in user_annotated_bboxes and replace them.
    Updates the reviewed_pages_json array field.
    """
    clean_path = req.file_path.strip()
    resolved_path = _resolve_pdf_path(clean_path)
    normalized_file_path = str(resolved_path.resolve()) if resolved_path else str(Path(clean_path).resolve())

    # Verify parent record exists in layout_analysis_progress
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT total_pages, reviewed_pages_json, active_profile FROM layout_analysis_progress WHERE file_path = ?",
            (normalized_file_path,),
        )
        row = cursor.fetchone()

        if not row:
            total_pages = await asyncio.to_thread(
                pdf_extractor.get_pdf_page_count, normalized_file_path
            )
            upsert_layout_analysis_progress(
                file_path=normalized_file_path,
                status="in_progress",
                total_pages=max(1, total_pages),
                reviewed_pages_json="[]",
                active_profile="native_digital",
            )
            reviewed_pages = []
        else:
            try:
                reviewed_pages = json.loads(row["reviewed_pages_json"] or "[]")
                if not isinstance(reviewed_pages, list):
                    reviewed_pages = []
            except Exception:
                reviewed_pages = []

    # Update reviewed_pages set
    if req.page_number not in reviewed_pages:
        reviewed_pages.append(req.page_number)
        reviewed_pages.sort()

    # Invariant 20: Batched transaction with BATCH_SIZE <= 1000
    BATCH_SIZE = 500
    bboxes_data = req.bboxes

    with closing(get_db_connection()) as conn:
        # Wipe existing records for this page
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute(
                "DELETE FROM user_annotated_bboxes WHERE file_path = ? AND page_number = ?",
                (normalized_file_path, req.page_number),
            )

            # Insert new boxes in batches
            insert_sql = """
                INSERT INTO user_annotated_bboxes (
                    id, file_path, page_number, x0, y0, x1, y1,
                    element_type, heading_level, display_order, raw_text,
                    is_custom_drawn, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('now'))
                ON CONFLICT(id) DO UPDATE SET
                    file_path = excluded.file_path,
                    page_number = excluded.page_number,
                    x0 = excluded.x0,
                    y0 = excluded.y0,
                    x1 = excluded.x1,
                    y1 = excluded.y1,
                    element_type = excluded.element_type,
                    heading_level = excluded.heading_level,
                    display_order = excluded.display_order,
                    raw_text = excluded.raw_text,
                    is_custom_drawn = excluded.is_custom_drawn,
                    updated_at = excluded.updated_at
            """

            for i in range(0, max(1, len(bboxes_data)), BATCH_SIZE):
                chunk = bboxes_data[i : i + BATCH_SIZE]
                if not chunk:
                    break
                rows_to_insert = []
                for box in chunk:
                    b_id = box.id or f"user_{req.page_number}_{box.display_order}_{uuid.uuid4().hex[:8]}"
                    rows_to_insert.append((
                        b_id,
                        normalized_file_path,
                        req.page_number,
                        box.x0,
                        box.y0,
                        box.x1,
                        box.y1,
                        box.element_type,
                        box.heading_level or 0,
                        box.display_order,
                        box.raw_text or "",
                        box.is_custom_drawn or 0,
                    ))
                conn.executemany(insert_sql, rows_to_insert)

            # Update reviewed_pages_json and status in layout_analysis_progress
            reviewed_json_str = json.dumps(reviewed_pages)
            conn.execute(
                """
                UPDATE layout_analysis_progress
                SET reviewed_pages_json = ?,
                    status = 'in_progress',
                    updated_at = unixepoch('now')
                WHERE file_path = ?
                """,
                (reviewed_json_str, normalized_file_path),
            )
            conn.execute("COMMIT;")
        except Exception as exc:
            conn.execute("ROLLBACK;")
            logger.exception("Failed to save page annotations: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database transaction failed: {exc}",
            )

    return {
        "status": "success",
        "file_path": normalized_file_path,
        "page_number": req.page_number,
        "saved_boxes_count": len(bboxes_data),
        "reviewed_pages": reviewed_pages,
    }


@router.post("/save-markdown")
async def save_markdown_override(req: SaveMarkdownRequest):
    """Takes file_path and markdown_text, saving the string directly
    to final_markdown_override in layout_analysis_progress.
    """
    clean_path = req.file_path.strip()
    resolved_path = _resolve_pdf_path(clean_path)
    normalized_file_path = str(resolved_path.resolve()) if resolved_path else str(Path(clean_path).resolve())

    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT total_pages, reviewed_pages_json, active_profile, status
            FROM layout_analysis_progress
            WHERE file_path = ?
            """,
            (normalized_file_path,),
        )
        row = cursor.fetchone()

        if not row:
            total_pages = await asyncio.to_thread(
                pdf_extractor.get_pdf_page_count, normalized_file_path
            )
            upsert_layout_analysis_progress(
                file_path=normalized_file_path,
                status="completed",
                total_pages=max(1, total_pages),
                reviewed_pages_json="[]",
                active_profile="native_digital",
                final_markdown_override=req.markdown_text,
            )
        else:
            conn.execute(
                """
                UPDATE layout_analysis_progress
                SET final_markdown_override = ?,
                    status = 'completed',
                    updated_at = unixepoch('now')
                WHERE file_path = ?
                """,
                (req.markdown_text, normalized_file_path),
            )

    return {
        "status": "success",
        "file_path": normalized_file_path,
        "markdown_length": len(req.markdown_text),
    }


class UpdateHeadingRequest(BaseModel):
    id: str
    heading_level: int = Field(..., ge=1, le=6)


class FinalizeAnalysisRequest(BaseModel):
    file_path: str
    export_to_lancedb: bool = True
    output_filename: Optional[str] = None


@router.get("/toc")
async def get_document_toc(file_path: str = Query(...)):
    """Returns the document-wide Table of Contents headings extracted across all pages.
    References parent indices and breadcrumbs using DocumentHeadingTracker.
    """
    clean_path = file_path.strip()
    resolved_path = _resolve_pdf_path(clean_path)
    normalized_file_path = str(resolved_path.resolve()) if resolved_path else str(Path(clean_path).resolve())

    headings_list = []
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, file_path, page_number, x0, y0, x1, y1,
                   element_type, heading_level, display_order, raw_text
            FROM user_annotated_bboxes
            WHERE file_path = ? AND LOWER(element_type) IN ('header', 'heading')
            ORDER BY page_number ASC, display_order ASC, y0 ASC
            """,
            (normalized_file_path,),
        )
        rows = cursor.fetchall()

        if rows:
            for r in rows:
                level = int(r["heading_level"] or 1)
                if level < 1:
                    level = 1
                if level > 6:
                    level = 6
                headings_list.append({
                    "id": str(r["id"]),
                    "page_number": int(r["page_number"]),
                    "x0": float(r["x0"] if r["x0"] is not None else 0.0),
                    "y0": float(r["y0"] if r["y0"] is not None else 0.0),
                    "x1": float(r["x1"] if r["x1"] is not None else 1.0),
                    "y1": float(r["y1"] if r["y1"] is not None else 1.0),
                    "element_type": str(r["element_type"]),
                    "heading_level": level,
                    "display_order": int(r["display_order"] or 0),
                    "title": str(r["raw_text"] or "").strip(),
                })

    # If user_annotated_bboxes has no headings yet, fall back to Indra parser full document scan
    if not headings_list and resolved_path and resolved_path.is_file():
        try:
            tracker = get_or_create_document_tracker(resolved_path, reset=True)
            # Scan pages with indra to populate tracker
            page_count = await asyncio.to_thread(pdf_extractor.get_pdf_page_count, str(resolved_path))
            for p in range(1, min(page_count + 1, 200)):
                indra_data = await asyncio.to_thread(
                    indra_parser.extract_page_with_indra,
                    str(resolved_path),
                    p,
                    profile_id="native_digital",
                )
                p_boxes = indra_data.get("layout_boxes", [])
                for b in p_boxes:
                    cat = str(b.get("category", "")).lower()
                    if cat in ("header", "heading"):
                        lvl = int(b.get("heading_level") or 1)
                        headings_list.append({
                            "id": f"gen_p{p}_{b.get('id', len(headings_list))}",
                            "page_number": p,
                            "x0": float(b.get("box", [0, 0, 0, 0])[0]),
                            "y0": float(b.get("box", [0, 0, 0, 0])[1]),
                            "x1": float(b.get("box", [0, 0, 0, 0])[2]),
                            "y1": float(b.get("box", [0, 0, 0, 0])[3]),
                            "element_type": "heading",
                            "heading_level": max(1, min(lvl, 6)),
                            "display_order": int(b.get("reading_order", len(headings_list))),
                            "title": str(b.get("text", "")).strip(),
                        })
        except Exception as e:
            logger.warning("Error generating fallback TOC: %s", e)

    # Initialize tracker with headings to compute parent indices and breadcrumbs
    tracker = DocumentHeadingTracker(default_title=Path(normalized_file_path).stem)
    # Stack stores tuples of (level, index, title)
    stack: list[tuple[int, int, str]] = []
    enriched_headings = []

    for idx, h in enumerate(headings_list):
        lvl = h["heading_level"]
        title = h["title"] or f"Heading {idx + 1}"

        # Pop stack entries with level >= current level
        while stack and stack[-1][0] >= lvl:
            stack.pop()

        parent_idx = stack[-1][1] if stack else None
        breadcrumb = " > ".join([s[2] for s in stack] + [title]) if stack else title

        enriched_headings.append({
            **h,
            "index": idx,
            "parent_index": parent_idx,
            "breadcrumb": breadcrumb,
        })
        stack.append((lvl, idx, title))

    return {
        "status": "success",
        "file_path": normalized_file_path,
        "headings": enriched_headings,
        "count": len(enriched_headings),
    }


@router.post("/update-heading")
async def update_heading_level(req: UpdateHeadingRequest):
    """Updates the heading level of a specific annotated heading box."""
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE user_annotated_bboxes
            SET heading_level = ?,
                element_type = 'heading',
                updated_at = unixepoch('now')
            WHERE id = ?
            """,
            (req.heading_level, req.id),
        )
        conn.commit()

        if cursor.rowcount == 0:
            return {"status": "not_found", "id": req.id, "heading_level": req.heading_level}

    return {
        "status": "success",
        "id": req.id,
        "heading_level": req.heading_level,
    }


@router.post("/finalize")
async def finalize_analysis(req: FinalizeAnalysisRequest):
    """Compiles finalized document layout data, constructs a unified Markdown document
    with customized reading order and calibrated heading tiers, saves it, updates
    layout_analysis_progress to 'completed', and forwards to LanceDB for ingestion.
    """
    clean_path = req.file_path.strip()
    resolved_path = _resolve_pdf_path(clean_path)
    normalized_file_path = str(resolved_path.resolve()) if resolved_path else str(Path(clean_path).resolve())

    # 1. Fetch total pages
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT total_pages, final_markdown_override FROM layout_analysis_progress WHERE file_path = ?",
            (normalized_file_path,),
        )
        row = cursor.fetchone()
        total_pages = int(row["total_pages"]) if (row and row["total_pages"]) else 1
        existing_override = row["final_markdown_override"] if row else None

    # 2. Fetch all user_annotated_bboxes sorted by page_number and display_order
    with closing(get_db_connection()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, page_number, element_type, heading_level, display_order, raw_text
            FROM user_annotated_bboxes
            WHERE file_path = ?
            ORDER BY page_number ASC, display_order ASC, y0 ASC
            """,
            (normalized_file_path,),
        )
        all_boxes = cursor.fetchall()

    compiled_markdown = ""
    if existing_override and existing_override.strip() and not all_boxes:
        compiled_markdown = existing_override.strip()
    else:
        # Construct unified markdown from annotated boxes grouped by page
        pages_content = []
        current_page = None
        page_chunks: list[str] = []

        def flush_page(p_num: int, chunks: list[str]):
            if not chunks:
                return
            page_text = "\n\n".join(chunks).strip()
            pages_content.append(f"<!-- Page {p_num} -->\n\n{page_text}")

        for b in all_boxes:
            p_num = int(b["page_number"])
            if current_page is not None and p_num != current_page:
                flush_page(current_page, page_chunks)
                page_chunks = []
            current_page = p_num

            el_type = str(b["element_type"]).lower()
            text = str(b["raw_text"] or "").strip()
            if not text or el_type == "ignore":
                continue

            if el_type in ("header", "heading"):
                level = max(1, min(int(b["heading_level"] or 1), 6))
                hashes = "#" * level
                # Clean existing hashes from beginning if any
                clean_title = text.lstrip("#").strip()
                page_chunks.append(f"{hashes} {clean_title}")
            elif el_type == "table":
                page_chunks.append(f"```table\n{text}\n```" if not text.startswith("|") else text)
            else:
                page_chunks.append(text)

        if current_page is not None and page_chunks:
            flush_page(current_page, page_chunks)

        compiled_markdown = "\n\n".join(pages_content).strip()

    # If still empty, fall back to indra_parser text extraction
    if not compiled_markdown and resolved_path and resolved_path.is_file():
        try:
            full_struct = await asyncio.to_thread(indra_parser.extract_pdf_structure, str(resolved_path))
            compiled_markdown = "\n\n".join([
                f"# {s.get('heading', '')}\n\n{s.get('text', '')}" for s in full_struct if s.get("text")
            ]).strip()
        except Exception as e:
            logger.warning("Indra structure fallback failed: %s", e)

    if not compiled_markdown:
        compiled_markdown = f"# {Path(normalized_file_path).stem}\n\nNo content extracted."

    # 3. Save finalized Markdown file
    output_dir = Path("storage/finalized_markdown")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_name = req.output_filename or f"{Path(normalized_file_path).stem}.md"
    if not out_name.endswith(".md"):
        out_name += ".md"
    md_file_path = output_dir / out_name
    md_file_path.write_text(compiled_markdown, encoding="utf-8")

    # 4. Update layout_analysis_progress status to 'completed'
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            UPDATE layout_analysis_progress
            SET status = 'completed',
                final_markdown_override = ?,
                updated_at = unixepoch('now')
            WHERE file_path = ?
            """,
            (compiled_markdown, normalized_file_path),
        )
        conn.commit()

    # 5. LanceDB Ingestion
    ingested_count = 0
    if req.export_to_lancedb:
        try:
            ingested_count = await asyncio.to_thread(
                docling_parser.ingest_to_lancedb,
                normalized_file_path,
                source_article=Path(normalized_file_path).stem,
                main_topic=Path(normalized_file_path).stem,
            )
        except Exception as exc:
            logger.exception("LanceDB ingestion failed for %s: %s", normalized_file_path, exc)

    return {
        "status": "success",
        "file_path": normalized_file_path,
        "analysis_status": "completed",
        "markdown_file": str(md_file_path),
        "markdown_chars": len(compiled_markdown),
        "ingested_chunks": ingested_count,
    }
