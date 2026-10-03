import re
import logging

logger = logging.getLogger(__name__)

# 1. Global caching for heavy NLP model to prevent memory leaks and blocking I/O
_medspacy_nlp = None
_medspacy_available = True

# 2. Pre-compile Regex once at startup for C-level speedup
_MEDICAL_REGEX = re.compile(
    r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b|\b[A-Za-z]+(?:itis|oma|osis|pathy|emia|plasia|algia)\b'
)

# 3. Global immutable frozenset for O(1) stop-word lookups
_STOP_WORDS = frozenset({
    "A", "An", "The", "In", "On", "At", "To", "Is", "Are", "Was", "Were",
    "It", "This", "That", "Which", "What", "When", "Where", "Why", "How",
    "None", "All", "Of", "These", "And", "Or", "But", "If"
})

def get_medspacy_model():
    """Singleton loader for medSpacy to ensure it only initializes once."""
    global _medspacy_nlp, _medspacy_available
    if not _medspacy_available:
        return None

    if _medspacy_nlp is None:
        try:
            import medspacy
            _medspacy_nlp = medspacy.load()
        except ImportError:
            logger.warning("medspacy not installed, falling back to regex")
            _medspacy_available = False

    return _medspacy_nlp

def extract_topics_local(question: str, options: dict, strategy: str = 'regex') -> dict:
    if strategy == 'medspacy':
        nlp = get_medspacy_model()
        if nlp:
            combined_text = f"{question} " + " ".join(options.values())
            doc = nlp(combined_text)

            # Extract un-negated entities
            topics = [ent.text for ent in doc.ents if not ent._.is_negated]

            if not topics:
                return {"main_topic": "General Medical Concept", "all_topics": ["General Medical Concept"]}

            # Fast O(N) deduplication preserving insertion order (Python 3.7+)
            unique_topics = list(dict.fromkeys(topics))
            return {"main_topic": unique_topics[0], "all_topics": unique_topics}
        else:
            strategy = 'regex'

    if strategy == 'regex':
        # Single concatenation avoids multiple regex engine calls
        combined_text = f"{question} " + " ".join(options.values())
        raw_matches = _MEDICAL_REGEX.findall(combined_text)

        seen = set()
        unique_topics = []

        # Single-pass filter and deduplicate
        for match in raw_matches:
            m = match.strip()
            if m and m not in _STOP_WORDS and m not in seen:
                seen.add(m)
                unique_topics.append(m)

        if not unique_topics:
             return {"main_topic": "General Medical Concept", "all_topics": ["General Medical Concept"]}

        return {"main_topic": unique_topics[0], "all_topics": unique_topics}

    return {"main_topic": "General Medical Concept", "all_topics": []}
