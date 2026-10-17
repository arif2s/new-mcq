"""
agents/topic_extractor.py

Compatibility re-exporter for agents.tema_q.topic_extractor.
"""

from agents.tema_q.topic_extractor import (
    extract_topics,
    extract_topics_local,
    filter_sibling_topics,
    get_topic_aliases,
    generate_candidate_variants,
)

__all__ = [
    "extract_topics",
    "extract_topics_local",
    "filter_sibling_topics",
    "get_topic_aliases",
    "generate_candidate_variants",
]
