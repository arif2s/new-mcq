from __future__ import annotations
import json
import re
from typing import Any, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)

_THINK_BLOCK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.IGNORECASE | re.DOTALL)
_CODE_FENCE_RE = re.compile(r"```(?:json|javascript|js)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


class ExtractedTopicsPayload(BaseModel):
    topics: list[str] = Field(default_factory=list)


class SubtopicCluster(BaseModel):
    canonical_subtopic: str
    variants: list[str] = Field(default_factory=list)


class SubtopicRoutePayload(BaseModel):
    clusters: list[SubtopicCluster] = Field(default_factory=list)


class SynthesizedNotePayload(BaseModel):
    topic: str
    consensus_facts: list[str] = Field(default_factory=list)
    unique_source_details: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    markdown_summary: str = ""


class MCQItemPayload(BaseModel):
    question: str
    options: list[str] = Field(default_factory=list)
    correct_answer: str
    explanation: str = ""
    subtopic: str = ""
    source_citations: list[str] = Field(default_factory=list)


class AuditVerdictPayload(BaseModel):
    stem_clarity: float = Field(default=1.0, ge=0.0, le=1.0)
    single_correct_answer: float = Field(default=1.0, ge=0.0, le=1.0)
    distractor_plausibility: float = Field(default=1.0, ge=0.0, le=1.0)
    source_grounding: float = Field(default=1.0, ge=0.0, le=1.0)
    overall_pass: bool = True
    critique: str = ""


def strip_reasoning_and_fences(raw_text: str) -> str:
    """Remove <think>...</think> blocks and markdown ```json fences from LLM output."""
    if not raw_text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", raw_text).strip()
    fence_match = _CODE_FENCE_RE.search(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    return cleaned


def extract_and_repair_json(raw_text: str) -> Any:
    """
    Deterministically extract and repair JSON objects or arrays from noisy local LLM outputs.
    Handles <think> blocks, markdown fences, trailing commas, and truncated closing brackets.
    """
    cleaned = strip_reasoning_and_fences(raw_text)
    if not cleaned:
        raise ValueError("Empty LLM completion after stripping reasoning blocks")

    # Locate outermost JSON object or array start
    first_obj = cleaned.find("{")
    first_arr = cleaned.find("[")
    if first_obj == -1 and first_arr == -1:
        raise ValueError("No JSON object or array delimiter found in LLM output")

    if first_obj != -1 and (first_arr == -1 or first_obj < first_arr):
        start_idx = first_obj
        last_idx = cleaned.rfind("}")
    else:
        start_idx = first_arr
        last_idx = cleaned.rfind("]")

    candidate = cleaned[start_idx : last_idx + 1] if last_idx > start_idx else cleaned[start_idx:]
    candidate = _TRAILING_COMMA_RE.sub(r"\1", candidate)

    # Attempt direct parse first
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Balance truncated braces/brackets for small local models
    open_braces = candidate.count("{") - candidate.count("}")
    open_brackets = candidate.count("[") - candidate.count("]")
    balanced = candidate
    if open_brackets > 0:
        balanced += "]" * open_brackets
    if open_braces > 0:
        balanced += "}" * open_braces
    balanced = _TRAILING_COMMA_RE.sub(r"\1", balanced)

    return json.loads(balanced)


def parse_llm_pydantic(raw_text: str, model_cls: type[T], default_fallback: T | None = None) -> T:
    """Parse raw LLM output into a validated Pydantic model instance."""
    try:
        data = extract_and_repair_json(raw_text)
        if isinstance(data, list) and "topics" in model_cls.model_fields:
            data = {"topics": data}
        return model_cls.model_validate(data)
    except Exception:
        if default_fallback is not None:
            return default_fallback
        raise
