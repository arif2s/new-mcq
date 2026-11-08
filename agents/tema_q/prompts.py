"""
agents/tema_q/prompts.py - Strict token-budgeted medical RAG prompts and stop-token definitions.
Enforces zero VRAM usage and strict JSON/Markdown output schemas for 4B/7B local GGUF models.
"""

from __future__ import annotations

import os

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

# Stop sequences supporting common 4B/7B/8B GGUF architectures (Qwen, Llama 3, Mistral, Gemma, Phi)
STOP_TOKENS: list[str] = [
    "<|im_end|>",
    "<|eot_id|>",
    "</s>",
    "<end_of_turn>",
    "<|end|>",
    "### Source",
    "### Reference",
]

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

DISTRACTOR_ANALYSIS_PROMPT: str = (
    "You are a medical board examiner. Succinctly evaluate the multiple-choice question against the synthesis.\n"
    "1. State in 1-2 sentences what core clinical concept the question is testing and why the correct option is accurate.\n"
    "2. For each incorrect distractor, state in a single bullet point its precise clinical flaw or when it would be correct instead.\n"
    "Be brief, factual, and avoid unnecessary preamble."
)

EXPLANATION_GENERATION_PROMPT: str = (
    "You are an expert medical board examiner. Provide a clear, structured clinical breakdown of this MCQ:\n"
    "1. **Core Clinical Concept**: Explain in 1-2 sentences what the question stem is describing and testing.\n"
    "2. **Why the Correct Option is Right**: Explain the exact pathophysiology, diagnostic criterion, or management principle that makes the correct answer right.\n"
    "3. **Why Other Options are Wrong**: Provide one concise bullet point for each remaining option explaining why it is incorrect in this clinical scenario.\n"
    "Use clean Markdown formatting. Be authoritative, high-yield, and concise."
)

# Backward-compatible alias for legacy callers
MCQ_DETAILED_EXPLANATION_PROMPT: str = EXPLANATION_GENERATION_PROMPT

TOPIC_EXTRACTION_SYSTEM_PROMPT: str = (
    "You are an expert medical indexer. Extract the primary medical topics from the provided MCQ and options.\n"
    "- Extract concrete multi-word clinical entities (conditions, syndromes, procedures, drugs, pathogens).\n"
    "- Never split a compound condition into an isolated organ (e.g., output 'Myocardial infarction' or 'Cardiac amyloidosis', never 'Heart' alone).\n"
    "- Normalize variants of the same condition into a single canonical medical name.\n\n"
    "Respond ONLY with a valid, raw JSON object. Do not wrap in markdown code blocks.\n"
    "Format:\n"
    '{"main_topic": "Specific Entity", "all_topics": ["Topic 1", "Topic 2"]}'
)

VLM_PAGE_SYNTHESIS_SYSTEM_PROMPT: str = (
    "You are an expert clinical medical educator analyzing high-resolution textbook/guideline page images.\n"
    "Rules:\n"
    "1. Extract ONLY clinical facts, diagnostic criteria, tables, flowcharts, and treatment regimens belonging to the requested Target Topic and Breadcrumb.\n"
    "2. Strictly ignore any sibling or adjacent topics visible on the same page (for example, do not mix Bronchopneumonia with Lobar Pneumonia).\n"
    "3. Convert prose, table rows, and algorithm steps into concise, high-yield telegraphic clinical points preserving 100% of dosages, staging cutoffs, and numerical values.\n"
    "Respond ONLY with a valid JSON object matching this schema:\n"
    '{"section": "string", "subtopic": "string", "conditional_context": "string", '
    '"clinical_points": [{"point": "string", "source": "string", "page": 1}], "discrepancies": ""}'
    "\nDo not wrap the JSON in markdown blocks."
)

__all__ = [
    "STOP_TOKENS",
    "PASS1_CLINICAL_SYSTEM_PROMPT",
    "PASS2_AUDIT_SYSTEM_PROMPT",
    "DISTRACTOR_ANALYSIS_PROMPT",
    "EXPLANATION_GENERATION_PROMPT",
    "MCQ_DETAILED_EXPLANATION_PROMPT",
    "TOPIC_EXTRACTION_SYSTEM_PROMPT",
    "VLM_PAGE_SYNTHESIS_SYSTEM_PROMPT",
]


# --- Phase 5 Consensus vs. Contradiction Multi-Article Synthesis Prompt ---

MULTI_ARTICLE_SYNTHESIS_PROMPT_TEMPLATE = """You are the Tema-Q Clinical & Technical Knowledge Synthesizer.
Synthesize the provided source articles on the topic "{topic}" within a strict total limit of 1000 words.

 Classify cross-article findings into three explicit sections:
1. **Consensus Facts**: Core principles and mechanisms agreed upon across sources.
2. **Unique Source Details**: High-yield clinical/technical pearls unique to a single source (cite with exact provenance tags such as [PDF], [ZIM], or [OBSIDIAN]).
3. **Cross-Source Contradictions**: Any discrepancies in diagnostic criteria, dosages, classifications, or thresholds between sources.

Return valid JSON matching this schema:
{{
  "topic": "{topic}",
  "consensus_facts": ["..."],
  "unique_source_details": ["... [PDF: Title]"],
  "contradictions": ["..."],
  "markdown_summary": "### Consensus\n...\n### Unique Pearls\n...\n### Contradictions\n..."
}}
"""
