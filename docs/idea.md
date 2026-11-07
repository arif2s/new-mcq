# `idea.md` — Question-Anchored Medical Subtopic Distillation & Micro-Budgeted Multi-Source Synthesis Blueprint

**Target Modules**:
1. `agents/tema_q/subtopic_router.py` (**NEW MODULE**: Pure-Prose Sanitizer, 7-Domain Medical Subtopic Ontology, Question-Anchored ONNX+Lexical Paragraph Router & "Solar" Sentence Filter)
2. `agents/tema_q/prompts.py` (**UPDATE**: Telegraphic Pass 1 Compression Prompt + Pass 2 Delta Micro-Editing Prompt)

3. `agents/tema_q/auditor.py` (**UPDATE**: Asymmetric Containment Subsumption, Numeric/Staging Upgrade Micro-Editor & Hard 500 / 1,000-Word Budget Enforcer)
4. `agents/tema_q/pipeline.py` (**UPDATE**: Subtopic-Bucketed Synthesis Loop, Indra Pure-Text PDF Integration & Zero-LLM-Gate Fast Pre-Filtering)
5. `agents/tema_q/topic_extractor.py` (**UPDATE**: Fine-Grained Subtopic Domain Classifier `classify_medical_domain` extending `semantic_category`)
6. `parsers/embedding_engine.py` (**UPDATE**: Single-string guard on `embed()` + Batched Question-Paragraph Cosine Matrix Scorer)

**Hardware & Runtime Invariants**:

* **Invariant 1 (Zero VRAM in Backend)**: `os.environ["CUDA_VISIBLE_DEVICES"] = ""` across all backend workers; reuses the singleton `get_embedding_engine()` ONNX session (`< 500 MB` RAM).
* **Invariant 4 (Hard Token & Word Caps)**:
* **Primary Source Cap ($B_1$)**: $\le 500\text{ words}$ per subtopic after Pass 1 (`primary_max_words = 500`).
* **Combined Multi-Source Cap ($B_{\text{total}}$)**: $\le 1,000\text{ words}$ per subtopic across all sources combined (`max_words = 1000`).
* **LM Studio Context Cap**: Prompt + output strictly $\le 4,096\text{ tokens}$ (`MAX_CONTEXT_TOKENS`).



---

## 1. System Architecture & Why Hybrid Compression + Micro-Editing Wins

When synthesizing notes across **ZIM articles**, **PDF textbook/guideline pages** (via `IndraModularEngine` / `docling_v2`), and **Obsidian Markdown notes**, full-article synthesis fails because:

1. Reference sources contain tables, figure captions, image tags, and off-topic boxes that pollute the context window.
2. Two articles on the same topic frequently use **different headings** (e.g., Article 1 uses `"Pathogenesis"` while Article 2 uses `"Mechanism of Injury"`, or an Obsidian note has no subheadings at all).
3. Sending a 2,500-word subtopic to a 4B/7B model in LM Studio for sequential `DELETE` micro-editing is slow and brittle, whereas **CPU Question-Anchored Sentence Pruning** + **Pass 1 Telegraphic Bulletization** ($\le 450\text{ words}$) + **Pass 2 Delta Micro-Editing** ($\le 1,000\text{ words}$ combined) guarantees compact, zero-duplication clinical notes.

### The 5-Stage Subtopic Distillation Funnel

$$\text{Raw Sources } \{D_1, \dots, D_K\} \xrightarrow{\text{Stage 1}} \text{Pure Prose Paragraphs} \xrightarrow{\text{Stage 2}} \text{Domain \& Subtopic Questions } \{Q_m\} \xrightarrow{\text{Stage 3}} \text{QA-Aligned Subtopic Buckets} \xrightarrow{\text{Stage 4}} \text{Pass 1 Bulletization } (\le 500\text{w}) \xrightarrow{\text{Stage 5}} \text{Pass 2+ Delta Micro-Edit } (\le 1000\text{w})$$

1. **Stage 1 — Pure-Prose Extraction & Visual Noise Stripping (`sanitize_clinical_prose`)**:
* Strips Markdown/HTML tables, ASCII grids, `![alt](img)` tags, `<figure>`/`<img>`/`<table>` blocks, `[Figure 1]`/`[Table 2]` callouts, and `> *Figure...*` caption blocks.
* When `indra` PDF parser is active, extracts strictly `el.role in ("heading", "paragraph", "list_item")` where `not el.in_table and not el.in_figure and not el.is_suppressed_furniture`.


2. **Stage 2 — 7-Domain Medical Ontology & Canonical Question Generator (`classify_medical_domain`)**:
* Extends `topic_extractor.py` (`disease`, `microorganism`, `drug_chemical`, `anatomy`, `procedure`, plus fine-grained detection for `pathology`, `medicolegal`, and `public_health`) to generate canonical subtopics and **Synthetic Clinical Anchor Questions** for the target entity $E$:
* *Microorganism* ($E = \text{"C. tetani"}$): `"Microscopy & Staining"` $\to$ *"What is the Gram reaction, staining procedure, microscopic morphology, and spore appearance of C. tetani?"*
* *Public Health / Vector-Borne* ($E = \text{"Malaria"}$ or $\text{"Dengue"}$): `"Transmission & Vector"` $\to$ *"What is the vector, reservoir, incubation period, and mode of transmission of Malaria?"*; `"Prevention & Control"` $\to$ *"What is the primary prevention, chemoprophylaxis, vaccine, and vector control for Dengue?"*
* *Pathology* ($E = \text{"Amyloidosis"}$): `"Morphology (Gross & Microscopic)"` $\to$ *"What is the gross morphology, histopathology, special stain (Congo red), and microscopy of Amyloidosis?"*




3. **Stage 3 — Hybrid Heading + QA-Transformer Paragraph Router & "Solar" Sentence Pruning**:
* Every paragraph $P_i$ is scored against each canonical subtopic $m \in \{1, \dots, M\}$ using a **Hybrid Relevance Score**:

$$R(P_i, m) = \alpha \cdot S_{\text{heading}}(H(P_i), m) + \beta \cdot \cos\!\big(\mathbf{e}(P_i), \mathbf{e}(Q_m)\big) + \gamma \cdot S_{\text{lex}}(P_i, A_m)$$



where $\mathbf{e}(\cdot)$ is the L2-normalized 384/768-d vector from `get_embedding_engine()` and $A_m$ is the expected answer-type lexicon for subtopic $m$ (e.g., `{"gram-positive", "acid-fast", "ziehl-neelsen", "drumstick", "motile", "spore"}` for *Microscopy & Staining*).
* **"Solar" (Stray/Outlier) Sentence Filter**: Within each matched paragraph, individual sentences $s_{i,j}$ are scored against $Q_m$. Any sentence whose cosine + lexical relevance falls below $\tau_{\text{solar}} = 0.22$ (and lacks numerical/dosage tokens) is a stray/"solar" sentence and is **pruned on CPU before touching LM Studio**.


4. **Stage 4 — Pass 1 Primary Source Telegraphic Compression ($\le 450\text{--}500\text{ Words}$)**:
* For each active subtopic bucket, Source 1's top-ranked sentences (capped at $\le 650\text{ words}$ input) are sent to LM Studio with `PASS1_SUBTOPIC_BULLET_PROMPT` to produce high-density telegraphic clinical points capped at $\le 500\text{ words}$.


5. **Stage 5 — Pass 2+ Novelty-Only Pre-Filter & Tema_Q Micro-Editing ($\le 1,000\text{ Words Combined}$)**:
* For Secondary Sources $D_2, \dots, D_K$, candidate sentences in subtopic $m$ are first checked on CPU against the existing Pass 1 `clinical_points`.
* Any sentence already covered ($\text{Containment} \ge 0.78$ and no new numbers/dosages) is dropped on CPU.
* Only **net-new or numerically divergent sentences** ($\le 450\text{ words}$) are sent to LM Studio (`PASS2_MICRO_EDIT_SYSTEM_PROMPT`) and merged via `auditor.merge_json_nodes(..., max_words=1000)`, which upgrades existing bullets in-place when more specific dosages/stages are found and halts once the subtopic hits $1,000\text{ words}$.



---

## 2. Step-by-Step Implementation Plan for the AI Agent

### Task 1: Fix Single-String Embedding Guard & Add Matrix Cosine Scorer in `parsers/embedding_engine.py`

In `parsers/embedding_engine.py`:

1. Make `CPUEmbeddingEngine.embed(texts)` accept `Union[str, List[str]]` so callers like `indra_parser.py` (`embedder.embed(content)`) never iterate character-by-character over a string.
2. Add a helper `score_texts_against_anchors(texts: List[str], anchors: List[str]) -> np.ndarray` that computes the $N \times M$ cosine similarity matrix in a single batched ONNX call.

```python
# In parsers/embedding_engine.py — Update CPUEmbeddingEngine.embed and add score_texts_against_anchors:

    def embed(self, texts: Union[str, List[str]]) -> Any:
        if not texts:
            return []
        if isinstance(texts, str):
            return self.embed_single(texts)

        with self._lock:
            if self.session is None or self.tokenizer is None:
                if not self._load_model():
                    return []

            try:
                clean_texts = [str(t).strip() or " " for t in texts]
                all_results: List[List[float]] = []
                for start_idx in range(0, len(clean_texts), self._BATCH_SIZE):
                    batch = clean_texts[start_idx : start_idx + self._BATCH_SIZE]
                    all_results.extend(self._embed_batch(batch))
                return all_results
            except Exception as exc:
                logger.error("Failed to compute embeddings: %s", exc)
                return []

    def score_texts_against_anchors(
        self, texts: List[str], anchors: List[str]
    ) -> Optional[np.ndarray]:
        """
        Computes an (N_texts x M_anchors) cosine similarity matrix in a single batched pass.
        Returns None if offline ONNX model is unavailable.
        """
        if not texts or not anchors:
            return None
        combined = list(texts) + list(anchors)
        vecs = self.embed(combined)
        if not vecs or len(vecs) != len(combined):
            return None
        mat = np.asarray(vecs, dtype=np.float32)
        text_mat = mat[: len(texts)]
        anchor_mat = mat[len(texts) :]
        return np.matmul(text_mat, anchor_mat.T)

```

---

### Task 2: Add Fine-Grained Domain Classifier `classify_medical_domain` to `agents/tema_q/topic_extractor.py`

In `agents/tema_q/topic_extractor.py`, `_normalize_semantic_category` restricts the SQLite schema column to `{"disease", "microorganism", "anatomy", "drug_chemical", "procedure", "unknown"}`. Keep `_normalize_semantic_category` intact for DB compatibility, and add a new exported helper `classify_medical_domain(topic: str, context_text: str = "") -> str` that resolves the 7 specialized synthesis domains (`"disease"`, `"microorganism"`, `"drug_chemical"`, `"anatomy"`, `"pathology"`, `"medicolegal"`, `"public_health"`):

```python
# In agents/tema_q/topic_extractor.py — Add before __all__ and export in __all__:

_MEDICOLEGAL_KEYWORDS: Set[str] = {
    "ipc", "bns", "crpc", "bnss", "iea", "bsa", "section", "autopsy", "postmortem",
    "rigor mortis", "algor mortis", "livor mortis", "cadaveric", "putrefaction",
    "mummification", "adipocere", "drowning", "hanging", "strangulation", "throttling",
    "smothering", "gagging", "choking", "burking", "infanticide", "battered baby",
    "virginity", "defloration", "pregnancy", "abortion", "impotence", "sterility",
    "inquest", "subpoena", "perjury", "dying declaration", "dying deposition",
    "consent", "malpractice", "negligence", "res ipsa loquitur", "euthanasia",
    "firearm", "rifling", "choking", "tattooing", "blackening", "abrasion",
    "contusion", "bruise", "laceration", "incised wound", "stab wound", "hesitation cuts",
    "defense wounds", "pugilistic", "heat hematoma", "antemortem", "postmortem",
    "thanatology", "forensic", "medicolegal", "testamentary", "mcnaughten",
}

_PUBLIC_HEALTH_KEYWORDS: Set[str] = {
    "epidemiology", "incidence", "prevalence", "mortality rate", "morbidity",
    "case fatality", "odds ratio", "relative risk", "attributable risk",
    "sensitivity", "specificity", "predictive value", "screening", "surveillance",
    "outbreak", "epidemic", "pandemic", "endemic", "sporadic", "zoonosis",
    "herd immunity", "incubation period", "serial interval", "generation time",
    "secondary attack rate", "primary prevention", "secondary prevention",
    "tertiary prevention", "primordial prevention", "immunization", "vaccine",
    "cold chain", "vvm", "national health mission", "nhm", "rntcp", "ntep",
    "nvbdcp", "icds", "asha", "anganwadi", "phc", "chc", "subcentre",
    "demographic cycle", "fertility rate", "birth rate", "death rate", "imr", "mmr",
    "water purification", "chlorination", "horrock", "air pollution", "waste management",
    "biomedical waste", "vector control", "sanitation", "quarantine", "isolation",
}

_PATHOLOGY_MORPHOLOGY_KEYWORDS: Set[str] = {
    "amyloidosis", "necrosis", "apoptosis", "calcification", "hypertrophy",
    "hyperplasia", "metaplasia", "dysplasia", "anaplasia", "hamartoma",
    "choristoma", "granuloma", "infarction", "thrombosis", "embolism",
    "biopsy", "histopathology", "microscopy", "gross morphology", "congo red",
    "birbeck", "psammoma", "reed-sternberg", "aschoff", "councilman", "mallory",
    "negri", "lewy", "neurofibrillary", "senile plaque", "russell body",
}


def classify_medical_domain(topic: str, context_text: str = "") -> str:
    """
    Classifies a clinical topic into one of 7 specialized subtopic domains:
    'medicolegal', 'public_health', 'microorganism', 'drug_chemical',
    'anatomy', 'pathology', or 'disease'.
    """
    combined = f"{topic or ''} {context_text or ''}".strip().lower()
    t_low = (topic or "").strip().lower()
    if not t_low:
        return "disease"

    # 1. Medicolegal / Forensic Medicine
    if any(_contains_phrase_bounded(combined, kw) for kw in _MEDICOLEGAL_KEYWORDS):
        if any(_contains_phrase_bounded(t_low, kw) for kw in _MEDICOLEGAL_KEYWORDS) or "forensic" in combined:
            return "medicolegal"

    # 2. Public Health / Preventive & Social Medicine (PSM)
    if any(_contains_phrase_bounded(t_low, kw) for kw in _PUBLIC_HEALTH_KEYWORDS):
        return "public_health"

    # 3. Microorganism (Bacteria, Viruses, Fungi, Parasites)
    if (
        any(g in t_low for g in KNOWN_GENERA)
        or any(v in t_low for v in VIRAL_PARASITIC_ENTITIES)
        or bool(ABBREV_SPECIES_REGEX.search(topic or ""))
        or any(w in t_low for w in ("bacillus", "bacilli", "coccus", "cocci", "virus", "parasite", "fungus", "protozoa", "helminth"))
    ):
        return "microorganism"

    # 4. Pharmacology / Drugs / Toxicology
    if (
        t_low in KNOWN_DRUGS
        or any(t_low.endswith(sfx) for sfx in PHARMA_SUFFIXES)
        or any(w in t_low for w in ("poisoning", "toxicity", "overdose", "antidote", "pharmacokinetics"))
    ):
        return "drug_chemical"

    # 5. Anatomy / Embryology
    ont = _resolve_clinical_concept(t_low)
    if ont.get("semantic_category") == "anatomy" or any(
        t_low.endswith(w)
        for w in ("nerve", "artery", "vein", "muscle", "ligament", "foramen", "fossa", "canal", "triangle", "plexus", "ganglion", "sinus", "fasci", "bone", "joint")
    ):
        return "anatomy"

    # 6. General / Systemic Pathology processes
    if any(_contains_phrase_bounded(t_low, kw) for kw in _PATHOLOGY_MORPHOLOGY_KEYWORDS):
        return "pathology"

    # 7. Default to Clinical Disease / Syndrome / Procedure
    return "disease"

```

---

### Task 3: Create `agents/tema_q/subtopic_router.py` (Pure-Prose Sanitizer, 7-Domain Subtopic Schemas, Question-Anchored Router & "Solar" Sentence Filter)

Create the new module `agents/tema_q/subtopic_router.py`. This module performs:

1. **`sanitize_clinical_prose(raw_text)`**: Removes all Markdown/HTML tables, ASCII box tables, `![...](...)` images, `<figure>`/`<img>` tags, and image/figure/table captions (`Figure 1:`, `Fig. 2:`, `Table 3:`).
2. **`DOMAIN_SUBTOPIC_SCHEMAS`**: Defines the canonical subtopics, heading regexes, answer-type signal lexicons, and dynamic question templates for all 7 medical domains (`disease`, `microorganism`, `drug_chemical`, `anatomy`, `pathology`, `medicolegal`, `public_health`).
3. **`route_and_prune_subtopics(topic, documents, mcq_context, max_source1_input_words=650, max_sec_input_words=450)`**:
* Splits each document into pure-prose sections/paragraphs.
* Aligns paragraphs across different article headings to the canonical subtopics using **Heading Match + Batched ONNX Question-Paragraph Cosine Similarity + Lexical Answer-Type Density**.
* Splits matched paragraphs into sentences and **prunes "solar" (stray/off-topic) sentences** whose relevance score to the subtopic question is below threshold (`< 0.22`), ensuring only laser-focused sentences enter Pass 1 and Pass 2.



```python
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
_OBSIDIAN_EMBED_RE = re.compile(r"^\s*!\[\[.*?(?:\.png\vert{}\.jpg\vert{}\.jpeg\vert{}\.gif\vert{}\.svg\vert{}\.webp\vert{}\.pdf).*?\]\]\s*$", re.IGNORECASE)
_MD_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_ASCII_GRID_ROW_RE = re.compile(r"^\s*\+[-=+:]+\+\s*$")
_CAPTION_LINE_RE = re.compile(
    r"^\s*(?:>\s*)?\*?(?:Figure|Fig\.|Table|Tab\.|Box|Chart|Plate|Scheme|Diagram|Photograph|Photomicrograph)\s+"
    r"(?:S?\d+[A-Za-z0-9.\-]*|[IVXLCDM]+)\s*[:.\-–—]",
    re.IGNORECASE,
)
_INLINE_FIG_REF_RE = re.compile(
    r"\(\s*(?:see\s+)?(?:Figures?\vert{}Figs?\.?\vert{}Tables?\vert{}Tabs?\.?\vert{}Boxes\vert{}Plates?)\s+[\dA-Za-z.\-,\s]+\)",
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
        if stripped.startswith("
```"):
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
            continue

        # Drop tab-separated or multi-pipe pseudo-table lines
        if stripped.count("|") >= 2 or stripped.count("\t") >= 2:
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
            ("autopsy", "postmortem", "antemortem", "ligature", "furrow", "parchment", "hyoid", "thyroid cartilage", "petechiae", "tardieu", "froth", "diatom", "getler", "paltauf", "carotid"),
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
        src_map = routed[m_i]
        if not src_map:
            continue

        q_anchor = questions[m_i]
        # Boost subtopic priority if the active MCQ question directly asks about this subtopic
        mcq_boost = 0.25 if (mcq_q and spec.heading_pattern.search(mcq_q)) else 0.0
        bucket_max_sc = max(sc for s_list in src_map.values() for sc, _ in s_list) + mcq_boost

        # Anchor on the earliest source that has content for this subtopic
        sorted_srcs = sorted(src_map.keys())
        primary_src_idx = sorted_srcs[0]
        primary_blocks = [b for _, b in sorted(src_map[primary_src_idx], key=lambda x: x[0], reverse=True)]
        primary_sents: List[str] = []
        for pb in primary_blocks:
            primary_sents.extend(_SENTENCE_SPLIT_RE.split(pb.text))

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
            primary_source_label=primary_blocks[0].source_label,
            primary_page=primary_blocks[0].page,
        )

        # Secondary sources for this exact subtopic (even if originally under a different heading!)
        for sec_src_idx in sorted_srcs[1:]:
            sec_blocks = [b for _, b in sorted(src_map[sec_src_idx], key=lambda x: x[0], reverse=True)]
            sec_sents: List[str] = []
            for sb in sec_blocks:
                sec_sents.extend(_SENTENCE_SPLIT_RE.split(sb.text))
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
                        "source_label": sec_blocks[0].source_label,
                        "page": sec_blocks[0].page,
                    }
                )

        buckets.append((bucket_max_sc, bucket))

    buckets.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in buckets[:max_subtopics]]


__all__ = [
    "SubtopicSpec",
    "DOMAIN_SUBTOPIC_SCHEMAS",
    "RawProseBlock",
    "RoutedSubtopicBucket",
    "sanitize_clinical_prose",
    "prune_solar_sentences",
    "route_and_prune_subtopics",
]

```

---

### Task 4: Upgrade `agents/tema_q/prompts.py` with Telegraphic Bulletization & Delta Micro-Editing Prompts

In `agents/tema_q/prompts.py`, update `PASS1_CLINICAL_SYSTEM_PROMPT` and `PASS2_AUDIT_SYSTEM_PROMPT` so 4B/7B models in LM Studio compress primary prose into telegraphic medical points ($\le 450\text{ words}$) and perform strict **Delta Micro-Editing** in Pass 2 (emitting only net-new facts or numeric/staging upgrades):

```python
# In agents/tema_q/prompts.py — Replace PASS1_CLINICAL_SYSTEM_PROMPT and PASS2_AUDIT_SYSTEM_PROMPT:

PASS1_CLINICAL_SYSTEM_PROMPT: str = (
    "You are an expert clinical medical educator. Convert the provided reference into a compact, "
    "high-yield set of telegraphic clinical bullet points strictly answering the target subtopic.\n"
    "Rules:\n"
    "1. Strip all conversational filler, historical anecdotes, figure/table mentions, and off-subtopic sentences.\n"
    "2. Preserve 100% of numerical values, drug dosages, staging cutoffs, vectors, staining procedures, microscopy findings, and classic triads.\n"
    "3. Keep each point concise (<= 25 words per point) and total output strictly <= 450 words.\n"
    "Respond ONLY with a valid JSON object matching this schema:\n"
    '{"section": "string", "subtopic": "string", "conditional_context": "string", '
    '"clinical_points": [{"point": "string", "source": "string", "page": 1}], "discrepancies": ""}'
    "\nDo not wrap the JSON in markdown blocks. Do not include introductory conversational filler."
)

PASS2_AUDIT_SYSTEM_PROMPT: str = (
    "You are a clinical medical micro-editor and variance auditor for '{subtopic}'.\n"
    "Compare the Secondary Reference against the existing Primary Baseline Points:\n"
    "1. DO NOT repeat facts already present in the Primary Baseline Points.\n"
    "2. Extract ONLY net-new clinical facts, missing diagnostic/staining/vector details, or more specific numeric dosage/staging thresholds.\n"
    "3. If drug dosages, staging criteria, or diagnostic cutoffs conflict with the Primary Baseline, record the exact conflict inside 'discrepancies'.\n"
    "Respond ONLY with a valid JSON object matching this schema:\n"
    '{"section": "string", "subtopic": "string", "conditional_context": "string", '
    '"clinical_points": [{"point": "string", "source": "string", "page": 1}], '
    '"discrepancies": "string"}'
    "\nDo not wrap the JSON in markdown blocks. Do not include introductory conversational filler."
)

```

---

### Task 5: Upgrade `agents/tema_q/auditor.py` with Pre-LLM Novelty Filtering, In-Place Numeric Micro-Upgrades & Hard 500 / 1,000-Word Caps

In `agents/tema_q/auditor.py`:

1. Add `filter_novel_secondary_sentences(secondary_text: str, base_points: List[Dict[str, Any]], max_words: int = 400) -> str`:
* Splits `secondary_text` into sentences on CPU and checks each sentence against `base_points`.
* If a secondary sentence has $\ge 75\%$ token containment in an existing base point **and** introduces no new numeric dosage (`_NUMERIC_DOSAGE_PATTERN`) or staging value (`_STAGING_PATTERN`), it is dropped **before** calling LM Studio! If 100% of sentences in `secondary_text` are already covered, `pipeline.py` skips the Pass 2 LLM call for that source altogether!


2. Upgrade `merge_json_nodes` with `max_words: int = 1000` and `primary_max_words: int = 500`:
* Enforces $\le 500\text{ words}$ when initializing the primary node (`not base`) and $\le 1,000\text{ words}$ when merging secondary nodes.
* Implements **In-Place Micro-Edit Replacement**: When a secondary point covers the same clinical stem as an existing base point ($\text{Containment}(T_{\text{base}}, T_{\text{sec}}) \ge 0.80$) and contains more specific numeric dosages or staging criteria (`_NUMERIC_DOSAGE_PATTERN` / `_STAGING_PATTERN`), it **replaces** the shorter base point in-place rather than duplicating it.



```python
# In agents/tema_q/auditor.py — Add helper functions and update merge_json_nodes:

def _count_words(text: str) -> int:
    return len((text or "").split())


def _token_containment(short_norm: str, long_norm: str) -> float:
    """Computes fraction of tokens in short_norm that are present in long_norm."""
    t_short = {w for w in short_norm.split() if len(w) >= 3}
    t_long = {w for w in long_norm.split() if len(w) >= 3}
    if not t_short or not t_long:
        return 0.0
    return len(t_short & t_long) / len(t_short)


def filter_novel_secondary_sentences(
    secondary_text: str,
    base_points: List[Dict[str, Any]],
    max_words: int = 400,
) -> str:
    """
    CPU pre-filter for Tema_Q Pass 2: strips sentences from secondary_text that are
    already covered by base_points unless they contain novel numeric dosages or staging values.
    """
    if not secondary_text or not secondary_text.strip():
        return ""
    if not base_points:
        words = secondary_text.split()
        return " ".join(words[:max_words])

    base_norms = [_normalize_text(pt.get("point", "")) for pt in base_points if pt.get("point")]
    base_combined = " ".join(pt.get("point", "") for pt in base_points if pt.get("point"))
    base_nums = {val.lower() for val, _ in _NUMERIC_DOSAGE_PATTERN.findall(base_combined)}

    sents = re.split(r"(?<=[.!?])\s+", secondary_text.strip())
    novel_sents: List[str] = []
    used_words = 0

    for s in sents:
        s_clean = s.strip()
        if not s_clean:
            continue
        s_norm = _normalize_text(s_clean)
        if not s_norm:
            continue

        s_nums = {val.lower() for val, _ in _NUMERIC_DOSAGE_PATTERN.findall(s_clean)}
        has_new_numbers = bool(s_nums - base_nums) or bool(_VARIANCE_PATTERN.search(s_clean))

        is_covered = any(
            _calculate_token_jaccard(s_norm, bn) >= 0.72 or _token_containment(s_norm, bn) >= 0.80
            for bn in base_norms
        )
        if is_covered and not has_new_numbers:
            continue

        w_cnt = len(s_clean.split())
        if used_words + w_cnt > max_words and novel_sents:
            break
        novel_sents.append(s_clean)
        used_words += w_cnt

    return " ".join(novel_sents).strip()


def _enforce_points_word_budget(
    points: List[Dict[str, Any]], max_words: int
) -> List[Dict[str, Any]]:
    """Bounds clinical_points to max_words while prioritizing numeric/staging bullets."""
    bounded: List[Dict[str, Any]] = []
    running_words = 0
    for pt in points:
        txt = str(pt.get("point") or "").strip()
        if not txt:
            continue
        w_cnt = _count_words(txt)
        if running_words + w_cnt <= max_words:
            bounded.append(pt)
            running_words += w_cnt
        else:
            remaining = max_words - running_words
            if remaining >= 12:
                truncated_txt = " ".join(txt.split()[:remaining]).rstrip(".,;:") + "."
                pt_copy = dict(pt)
                pt_copy["point"] = truncated_txt
                bounded.append(pt_copy)
            break
    return bounded

```

And inside `merge_json_nodes` in `agents/tema_q/auditor.py`, add `max_words: int = 1000, primary_max_words: int = 500` to the signature:

* When `not base`: cap `merged["clinical_points"] = _enforce_points_word_budget(pts, primary_max_words)`.
* During the secondary point merge loop: if `_token_containment(ex_norm, norm_text) >= 0.80` and `bool(_NUMERIC_DOSAGE_PATTERN.search(pt_text)) and not bool(_NUMERIC_DOSAGE_PATTERN.search(ex_pt["point"]))`, **upgrade the existing point in-place** (`ex_pt["point"] = pt_text; ex_pt["source"] = f"{ex_pt['source']} + {meta_source}"`).
* At the end of `merge_json_nodes`: cap `merged["clinical_points"] = _enforce_points_word_budget(merged["clinical_points"], max_words)` so no subtopic ever exceeds `1,000` words!

---

### Task 6: Upgrade `agents/tema_q/pipeline.py` to Run Subtopic-Bucketed Synthesis & Support `indra` Pure-Text Extraction

In `agents/tema_q/pipeline.py`:

1. **Add `"indra"` Pure-Prose Extraction to `_extract_pdf_sync` (`lines 189–206`)**:
When `parser == "indra"` (or as the primary structured extractor), call `extract_page_with_indra(file_path, page)` and extract only non-table, non-caption elements (`e["role"] != "figure_caption"`):

```python
   def _extract_pdf_sync(file_path: str, page: int) -> str:
       parser = _get_pdf_parser_setting()
       if parser == "indra":
           try:
               from parsers.indra_parser import extract_page_with_indra
               res = extract_page_with_indra(file_path, page_number=page)
               lines = res.get("lines") or []
               if lines:
                   prose_lines = [
                       f"{'#' * int(ln.get('heading_level') or 2)} {ln['text']}"
                       if ln.get("role") == "heading"
                       else str(ln.get("text") or "")
                       for ln in lines
                       if ln.get("role") not in ("furniture", "figure_caption")
                   ]
                   if prose_lines:
                       return "\n".join(prose_lines)
           except Exception as exc:
               logger.warning("Indra extraction fallback: %s", exc)
       # Keep existing docling_v2 / docling / extract_page_slice fallbacks...


```

2. **Upgrade `run_tema_q_synthesis` (`lines 346–568`) to Use `route_and_prune_subtopics` & `filter_novel_secondary_sentences**`:
* Replace the slow per-document LLM `"YES/NO"` gate with `subtopic_buckets = await asyncio.to_thread(route_and_prune_subtopics, topic, documents, mcq_context)`.
* For each `bucket` in `subtopic_buckets`:
* **Pass 1 (Primary Source $\le 500$ words)**: Send `bucket.primary_text` ($\le 650$ words of pure, question-matched sentences) to `call_llm_async(PASS1_CLINICAL_SYSTEM_PROMPT, ...)` with `Target Subtopic: {bucket.display_name} ({bucket.question_anchor})` and merge via `merge_json_nodes(None, base_note_json, metadata=..., primary_max_words=500)`.
* **Pass 2+ (Secondary Sources $\le 1,000$ words combined)**:
* Run `novel_sec_text = filter_novel_secondary_sentences(sec_entry["text"], subtopic_node.get("clinical_points", []), max_words=400)` on CPU.
* If `novel_sec_text` is empty (all facts already covered in Pass 1), **skip the LM Studio call entirely**!
* Otherwise, pass the existing Pass 1 baseline points + `novel_sec_text` to `call_llm_async(pass2_sys_prompt, ...)` and merge via `merge_json_nodes(subtopic_node, sec_json, metadata=..., max_words=1000)`.




* Combine all synthesized subtopic nodes into `final_article` (preserving backward-compatible keys `section`, `subtopic`, `conditional_context`, `clinical_points`, `subtopics`, and `discrepancies`) so `_format_article_as_markdown(final_article, topic)` renders clean `### {Subtopic Display Name}` headers followed by cited telegraphic bullets!



---

## 3. Verification & Unit Test Suite (`tests/test_subtopic_synthesis.py`)

Have your AI agent create and run `tests/test_subtopic_synthesis.py` after applying Tasks 1–6:

```python
from agents.tema_q.auditor import filter_novel_secondary_sentences, merge_json_nodes
from agents.tema_q.subtopic_router import route_and_prune_subtopics, sanitize_clinical_prose
from agents.tema_q.topic_extractor import classify_medical_domain


def test_pure_prose_sanitizer_removes_tables_and_captions():
    raw = (
        "# Amyloidosis\n"
        "![Congo red](img01.png)\n"
        "> *Figure 1: Apple-green birefringence under polarized light.*\n"
        "| Type | Precursor | Organ |\n"
        "| --- | --- | --- |\n"
        "| AL | Light chain | Kidney |\n"
        "Amyloidosis shows extracellular deposition of fibrillar proteins with beta-pleated sheet configuration (see Table 1).\n"
    )
    cleaned = sanitize_clinical_prose(raw)
    assert "img01.png" not in cleaned
    assert "Figure 1:" not in cleaned
    assert "| AL |" not in cleaned
    assert "(see Table 1)" not in cleaned
    assert "beta-pleated sheet configuration" in cleaned


def test_seven_domain_classification():
    assert classify_medical_domain("Clostridium tetani") == "microorganism"
    assert classify_medical_domain("Amoxicillin") == "drug_chemical"
    assert classify_medical_domain("Amyloidosis") == "pathology"
    assert classify_medical_domain("Brachial plexus") == "anatomy"
    assert classify_medical_domain("Rigor mortis", "Forensic autopsy time since death") == "medicolegal"
    assert classify_medical_domain("Primary prevention", "National immunization schedule") == "public_health"
    assert classify_medical_domain("Myocardial infarction") == "disease"


def test_cross_article_mismatched_headings_route_to_same_subtopic():
    docs = [
        {
            "source_type": "pdf",
            "source_label": "Harrison",
            "page": 12,
            "raw_text": (
                "# Malaria\n"
                "## Etiology and Transmission\n"
                "Malaria is transmitted by the bite of female Anopheles mosquito carrying Plasmodium sporozoites.\n"
            ),
        },
        {
            "source_type": "zim",
            "source_label": "ZIM: Malaria",
            "page": 1,
            "raw_text": (
                "# Malaria\n"
                "## Vector Lifecycle\n"
                "The definitive host and vector for malaria is the female Anopheles mosquito, which injects sporozoites during a blood meal.\n"
            ),
        },
    ]
    buckets = route_and_prune_subtopics("Malaria", docs)
    etiol_buckets = [b for b in buckets if b.subtopic_id == "etiology_transmission"]
    assert len(etiol_buckets) == 1
    assert "Anopheles" in etiol_buckets[0].primary_text
    assert len(etiol_buckets[0].secondary_entries) == 1


def test_500_and_1000_word_budgets_enforced_in_merge():
    long_pt_1 = " ".join(["clinical_token"] * 300)
    long_pt_2 = " ".join(["diagnostic_token"] * 300)
    base = merge_json_nodes(
        None,
        {"subtopic": "Malaria", "clinical_points": [long_pt_1, long_pt_2]},
        metadata={"source": "Doc1", "page": 1},
        primary_max_words=500,
    )
    base_words = sum(len(p["point"].split()) for p in base["clinical_points"])
    assert base_words <= 500

    sec_pt_1 = " ".join(["treatment_token"] * 350)
    sec_pt_2 = " ".join(["prophylaxis_token"] * 350)
    merged = merge_json_nodes(
        base,
        {"subtopic": "Malaria", "clinical_points": [sec_pt_1, sec_pt_2]},
        metadata={"source": "Doc2", "page": 2},
        max_words=1000,
    )
    total_words = sum(len(p["point"].split()) for p in merged["clinical_points"])
    assert total_words <= 1000

```
