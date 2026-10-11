# Expanded stop sequences to support all common 4B/7B architectures (Qwen, Llama 3, Mistral, Gemma, Phi)
# Note: Removed "### Discrepancies" to prevent halting Pass 2 before variances are generated
STOP_TOKENS = [
    "<|im_end|>",       # ChatML / Qwen
    "<|eot_id|>",       # Llama 3 / 3.1 / 3.2
    "</s>",             # Mistral / Llama 2
    "<end_of_turn>",    # Gemma
    "<|end|>",          # Phi-3 / Phi-4
    "### Source",       # RAG delimiter guard
    "### Reference",    # RAG delimiter guard
]

PASS1_CLINICAL_SYSTEM_PROMPT = (
    "You are an expert clinical surgical educator. Write a concise, high-yield clinical synopsis "
    "based strictly on the provided reference. Focus on diagnostic criteria, anatomical "
    "considerations, operative thresholds, and material specifications. "
    "Respond ONLY with a valid JSON object matching this schema:\n"
    '{"section": "string", "subtopic": "string", "conditional_context": "string", "clinical_points": [{"point": "string", "source": "string", "page": 1}]}'
    "\nDo not wrap the JSON in markdown blocks. Do not include introductory conversational filler."
)

PASS2_AUDIT_SYSTEM_PROMPT = (
    "Merge these clinical points for '{subtopic}'. Retain all conditions. "
    "If drug dosages or diagnostic cutoffs conflict, output a flag labeled '### Discrepancies & Variances'. "
    "Do not hallucinate. Output the merged points as a JSON array of objects matching: "
    '[{"point": "string", "source": "string", "page": 1}]'
)

DISTRACTOR_ANALYSIS_PROMPT = (
    "You are a medical board examiner. Succinctly evaluate the multiple-choice question against the synthesis.\n"
    "1. State in 1-2 sentences why the correct option is clinically accurate.\n"
    "2. For each incorrect distractor, state in a single bullet point its precise clinical flaw or error.\n"
    "Be brief, factual, and avoid unnecessary preamble."
)

TOPIC_EXTRACTION_SYSTEM_PROMPT = (
    "You are an expert medical indexer. Extract the primary medical topics from the provided MCQ and options.\n"
    "- Extract concrete entities (conditions, anatomical sites, procedures, drugs, pathogens).\n"
    "- Exclude broad systems (e.g., 'Cardiology', 'Surgery') and test metadata.\n"
    "- Choose exactly one specific 'main_topic'.\n\n"
    "Respond ONLY with a valid, raw JSON object. Do not wrap in markdown ```json code blocks. "
    "Do not include any text before or after the JSON.\n"
    "Format:\n"
    "{\"main_topic\": \"Specific Entity\", \"all_topics\": [\"Topic 1\", \"Topic 2\"]}"
)
