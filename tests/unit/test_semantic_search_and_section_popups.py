# File: tests/unit/test_semantic_search_and_section_popups.py
from __future__ import annotations

import concurrent.futures
import functools
import os
from pathlib import Path
from unittest.mock import patch

os.environ["CUDA_VISIBLE_DEVICES"] = ""

import pytest
from fastapi.testclient import TestClient

from server.routes_search import (
    decompose_semantic_clinical_query,
    render_pdf_section_via_indra_ocr,
    slice_section_by_heading,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


def with_timeout(seconds: float = 5.0):
    """Enforces a hard per-test execution timeout with zero external plugins."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(fn, *args, **kwargs)
                return future.result(timeout=seconds)
        return wrapper
    return decorator


@with_timeout(5.0)
def test_decompose_semantic_clinical_query_compound_and_simple():
    """Issues #14, #15, #16: Verify 'treatment of amyloidosis' decomposes into entity + treatment aspect."""
    q1 = decompose_semantic_clinical_query("treatment of amyloidosis")
    assert q1["core_entity"] == "amyloidosis"
    assert q1["aspect_key"] == "treatment"
    assert "therapy" in q1["aspect_synonyms"]
    assert "management" in q1["aspect_synonyms"]

    q2 = decompose_semantic_clinical_query("amyloidosis")
    assert q2["core_entity"] == "amyloidosis"
    assert q2["aspect_key"] == ""

    q3 = decompose_semantic_clinical_query("diagnosis of cardiac amyloidosis")
    assert "cardiac amyloidosis" in q3["core_entity"]
    assert q3["aspect_key"] == "diagnosis"
    assert "biopsy" in q3["aspect_synonyms"]


@with_timeout(5.0)
def test_slice_section_by_heading_isolates_only_matching_section():
    """Issue #16: Verify only the matching heading section is returned for popup display."""
    sample_article = (
        "# Amyloidosis\n"
        "Amyloidosis is a disorder of protein misfolding leading to extracellular fibril deposition.\n\n"
        "## Pathogenesis\n"
        "AL fibrils derive from immunoglobulin light chains produced by plasma cell clones.\n\n"
        "## Diagnosis and Biopsy\n"
        "Abdominal fat pad aspiration stained with Congo red shows apple-green birefringence.\n\n"
        "## Treatment and Management\n"
        "First-line therapy for systemic AL amyloidosis is Dara-CyBorD (daratumumab, "
        "cyclophosphamide, bortezomib, and dexamethasone). Autologous stem cell transplant "
        "is considered in eligible patients.\n\n"
        "## Prognosis\n"
        "Median survival depends on cardiac staging (NT-proBNP and troponin levels)."
    )

    intent = decompose_semantic_clinical_query("treatment of amyloidosis")
    heading, isolated = slice_section_by_heading(
        sample_article,
        core_entity=intent["core_entity"],
        aspect_synonyms=intent["aspect_synonyms"],
    )
    assert "treatment" in heading.lower() or "management" in heading.lower()
    assert "Dara-CyBorD" in isolated
    assert "Abdominal fat pad aspiration" not in isolated
    assert "Median survival depends" not in isolated


@with_timeout(5.0)
def test_indra_ocr_section_popup_rendering_mocked():
    """Issue #16: Verify PDF section popup uses Indra base OCR + heading calibration and slices the target heading."""
    fake_indra_page = {
        "markdown": (
            "# Systemic Amyloidosis\n"
            "General overview of systemic amyloidosis.\n\n"
            "## Treatment\n"
            "Tafamidis 61 mg daily stabilizes transthyretin tetramers in ATTR cardiomyopathy.\n\n"
            "## Epidemiology\n"
            "More common in elderly males."
        )
    }
    intent = decompose_semantic_clinical_query("treatment of amyloidosis")
    with patch(
        "parsers.indra.extract_page_with_indra_base_heading_opt",
        return_value=fake_indra_page,
    ):
        res = render_pdf_section_via_indra_ocr(
            file_path="dummy_harrison.pdf",
            page_number=105,
            core_entity=intent["core_entity"],
            aspect_synonyms=intent["aspect_synonyms"],
        )
        assert "treatment" in res["heading"].lower()
        assert "Tafamidis 61 mg" in res["rendered_text"]
        assert "More common in elderly males" not in res["rendered_text"]
        assert "indra" in res["engine"].lower()


@with_timeout(5.0)
def test_semantic_hybrid_endpoint_returns_files_and_section_popups():
    """Verify /api/search/semantic-hybrid returns matching_files and section_popup_links for 'treatment of amyloidosis'."""
    from server.app import app

    fake_search_index_hits = {
        "zim": [
            {
                "title": "Amyloidosis",
                "file_path": "data/wiki.zim",
                "source": "zim",
                "text": (
                    "# Amyloidosis\nOverview.\n\n"
                    "## Treatment\nBortezomib and daratumumab are used for AL amyloidosis."
                ),
            }
        ],
        "obsidian": [
            {
                "title": "Amyloidosis Notes",
                "file_path": "vault/Pathology/Amyloidosis.md",
                "source": "obsidian",
                "text": (
                    "# Amyloidosis\nPathology notes.\n\n"
                    "## Management\nTreat underlying plasma cell dyscrasia with Dara-CyBorD."
                ),
            }
        ],
        "pdf": [
            {
                "title": "Robbins Pathology.pdf",
                "file_path": "pdfs/Robbins.pdf",
                "page": 212,
                "source": "pdf",
                "text": "## Treatment of Amyloidosis\nDaratumumab regimen.",
            }
        ],
    }

    client = TestClient(app)
    with patch("server.routes_search.search_index", return_value=fake_search_index_hits):
        resp = client.get("/api/search/semantic-hybrid", params={"q": "treatment of amyloidosis", "limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query_decomposition"]["core_entity"] == "amyloidosis"
        assert data["query_decomposition"]["aspect_key"] == "treatment"
        assert len(data["matching_files"]) >= 3
        assert len(data["section_popup_links"]) >= 3
        first_popup = data["section_popup_links"][0]
        assert "/api/search/section-popup" in first_popup["popup_url"]


@with_timeout(5.0)
def test_test_features_js_wires_semantic_hybrid_and_popup_modal():
    """Static verification that ui/js/test_features.js and ui/index.html wire the popup links."""
    tf_js = (ROOT_DIR / "ui" / "js" / "test_features.js").read_text(encoding="utf-8")
    index_html = (ROOT_DIR / "ui" / "index.html").read_text(encoding="utf-8")
    combined = tf_js + "\n" + index_html

    assert "/api/search/semantic-hybrid" in combined
    assert "/api/search/section-popup" in combined or "popup_url" in combined
    assert "open-section-popup-link" in combined
