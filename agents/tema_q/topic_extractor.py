import re
import logging
from typing import Dict, Any, List, Set, Tuple, Optional
from contextlib import closing

from core.database import get_db_connection
from parsers.zim_reader import resolve_zim_topic

logger = logging.getLogger(__name__)

_medspacy_nlp = None
_medspacy_available = True

# Multi-word clinical noun-phrase & suffix regex
_CLINICAL_PHRASE_REGEX = re.compile(
    r"\b(?:"
    r"(?:[A-Z][a-z]+|[a-z]+)\s+(?:of\s+(?:the\s+)?)?(?:[A-Za-z]+\s+)?(?:heart|kidney|liver|lung|brain|bone|nerve|artery|vein|valve|gland|tract|syndrome|disease|disorder|infarction|insufficiency|stenosis|thrombosis|embolism|amyloidosis|carcinoma|sarcoma|lymphoma|leukemia|neuropathy|myopathy|retinopathy|nephropathy|cardiomyopathy|aneurysm|fracture|hemorrhage|pneumonia|fibrosis|cirrhosis|sclerosis|hypertension|hypotension|diabetes|sepsis|shock|lesion|palsy|ulcer|abscess|calculi|calculus|attack)"
    r"|(?:[A-Z][a-z]+(?:\s+[A-Z]?[a-z]+){1,3})"
    r"|(?:[A-Za-z]{4,}(?:itis|oma|osis|pathy|emia|plasia|algia|ectomy|otomy|ostomy|plasty|scopy|graphy|megaly|penia|cytosis|rrhea|rrhagia|malacia|sclerosis|stenosis|trophia|trophy))"
    r")\b"
)

_STANDALONE_GENERIC_WORDS = frozenset({
    "heart", "lung", "lungs", "liver", "kidney", "kidneys", "brain", "bone", "bones",
    "blood", "cell", "cells", "tissue", "organ", "nerve", "artery", "vein", "muscle",
    "skin", "eye", "ear", "hand", "foot", "leg", "arm", "chest", "abdomen", "neck",
    "head", "back", "patient", "patients", "history", "year", "years", "old", "male",
    "female", "man", "woman", "boy", "girl", "child", "infant", "day", "days", "week",
    "weeks", "month", "months", "hour", "hours", "minute", "minutes", "presentation",
    "diagnosis", "treatment", "management", "therapy", "symptom", "symptoms", "sign",
    "signs", "finding", "findings", "test", "tests", "study", "studies", "level",
    "levels", "normal", "abnormal", "acute", "chronic", "severe", "mild", "moderate",
    "primary", "secondary", "left", "right", "upper", "lower", "anterior", "posterior",
    "medial", "lateral", "proximal", "distal", "bilateral", "unilateral", "most",
    "likely", "common", "cause", "following", "which", "what", "where", "when", "why",
    "how", "true", "false", "except", "correct", "incorrect", "statement", "option",
    "options", "answer", "explanation", "none", "all", "above", "below", "both",
    "neither", "either", "initial", "next", "step", "best", "first", "line", "choice",
    "drug", "dose", "dosage", "administration", "prescribed", "given", "shows", "reveals",
    "associated", "characterized", "condition", "disease", "disorder", "syndrome",
    "examination", "physical", "laboratory", "serum", "plasma", "urine", "biopsy",
    "confirms", "presents", "after", "with",
})

_STOP_WORDS = frozenset({
    "a", "an", "the", "in", "on", "at", "to", "for", "with", "by", "from", "of",
    "is", "are", "was", "were", "be", "been", "being", "has", "have", "had", "do",
    "does", "did", "can", "could", "will", "would", "shall", "should", "may", "might",
    "must", "it", "its", "this", "that", "these", "those", "their", "his", "her",
    "our", "your", "they", "them", "he", "she", "we", "you", "who", "whom", "whose",
    "and", "or", "but", "if", "then", "else", "when", "while", "where", "because",
    "although", "though", "since", "unless", "until", "whether", "before", "after",
    "during", "through", "between", "among", "within", "without", "against", "under",
    "over", "above", "below", "into", "onto", "upon", "about", "around", "across",
    "not", "no", "nor", "only", "also", "very", "more", "most", "less", "least",
})

_ORGAN_TO_ADJECTIVE = {
    "heart": "cardiac",
    "kidney": "renal",
    "kidneys": "renal",
    "liver": "hepatic",
    "lung": "pulmonary",
    "lungs": "pulmonary",
    "brain": "cerebral",
    "stomach": "gastric",
    "bone": "osseous",
    "bones": "osseous",
    "skin": "cutaneous",
    "eye": "ocular",
    "eyes": "ocular",
    "spleen": "splenic",
    "pancreas": "pancreatic",
    "thyroid": "thyroid",
    "adrenal": "adrenal",
    "intestine": "intestinal",
    "bowel": "intestinal",
    "colon": "colonic",
    "esophagus": "esophageal",
    "bladder": "vesical",
    "gallbladder": "biliary",
    "vessel": "vascular",
    "vessels": "vascular",
    "nerve": "neural",
}

_CLINICAL_SYNONYMS = {
    "heart attack": "Myocardial infarction",
    "acute heart attack": "Acute myocardial infarction",
    "heart failure": "Heart failure",
    "congestive heart failure": "Heart failure",
    "stroke": "Ischemic stroke",
    "brain attack": "Stroke",
    "high blood pressure": "Hypertension",
    "low blood pressure": "Hypotension",
    "kidney stones": "Nephrolithiasis",
    "kidney stone": "Nephrolithiasis",
    "renal stone": "Nephrolithiasis",
    "renal calculi": "Nephrolithiasis",
    "gallstones": "Cholelithiasis",
    "gallstone": "Cholelithiasis",
    "pinched nerve": "Radiculopathy",
    "collapsed lung": "Pneumothorax",
    "blood clot": "Thromboembolism",
    "lung clot": "Pulmonary embolism",
    "enlarged heart": "Cardiomegaly",
    "fatty liver": "Hepatic steatosis",
}

_CLINICAL_SUFFIX_RE = re.compile(
    r"(?:itis|oma|osis|pathy|emia|plasia|algia|ectomy|otomy|ostomy|plasty|scopy|graphy|"
    r"megaly|penia|cytosis|rrhea|rrhagia|malacia|sclerosis|stenosis|infarction|syndrome|"
    r"carcinoma|sarcoma|lymphoma|leukemia|aneurysm|pneumonia|cirrhosis|thrombosis|embolism|"
    r"diabetes|hypertension|hypotension|sepsis|palsy|fracture|hemorrhage|ulcer|abscess)$",
    re.IGNORECASE,
)


def get_medspacy_model():
    """Singleton loader for medSpaCy with ConText negation and PyRuSH sentence segmentation."""
    global _medspacy_nlp, _medspacy_available
    if not _medspacy_available:
        return None

    if _medspacy_nlp is None:
        try:
            import medspacy
            from medspacy.target_matcher import TargetRule

            logging.getLogger("PyRuSH.PyRuSHSentencizer").setLevel(logging.ERROR)
            try:
                from loguru import logger as loguru_logger
                loguru_logger.disable("PyRuSH")
            except ImportError:
                pass

            try:
                _medspacy_nlp = medspacy.load("en_core_web_sm")
            except Exception:
                _medspacy_nlp = medspacy.load()

            if "medspacy_target_matcher" in _medspacy_nlp.pipe_names:
                matcher = _medspacy_nlp.get_pipe("medspacy_target_matcher")
                matcher.add([
                    TargetRule("heart attack", "CONDITION"),
                    TargetRule("myocardial infarction", "CONDITION"),
                    TargetRule("cardiac amyloidosis", "CONDITION"),
                    TargetRule("amyloidosis of the heart", "CONDITION"),
                    TargetRule("amyloidosis of heart", "CONDITION"),
                    TargetRule("amyloidosis", "CONDITION"),
                    TargetRule("dyspnea", "SYMPTOM"),
                    TargetRule("asthma", "CONDITION"),
                    TargetRule("amoxicillin", "MEDICATION"),
                    TargetRule("hypertension", "CONDITION"),
                    TargetRule("diabetes mellitus", "CONDITION"),
                    TargetRule("pulmonary embolism", "CONDITION"),
                    TargetRule("vagus nerve lesion", "CONDITION"),
                    TargetRule("appendectomy", "PROCEDURE"),
                ])
        except Exception as e:
            logger.debug(f"medSpaCy unavailable ({e}), using clinical phrase extractor.")
            _medspacy_available = False

    return _medspacy_nlp


def normalize_topic_key(text: str) -> str:
    """Produces a lowercase alphanumeric key for ignore-list and deduplication checks."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def get_ignored_topics_set() -> Set[str]:
    """Fetches all permanently ignored topic keys from SQLite ignored_topics."""
    ignored: Set[str] = set()
    try:
        with closing(get_db_connection()) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT topic_norm FROM ignored_topics")
            for row in cursor.fetchall():
                val = row[0] if isinstance(row, tuple) else row["topic_norm"]
                if val:
                    ignored.add(val)
    except Exception as e:
        logger.debug(f"Could not read ignored_topics: {e}")
    return ignored


def add_ignored_topic(term: str) -> bool:
    """Adds a term to the permanent ignore list (triggered by double-click in UI)."""
    norm = normalize_topic_key(term)
    if not norm:
        return False
    try:
        with closing(get_db_connection()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ignored_topics (topic_norm, original_text, created_at) VALUES (?, ?, unixepoch('now'))",
                (norm, term.strip())
            )
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to add ignored topic '{term}': {e}")
        return False


def remove_ignored_topic(term: str) -> bool:
    """Removes a term from the ignore list if needed."""
    norm = normalize_topic_key(term)
    if not norm:
        return False
    try:
        with closing(get_db_connection()) as conn:
            conn.execute("DELETE FROM ignored_topics WHERE topic_norm = ?", (norm,))
            conn.commit()
        return True
    except Exception:
        return False


def canonicalize_clinical_phrase(raw_phrase: str) -> Tuple[str, str]:
    """
    Canonicalizes syntactic and semantic variants into a single clinical topic:
    - 'amyloidosis of the heart' / 'amyloidosis of heart' / 'amyloidosis heart' / 'heart amyloidosis'
      -> ('Cardiac amyloidosis', 'amyloidosis cardiac')
    - 'heart attack' -> ('Myocardial infarction', 'infarction myocardial')
    Returns (display_name, canonical_signature).
    """
    cleaned = re.sub(r"[^\w\s-]", " ", (raw_phrase or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "", ""

    lower = cleaned.lower()

    words = [w for w in lower.split() if w not in _STOP_WORDS or w == "of"]
    while words and (
        words[0] in ("of", "the", "a", "an", "in", "on", "with", "due", "to", "no", "not")
        or (words[0] in _STANDALONE_GENERIC_WORDS and len(words) > 1 and words[0] not in _ORGAN_TO_ADJECTIVE)
    ):
        words.pop(0)
    while words and (
        words[-1] in ("of", "the", "a", "an", "in", "on", "with", "due", "to")
        or (words[-1] in _STANDALONE_GENERIC_WORDS and len(words) > 1 and words[-1] not in _ORGAN_TO_ADJECTIVE)
    ):
        words.pop()

    if not words:
        return "", ""

    rejoined = " ".join(words)

    if rejoined in _CLINICAL_SYNONYMS:
        canon_disp = _CLINICAL_SYNONYMS[rejoined]
        sig_tokens = sorted(normalize_topic_key(canon_disp).split())
        return canon_disp, " ".join(sig_tokens)

    # Normalize 'X of (the) Y' -> '<Adj(Y)> X' (e.g., 'amyloidosis of the heart' -> 'cardiac amyloidosis')
    m_prep = re.match(r"^([a-z0-9-]+)\s+of\s+(?:the\s+)?([a-z0-9-]+)$", rejoined)
    if m_prep:
        cond, organ = m_prep.group(1), m_prep.group(2)
        adj = _ORGAN_TO_ADJECTIVE.get(organ, organ)
        words = [adj, cond]

    # Normalize 2-word '<condition> <organ>' or '<organ> <condition>'
    elif len(words) == 2 and "of" not in words:
        w0, w1 = words[0], words[1]
        if f"{w0} {w1}" in _CLINICAL_SYNONYMS:
            canon_disp = _CLINICAL_SYNONYMS[f"{w0} {w1}"]
            return canon_disp, " ".join(sorted(normalize_topic_key(canon_disp).split()))
        if w1 in _ORGAN_TO_ADJECTIVE and w0 not in _ORGAN_TO_ADJECTIVE:
            words = [_ORGAN_TO_ADJECTIVE[w1], w0]
        elif w0 in _ORGAN_TO_ADJECTIVE and w1 not in _ORGAN_TO_ADJECTIVE:
            words = [_ORGAN_TO_ADJECTIVE[w0], w1]

    final_words = [w for w in words if w not in _STOP_WORDS]
    if not final_words:
        return "", ""

    if len(final_words) == 1 and final_words[0] in _STANDALONE_GENERIC_WORDS:
        return "", ""

    phrase_lower = " ".join(final_words)
    if phrase_lower in _CLINICAL_SYNONYMS:
        display = _CLINICAL_SYNONYMS[phrase_lower]
    else:
        display = phrase_lower[0].upper() + phrase_lower[1:]

    sig_tokens = sorted(_ORGAN_TO_ADJECTIVE.get(w, w) for w in normalize_topic_key(display).split())
    signature = " ".join(sig_tokens)
    return display, signature


def check_zim_refinement(topic: str) -> bool:
    """Checks if the topic matches an article or redirect in the ZIM archive or zim_titles table."""
    res = resolve_zim_topic(topic)
    return bool(res.get("matched"))


def check_tantivy_refinement(topic: str) -> bool:
    """Checks if the topic matches any indexed document in Tantivy."""
    try:
        from parsers.tantivy_engine import search_index
        results = search_index(topic, limit_per_source=1)
        return bool(results.get("pdf") or results.get("obsidian") or results.get("zim"))
    except Exception:
        return False


def _has_strong_clinical_morphology(topic: str) -> bool:
    """
    Determines whether a topic has strong clinical morphology (multi-word clinical entity
    or established diagnostic/surgical suffix).
    """
    words = topic.lower().split()
    if any(_CLINICAL_SUFFIX_RE.search(w) for w in words):
        return True
    if len(words) >= 2 and any(w in _ORGAN_TO_ADJECTIVE.values() for w in words):
        return True
    return False


def _extract_raw_candidates_from_text(text: str, strategy: str = "medspacy") -> List[str]:
    """
    Extracts candidate clinical phrases while respecting negation ('no asthma')
    and keeping multi-word phrases intact.
    """
    if not text or not text.strip():
        return []

    candidates: List[str] = []

    negated_spans: Set[str] = set()
    for neg_match in re.finditer(
        r"\b(?:no|not|without|denies|denied|absence\s+of|rules?\s+out)\s+(?:evidence\s+of\s+|history\s+of\s+)?([A-Za-z]+(?:\s+[A-Za-z]+){0,2})",
        text,
        flags=re.IGNORECASE,
    ):
        neg_phrase = neg_match.group(1).strip().lower()
        for tok in neg_phrase.split():
            if len(tok) >= 4:
                negated_spans.add(tok)

    if strategy == "medspacy":
        nlp = get_medspacy_model()
        if nlp is not None:
            try:
                doc = nlp(text)
                for ent in doc.ents:
                    if getattr(ent._, "is_negated", False):
                        continue
                    ent_text = ent.text.strip()
                    if any(neg in ent_text.lower().split() for neg in negated_spans):
                        continue
                    candidates.append(ent_text)
            except Exception as e:
                logger.debug(f"medSpaCy parse fallback: {e}")

    for m in _CLINICAL_PHRASE_REGEX.finditer(text):
        phrase = m.group(0).strip()
        if any(neg in phrase.lower().split() for neg in negated_spans):
            continue
        candidates.append(phrase)

    return candidates


def deduplicate_and_classify_topics(
    raw_candidates: List[str],
    use_zim_refinement: bool = False,
    use_tantivy_refinement: bool = False,
) -> List[Dict[str, Any]]:
    """
    1. Filters out permanently ignored topics (from ignored_topics table).
    2. Canonicalizes syntactic/organ variants ('amyloidosis of heart', 'amyloidosis heart', 'cardiac amyloidosis').
    3. Resolves ZIM redirects so synonyms ('heart attack' -> 'Myocardial infarction') merge into 1 entry.
    4. Suppresses single-word sub-fragments when a more specific multi-word topic exists.
    5. Classifies each unique topic as:
       - verified=True (black text, pre-selected in green)
       - verified=False (grey doubtful text, unselected by default).
    """
    ignored_set = get_ignored_topics_set()
    grouped: Dict[str, Dict[str, Any]] = {}

    for raw in raw_candidates:
        display, sig = canonicalize_clinical_phrase(raw)
        if not display or not sig:
            continue

        norm_key = normalize_topic_key(display)
        if not norm_key or norm_key in ignored_set or sig in ignored_set:
            continue

        zim_info = resolve_zim_topic(display)
        zim_matched = bool(zim_info.get("matched"))
        if zim_matched and zim_info.get("canonical_title"):
            display = zim_info["canonical_title"]
            _, z_sig = canonicalize_clinical_phrase(display)
            if z_sig:
                sig = z_sig

        norm_key = normalize_topic_key(display)
        if norm_key in ignored_set:
            continue

        is_verified = zim_matched or _has_strong_clinical_morphology(display)

        if use_zim_refinement and not zim_matched:
            continue
        if use_tantivy_refinement and not check_tantivy_refinement(display):
            continue

        if sig not in grouped:
            grouped[sig] = {
                "name": display,
                "signature": sig,
                "verified": is_verified,
                "selected": is_verified,
                "zim_matched": zim_matched,
            }
        else:
            existing = grouped[sig]
            if zim_matched and not existing["zim_matched"]:
                existing["name"] = display
                existing["zim_matched"] = True
            existing["verified"] = existing["verified"] or is_verified
            existing["selected"] = existing["selected"] or is_verified

    items = list(grouped.values())

    multi_word_token_sets = [
        set(item["signature"].split())
        for item in items
        if len(item["signature"].split()) >= 2
    ]

    filtered_items: List[Dict[str, Any]] = []
    for item in items:
        tokens = set(item["signature"].split())
        if len(tokens) == 1:
            tok = next(iter(tokens))
            if any(tok in mw_set for mw_set in multi_word_token_sets):
                continue
        filtered_items.append({
            "name": item["name"],
            "verified": bool(item["verified"]),
            "selected": bool(item["selected"]),
            "confidence": "high" if item["verified"] else "low",
        })

    filtered_items.sort(key=lambda x: (0 if x["verified"] else 1))
    return filtered_items


def extract_topics_local(
    question: str,
    options: Optional[dict] = None,
    explanation: str = "",
    strategy: str = "medspacy",
    use_zim_refinement: bool = False,
    use_tantivy_refinement: bool = False,
) -> Dict[str, Any]:
    """
    Main local topic extractor for MCQs, options, and explanations.
    Returns:
      {
        "main_topic": str,
        "all_topics": List[str],
        "topic_details": List[{"name": str, "verified": bool, "selected": bool, "confidence": str}]
      }
    """
    options = options or {}
    combined_parts = [question or ""]
    for opt_val in options.values():
        if opt_val:
            combined_parts.append(str(opt_val))
    if explanation:
        combined_parts.append(explanation)

    combined_text = ". ".join(p for p in combined_parts if p)
    raw_candidates = _extract_raw_candidates_from_text(combined_text, strategy=strategy)

    details = deduplicate_and_classify_topics(
        raw_candidates,
        use_zim_refinement=use_zim_refinement,
        use_tantivy_refinement=use_tantivy_refinement,
    )

    if not details:
        return {
            "main_topic": "General Medical Concept",
            "all_topics": ["General Medical Concept"],
            "topic_details": [
                {"name": "General Medical Concept", "verified": False, "selected": False, "confidence": "low"}
            ],
        }

    all_names = [d["name"] for d in details]
    verified_names = [d["name"] for d in details if d["verified"]]
    main_topic = verified_names[0] if verified_names else all_names[0]

    return {
        "main_topic": main_topic,
        "all_topics": all_names,
        "topic_details": details,
    }


def extract_topics(
    text: str,
    use_zim_refinement: bool = False,
    use_tantivy_refinement: bool = False,
) -> List[str]:
    """Direct text-to-topic-list helper used by /api/mcq/topics/extract."""
    res = extract_topics_local(
        question=text,
        options={},
        explanation="",
        strategy="medspacy",
        use_zim_refinement=use_zim_refinement,
        use_tantivy_refinement=use_tantivy_refinement,
    )
    return [t for t in res.get("all_topics", []) if t != "General Medical Concept"]


def filter_sibling_topics(query: str, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filters out chunks whose primary subject is a distinct sibling differential diagnosis
    that does not overlap with the queried clinical topic.
    """
    if not chunks:
        return []

    query_res = extract_topics_local(query, {}, strategy="medspacy")
    query_topics = [t.lower() for t in query_res.get("all_topics", []) if t != "General Medical Concept"]
    if not query_topics:
        query_topics = [query.lower().strip()]

    filtered_chunks = []
    for chunk in chunks:
        chunk_text = (
            chunk.get("heading_path", "")
            + " "
            + (chunk.get("child_content", "") or chunk.get("snippet", "") or chunk.get("parent_content", "") or chunk.get("title", ""))
        ).strip()
        if not chunk_text:
            filtered_chunks.append(chunk)
            continue

        chunk_lower = chunk_text.lower()
        if any(qt in chunk_lower for qt in query_topics):
            filtered_chunks.append(chunk)
            continue

        title_str = chunk.get("title", "") or chunk.get("main_topic", "")
        if title_str and check_zim_refinement(title_str) and title_str.lower() not in query.lower():
            continue
        filtered_chunks.append(chunk)

    return filtered_chunks or chunks
