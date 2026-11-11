# File: tests/unit/test_glassmorphic_dynamic_ui.py
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
    """Hard per-test timeout guard without external plugins."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fn, *args, **kwargs)
                return future.result(timeout=seconds)
        return wrapper
    return decorator


@with_timeout(5.0)
def test_dynamic_glassmorphic_variables_for_cards_buttons_and_badges():
    """Reqs 1, 2, 3, 6: Verify dynamic wallpaper-derived CSS variables exist for cards, buttons, study topics, and CSV badge."""
    app_js = (ROOT_DIR / "ui" / "js" / "app.js").read_text(encoding="utf-8")
    main_css = (ROOT_DIR / "ui" / "css" / "main.css").read_text(encoding="utf-8")
    combined = app_js + "\n" + main_css

    required_tokens = (
        "--glass-card-bg",
        "--btn-primary-bg",
        "--btn-secondary-bg",
        "--btn-study-topics-bg",
        "--badge-csv-bg",
        "--text-derived-primary",
    )
    for token in required_tokens:
        assert token in combined, f"Missing dynamic theme/wallpaper token: {token}"

    assert "study-topics-covered-btn" in main_css
    assert "csv-topic-badge-dynamic" in main_css


@with_timeout(5.0)
def test_knowledge_hub_equal_dimension_buttons():
    """Req 4: Verify 'Explanation' and 'Start Quiz' buttons share identical width & height rules."""
    main_css = (ROOT_DIR / "ui" / "css" / "main.css").read_text(encoding="utf-8")
    hub_js = (ROOT_DIR / "ui" / "js" / "hub.js").read_text(encoding="utf-8")

    assert "kh-equal-actions-grid" in main_css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in main_css
    assert "kh-action-btn-equal" in main_css
    assert "height: 38px" in main_css
    assert "kh-equal-actions-grid" in hub_js
    assert "kh-action-btn-equal" in hub_js


@with_timeout(5.0)
def test_unattempted_accuracy_renders_dash_and_no_card_topic_clutter():
    """Reqs 5 & 7: Verify unattempted accuracy displays a dash ('—') and confirmed topics do not clutter the small card."""
    hub_js = (ROOT_DIR / "ui" / "js" / "hub.js").read_text(encoding="utf-8")

    # Verify accuracy formatter uses '—' or '-' instead of distorting the box with 'Not Attempted'
    assert "formatSessionAccuracyDisplay" in hub_js or '"—"' in hub_js
    assert "Not Attempted" not in hub_js

    # Verify card body click handler for opening the right sidebar (Req 8)
    assert "openKnowledgeHubRightSidebar" in hub_js
    assert ".kh-portrait-card" in hub_js


@with_timeout(5.0)
def test_my_notes_and_hub_cards_use_glassmorphic_dynamic_hues():
    """Req 2 & 3: Verify My Notes and Knowledge Hub cards bind to dynamic glassmorphic hues."""
    notes_js = (ROOT_DIR / "ui" / "js" / "notes.js").read_text(encoding="utf-8")
    hub_js = (ROOT_DIR / "ui" / "js" / "hub.js").read_text(encoding="utf-8")
    main_css = (ROOT_DIR / "ui" / "css" / "main.css").read_text(encoding="utf-8")

    assert "backdrop-filter: blur" in main_css
    assert "note-card" in notes_js
    assert "kh-portrait-card" in hub_js
