"""
core/reporter.py - Jinja2 HTML dossier report compiler for clinical topic syntheses.
Enforces zero VRAM usage, SynthesisOutputDict contract adherence, Invariant 13 reference polymorphism,
markdown rendering, and thread-safe atomic file writes.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from jinja2 import BaseLoader, Environment

from config import REPORTS_DIR

logger = logging.getLogger(__name__)

_SLUG_REGEX = re.compile(r"[^a-z0-9]+")
_BARE_INEQUALITY_RE = re.compile(r"<(?=\s*\d|\s*[=<>]|\s+[a-zA-Z])")
_DANGEROUS_TAG_RE = re.compile(
    r"<\s*/?\s*(?:script|iframe|object|embed|applet|meta|link|base)\b[^>]*>",
    flags=re.IGNORECASE,
)
_EVENT_ATTR_RE = re.compile(r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", flags=re.IGNORECASE)

DOSSIER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ display_title }} - Clinical Synthesis Dossier</title>
  <style>
    :root {
      --bg-main: #0f172a;
      --bg-card: rgba(30, 41, 59, 0.85);
      --border-card: rgba(255, 255, 255, 0.12);
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #a78bfa;
      --accent-sub: #c4b5fd;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg-main);
      color: var(--text-main);
      padding: 2.5rem 1.5rem;
      line-height: 1.65;
      margin: 0;
    }
    .container { max-width: 900px; margin: 0 auto; }
    header { border-bottom: 1px solid var(--border-card); padding-bottom: 1rem; margin-bottom: 2rem; }
    h1 { color: var(--accent); margin: 0 0 0.5rem 0; font-size: 2rem; font-weight: 700; }
    h2 { color: var(--accent-sub); margin-top: 0; font-size: 1.35rem; font-weight: 600; border-bottom: 1px solid rgba(255,255,255,0.06); padding-bottom: 0.5rem; margin-bottom: 1rem; }
    h3 { color: #e2e8f0; font-size: 1.1rem; margin-top: 1.25rem; }
    .box {
      background: var(--bg-card);
      border: 1px solid var(--border-card);
      padding: 1.5rem;
      border-radius: 12px;
      margin-bottom: 1.75rem;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
      backdrop-filter: blur(8px);
    }
    .meta-row { display: flex; gap: 1.5rem; flex-wrap: wrap; font-size: 0.88rem; color: #cbd5e1; margin-bottom: 1.25rem; }
    .meta-pill { background: rgba(255, 255, 255, 0.08); padding: 3px 10px; border-radius: 6px; }
    .enhanced-box { border-left: 4px solid #38bdf8; background: rgba(14, 165, 233, 0.12); }
    .variance-box { border-left: 4px solid #ef4444; background: rgba(127, 29, 29, 0.25); }
    .distractor-box { border-left: 4px solid #f59e0b; background: rgba(120, 53, 15, 0.2); }
    ul { padding-left: 1.3rem; margin: 0.75rem 0; }
    li { margin-bottom: 0.5rem; }
    .badge {
      display: inline-block;
      font-size: 0.75rem;
      background: rgba(139, 92, 246, 0.2);
      color: #ddd6fe;
      border: 1px solid rgba(139, 92, 246, 0.4);
      padding: 2px 8px;
      border-radius: 999px;
      margin-left: 6px;
    }
    .ref-tag {
      display: inline-block;
      font-size: 0.72rem;
      font-weight: 700;
      padding: 2px 7px;
      border-radius: 4px;
      margin-right: 8px;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }
    .ref-pdf { background: rgba(239, 68, 68, 0.2); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.3); }
    .ref-obsidian { background: rgba(168, 85, 247, 0.2); color: #d8b4fe; border: 1px solid rgba(168, 85, 247, 0.3); }
    .ref-zim { background: rgba(59, 130, 246, 0.2); color: #93c5fd; border: 1px solid rgba(59, 130, 246, 0.3); }
    .ref-lancedb, .ref-tantivy, .ref-doc { background: rgba(16, 185, 129, 0.2); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.3); }
    .citation-link { color: var(--text-main); text-decoration: none; cursor: pointer; }
    .citation-link:hover { color: var(--accent-sub); text-decoration: underline; }
    .prose-content { line-height: 1.7; font-size: 0.98rem; }
    .prose-content table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
    .prose-content th, .prose-content td { border: 1px solid rgba(255, 255, 255, 0.15); padding: 8px 12px; text-align: left; }
    .prose-content th { background: rgba(255, 255, 255, 0.08); font-weight: 600; color: #e2e8f0; }
    .prose-content pre { background: rgba(0, 0, 0, 0.3); padding: 1rem; border-radius: 8px; overflow-x: auto; }
    .snippet-preview { font-size: 0.85rem; color: var(--text-muted); margin-top: 0.25rem; font-style: italic; }
    footer { text-align: center; font-size: 0.8rem; color: var(--text-muted); margin-top: 3rem; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>{{ display_title }}</h1>
      <div class="meta-row">
        {% if topic_key %}<span class="meta-pill">Key: <code>{{ topic_key }}</code></span>{% endif %}
        {% if article.section %}<span class="meta-pill">Section: {{ article.section }}</span>{% endif %}
        {% if article.conditional_context %}<span class="meta-pill">Context: {{ article.conditional_context }}</span>{% endif %}
      </div>
    </header>

    {% if enhanced_explanation %}
    <div class="box enhanced-box">
      <h2>Enhanced Clinical Explanation</h2>
      <div class="prose-content">{{ enhanced_explanation_html | safe }}</div>
    </div>
    {% endif %}

    <div class="box">
      <h2>{{ article.subtopic or display_title }}</h2>
      {% if article.clinical_points %}
      <ul>
        {% for pt in article.clinical_points %}
        <li>
          {{ pt.point }}
          {% if pt.source %}
          <span class="badge">[Ref: {{ pt.source }}{% if pt.page %} p.{{ pt.page }}{% endif %}]</span>
          {% endif %}
        </li>
        {% endfor %}
      </ul>
      {% endif %}
      {% if article.rendered_html %}
      <div class="prose-content">{{ article.rendered_html | safe }}</div>
      {% elif not article.clinical_points %}
      <p style="color: var(--text-muted);">No synthesized clinical points recorded.</p>
      {% endif %}

      {% if variance_text %}
      <div class="box variance-box" style="margin-top: 1.5rem; margin-bottom: 0;">
        <h2>Clinical Discrepancies &amp; Staging Variances</h2>
        <div class="prose-content">{{ variance_html | safe }}</div>
      </div>
      {% endif %}
    </div>

    {% if distractor_analysis and distractor_analysis != 'N/A' %}
    <div class="box distractor-box">
      <h2>MCQ &amp; Distractor Rationale</h2>
      <div class="prose-content">{{ distractor_analysis_html | safe }}</div>
    </div>
    {% endif %}

    <div class="box">
      <h2>Verified Reference Sources ({{ references | length }})</h2>
      {% if references %}
      <ul style="list-style: none; padding-left: 0;">
        {% for ref in references %}
        <li style="margin-bottom: 1rem; padding-bottom: 0.75rem; border-bottom: 1px solid rgba(255,255,255,0.05);">
          <div>
            <span class="ref-tag ref-{{ ref.source }}">{{ ref.source }}</span>
            <a href="#" class="citation-link" data-source-type="{{ ref.source }}" data-file-path="{{ ref.file_path }}" data-page="{{ ref.page or 1 }}" data-article-id="{{ ref.title }}">
              <strong>{{ ref.title }}</strong>
            </a>
            {% if ref.heading %} &mdash; <em>{{ ref.heading }}</em>{% endif %}
            {% if ref.page %} <span style="color: var(--text-muted);">(Page {{ ref.page }})</span>{% endif %}
          </div>
          {% if ref.snippet %}
          <div class="snippet-preview">"{{ ref.snippet }}"</div>
          {% endif %}
        </li>
        {% endfor %}
      </ul>
      {% else %}
      <p style="color: var(--text-muted);">No external references linked.</p>
      {% endif %}
    </div>

    <footer>
      Generated by Offline Clinical Education &amp; MCQ Platform &bull; Medical Knowledge RAG
    </footer>
  </div>
</body>
</html>
"""

_JINJA_ENV = Environment(loader=BaseLoader(), autoescape=True)
_COMPILED_TEMPLATE = _JINJA_ENV.from_string(DOSSIER_TEMPLATE)


def _sanitize_rendered_html(rendered: str) -> str:
    """Strips dangerous tags and inline event handlers to satisfy Invariant 5."""
    if not rendered:
        return ""
    cleaned = _DANGEROUS_TAG_RE.sub("", rendered)
    cleaned = _EVENT_ATTR_RE.sub("", cleaned)
    return cleaned


def _render_markdown_to_html(raw_text: str) -> str:
    """Renders markdown content to sanitized HTML while protecting medical inequality symbols."""
    if not raw_text or not str(raw_text).strip():
        return ""

    safe_input = _BARE_INEQUALITY_RE.sub("&lt;", str(raw_text).strip())

    try:
        import markdown

        rendered = markdown.markdown(
            safe_input,
            extensions=["tables", "fenced_code", "nl2br"],
        )
        return _sanitize_rendered_html(rendered)
    except ImportError:
        pass

    # High-fidelity safe fallback
    escaped = html.escape(str(raw_text).strip())
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"^###\s+(.+)$", r"<h3>\1</h3>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^##\s+(.+)$", r"<h2>\1</h2>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^#\s+(.+)$", r"<h1>\1</h1>", escaped, flags=re.MULTILINE)
    paragraphs = [
        f"<p>{p.strip().replace(chr(10), '<br>')}</p>"
        for p in escaped.split("\n\n")
        if p.strip()
    ]
    return "\n".join(paragraphs)


def _normalize_references_polymorphic(
    search_results: Any,
    top_references: Any,
) -> List[Dict[str, Any]]:
    """
    Polymorphically extracts and dedupes reference evidence matching Invariant 13.
    Consumes dictionaries (SearchResultsDict or single ChunkDict), lists (list[ChunkDict]),
    serialized JSON strings, or None without assuming a dictionary structure.
    """
    normalized: List[Dict[str, Any]] = []
    seen: set = set()

    def _add_item(item: Any, default_src: str = "doc") -> None:
        if not isinstance(item, dict):
            return

        title = (
            item.get("title")
            or item.get("source_article")
            or item.get("main_topic")
            or "Untitled Evidence"
        )
        heading = (
            item.get("heading")
            or item.get("heading_path")
            or item.get("subheading")
            or ""
        )
        raw_page = item.get("page") or item.get("page_number")
        page: Optional[int] = None
        if raw_page is not None:
            try:
                page = max(1, int(raw_page))
            except (ValueError, TypeError):
                page = None

        src = str(
            item.get("source") or item.get("source_type") or default_src
        ).strip().lower()
        if src in ("none", "", "all", "lancedb", "tantivy"):
            f_path_low = str(item.get("file_path", "")).lower()
            if f_path_low.endswith(".pdf"):
                src = "pdf"
            elif f_path_low.endswith(".md"):
                src = "obsidian"
            elif f_path_low.endswith(".zim"):
                src = "zim"
            elif src in ("none", "", "all"):
                src = "doc"

        snippet = item.get("snippet") or (
            str(item.get("text", ""))[:280] if item.get("text") else ""
        )
        snippet = str(snippet).strip()
        if len(snippet) > 280:
            snippet = snippet[:280] + "..."

        key = (src, str(title).strip().lower(), str(heading).strip().lower(), page)
        if key not in seen:
            seen.add(key)
            normalized.append({
                "source": src,
                "source_type": src,
                "title": str(title).strip(),
                "heading": str(heading).strip(),
                "page": page,
                "snippet": snippet,
                "file_path": str(item.get("file_path", "")),
            })

    def _ingest_source(payload: Any) -> None:
        if not payload:
            return
        if isinstance(payload, str):
            raw_str = payload.strip()
            if not raw_str:
                return
            try:
                payload = json.loads(raw_str)
            except Exception:
                return

        if isinstance(payload, list):
            for sub_item in payload:
                _add_item(sub_item)
        elif isinstance(payload, dict):
            # Detect if dict is a single ChunkDict vs a grouped SearchResultsDict
            if any(k in payload for k in ("title", "source_article", "snippet", "file_path")):
                _add_item(payload)
            else:
                for src_key, items in payload.items():
                    if isinstance(items, list):
                        for sub_item in items:
                            _add_item(sub_item, default_src=str(src_key))
                    elif isinstance(items, dict):
                        _add_item(items, default_src=str(src_key))

    _ingest_source(search_results)
    _ingest_source(top_references)
    return normalized


def _normalize_clinical_points(raw_points: Any) -> List[Dict[str, Any]]:
    """Normalizes clinical_points across list[dict], list[str], or nested subtopic structures."""
    points: List[Dict[str, Any]] = []
    if not raw_points or not isinstance(raw_points, list):
        return points

    for item in raw_points:
        if isinstance(item, str):
            clean_pt = item.strip()
            if clean_pt:
                points.append({"point": clean_pt, "source": "", "page": None})
        elif isinstance(item, dict):
            pt_text = str(
                item.get("point")
                or item.get("text")
                or item.get("fact")
                or item.get("content")
                or ""
            ).strip()
            if not pt_text:
                continue
            src = str(item.get("source") or item.get("source_article") or "").strip()
            raw_page = item.get("page") or item.get("page_number")
            page_val: Optional[int] = None
            if raw_page is not None:
                try:
                    page_val = int(raw_page)
                except (ValueError, TypeError):
                    page_val = None
            points.append({"point": pt_text, "source": src, "page": page_val})
    return points


def _normalize_article_payload(unified_raw: Any, fallback_topic: str) -> Dict[str, Any]:
    """Parses unified_article whether stored as JSON string, dict, or plain markdown text."""
    parsed_dict: Optional[Dict[str, Any]] = None

    if isinstance(unified_raw, dict):
        parsed_dict = dict(unified_raw)
    elif isinstance(unified_raw, str) and unified_raw.strip():
        try:
            candidate = json.loads(unified_raw)
            if isinstance(candidate, dict):
                parsed_dict = candidate
        except Exception:
            pass

    if parsed_dict is not None:
        raw_prose = str(
            parsed_dict.get("raw_text")
            or parsed_dict.get("text")
            or parsed_dict.get("content")
            or parsed_dict.get("markdown")
            or parsed_dict.get("summary")
            or ""
        ).strip()
        clinical_points = _normalize_clinical_points(parsed_dict.get("clinical_points"))

        # Also extract points from nested subtopics/nodes if present
        if not clinical_points and isinstance(parsed_dict.get("subtopics"), list):
            for sub in parsed_dict["subtopics"]:
                if isinstance(sub, dict):
                    clinical_points.extend(
                        _normalize_clinical_points(sub.get("clinical_points"))
                    )

        return {
            "section": str(parsed_dict.get("section") or "General"),
            "subtopic": str(parsed_dict.get("subtopic") or fallback_topic),
            "conditional_context": str(parsed_dict.get("conditional_context") or ""),
            "clinical_points": clinical_points,
            "raw_text": raw_prose,
            "rendered_html": _render_markdown_to_html(raw_prose),
            "discrepancies": str(
                parsed_dict.get("discrepancies") or parsed_dict.get("variance_audit") or ""
            ),
        }

    if isinstance(unified_raw, str) and unified_raw.strip():
        return {
            "section": "General",
            "subtopic": fallback_topic,
            "conditional_context": "",
            "clinical_points": [],
            "raw_text": unified_raw,
            "rendered_html": _render_markdown_to_html(unified_raw),
            "discrepancies": "",
        }

    return {
        "section": "General",
        "subtopic": fallback_topic,
        "conditional_context": "",
        "clinical_points": [],
        "raw_text": "",
        "rendered_html": "",
        "discrepancies": "",
    }


def generate_topic_report(
    topic: str,
    search_results: Optional[Union[Dict[str, Any], List[Dict[str, Any]]]] = None,
    synthesis_output: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Compiles a structured HTML dossier for a clinical topic and writes it atomically to disk.
    Conforms strictly to SynthesisOutputDict, ChunkDict, and Invariant 13.
    Returns the resolved absolute path to the generated HTML dossier.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    synth = synthesis_output if isinstance(synthesis_output, dict) else {}

    display_title = str(synth.get("display_title") or topic or "Clinical Topic").strip()
    topic_key = str(synth.get("topic_key") or "").strip()

    slug_base = topic_key or topic or "unnamed_clinical_topic"
    slug = _SLUG_REGEX.sub("_", slug_base.lower()).strip("_")[:100]
    if not slug:
        slug = "clinical_topic"

    output_path = (REPORTS_DIR / f"{slug}_report.html").resolve()

    # Normalize clinical article body
    article_data = _normalize_article_payload(synth.get("unified_article"), display_title)

    # Resolve variance audit from synthesis output or nested article dict
    variance_text = str(
        synth.get("variance_audit") or article_data.get("discrepancies") or ""
    ).strip()
    if variance_text.lower() in (
        "n/a",
        "none",
        "no significant variance detected.",
        "no discrepancies",
    ):
        variance_text = ""

    # Normalize and deduplicate references polymorphically (Invariant 13)
    normalized_refs = _normalize_references_polymorphic(
        search_results=search_results,
        top_references=synth.get("top_references"),
    )

    enhanced_exp = str(synth.get("enhanced_explanation") or "").strip()
    distractor_an = str(synth.get("distractor_analysis") or "").strip()

    html_content = _COMPILED_TEMPLATE.render(
        topic=topic,
        topic_key=topic_key,
        display_title=display_title,
        article=article_data,
        enhanced_explanation=enhanced_exp,
        enhanced_explanation_html=_render_markdown_to_html(enhanced_exp),
        distractor_analysis=distractor_an,
        distractor_analysis_html=_render_markdown_to_html(distractor_an),
        variance_text=variance_text,
        variance_html=_render_markdown_to_html(variance_text),
        references=normalized_refs,
    )

    # Process/thread-safe atomic file replacement (Section 3)
    pid = os.getpid()
    tid = threading.get_ident()
    temp_path = output_path.with_name(f".{output_path.name}.{pid}_{tid}.tmp")

    try:
        temp_path.write_text(html_content, encoding="utf-8")
        os.replace(temp_path, output_path)
    except Exception as exc:
        if temp_path.exists():
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        logger.error(
            "Failed to generate clinical dossier report for %s: %s",
            topic,
            exc,
            exc_info=True,
        )
        raise

    return str(output_path)
