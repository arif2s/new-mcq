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
            # Silence PyRuSH warnings as required by Memory guidelines
            logging.getLogger("PyRuSH.PyRuSHSentencizer").setLevel(logging.WARNING)
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
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_sources'")
                if not cursor.fetchone():
                     return False

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

def filter_sibling_topics(query: str, chunks: list[dict]) -> list[dict]:
    # Extract topics from the query
    query_topics = extract_topics_local(query, {}, strategy='medspacy').get("all_topics", [])
    if not query_topics:
        query_topics = [query]

    query_topic_lower = set(t.lower() for t in query_topics)

    filtered_chunks = []

    nlp = get_medspacy_model()

    for chunk in chunks:
        # We need to cross reference extracted entities using MedSpacy
        # and checking if the entity is an established differential diagnosis / sibling condition

        # Consider the child_content or parent_content or title
        chunk_text = chunk.get("child_content", "") or chunk.get("parent_content", "") or chunk.get("title", "")
        if not chunk_text:
            filtered_chunks.append(chunk)
            continue

        chunk_topics = []
        if nlp:
            doc = nlp(chunk_text)
            chunk_topics = [ent.text for ent in doc.ents if not ent._.is_negated]
        else:
             chunk_topics = extract_topics_local(chunk_text, {}, strategy='regex').get("all_topics", [])

        if not chunk_topics:
             chunk_topics = [chunk.get("title", "")]

        chunk_topic_lower = set(t.lower() for t in chunk_topics)

        # Check if there is an overlap
        has_overlap = False
        for qt in query_topic_lower:
            for ct in chunk_topic_lower:
                if qt in ct or ct in qt:
                    has_overlap = True
                    break
            if has_overlap:
                break

        if not has_overlap:
            # If no overlap, check if primary entity is a sibling condition (e.g., via zim_titles check)
            # If it is a recognized distinct condition, we drop it.
            primary_entity = chunk_topics[0] if chunk_topics else ""
            if primary_entity:
                is_sibling = check_zim_refinement(primary_entity)
                if not is_sibling:
                     # It's just generic text that doesn't overlap but isn't a sibling condition
                     filtered_chunks.append(chunk)
                # Else: it's a distinct condition (sibling) that doesn't overlap, so we penalize/drop it (don't append)
            else:
                 filtered_chunks.append(chunk)
        else:
            filtered_chunks.append(chunk)

    return filtered_chunks
