"""
agents/tema_q/telemetry.py - Full-pipeline diagnostic telemetry & bottleneck analyzer
for the 1-2-3 Clinical RAG Synthesis Pipeline.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ["CUDA_VISIBLE_DEVICES"] = ""

from agents.tema_q.auditor import _NUMERIC_DOSAGE_PATTERN, _STAGING_PATTERN
from config import REPORTS_DIR

TRACE_LOG_DIR = Path(REPORTS_DIR) / "pipeline_traces"


def extract_clinical_numbers_and_stages(text: str) -> List[str]:
    """Extracts canonical numeric dosages and staging tokens for source-vs-LLM fidelity checks."""
    if not text:
        return []
    dosages = [
        f"{val.lower()} {unit.lower()}"
        for val, unit in _NUMERIC_DOSAGE_PATTERN.findall(text)
    ]
    stages = [
        f"{prefix.lower()} {val.lower()}"
        for prefix, val in _STAGING_PATTERN.findall(text)
    ]
    return sorted(set(dosages + stages))


@dataclass
class HeadingDispatchTrace:
    subtopic_id: str
    subtopic_name: str
    heading_breadcrumb: str
    source_label: str
    source_type: str
    page: int
    pass_number: int  # 1 = Primary, 2 = Secondary Delta
    model_id: str
    input_word_count: int
    input_token_count: int
    prompt_sent: str
    raw_llm_response: str
    parsed_points_count: int
    output_word_count: int
    json_repaired_or_fallback: bool
    latency_ms: float
    tokens_per_sec: float
    source_dosages_and_stages: List[str] = field(default_factory=list)
    retained_dosages_and_stages: List[str] = field(default_factory=list)
    missing_dosages_and_stages: List[str] = field(default_factory=list)
    retention_score: float = 1.0


@dataclass
class PipelineDiagnosticTrace:
    trace_id: str
    topic: str
    model_id: str
    started_at: float = field(default_factory=time.time)
    total_duration_ms: float = 0.0
    stage_timings_ms: Dict[str, float] = field(default_factory=dict)

    # Circle 1: Topic Extraction
    circle1_extracted_topics: Dict[str, Any] = field(default_factory=dict)

    # Circle 2: Retrieval & Resolution
    circle2_raw_hits_summary: Dict[str, int] = field(default_factory=dict)
    circle2_resolved_documents: List[Dict[str, Any]] = field(default_factory=list)

    # Circle 2.5: Heading Segmentation & Subtopic Routing
    extracted_headings_per_doc: List[Dict[str, Any]] = field(default_factory=list)
    routed_buckets_summary: List[Dict[str, Any]] = field(default_factory=list)
    solar_pruned_sentences: List[Dict[str, Any]] = field(default_factory=list)
    pass2_pre_filtered_sentences: List[Dict[str, Any]] = field(default_factory=list)

    # Circle 3: Heading-Wise LM Studio Calls & Auditor Merge
    llm_dispatches: List[HeadingDispatchTrace] = field(default_factory=list)
    auditor_merge_events: List[Dict[str, Any]] = field(default_factory=list)
    detected_variances: str = ""

    # Automated Bottleneck & Error Flags
    anomaly_flags: List[Dict[str, str]] = field(default_factory=list)

    def finalize_and_analyze(self) -> Dict[str, Any]:
        self.total_duration_ms = round((time.time() - self.started_at) * 1000.0, 2)
        self.anomaly_flags.clear()

        # 1. Check Retrieval Misses
        for src_type in ("zim", "pdf", "obsidian"):
            if self.circle2_raw_hits_summary.get(src_type, 0) == 0:
                self.anomaly_flags.append({
                    "code": f"RETRIEVAL_MISS_{src_type.upper()}",
                    "severity": "warning",
                    "detail": f"Zero hits retrieved from {src_type.upper()} source for topic '{self.topic}'.",
                })

        # 2. Check Heading Segmentation Quality
        for doc_h in self.extracted_headings_per_doc:
            if doc_h.get("headings_count", 0) <= 1 and doc_h.get("total_words", 0) > 250:
                self.anomaly_flags.append({
                    "code": "HEADING_FRAGMENTATION",
                    "severity": "high",
                    "detail": (
                        f"Source '{doc_h.get('source_label')}' ({doc_h.get('total_words')} words) "
                        f"yielded only {doc_h.get('headings_count')} heading block ('Overview'). "
                        f"Sub-headings were not detected or were stripped."
                    ),
                })

        # 3. Check LLM JSON Fallback & Dosage Omissions
        for disp in self.llm_dispatches:
            if disp.json_repaired_or_fallback:
                self.anomaly_flags.append({
                    "code": "LLM_JSON_FALLBACK",
                    "severity": "critical",
                    "detail": (
                        f"Model '{disp.model_id}' emitted malformed JSON on "
                        f"'{disp.heading_breadcrumb}' (Pass {disp.pass_number})."
                    ),
                })
            if disp.missing_dosages_and_stages:
                self.anomaly_flags.append({
                    "code": "DOSAGE_OR_STAGING_DROPPED",
                    "severity": "high",
                    "detail": (
                        f"Model '{disp.model_id}' omitted source numeric/staging tokens "
                        f"{disp.missing_dosages_and_stages} in '{disp.heading_breadcrumb}'."
                    ),
                })

        return self.to_dict()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save_to_disk(self) -> str:
        TRACE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", self.topic.lower()).strip("_")[:50] or "trace"
        model_slug = re.sub(r"[^a-z0-9]+", "_", (self.model_id or "default").lower()).strip("_")[:40]
        file_path = TRACE_LOG_DIR / f"{slug}_{model_slug}_{int(self.started_at)}.json"
        file_path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return str(file_path)
