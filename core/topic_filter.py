from functools import lru_cache
from pathlib import Path
import re
from typing import Any

IGNORE_FILE_PATH = Path(__file__).resolve().parent.parent / "Ignore_list.txt"
_NON_ALNUM_RE = re.compile(r"^[\W\d_]+$")
_TOKEN_SPLIT_RE = re.compile(r"[\s_\-/]+")


@lru_cache(maxsize=1)
def load_ignore_set() -> frozenset[str]:
    if not IGNORE_FILE_PATH.exists():
        return frozenset()
    terms: set[str] = set()
    for line in IGNORE_FILE_PATH.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip().lower()
        if cleaned and not cleaned.startswith("#"):
            terms.add(cleaned)
    return frozenset(terms)


def is_valid_topic(topic: str | None) -> bool:
    """Return True only if `topic` is a meaningful domain topic and not in Ignore_list.txt."""
    if not topic or not isinstance(topic, str):
        return False
    normalized = topic.strip().lower()
    if len(normalized) < 3:
        return False
    if _NON_ALNUM_RE.match(normalized):
        return False
    ignore_set = load_ignore_set()
    if normalized in ignore_set:
        return False
    # Reject if every constituent word in a multi-word phrase is an ignored stopword
    tokens = [t for t in _TOKEN_SPLIT_RE.split(normalized) if t]
    if not tokens or all(t in ignore_set for t in tokens):
        return False
    return True


def filter_topics(topics: list[str]) -> list[str]:
    """Filter and deduplicate a list of topic strings while preserving order."""
    seen: set[str] = set()
    valid: list[str] = []
    for t in topics:
        if not is_valid_topic(t):
            continue
        key = t.strip().lower()
        if key not in seen:
            seen.add(key)
            valid.append(t.strip())
    return valid


def canonicalize_topic_norm(text: str) -> str:
    """Produces a case-folded, singularized canonical key for strict deduplication."""
    if not text:
        return ""
    cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", str(text).strip().lower())
    cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned)
    cleaned = re.sub(r"[\s_\-]+", " ", cleaned).strip()
    tokens = []
    for tok in cleaned.split():
        if len(tok) > 4 and tok.endswith("oses"):
            tok = tok[:-4] + "osis"
        elif len(tok) > 4 and tok.endswith("ies") and not tok.endswith("series"):
            tok = tok[:-3] + "y"
        elif len(tok) > 4 and tok.endswith("es") and not tok.endswith(("ses", "xes", "zes", "ches", "shes")):
            tok = tok[:-1]
        elif len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is", "os")):
            tok = tok[:-1]
        tokens.append(tok)
    return " ".join(tokens)


def deduplicate_note_topics(
    candidates: list[Any],
    ignored_norms: set[str] | frozenset[str],
    suppressed_norms: set[str] | frozenset[str],
) -> list[dict[str, Any]]:
    """
    Deduplicates extracted topics by canonical norm and CUI while enforcing
    Ignore_list.txt, SQLite ignored_topics, per-note suppressed_topics_json,
    CLINICAL_STOPWORDS, and ISOLATED_GENERIC_WORDS.
    """
    from agents.tema_q.topic_extractor import CLINICAL_STOPWORDS, ISOLATED_GENERIC_WORDS

    file_ignore = load_ignore_set()
    all_excluded = {canonicalize_topic_norm(x) for x in (set(ignored_norms) | set(suppressed_norms) | set(file_ignore)) if x}
    for w in CLINICAL_STOPWORDS:
        all_excluded.add(canonicalize_topic_norm(w))
    for w in ISOLATED_GENERIC_WORDS:
        all_excluded.add(canonicalize_topic_norm(w))

    seen_norms: dict[str, dict[str, Any]] = {}
    seen_cuis: dict[str, str] = {}

    for item in candidates:
        if isinstance(item, dict):
            raw_name = str(item.get("name") or item.get("topic_name") or item.get("topic_text") or "").strip()
            cui = str(item.get("cui") or "").strip()
        else:
            raw_name = str(item or "").strip()
            cui = ""

        if not raw_name or not is_valid_topic(raw_name):
            continue

        norm = canonicalize_topic_norm(raw_name)
        if not norm or norm in all_excluded or raw_name.lower() in file_ignore:
            continue

        target_norm = seen_cuis.get(cui, norm) if cui else norm
        if target_norm in all_excluded:
            continue

        if cui and cui not in seen_cuis:
            seen_cuis[cui] = target_norm

        topic_key = re.sub(r"[\s_]+", "-", target_norm)
        if target_norm not in seen_norms:
            seen_norms[target_norm] = {
                "topic_text": raw_name,
                "topic_norm": target_norm,
                "topic_key": topic_key,
                "cui": cui,
                "surface_variants": [raw_name],
            }
        else:
            variants = seen_norms[target_norm]["surface_variants"]
            if raw_name not in variants:
                variants.append(raw_name)

    return list(seen_norms.values())


# Alias for Requirement C1
normalize_and_deduplicate_note_topics = deduplicate_note_topics
