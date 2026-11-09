"""
agents/tema_q/subtopic_router.py

Pure-prose sanitizer, 7-domain medical subtopic ontology, question-anchored
paragraph router, and CPU 'solar' (stray) sentence pruner.
Enforces Invariant 1 (Zero VRAM) and prepares compact, subtopic-aligned text
budgets for LM Studio Pass 1 bulletization and Pass 2 Tema_Q micro-editing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

os.environ["CUDA_VISIBLE_DEVICES"] = ""

import numpy as np

from agents.tema_q.topic_extractor import classify_medical_domain
from parsers.embedding_engine import get_embedding_engine

logger = logging.getLogger("medisearch.subtopic_router")

# ---------------------------------------------------------------------------
# 1. Pure-Prose Sanitizer (Strips Images, Captions, Tables & Visual Clutter)
# ---------------------------------------------------------------------------
_HTML_TABLE_FIG_RE = re.compile(
    r"<(?:table|figure|figcaption|img|svg|canvas|script|style)\b[^>]*>.*?</(?:table|figure|figcaption|img|svg|canvas|script|style)>|"
    r"<(?:img|br|hr|input|meta|link)\b[^>]*/?>",
    flags=re.DOTALL | re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^\)]*\)|!\[\[[^\]]+\]\]")
_OBSIDIAN_EMBED_RE = re.compile(
    r"^\s*!\[\[.*?(?:\.png|\.jpg|\.jpeg|\.gif|\.svg|\.webp|\.pdf).*?\]\]\s*$",
    re.IGNORECASE,
)
_MD_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_ASCII_GRID_ROW_RE = re.compile(r"^\s*\+[-=+:]+\+\s*$")
_CAPTION_LINE_RE = re.compile(
    r"^\s*(?:>\s*)?\*?(?:Figure|Fig\.|Table|Tab\.|Box|Chart|Plate|Scheme|Diagram|Photograph|Photomicrograph)\s+"
    r"(?:S?\d+[A-Za-z0-9.\-]*|[IVXLCDM]+)\s*[:.\-–—]",
    re.IGNORECASE,
)
_INLINE_FIG_REF_RE = re.compile(
    r"\(\s*(?:see\s+)?(?:Figures?|Figs?\.?|Tables?|Tabs?\.?|Boxes|Plates?)\s+[\dA-Za-z.\-,\s]+\)",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])(?<!\b[A-Z]\.)(?<!\bvs\.)(?<!\bVs\.)(?<!\beg\.)(?<!\bie\.)(?<!\bdr\.)(?<!\bDr\.)(?<!\bFig\.)(?<!\bTab\.)(?<!\bsp\.)(?<!\bssp\.)\s+"
)
_NUMERIC_OR_CLINICAL_SIGNAL_RE = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*(?:mg|g|mcg|ml|l|mmol|meq|mmhg|cm|mm|%|iu|hrs?|days?|wks?|mos?|yrs?)|"
    r"gram[- ]?(?:positive|negative)|acid[- ]?fast|congo\s+red|gold\s+standard|first[- ]?line|drug\s+of\s+choice|doc\b|triad\b|vector\b)",
    re.IGNORECASE,
)


def sanitize_clinical_prose(raw_text: str) -> str:
    """
    Extracts strictly pure clinical prose and headings from raw Markdown/HTML/PDF/ZIM text.
    Removes all tables, image embeds, figure/table captions, and inline figure references.
    """
    if not raw_text:
        return ""

    text = _HTML_TABLE_FIG_RE.sub("\n", str(raw_text))
    text = _MD_IMAGE_RE.sub("", text)
    text = _INLINE_FIG_REF_RE.sub("", text)
    text = _HTML_TAG_RE.sub(" ", text)

    clean_lines: List[str] = []
    in_code_fence = False
    skip_next_caption_continuation = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence:
            continue

        if not stripped:
            skip_next_caption_continuation = False
            if clean_lines and clean_lines[-1] != "":
                clean_lines.append("")
            continue

        # Drop Obsidian media embeds, Markdown table rows, and ASCII grid borders
        if (
            _OBSIDIAN_EMBED_RE.match(stripped)
            or _MD_TABLE_ROW_RE.match(stripped)
            or _ASCII_GRID_ROW_RE.match(stripped)
        ):
            skip_next_caption_continuation = False
            continue

        # Drop tab-separated or multi-pipe pseudo-table lines
        if stripped.count("|") >= 2 or stripped.count("\t") >= 2:
            skip_next_caption_continuation = False
            continue

        # Drop figure/table/diagram captions
        if _CAPTION_LINE_RE.match(stripped):
            skip_next_caption_continuation = True
            continue

        if skip_next_caption_continuation and len(stripped.split()) <= 14 and not stripped.startswith("#"):
            skip_next_caption_continuation = False
            continue
        skip_next_caption_continuation = False

        # Collapse horizontal whitespace
        norm_line = re.sub(r"[ \t]+", " ", stripped)
        clean_lines.append(norm_line)

    return "\n".join(clean_lines).strip()


# ---------------------------------------------------------------------------
# 2. Seven-Domain Medical Subtopic Schemas & Question Templates
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SubtopicSpec:
    subtopic_id: str
    display_name: str
    heading_pattern: re.Pattern
    question_template: str
    signal_keywords: Tuple[str, ...]


DOMAIN_SUBTOPIC_SCHEMAS: Dict[str, Tuple[SubtopicSpec, ...]] = {
    "disease": (
        SubtopicSpec(
            "definition_epidemiology",
            "Definition & Epidemiology",
            re.compile(r"\b(?:definition|overview|introduction|epidemiology|incidence|prevalence|background)\b", re.I),
            "What is the definition, epidemiology, incidence, and affected population of {topic}?",
            ("defined as", "characterized by", "incidence", "prevalence", "common in", "mortality", "bimodal", "endemic"),
        ),
        SubtopicSpec(
            "etiology_transmission",
            "Causes, Vector & Pathogenesis",
            re.compile(r"\b(?:etiolog|cause|risk\s+factor|transmission|vector|pathogenesis|pathophysiolog|mechanism|genetics?)\b", re.I),
            "What are the causes, etiology, vector, mode of transmission, risk factors, and pathogenesis of {topic}?",
            ("caused by", "vector", "transmitted", "mosquito", "anopheles", "aedes", "culex", "mutation", "autoantibody", "risk factor", "pathogenesis", "deficiency", "reservoir"),
        ),
        SubtopicSpec(
            "clinical_features",
            "Clinical Features (Signs & Symptoms)",
            re.compile(r"\b(?:clinical\s+feature|presentation|signs?\s+and\s+symptoms?|symptoms?|signs?|physical\s+exam|manifestation)\b", re.I),
            "What are the clinical features, presenting symptoms, physical signs, and classic triad of {topic}?",
            ("presents with", "symptom", "sign", "triad", "tetrad", "pain", "fever", "rash", "murmur", "hepatomegaly", "splenomegaly", "characteristic", "pathognomonic"),
        ),
        SubtopicSpec(
            "diagnosis_pathology",
            "Diagnosis, Staging & Pathology",
            re.compile(r"\b(?:diagnos|investigation|workup|laboratory|imaging|biopsy|histopatholog|morpholog|microscop|gross|staging|criteria)\b", re.I),
            "What are the diagnostic criteria, gold standard test, laboratory findings, imaging, gross morphology, and microscopy of {topic}?",
            ("gold standard", "diagnosis", "biopsy", "stain", "microscopy", "gross", "histology", "ct", "mri", "ultrasound", "elevated", "positive", "criteria", "stage", "grade"),
        ),
        SubtopicSpec(
            "treatment_prevention",
            "Treatment & Prevention",
            re.compile(r"\b(?:treatment|management|therap|drug|pharmacolog|surger|intervention|prevention|prophylaxis|vaccine)\b", re.I),
            "What is the first-line treatment, drug of choice, surgical management, and primary prevention or prophylaxis for {topic}?",
            ("treatment", "management", "first-line", "drug of choice", "doc", "dose", "mg", "surgery", "resection", "prevention", "prophylaxis", "vaccine", "contraindicated"),
        ),
        SubtopicSpec(
            "complications_prognosis",
            "Complications & Prognosis",
            re.compile(r"\b(?:complication|prognosis|sequelae|outcome|mortality|natural\s+history)\b", re.I),
            "What are the major complications, sequelae, and prognosis of {topic}?",
            ("complication", "prognosis", "rupture", "hemorrhage", "failure", "sepsis", "stricture", "malignancy", "survival", "recurrence", "sequelae"),
        ),
    ),
    "microorganism": (
        SubtopicSpec(
            "morphology_staining",
            "Taxonomy, Microscopy & Staining",
            re.compile(r"\b(?:morpholog|microscop|stain|structure|classification|taxonomy|characteristics|capsule|spore)\b", re.I),
            "What is the Gram reaction, staining procedure, microscopic morphology, motility, capsule, and spore appearance of {topic}?",
            ("gram-positive", "gram-negative", "acid-fast", "ziehl-neelsen", "stain", "Giemsa", "india ink", "silver stain", "bacilli", "rods", "cocci", "spirochete", "spore", "drumstick", "motile", "capsule", "catalase", "oxidase"),
        ),
        SubtopicSpec(
            "culture_biochemical",
            "Culture Media & Virulence Factors",
            re.compile(r"\b(?:culture|media|growth|biochemical|virulence|toxin|antigen|pathogenesis)\b", re.I),
            "What are the special culture media, biochemical tests, toxins, and virulence factors of {topic}?",
            ("agar", "medium", "media", "macconkey", "blood agar", "chocolate agar", "lowenstein", "thayer-martin", "Robertson", "toxin", "exotoxin", "endotoxin", "hemolysis", "ferment", "urease", "coagulase"),
        ),
        SubtopicSpec(
            "transmission_reservoir",
            "Reservoir, Vector & Transmission",
            re.compile(r"\b(?:transmission|reservoir|vector|lifecycle|life\s+cycle|host|epidemiolog)\b", re.I),
            "What is the natural reservoir, definitive/intermediate host, vector, and mode of transmission of {topic}?",
            ("reservoir", "vector", "host", "definitive host", "intermediate host", "transmitted", "fecal-oral", "droplet", "aerosol", "tick", "flea", "mosquito", "sandfly", "sporozoite", "trophozoite"),
        ),
        SubtopicSpec(
            "clinical_syndromes",
            "Clinical Syndromes & Diseases",
            re.compile(r"\b(?:clinical|disease|manifestation|syndrome|infection|symptom|sign|complication)\b", re.I),
            "What clinical diseases, syndromes, signs, and complications are caused by {topic}?",
            ("causes", "syndrome", "pneumonia", "meningitis", "diarrhea", "ulcer", "abscess", "paralysis", "spasm", "trismus", "opisthotonus", "rash", "fever"),
        ),
        SubtopicSpec(
            "lab_diagnosis_treatment",
            "Lab Diagnosis, Antimicrobials & Vaccine",
            re.compile(r"\b(?:diagnos|serolog|pcr|treatment|antibiotic|antimicrobial|drug|therap|vaccine|prevention|prophylaxis)\b", re.I),
            "How is {topic} diagnosed in the lab and what is the drug of choice, antibiotic regimen, and vaccine/prophylaxis?",
            ("pcr", "elisa", "serology", "antibody", "antigen", "drug of choice", "doc", "penicillin", "metronidazole", "doxycycline", "vancomycin", "ceftriaxone", "resistance", "vaccine", "toxoid", "immunoglobulin"),
        ),
    ),
    "drug_chemical": (
        SubtopicSpec(
            "class_mechanism",
            "Class & Mechanism of Action",
            re.compile(r"\b(?:class|mechanism|pharmacodynamics?|action|receptor|target|enzyme)\b", re.I),
            "What is the pharmacological class, target receptor/enzyme, and exact mechanism of action of {topic}?",
            ("inhibits", "inhibitor", "agonist", "antagonist", "blocker", "receptor", "enzyme", "channel", "binds", "irreversible", "competitive", "synthesis"),
        ),
        SubtopicSpec(
            "pharmacokinetics",
            "Pharmacokinetics & Dosing",
            re.compile(r"\b(?:pharmacokinetics?|absorption|distribution|metabolism|excretion|elimination|half-life|dosing|route)\b", re.I),
            "What is the route of administration, bioavailability, CYP metabolism, half-life, and excretion of {topic}?",
            ("oral", "intravenous", "bioavailability", "protein binding", "cyp", "hepatic", "renal", "excreted", "half-life", "zero-order", "first-order", "prodrug", "blood-brain"),
        ),
        SubtopicSpec(
            "indications",
            "Clinical Indications & Uses",
            re.compile(r"\b(?:indication|clinical\s+use|therapeutic|treatment|application)\b", re.I),
            "What are the primary clinical indications and therapeutic uses of {topic}?",
            ("indicated", "first-line", "drug of choice", "used in", "treatment of", "prophylaxis", "maintenance", "acute", "emergency"),
        ),
        SubtopicSpec(
            "adverse_effects_toxicity",
            "Adverse Effects, Toxicity & Antidote",
            re.compile(r"\b(?:adverse|side\s+effect|toxicity|poisoning|overdose|antidote|black\s+box)\b", re.I),
            "What are the characteristic adverse effects, organ toxicities, overdose features, and specific antidote for {topic}?",
            ("adverse effect", "side effect", "toxicity", "nephrotoxicity", "hepatotoxicity", "ototoxicity", "myelosuppression", "qt prolongation", "teratogenic", "antidote", "overdose"),
        ),
        SubtopicSpec(
            "contraindications_interactions",
            "Contraindications & Drug Interactions",
            re.compile(r"\b(?:contraindication|interaction|precaution|pregnancy|warning)\b", re.I),
            "What are the absolute contraindications and major CYP/pharmacodynamic drug interactions of {topic}?",
            ("contraindicated", "pregnancy", "interaction", "inducer", "inhibitor", "disulfiram", "serotonin syndrome", "hyperkalemia", "avoid in"),
        ),
    ),
    "pathology": (
        SubtopicSpec(
            "definition_types",
            "Definition, Classification & Etiology",
            re.compile(r"\b(?:definition|classification|types?|subtypes?|etiology|causes?| biochemical)\b", re.I),
            "What is the definition, biochemical/fibril classification, subtypes, and etiology of {topic}?",
            ("defined as", "classification", "primary", "secondary", "al", "aa", "attr", "beta-2", "precursor", "protein", "folding", "deposition"),
        ),
        SubtopicSpec(
            "pathogenesis_mechanism",
            "Pathogenesis & Molecular Mechanism",
            re.compile(r"\b(?:pathogenesis|mechanism|molecular|pathway|genetics?)\b", re.I),
            "What is the molecular pathogenesis and cellular mechanism of {topic}?",
            ("misfolding", "beta-pleated", "cytokine", "il-1", "il-6", "tnf", "saa", "light chain", "mutation", "transthyretin", "macrophage", "proteolysis"),
        ),
        SubtopicSpec(
            "gross_morphology",
            "Gross Morphology & Organ Involvement",
            re.compile(r"\b(?:gross|macroscop|organ|appearance|anatom)\b", re.I),
            "What is the gross morphology and macroscopic organ appearance (kidney, spleen, liver, heart) in {topic}?",
            ("gross", "enlarged", "waxy", "firm", "lardaceous", "sago spleen", "tapioca", "pale", "hypertrophy", "macroglossia", "atrophy", "cut surface"),
        ),
        SubtopicSpec(
            "microscopy_staining",
            "Microscopy, Histopathology & Special Stains",
            re.compile(r"\b(?:microscop|histopatholog|histolog|stain|congo|birefringence|immuno|electron)\b", re.I),
            "What is the light microscopy, electron microscopy, special staining procedure, and birefringence of {topic}?",
            ("microscopy", "extracellular", "eosinophilic", "hyaline", "amorphous", "congo red", "apple-green", "birefringence", "polarized", "thioflavin", "fibrils", "7.5", "10 nm", "mesangial", "space of disse"),
        ),
        SubtopicSpec(
            "clinical_diagnosis_treatment",
            "Clinical Features, Biopsy & Management",
            re.compile(r"\b(?:clinical|diagnos|biopsy|treatment|management|prognosis)\b", re.I),
            "What are the clinical manifestations, biopsy sites of choice, diagnostic tests, and treatment of {topic}?",
            ("nephrotic", "proteinuria", "restrictive cardiomyopathy", "neuropathy", "abdominal fat pad", "rectal biopsy", "gingival", "immunofixation", "bortezomib", "daratumumab", "tafamide", "transplant"),
        ),
    ),
    "anatomy": (
        SubtopicSpec(
            "origin_course_relations",
            "Origin, Course, Boundaries & Relations",
            re.compile(r"\b(?:origin|root|course|boundar|relation|attachment|insertion|wall|content)\b", re.I),
            "What is the embryological/anatomical origin, root value, course, boundaries, contents, and relations of {topic}?",
            ("originates", "arises", "root value", "c5", "c6", "c7", "c8", "t1", "l4", "s1", "passes through", "foramen", "anterior to", "posterior to", "medial", "lateral", "boundaries", "contents"),
        ),
        SubtopicSpec(
            "branches_distribution_supply",
            "Branches, Innervation & Blood Supply",
            re.compile(r"\b(?:branch|tributar|distribution|supply|innervation|motor|sensory|drainage|lymphatic)\b", re.I),
            "What are the branches, motor and sensory innervation, arterial supply, venous drainage, and lymphatics of {topic}?",
            ("branches", "supplies", "innervates", "motor", "sensory", "artery", "vein", "nerve", "ganglion", "lymph", "anastomosis", "cutaneous"),
        ),
        SubtopicSpec(
            "clinical_correlates_lesions",
            "Clinical Correlates, Lesions & Surgical Landmarks",
            re.compile(r"\b(?:clinical|applied|lesion|injury|palsy|fracture|syndrome|surgical|landmark|test)\b", re.I),
            "What are the clinical correlates, nerve/vascular lesion deficits, deformities, and surgical landmarks of {topic}?",
            ("injury", "lesion", "palsy", "paralysis", "claw hand", "wrist drop", "ape thumb", "foot drop", "winging", "trendelenburg", "surgical neck", "midshaft", "allen test", "tinel"),
        ),
    ),
    "medicolegal": (
        SubtopicSpec(
            "definition_legal_sections",
            "Definition & Statutory Legal Sections (IPC/BNS/CrPC)",
            re.compile(r"\b(?:definition|section|ipc|bns|crpc|bnss|iea|bsa|law|legal|statut)\b", re.I),
            "What is the medicolegal definition and exact statutory legal sections (IPC, BNS, CrPC, BNSS, IEA) governing {topic}?",
            ("section", "ipc", "bns", "crpc", "bnss", "iea", "bsa", "defined under", "punishment", "cognizable", "bailable", "magistrate", "coroner"),
        ),
        SubtopicSpec(
            "mechanism_autopsy_features",
            "Mechanism, External Signs & Autopsy Findings",
            re.compile(r"\b(?:mechanism|pathophysiolog|sign|external|internal|autopsy|postmortem|pm\s+finding|appearance)\b", re.I),
            "What are the classical external signs, internal autopsy/postmortem findings, and diagnostic tests for {topic}?",
            ("autopsy", "postmortem", "antemortem", "ligature", "furrow", "parchment", "hyoid", "thyroid cartilage", "peteeae", "petechiae", "tardieu", "froth", "diatom", "getler", "paltauf", "carotid"),
        ),
        SubtopicSpec(
            "medicolegal_importance",
            "Medicolegal Importance & Differential Diagnosis",
            re.compile(r"\b(?:medicolegal|importance|significance|differential|homicid|suicid|accidental|artifact)\b", re.I),
            "What is the medicolegal importance and how do you differentiate suicidal, homicidal, and accidental {topic}?",
            ("suicidal", "homicidal", "accidental", "simulated", "postmortem staining", "hypostasis", "time since death", "cause of death", "manner of death", "weapon", "defense wound"),
        ),
    ),
    "public_health": (
        SubtopicSpec(
            "epidemiological_determinants",
            "Epidemiology, Agent-Host-Environment & Indices",
            re.compile(r"\b(?:epidemiolog|agent|host|environment|determinant|index|indices|indicator|rate|ratio|formula)\b", re.I),
            "What are the epidemiological determinants (agent, host, environment), incubation period, and statistical formulas/indices for {topic}?",
            ("agent", "reservoir", "source", "incubation", "communicability", "index", "rate", "ratio", "numerator", "denominator", "multiplier", "prevalence", "incidence", "breteau", "house index"),
        ),
        SubtopicSpec(
            "prevention_levels_control",
            "Levels of Prevention, Vaccines & Vector Control",
            re.compile(r"\b(?:prevention|primordial|primary|secondary|tertiary|control|vaccine|immunization|prophylaxis|vector\s+control)\b", re.I),
            "What are the primordial, primary, secondary, and tertiary prevention measures, vaccines, chemoprophylaxis, and vector control for {topic}?",
            ("primordial", "primary prevention", "secondary prevention", "tertiary prevention", "specific protection", "early diagnosis", "vaccine", "schedule", "dose", "chemoprophylaxis", "insecticide", "larvicide", "source reduction"),
        ),
        SubtopicSpec(
            "national_programme_guidelines",
            "National Health Programme & WHO Guidelines",
            re.compile(r"\b(?:programme|program|national|nhm|who|guideline|strategy|surveillance|screening)\b", re.I),
            "What are the National Health Programme guidelines, WHO classification/strategies, and screening protocols for {topic}?",
            ("programme", "national", "nvbdcp", "ntep", "nhm", "who", "criteria", "regimen", "category", "mda", "surveillance", "active case", "passive", "asha"),
        ),
    ),
}


# ---------------------------------------------------------------------------
# 3. Document Section Parser & Question-Anchored Subtopic Router
# ---------------------------------------------------------------------------
@dataclass
class RawProseBlock:
    source_idx: int
    source_label: str
    source_type: str
    page: int
    heading: str
    text: str
    word_count: int


@dataclass
class RoutedSubtopicBucket:
    subtopic_id: str
    display_name: str
    question_anchor: str
    primary_text: str = ""
    primary_source_label: str = ""
    primary_page: int = 1
    secondary_entries: List[Dict[str, Any]] = field(default_factory=list)


def _split_into_headed_prose_blocks(
    doc: Dict[str, Any], source_idx: int
) -> List[RawProseBlock]:
    """Splits sanitized document text into headed paragraph blocks."""
    raw = str(doc.get("raw_text") or doc.get("header") or "")
    # Remove synthetic "Source (...):" wrapper line if present
    raw = re.sub(r"^Source\s*\([^\)]+\)\s*:\s*\n?", "", raw, flags=re.I)
    clean_text = sanitize_clinical_prose(raw)
    if not clean_text:
        return []

    source_label = str(doc.get("source_label") or "Reference")
    source_type = str(doc.get("source_type") or "ref")
    page = max(1, int(doc.get("page") or 1))

    blocks: List[RawProseBlock] = []
    cur_heading = "Overview"
    cur_paras: List[str] = []

    def _flush() -> None:
        if not cur_paras:
            return
        joined = "\n".join(cur_paras).strip()
        words = len(joined.split())
        if words >= 5:
            blocks.append(
                RawProseBlock(
                    source_idx=source_idx,
                    source_label=source_label,
                    source_type=source_type,
                    page=page,
                    heading=cur_heading,
                    text=joined,
                    word_count=words,
                )
            )
        cur_paras.clear()

    for line in clean_text.splitlines():
        s = line.strip()
        if not s:
            if sum(len(p.split()) for p in cur_paras) >= 90:
                _flush()
            continue

        # Detect Markdown headings (# Heading) or short standalone ALL-CAPS/Colon section headers
        if s.startswith("#"):
            _flush()
            cur_heading = s.lstrip("#").strip()
            continue
        if (
            len(s.split()) <= 7
            and not s.endswith(".")
            and (s.isupper() or (s.endswith(":") and s[0].isupper()))
        ):
            _flush()
            cur_heading = s.rstrip(":").strip()
            continue

        cur_paras.append(s)

    _flush()
    return blocks


def _lexical_signal_score(text_lower: str, keywords: Sequence[str]) -> float:
    if not text_lower or not keywords:
        return 0.0
    hits = sum(1 for kw in keywords if kw in text_lower)
    return min(1.0, hits / 3.0)


def prune_solar_sentences(
    sentences: List[str],
    question_anchor: str,
    signal_keywords: Sequence[str],
    max_words: int,
    min_relevance: float = 0.20,
) -> str:
    """
    Scores individual sentences against the canonical subtopic question + lexical signals,
    drops 'solar' (stray/off-subtopic) sentences, deduplicates near-identical sentences,
    and packs the highest-yield sentences in reading order up to max_words.
    """
    if not sentences:
        return ""

    clean_sents = [s.strip() for s in sentences if len(s.strip().split()) >= 4]
    if not clean_sents:
        return " ".join(sentences)[: max_words * 6]

    embedder = get_embedding_engine()
    cos_matrix = embedder.score_texts_against_anchors(clean_sents, [question_anchor])

    scored_sents: List[Tuple[int, float, str, int]] = []
    for idx, sent in enumerate(clean_sents):
        s_low = sent.lower()
        w_cnt = len(sent.split())
        lex_sc = _lexical_signal_score(s_low, signal_keywords)
        has_num_or_sig = 0.15 if _NUMERIC_OR_CLINICAL_SIGNAL_RE.search(sent) else 0.0

        if cos_matrix is not None:
            cos_sc = float(max(0.0, cos_matrix[idx, 0]))
            total_sc = 0.55 * cos_sc + 0.30 * lex_sc + has_num_or_sig
        else:
            # Offline lexical fallback if no ONNX model is present
            q_words = {
                w for w in re.findall(r"[a-z]{4,}", question_anchor.lower())
                if w not in {"what", "which", "when", "where", "does", "with", "from"}
            }
            s_words = set(re.findall(r"[a-z]{4,}", s_low))
            q_overlap = len(q_words & s_words) / max(1, min(4, len(q_words)))
            total_sc = 0.45 * min(1.0, q_overlap) + 0.40 * lex_sc + has_num_or_sig

        # Drop 'solar' (stray/off-subtopic) sentences unless only 1-2 sentences exist
        if total_sc >= min_relevance or len(clean_sents) <= 2:
            scored_sents.append((idx, total_sc, sent, w_cnt))

    if not scored_sents:
        scored_sents = [(i, 0.5, s, len(s.split())) for i, s in enumerate(clean_sents[:5])]

    # Rank by score descending to select within max_words budget, then restore reading order
    scored_sents.sort(key=lambda x: x[1], reverse=True)
    selected_indices: List[int] = []
    selected_token_sets: List[Set[str]] = []
    used_words = 0

    for idx, sc, sent, w_cnt in scored_sents:
        if used_words + w_cnt > max_words and selected_indices:
            continue
        toks = set(re.findall(r"\w{3,}", sent.lower()))
        # Intra-subtopic sentence deduplication (Jaccard >= 0.78)
        if toks and any(
            len(toks & prev_t) / max(1, len(toks | prev_t)) >= 0.78
            for prev_t in selected_token_sets
        ):
            continue
        selected_indices.append(idx)
        if toks:
            selected_token_sets.append(toks)
        used_words += w_cnt

    selected_indices.sort()
    return " ".join(clean_sents[i] for i in selected_indices).strip()


def route_and_prune_subtopics(
    topic: str,
    documents: List[Dict[str, Any]],
    mcq_context: Optional[Dict[str, Any]] = None,
    max_subtopics: int = 5,
    max_source1_input_words: int = 650,
    max_sec_input_words: int = 450,
) -> List[RoutedSubtopicBucket]:
    """
    Routes paragraphs from all resolved documents into aligned medical subtopic buckets
    using Heading Regex + Question-Anchored ONNX Cosine Similarity + Lexical Signals,
    and prunes stray ('solar') sentences on CPU.
    """
    if not documents:
        return []

    mcq_q = str((mcq_context or {}).get("question") or "")
    domain = classify_medical_domain(topic, context_text=mcq_q)
    schema = list(DOMAIN_SUBTOPIC_SCHEMAS.get(domain, DOMAIN_SUBTOPIC_SCHEMAS["disease"]))

    # Extract all pure-prose blocks across all documents
    all_blocks: List[RawProseBlock] = []
    for s_idx, doc in enumerate(documents):
        all_blocks.extend(_split_into_headed_prose_blocks(doc, s_idx))

    if not all_blocks:
        return []

    questions = [spec.question_template.format(topic=topic) for spec in schema]
    block_texts = [f"{b.heading}: {b.text[:600]}" for b in all_blocks]

    embedder = get_embedding_engine()
    cos_mat = embedder.score_texts_against_anchors(block_texts, questions)

    # Map: subtopic_idx -> source_idx -> list of (score, RawProseBlock)
    routed: Dict[int, Dict[int, List[Tuple[float, RawProseBlock]]]] = {
        m_i: {} for m_i in range(len(schema))
    }

    for b_i, blk in enumerate(all_blocks):
        best_m = 0
        best_score = -1.0
        b_low = blk.text.lower()

        for m_i, spec in enumerate(schema):
            h_match = 1.0 if spec.heading_pattern.search(blk.heading) else 0.0
            lex_sc = _lexical_signal_score(b_low, spec.signal_keywords)
            cos_sc = float(max(0.0, cos_mat[b_i, m_i])) if cos_mat is not None else 0.0

            # Hybrid routing score: Heading match + Question-Paragraph Cosine + Answer-Type Lexicon
            score = 0.45 * h_match + 0.35 * cos_sc + 0.20 * lex_sc
            if score > best_score:
                best_score = score
                best_m = m_i

        routed[best_m].setdefault(blk.source_idx, []).append((best_score, blk))

    # Build pruned RoutedSubtopicBucket list
    buckets: List[Tuple[float, RoutedSubtopicBucket]] = []
    for m_i, spec in enumerate(schema):
        src_map = routed.get(m_i)
        if not src_map:
            continue

        # Filter out empty source lists and collect valid score entries
        score_candidates: List[float] = [
            score for s_list in src_map.values() if s_list for score, _ in s_list
        ]
        if not score_candidates:
            continue

        q_anchor = questions[m_i]
        # Boost subtopic priority if the active MCQ question directly asks about this subtopic
        mcq_boost = 0.25 if (mcq_q and spec.heading_pattern and spec.heading_pattern.search(mcq_q)) else 0.0
        bucket_max_sc = max(score_candidates) + mcq_boost

        # Anchor on the earliest source that has content for this subtopic
        sorted_srcs = [src_key for src_key in sorted(src_map.keys()) if src_map[src_key]]
        if not sorted_srcs:
            continue
        primary_src_idx = sorted_srcs[0]
        primary_blocks = [b for _, b in sorted(src_map[primary_src_idx], key=lambda x: x[0], reverse=True)]
        if not primary_blocks:
            continue

        primary_sents: List[str] = []
        for pb in primary_blocks:
            text = getattr(pb, "text", "")
            if text:
                primary_sents.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip())

        if not primary_sents:
            continue

        pruned_primary = prune_solar_sentences(
            primary_sents,
            question_anchor=q_anchor,
            signal_keywords=spec.signal_keywords,
            max_words=max_source1_input_words,
        )
        if not pruned_primary:
            continue

        bucket = RoutedSubtopicBucket(
            subtopic_id=spec.subtopic_id,
            display_name=spec.display_name,
            question_anchor=q_anchor,
            primary_text=pruned_primary,
            primary_source_label=getattr(primary_blocks[0], "source_label", "Unknown"),
            primary_page=getattr(primary_blocks[0], "page", 1),
        )

        # Secondary sources for this exact subtopic (even if originally under a different heading!)
        for sec_src_idx in sorted_srcs[1:]:
            sec_blocks = [b for _, b in sorted(src_map[sec_src_idx], key=lambda x: x[0], reverse=True)]
            if not sec_blocks:
                continue
            sec_sents: List[str] = []
            for sb in sec_blocks:
                text = getattr(sb, "text", "")
                if text:
                    sec_sents.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip())
            if not sec_sents:
                continue
            pruned_sec = prune_solar_sentences(
                sec_sents,
                question_anchor=q_anchor,
                signal_keywords=spec.signal_keywords,
                max_words=max_sec_input_words,
            )
            if pruned_sec:
                bucket.secondary_entries.append(
                    {
                        "text": pruned_sec,
                        "source_label": getattr(sec_blocks[0], "source_label", "Unknown"),
                        "page": getattr(sec_blocks[0], "page", 1),
                    }
                )

        buckets.append((bucket_max_sc, bucket))

    buckets.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in buckets[:max_subtopics]]


def route_subtopic_blocks(
    spec: Any,
    src_map: Dict[Any, List[Any]],
    mcq_q: Optional[str],
    q_anchor: Optional[str],
    max_source1_input_words: int,
) -> Optional[Any]:
    """Guarded routing engine handling empty source collections and bounded iterations."""
    if not src_map:
        return None

    score_candidates: List[float] = [
        score for s_list in src_map.values() if s_list for score, _ in s_list
    ]
    if not score_candidates:
        return None

    mcq_boost = 0.25 if (mcq_q and getattr(spec, "heading_pattern", None) and spec.heading_pattern.search(mcq_q)) else 0.0
    bucket_max_sc = max(score_candidates) + mcq_boost

    sorted_srcs = [src_key for src_key in sorted(src_map.keys()) if src_map[src_key]]
    if not sorted_srcs:
        return None

    primary_src_idx = sorted_srcs[0]
    sorted_blocks = sorted(src_map[primary_src_idx], key=lambda x: x[0], reverse=True)
    if not sorted_blocks:
        return None

    primary_blocks = [block for _, block in sorted_blocks]
    primary_sents: List[str] = []
    for block in primary_blocks:
        text = getattr(block, "text", "")
        if text:
            primary_sents.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip())

    if not primary_sents:
        return None

    pruned_primary = prune_solar_sentences(
        primary_sents,
        question_anchor=q_anchor or "",
        signal_keywords=getattr(spec, "signal_keywords", ()),
        max_words=max_source1_input_words,
    )
    if not pruned_primary:
        return None

    return RoutedSubtopicBucket(
        subtopic_id=getattr(spec, "subtopic_id", "general"),
        display_name=getattr(spec, "display_name", "General"),
        question_anchor=q_anchor or "",
        primary_text=pruned_primary,
        primary_source_label=getattr(primary_blocks[0], "source_label", "Unknown"),
        primary_page=getattr(primary_blocks[0], "page", 1),
    )


__all__ = [
    "SubtopicSpec",
    "DOMAIN_SUBTOPIC_SCHEMAS",
    "RawProseBlock",
    "RoutedSubtopicBucket",
    "sanitize_clinical_prose",
    "prune_solar_sentences",
    "route_and_prune_subtopics",
    "route_subtopic_blocks",
]


# --- Phase 5 Lexical & Singular/Plural Subtopic Clustering ---
import re as _re
from core.topic_filter import filter_topics as _filter_topics

_SUBTOPIC_NORM_RE = _re.compile(r"[^a-z0-9\s]+")


def normalize_subtopic_stem(subtopic: str) -> str:
    cleaned = _SUBTOPIC_NORM_RE.sub(" ", (subtopic or "").lower()).strip()
    tokens = []
    for tok in cleaned.split():
        if len(tok) > 4 and tok.endswith("oses"):
            tok = tok[:-4] + "osis"
        elif len(tok) > 4 and tok.endswith("ies"):
            tok = tok[:-3] + "y"
        elif len(tok) > 3 and tok.endswith("es") and not tok.endswith(("ses", "xes", "zes")):
            tok = tok[:-1]
        elif len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
            tok = tok[:-1]
        tokens.append(tok)
    return " ".join(tokens)


def deduplicate_subtopics(subtopics: list[str]) -> list[str]:
    """Group near-duplicate singular/plural and punctuation variants into canonical subtopics."""
    valid = _filter_topics(subtopics)
    seen_stems: dict[str, str] = {}
    for item in valid:
        stem = normalize_subtopic_stem(item)
        if stem and stem not in seen_stems:
            seen_stems[stem] = item.strip()
    return list(seen_stems.values())
