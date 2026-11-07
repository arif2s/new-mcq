from functools import lru_cache
from pathlib import Path
import re

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
