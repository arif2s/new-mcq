# File: tests/unit/test_card_geometry_and_overflow.py
from __future__ import annotations

import concurrent.futures
import functools
import os
import re
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def with_timeout(seconds: float = 5.0):
    """Enforces a strict per-test timeout without requiring external plugins."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fn, *args, **kwargs)
                return future.result(timeout=seconds)
        return wrapper
    return decorator


def _extract_css_block(css_text: str, selector: str) -> str:
    """Extracts the declaration block for a given CSS selector."""
    pattern = re.compile(
        rf"(?:^|\n|\s){re.escape(selector)}\s*(?:,[^{{]*)?\{{([^}}]+)\}}",
        re.MULTILINE,
    )
    matches = pattern.findall(css_text)
    return "\n".join(matches)


@with_timeout(5.0)
def test_my_notes_card_overflow_prevention_css():
    """Issue #7: Verify .my-notes-grid, .note-card, .note-card-title, and .note-card-preview prevent overflow."""
    main_css = (ROOT_DIR / "ui" / "css" / "main.css").read_text(encoding="utf-8")

    grid_block = _extract_css_block(main_css, ".my-notes-grid")
    assert grid_block, "Missing .my-notes-grid rule in ui/css/main.css"
    assert "minmax(min(100%" in grid_block

    card_block = _extract_css_block(main_css, ".note-card")
    assert card_block, "Missing .note-card rule in ui/css/main.css"
    assert "min-width: 0" in card_block
    assert "max-width: 100%" in card_block
    assert "overflow: hidden" in card_block

    title_block = _extract_css_block(main_css, ".note-card-title")
    assert title_block, "Missing .note-card-title rule in ui/css/main.css"
    assert "overflow-wrap: anywhere" in title_block
    assert "-webkit-line-clamp" in title_block

    preview_block = _extract_css_block(main_css, ".note-card-preview")
    assert preview_block, "Missing .note-card-preview rule in ui/css/main.css"
    assert "overflow-wrap: anywhere" in preview_block
    assert "min-height: 0" in preview_block


@with_timeout(5.0)
def test_knowledge_hub_portrait_card_geometry_css():
    """Issue #8: Verify .kh-portrait-card enforces width < height (3/4 aspect ratio) and text clamping."""
    main_css = (ROOT_DIR / "ui" / "css" / "main.css").read_text(encoding="utf-8")

    kh_grid = _extract_css_block(main_css, ".knowledge-hub-grid")
    assert kh_grid, "Missing .knowledge-hub-grid rule in ui/css/main.css"
    assert "minmax(220px, 250px)" in kh_grid

    kh_card = _extract_css_block(main_css, ".kh-portrait-card")
    assert kh_card, "Missing .kh-portrait-card rule in ui/css/main.css"
    assert "aspect-ratio: 3 / 4" in kh_card or "aspect-ratio: 3/4" in kh_card

    # Verify mathematical portrait constraint: max-width (<= 250px) < min-height (>= 330px)
    max_w_match = re.search(r"max-width:\s*(\d+)px", kh_card)
    min_h_match = re.search(r"min-height:\s*(\d+)px", kh_card)
    assert max_w_match and min_h_match, "kh-portrait-card must define numeric max-width and min-height in px"
    max_w = int(max_w_match.group(1))
    min_h = int(min_h_match.group(1))
    assert max_w < min_h, f"Expected portrait card width ({max_w}px) < height ({min_h}px)"

    kh_title = _extract_css_block(main_css, ".kh-card-title")
    assert "-webkit-line-clamp" in kh_title
    assert "overflow-wrap: anywhere" in kh_title

    kh_body = _extract_css_block(main_css, ".kh-card-body")
    assert "mask-image: linear-gradient" in kh_body
    assert "overflow: hidden" in kh_body


@with_timeout(5.0)
def test_js_and_html_bind_card_geometry_classes():
    """Verify ui/js/notes.js, ui/js/hub.js, and ui/index.html apply the card geometry classes."""
    notes_js = (ROOT_DIR / "ui" / "js" / "notes.js").read_text(encoding="utf-8")
    hub_js = (ROOT_DIR / "ui" / "js" / "hub.js").read_text(encoding="utf-8")
    index_html = (ROOT_DIR / "ui" / "index.html").read_text(encoding="utf-8")

    notes_combined = notes_js + "\n" + index_html
    hub_combined = hub_js + "\n" + index_html

    for cls_name in ("my-notes-grid", "note-card", "note-card-title", "note-card-preview", "note-card-footer"):
        assert cls_name in notes_combined, f"Missing '{cls_name}' in ui/js/notes.js or ui/index.html"

    for cls_name in ("knowledge-hub-grid", "kh-portrait-card", "kh-card-header", "kh-card-title", "kh-card-body", "kh-card-footer"):
        assert cls_name in hub_combined, f"Missing '{cls_name}' in ui/js/hub.js or ui/index.html"
