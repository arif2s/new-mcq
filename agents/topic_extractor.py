"""Compatibility re-exporter for topic extraction functions."""

import os

os.environ["CUDA_VISIBLE_DEVICES"] = ""

from agents.tema_q.topic_extractor import (
    add_ignored_topic,
    canonicalize_clinical_phrase,
    extract_all_clinical_modifiers,
    extract_clinical_modifiers,
    extract_topics,
    extract_topics_local,
    filter_sibling_topics,
    generate_candidate_variants,
    get_ignored_topics_set,
    get_medspacy_model,
    get_topic_aliases,
    normalize_topic_key,
    remove_ignored_topic,
    resolve_zim_topic,
)

__all__ = [
    "extract_topics",
    "extract_topics_local",
    "filter_sibling_topics",
    "get_topic_aliases",
    "generate_candidate_variants",
    "canonicalize_clinical_phrase",
    "extract_clinical_modifiers",
    "extract_all_clinical_modifiers",
    "get_ignored_topics_set",
    "add_ignored_topic",
    "remove_ignored_topic",
    "normalize_topic_key",
    "resolve_zim_topic",
    "get_medspacy_model",
]
