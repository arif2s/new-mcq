import spacy
import medspacy
from medspacy.target_matcher import TargetRule
from core.database import get_db_connection
from parsers.tantivy_engine import search_index

# Disable PyRuSH logger strictly
import logging
logger = logging.getLogger("PyRuSH.PyRuSHSentencizer")
logger.setLevel(logging.WARNING)
for handler in logger.handlers:
    logger.removeHandler(handler)

# Disable loguru for PyRuSH if used
try:
    from loguru import logger as loguru_logger
    loguru_logger.disable("PyRuSH")
except ImportError:
    pass


nlp = medspacy.load("en_core_web_sm", enable=["medspacy_pyrush", "medspacy_target_matcher", "medspacy_context"])

# Configure target matchers for clinical entities
target_matcher = nlp.get_pipe("medspacy_target_matcher")
rules = [
    # Validation targets
    TargetRule("dyspnea", "DISEASE"),
    TargetRule("amoxicillin", "PHARMACOLOGY"),
    TargetRule("asthma", "DISEASE"),
    TargetRule("myocardial infarction", "DISEASE"),
    TargetRule("hypertension", "DISEASE"),
    TargetRule("diabetes", "DISEASE"),
    TargetRule("chest pain", "DISEASE"),
    TargetRule("penicillin", "PHARMACOLOGY"),
    TargetRule("aspirin", "PHARMACOLOGY"),
    TargetRule("heart", "ANATOMICAL_SITE"),
    TargetRule("lung", "ANATOMICAL_SITE"),
    TargetRule("liver", "ANATOMICAL_SITE"),
    TargetRule("appendectomy", "PROCEDURE"),
    TargetRule("surgery", "PROCEDURE"),
    TargetRule("mri", "PROCEDURE"),
    TargetRule("ct scan", "PROCEDURE"),
]
target_matcher.add(rules)

def check_zim_refinement(term: str) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM zim_titles WHERE title COLLATE NOCASE = ?", (term,))
        return cursor.fetchone() is not None

def check_tantivy_refinement(term: str) -> bool:
    results = search_index(term, limit_per_source=1)
    # Check if there are any results in any of the categories
    has_results = any(len(docs) > 0 for docs in results.values())
    return has_results

def extract_topics(text: str, use_zim_refinement: bool = False, use_tantivy_refinement: bool = False) -> list:
    doc = nlp(text)

    extracted_entities = set()

    # Generic stop words and non-specific verbs to exclude
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "and", "or", "but", "to", "of", "in", "for", "with", "on", "at", "by"}

    for ent in doc.ents:
        # Exclude negated terms
        if ent._.is_negated:
            continue

        term = ent.text.strip().title()
        term_lower = term.lower()

        # Exclude stop words and non-specific terms
        if term_lower in stop_words:
            continue

        # Optional ZIM refinement
        if use_zim_refinement:
            if not check_zim_refinement(term):
                continue

        # Optional Tantivy refinement
        if use_tantivy_refinement:
            if not check_tantivy_refinement(term):
                continue

        extracted_entities.add(term)

    return sorted(list(extracted_entities))
