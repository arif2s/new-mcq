import re
import logging
from core.database import get_db_connection

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

def check_zim_refinement(topic: str) -> bool:
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # First check if the zim_titles table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='zim_titles'")
            if cursor.fetchone():
                cursor.execute("SELECT 1 FROM zim_titles WHERE title LIKE ? COLLATE NOCASE LIMIT 1", (f"%{topic}%",))
                if cursor.fetchone():
                    return True
            else:
                logger.warning("zim_titles table not found")

                # Check directly in ZIM files if zim_titles doesn't exist
                cursor.execute("SELECT path FROM knowledge_sources WHERE type = 'zim' AND enabled = 1")
                for row in cursor.fetchall():
                    zim_path = row['path']
                    try:
                        import libzim
                        archive = libzim.Archive(zim_path)
                        # We would need to search but libzim doesn't support easy title search
                        # So we assume the table needs to exist, let's just return False for now if table doesn't exist
                    except Exception as e:
                        logger.warning(f"Failed to check zim file directly: {e}")
    except Exception as e:
        logger.warning(f"ZIM refinement check failed: {e}")
    return False

def check_tantivy_refinement(topic: str) -> bool:
    try:
        from parsers.tantivy_engine import search_index
        results = search_index(topic, limit_per_source=1)
        if results.get("pdf") or results.get("obsidian") or results.get("zim"):
            return True
    except Exception as e:
        logger.warning(f"Tantivy refinement check failed: {e}")
    return False

def extract_topics_local(question: str, options: dict, explanation: str = '', strategy: str = 'regex', use_zim_refinement: bool = False, use_tantivy_refinement: bool = False) -> dict:
    topics_extracted = []

    if strategy == 'medspacy':
        nlp = get_medspacy_model()
        if nlp:
            combined_text = f"{question} " + " ".join(options.values()) + f" {explanation}"
            doc = nlp(combined_text)

            # Extract un-negated entities and filter stop words
            topics_extracted = [ent.text for ent in doc.ents if not ent._.is_negated and ent.text not in _STOP_WORDS]
        else:
            strategy = 'regex'

    if strategy == 'regex' or not topics_extracted:
        # Single concatenation avoids multiple regex engine calls
        combined_text = f"{question} " + " ".join(options.values()) + f" {explanation}"
        raw_matches = _MEDICAL_REGEX.findall(combined_text)

        # Filter stop words
        topics_extracted = [m.strip() for m in raw_matches if m.strip() and m.strip() not in _STOP_WORDS]

    # Fast O(N) deduplication preserving insertion order
    unique_topics = list(dict.fromkeys(topics_extracted))

    if use_zim_refinement:
        unique_topics = [t for t in unique_topics if check_zim_refinement(t)]

    if use_tantivy_refinement:
        unique_topics = [t for t in unique_topics if check_tantivy_refinement(t)]

    if not unique_topics:
        return {"main_topic": "General Medical Concept", "all_topics": ["General Medical Concept"]}

    return {"main_topic": unique_topics[0], "all_topics": unique_topics}
