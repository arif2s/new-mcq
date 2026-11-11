# File: core/topic_filter.py
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional, Set

IGNORE_FILE_PATH = Path(__file__).resolve().parent.parent / "Ignore_list.txt"
_NON_ALNUM_RE = re.compile(r"^[\W\d_]+$")
_TOKEN_SPLIT_RE = re.compile(r"[\s_\-/]+")
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_WRAP_PUNCT_RE = re.compile(r"^[^\w]+|[^\w]+$")

_CACHED_IGNORE_MTIME: float = -1.0
_CACHED_IGNORE_SET: frozenset[str] = frozenset()


def load_ignore_set() -> frozenset[str]:
    """Loads Ignore_list.txt with automatic mtime invalidation."""
    global _CACHED_IGNORE_MTIME, _CACHED_IGNORE_SET
    if not IGNORE_FILE_PATH.exists():
        return frozenset()
    try:
        mtime = IGNORE_FILE_PATH.stat().st_mtime
        if mtime == _CACHED_IGNORE_MTIME and _CACHED_IGNORE_SET:
            return _CACHED_IGNORE_SET
        terms: set[str] = set()
        for line in IGNORE_FILE_PATH.read_text(encoding="utf-8").splitlines():
            cleaned = line.strip().lower()
            if cleaned and not cleaned.startswith("#"):
                terms.add(cleaned)
                terms.add(canonicalize_topic_norm(cleaned))
        _CACHED_IGNORE_MTIME = mtime
        _CACHED_IGNORE_SET = frozenset(t for t in terms if t)
        return _CACHED_IGNORE_SET
    except Exception:
        return _CACHED_IGNORE_SET


def canonicalize_topic_norm(text: str | None) -> str:
    """
    Normalizes a clinical topic into a canonical, case-folded, singularized key.
    Collapses 'Amyloidosis', 'amyloidosis', 'Amyloidoses', 'the amyloidosis' -> 'amyloidosis'.
    """
    if not text or not isinstance(text, str):
        return ""
    cleaned = _WRAP_PUNCT_RE.sub("", text.strip().lower())
    cleaned = _LEADING_ARTICLE_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"[\s_\-]+", " ", cleaned).strip()
    if not cleaned:
        return ""

    tokens: list[str] = []
    for tok in cleaned.split():
        if len(tok) > 4 and tok.endswith("oses"):
            tok = tok[:-4] + "osis"
        elif len(tok) > 4 and tok.endswith("ies") and not tok.endswith("series"):
            tok = tok[:-3] + "y"
        elif len(tok) > 4 and tok.endswith("es") and not tok.endswith(("ses", "xes", "zes", "ches", "shes")):
            tok = tok[:-1]
        elif len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is", "os", "as")):
            tok = tok[:-1]
        tokens.append(tok)
    return " ".join(tokens)


def is_valid_topic(topic: str | None, extra_ignored: Optional[Set[str] | frozenset[str]] = None) -> bool:
    """Return True only if `topic` is a valid clinical topic and not in Ignore_list.txt or extra_ignored."""
    if not topic or not isinstance(topic, str):
        return False
    normalized = topic.strip().lower()
    if len(normalized) < 3 or _NON_ALNUM_RE.match(normalized):
        return False

    norm_stem = canonicalize_topic_norm(normalized)
    if not norm_stem or len(norm_stem) < 3:
        return False

    ignore_set = load_ignore_set()
    if normalized in ignore_set or norm_stem in ignore_set:
        return False
    if extra_ignored and (normalized in extra_ignored or norm_stem in extra_ignored):
        return False

    tokens = [t for t in _TOKEN_SPLIT_RE.split(norm_stem) if t]
    if not tokens or all((t in ignore_set or (extra_ignored and t in extra_ignored)) for t in tokens):
        return False
    return True


def filter_topics(topics: Iterable[str], extra_ignored: Optional[Set[str] | frozenset[str]] = None) -> list[str]:
    """Filter and deduplicate a list of topic strings by canonical stem while preserving order."""
    seen_norms: set[str] = set()
    valid: list[str] = []
    for t in topics or []:
        if not isinstance(t, str) or not is_valid_topic(t, extra_ignored=extra_ignored):
            continue
        norm = canonicalize_topic_norm(t)
        if norm and norm not in seen_norms:
            seen_norms.add(norm)
            valid.append(t.strip())
    return valid


def generate_surface_regex_variants(topic_text: str, topic_norm: str, variants: Iterable[str] = ()) -> list[str]:
    """Generates surface forms (including plural/singular variants) for whole-word text highlighting."""
    forms: set[str] = set()
    for candidate in [topic_text, topic_norm, *(variants or [])]:
        c = str(candidate or "").strip()
        if len(c) >= 3:
            forms.add(c)
            low = c.lower()
            if low.endswith("osis"):
                forms.add(c[:-4] + "oses")
            elif low.endswith("y") and len(c) > 4:
                forms.add(c[:-1] + "ies")
            elif not low.endswith("s"):
                forms.add(c + "s")
    return sorted(forms, key=len, reverse=True)


def deduplicate_note_topics(
    candidates: Iterable[Any],
    ignored_norms: Optional[Set[str] | frozenset[str]] = None,
    suppressed_norms: Optional[Set[str] | frozenset[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Deduplicates extracted note topics by canonical morphological stem and UMLS CUI,
    filtering against Ignore_list.txt, SQLite ignored_topics, and note suppressed_topics_json.
    """
    file_ignore = load_ignore_set()
    combined_excluded: set[str] = set(file_ignore)

    try:
        from agents.tema_q.topic_extractor import CLINICAL_STOPWORDS, ISOLATED_GENERIC_WORDS
        for w in CLINICAL_STOPWORDS:
            combined_excluded.add(w.lower())
            combined_excluded.add(canonicalize_topic_norm(w))
        for w in ISOLATED_GENERIC_WORDS:
            combined_excluded.add(w.lower())
            combined_excluded.add(canonicalize_topic_norm(w))
    except Exception:
        pass

    for src_set in (ignored_norms, suppressed_norms):
        if src_set:
            for item in src_set:
                s = str(item or "").strip().lower()
                if s:
                    combined_excluded.add(s)
                    combined_excluded.add(canonicalize_topic_norm(s))

    by_norm: Dict[str, Dict[str, Any]] = {}
    cui_to_norm: Dict[str, str] = {}

    for cand in candidates or []:
        if isinstance(cand, dict):
            raw_name = str(
                cand.get("topic_text") or cand.get("name") or cand.get("topic_name") or ""
            ).strip()
            cui = str(cand.get("cui") or "").strip()
            existing_variants = cand.get("surface_variants") or []
        else:
            raw_name = str(cand or "").strip()
            cui = ""
            existing_variants = []

        if not raw_name or not is_valid_topic(raw_name, extra_ignored=combined_excluded):
            continue

        norm = canonicalize_topic_norm(raw_name)
        if not norm or norm in combined_excluded:
            continue

        primary_norm = cui_to_norm.get(cui, norm) if cui else norm
        if primary_norm in combined_excluded:
            continue

        if cui and cui not in cui_to_norm:
            cui_to_norm[cui] = primary_norm

        topic_key = re.sub(r"[\s_]+", "-", primary_norm)
        if primary_norm not in by_norm:
            base_vars = [raw_name]
            for ev in existing_variants:
                if ev and ev not in base_vars:
                    base_vars.append(ev)
            by_norm[primary_norm] = {
                "topic_text": raw_name,
                "topic_norm": primary_norm,
                "topic_key": topic_key,
                "cui": cui,
                "surface_variants": base_vars,
            }
        else:
            entry = by_norm[primary_norm]
            if raw_name and raw_name not in entry["surface_variants"]:
                entry["surface_variants"].append(raw_name)
            for ev in existing_variants:
                if ev and ev not in entry["surface_variants"]:
                    entry["surface_variants"].append(ev)

    return list(by_norm.values())


# Alias for Requirement C1
normalize_and_deduplicate_note_topics = deduplicate_note_topics
