from __future__ import annotations

import base64
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from parsers.indra.document import DocumentHeadingTracker
from parsers.indra.engine import IndraModularEngine
from parsers.indra.rules import RegexCatalog, enrich_lines
from parsers.indra.types import PageElement, PageElements, TableGrid

logger = logging.getLogger(__name__)


def build_layout_svg_overlay(
    blocks: list[dict[str, Any]],
    lines: list[dict[str, Any]],
    width: int,
    height: int,
) -> str:
    """Builds an SVG overlay representing structural layout blocks and recognized text lines."""
    width = max(100, int(width))
    height = max(100, int(height))

    label_palette: dict[str, tuple[str, str]] = {
        "Title": ("#ef4444", "rgba(239, 68, 68, 0.15)"),
        "Section-Header": ("#f97316", "rgba(249, 115, 22, 0.15)"),
        "Header": ("#f97316", "rgba(249, 115, 22, 0.15)"),
        "Table": ("#10b981", "rgba(16, 185, 129, 0.12)"),
        "Figure": ("#8b5cf6", "rgba(139, 92, 246, 0.12)"),
        "Picture": ("#8b5cf6", "rgba(139, 92, 246, 0.12)"),
        "List-Group": ("#8b5cf6", "rgba(139, 92, 246, 0.12)"),
        "Caption": ("#ec4899", "rgba(236, 72, 153, 0.12)"),
        "Footnote": ("#64748b", "rgba(100, 116, 139, 0.12)"),
        "Page-Header": ("#94a3b8", "rgba(148, 163, 184, 0.10)"),
        "Page-Footer": ("#94a3b8", "rgba(148, 163, 184, 0.10)"),
        "Text": ("#3b82f6", "rgba(59, 130, 246, 0.08)"),
    }

    svg_parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" height="100%" style="max-width:100%;height:auto;display:block;">'
    ]

    for blk in blocks:
        raw_box = blk.get("bbox") or blk.get("box") or [0, 0, 0, 0]
        if len(raw_box) != 4:
            continue
        x0, y0, x1, y1 = float(raw_box[0]), float(raw_box[1]), float(raw_box[2]), float(raw_box[3])
        if x0 <= 1.0 and x1 <= 1.0 and y0 <= 1.0 and y1 <= 1.0:
            x0, x1 = x0 * width, x1 * width
            y0, y1 = y0 * height, y1 * height
        bw, bh = max(1.0, x1 - x0), max(1.0, y1 - y0)
        lbl = str(blk.get("label", "Text"))
        stroke_color, fill_color = label_palette.get(lbl, ("#3b82f6", "rgba(59, 130, 246, 0.08)"))
        svg_parts.append(
            f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
            f'fill="{fill_color}" stroke="{stroke_color}" stroke-width="1.5" stroke-dasharray="3,3" />'
        )

    for ln in lines:
        raw_box = ln.get("box") or ln.get("bbox") or [0, 0, 0, 0]
        if len(raw_box) != 4:
            continue
        x0, y0, x1, y1 = float(raw_box[0]), float(raw_box[1]), float(raw_box[2]), float(raw_box[3])
        if x0 <= 1.0 and x1 <= 1.0 and y0 <= 1.0 and y1 <= 1.0:
            x0, x1 = x0 * width, x1 * width
            y0, y1 = y0 * height, y1 * height
        bw, bh = max(1.0, x1 - x0), max(1.0, y1 - y0)
        svg_parts.append(
            f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
            f'fill="rgba(245, 158, 11, 0.04)" stroke="#f59e0b" stroke-width="0.8" />'
        )

    svg_parts.append("</svg>")
    return "".join(svg_parts)


class IndraHybridLayoutRefiner:
    """Refines raw page IR into formatted Markdown, heading hierarchies, and layout structures."""

    def __init__(
        self,
        page_number: int = 1,
        dpi: int = 200,
        default_title: str = "Document",
        heading_tracker: Optional[DocumentHeadingTracker] = None,
        engine: Optional[IndraModularEngine] = None,
        pdf_path: Optional[Path] = None,
        **kwargs: Any,
    ) -> None:
        self.page_number = page_number
        self.dpi = dpi
        self.default_title = default_title
        self.heading_tracker = heading_tracker or DocumentHeadingTracker(default_title=default_title)
        self.engine = engine or IndraModularEngine(dpi=dpi)
        self.pdf_path = pdf_path

    def refine_page(self, ir: PageElements) -> dict[str, Any]:
        t0 = time.perf_counter()
        hierarchy_tree: list[dict[str, Any]] = []
        md_blocks: list[str] = []
        headings: list[str] = []
        root_title = self.default_title

        # Render page image to base64 if PDF path is available
        page_image_b64 = ""
        w_px, h_px = int(ir.width_pt * (self.dpi / 72.0)), int(ir.height_pt * (self.dpi / 72.0))
        if self.pdf_path and self.pdf_path.is_file():
            try:
                import pypdfium2 as pdfium

                doc = pdfium.PdfDocument(str(self.pdf_path))
                if 1 <= self.page_number <= len(doc):
                    page = doc[self.page_number - 1]
                    bitmap = page.render(scale=float(self.dpi) / 72.0)
                    pil_img = bitmap.to_pil()
                    bitmap.close()
                    page.close()
                    import io

                    buf = io.BytesIO()
                    pil_img.save(buf, format="PNG")
                    page_image_b64 = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
                    w_px, h_px = pil_img.size
                    pil_img.close()
                doc.close()
            except Exception as e:
                logger.debug("Failed to render page image to base64: %s", e)

        # Initialize cross-page heading stacks
        init_stack = self.heading_tracker.get_initial_stack_for_page(self.page_number)
        if init_stack:
            self.engine.active_titles.update(init_stack)
            self.heading_tracker.active_stack.update(init_stack)

        preassigned_page_toc = self.heading_tracker.get_page_toc_entries(self.page_number)
        if preassigned_page_toc:
            self.engine._align_and_interleave_toc_headings(ir, preassigned_page_toc)

        seen_heading_keys: set[tuple[int, str]] = set()

        cur_sec_blocks: list[str] = []
        sections_for_chunks: list[tuple[str, str, list[str]]] = []
        cur_sec_path = " > ".join([root_title] + [self.engine.active_titles[k] for k in sorted(self.engine.active_titles)]) if self.engine.active_titles else root_title
        cur_sec_sub = self.engine.active_titles[max(self.engine.active_titles)] if self.engine.active_titles else root_title

        for el in ir.elements:
            if el.is_suppressed_furniture or el.in_table or not el.text.strip():
                continue

            if el.role == "heading":
                lvl = max(1, min(6, int(el.heading_level or 2)))
                clean_h = el.text.strip()
                # Deduplicate identical consecutive headings on the same page
                h_key = (lvl, clean_h.lower())
                if h_key in seen_heading_keys:
                    continue
                seen_heading_keys.add(h_key)

                # Update hierarchical breadcrumb stack (root > H1 > H2 > H3 > H4)
                self.engine.active_titles[lvl] = clean_h
                self.heading_tracker.active_stack[lvl] = clean_h
                for deeper in [k for k in list(self.engine.active_titles) if k > lvl]:
                    self.engine.active_titles.pop(deeper, None)
                    self.heading_tracker.active_stack.pop(deeper, None)

                h_path = el.heading_path or " > ".join(
                    [root_title] + [self.engine.active_titles[k] for k in sorted(self.engine.active_titles)]
                )
                el.heading_path = h_path
                headings.append(clean_h)
                node = {
                    "page": self.page_number,
                    "level": lvl,
                    "tag": f"H{lvl}",
                    "title": clean_h,
                    "heading_path": h_path,
                    "order": len(hierarchy_tree) + 1,
                    "confidence": el.heading_confidence,
                    "factor_scores": el.factor_scores,
                }
                hierarchy_tree.append(node)
                if cur_sec_blocks:
                    sections_for_chunks.append((cur_sec_path, cur_sec_sub, list(cur_sec_blocks)))
                    cur_sec_blocks.clear()
                cur_sec_path = h_path
                cur_sec_sub = clean_h
                md_line = f"{'#' * lvl} {clean_h}"
                md_blocks.append(md_line)
                cur_sec_blocks.append(md_line)

            elif el.role == "list_item":
                raw_li = el.text.strip()
                stripped_li = re.sub(r"^[•*\-–—\u2022\s]+", "", raw_li)
                if m_desc := RegexCatalog.DESCRIPTION_SEPARATOR.match(stripped_li):
                    md_line = f"- **{m_desc.group('term').strip()}:** {m_desc.group('desc').strip()}"
                else:
                    md_line = f"- {stripped_li}"
                md_blocks.append(md_line)
                cur_sec_blocks.append(md_line)

            elif el.role == "figure_caption":
                cap_line = f"> *{el.text.strip()}*"
                md_blocks.append(cap_line)
                cur_sec_blocks.append(cap_line)

            else:
                # Paragraph (strictly mutually exclusive from heading, list_item, figure_caption)
                p_txt = el.text.strip()
                md_blocks.append(p_txt)
                cur_sec_blocks.append(p_txt)

        # Emit tables if present
        for t in ir.tables:
            if not t.rows:
                continue
            t_lines = []
            headers = t.headers if t.headers else t.rows[0]
            start_row_idx = 0 if t.headers else 1
            t_lines.append("| " + " | ".join(headers) + " |")
            t_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for r in t.rows[start_row_idx:]:
                padded = r + [""] * (len(headers) - len(r))
                t_lines.append("| " + " | ".join(padded[:len(headers)]) + " |")
            table_md = "\n".join(t_lines)
            md_blocks.append(table_md)
            cur_sec_blocks.append(table_md)

        if cur_sec_blocks:
            sections_for_chunks.append((cur_sec_path, cur_sec_sub, list(cur_sec_blocks)))

        md_out = "\n\n".join(md_blocks)
        elapsed = round((time.perf_counter() - t0) * 1000.0, 2)

        out_lines = [
            {
                "text": e.text,
                "box": [
                    e.bbox_norm[0] * w_px,
                    e.bbox_norm[1] * h_px,
                    e.bbox_norm[2] * w_px,
                    e.bbox_norm[3] * h_px,
                ],
                "bbox_norm": e.bbox_norm,
                "role": e.role,
                "heading_level": e.heading_level if e.role == "heading" else 0,
            }
            for e in ir.elements
            if not e.is_suppressed_furniture
        ]

        out_blocks = [
            {
                "label": "Section-Header" if e.role == "heading" else ("List-Group" if e.role == "list_item" else "Text"),
                "bbox": [
                    e.bbox_norm[0] * w_px,
                    e.bbox_norm[1] * h_px,
                    e.bbox_norm[2] * w_px,
                    e.bbox_norm[3] * h_px,
                ],
            }
            for e in ir.elements
            if not e.is_suppressed_furniture
        ]

        svg_overlay = build_layout_svg_overlay(out_blocks, out_lines, w_px, h_px)

        return {
            "status": "success",
            "engine": "indra",
            "mode": self.engine.mode,
            "math": self.engine.math,
            "page": self.page_number,
            "page_number": self.page_number,
            "profile_id": ir.profile_id,
            "strategy_key": "NativeDigitalStrategy",
            "markdown": md_out,
            "rehydrated_html": md_out,
            "text": md_out,
            "extracted_text": md_out,
            "main_heading": headings[0] if headings else root_title,
            "heading_path": cur_sec_path,
            "subheading": headings[0] if headings else root_title,
            "headings": headings,
            "hierarchy_tree": hierarchy_tree,
            "active_heading_stack": dict(self.engine.active_titles),
            "initial_heading_stack": dict(init_stack),
            "document_toc": hierarchy_tree,
            "page_toc": hierarchy_tree,
            "heading_mismatches": [],
            "document_mismatches": [],
            "blocks": out_blocks,
            "layout_boxes": out_blocks,
            "filtered_furniture": [],
            "media_placeholders": [],
            "second_opinion_repairs": [],
            "lines": out_lines,
            "pictures": [],
            "tables": ir.tables,
            "source": ir.source,
            "rotation": ir.rotation,
            "skew": ir.skew,
            "vertical": ir.vertical,
            "timings": {"total_ms": elapsed},
            "elapsed_ms": elapsed,
            "svg_overlay": svg_overlay,
            "page_image_b64": page_image_b64,
            "page_image_base64": page_image_b64,
            "page_width": ir.width_pt,
            "page_height": ir.height_pt,
            "sections_for_chunks": sections_for_chunks or ([(root_title, root_title, [md_out])] if md_out else []),
            "reconciled_toc_mismatches": [],
            "heading_audit": {"page_mismatches": [], "document_mismatches": []},
        }
