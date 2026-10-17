import pytest
from agents.topic_extractor import get_topic_aliases, generate_candidate_variants
from parsers.scispacy_linker_engine import normalize_alias, get_concept_aliases
from parsers.zim_reader import resolve_zim_topic, search_zim_articles
from parsers.tantivy_engine import search_index
from server.routes_search import perform_search, SearchRequest


def test_candidate_variants_comma_inversion():
    variants = generate_candidate_variants("transplantation, renal")
    assert "transplantation, renal" in variants
    assert "renal transplantation" in variants
    assert "kidney transplantation" in variants or "renal transplant" in variants


def test_candidate_variants_organ_transposition():
    variants_renal = generate_candidate_variants("renal transplantation")
    assert "kidney transplantation" in variants_renal or "renal transplant" in variants_renal

    variants_cardiac = generate_candidate_variants("cardiac amyloidosis")
    assert "heart amyloidosis" in variants_cardiac or "cardiac amyloidosis" in variants_cardiac


def test_normalize_alias_comma_inversion():
    assert normalize_alias("transplantation, renal") == "renal transplantation"
    assert normalize_alias("failure, acute renal") == "acute renal failure"
    assert normalize_alias("renal transplantation") == "renal transplantation"


def test_get_topic_aliases():
    aliases = get_topic_aliases("transplantation, renal")
    assert len(aliases) >= 2
    assert "transplantation, renal" in aliases
    assert "renal transplantation" in aliases


def test_search_index_with_comma_inverted_topic():
    res = search_index("transplantation, renal", limit_per_source=3)
    assert isinstance(res, dict)
    assert "obsidian" in res
    assert "pdf" in res
    assert "zim" in res


def test_perform_search_with_comma_inverted_topic():
    req = SearchRequest(query="transplantation, renal", limit=5)
    res = perform_search(req)
    assert res["status"] == "success"
    assert "all_results" in res
    assert "candidates" in res
