import re
import os
import json
from typing import Dict, Any
from jinja2 import Environment, BaseLoader
from config import REPORTS_DIR

_SLUG_REGEX = re.compile(r"[^a-z0-9]+")

DOSSIER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ topic }} - Clinical Synthesis Dossier</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; line-height: 1.65; margin: 0; }
    .container { max-width: 880px; margin: 0 auto; }
    h1 { color: #a78bfa; border-bottom: 1px solid rgba(255,255,255,0.12); padding-bottom: 0.75rem; }
    h2, h3 { color: #c4b5fd; margin-top: 0; }
    .box { background: rgba(30, 41, 59, 0.85); border: 1px solid rgba(255,255,255,0.12); padding: 1.5rem; border-radius: 12px; margin-bottom: 1.5rem; }
    .meta-row { display: flex; gap: 1.5rem; flex-wrap: wrap; font-size: 0.9rem; color: #cbd5e1; margin-bottom: 1rem; }
    .variance { border-left: 4px solid #ef4444; background: rgba(127, 29, 29, 0.25); padding: 1rem 1.25rem; border-radius: 8px; margin-top: 1rem; }
    .distractor-box { border-left: 4px solid #f59e0b; background: rgba(120, 53, 15, 0.2); }
    ul { padding-left: 1.25rem; }
    li { margin-bottom: 0.6rem; }
    .badge { display: inline-block; font-size: 0.78rem; background: rgba(139, 92, 246, 0.2); color: #ddd6fe; border: 1px solid rgba(139, 92, 246, 0.4); padding: 2px 8px; border-radius: 999px; margin-left: 8px; }
    .ref-tag { display: inline-block; font-size: 0.75rem; font-weight: 700; padding: 2px 6px; border-radius: 4px; margin-right: 6px; text-transform: uppercase; }
    .ref-pdf { background: rgba(239, 68, 68, 0.2); color: #fca5a5; }
    .ref-obsidian { background: rgba(168, 85, 247, 0.2); color: #d8b4fe; }
    .ref-zim { background: rgba(59, 130, 246, 0.2); color: #93c5fd; }
    pre { white-space: pre-wrap; font-family: inherit; margin: 0; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Clinical Synthesis: {{ topic }}</h1>

    <div class="box">
      <h2>{{ article.subtopic or topic }}</h2>
      <div class="meta-row">
        {% if article.section %}<span><strong>Section:</strong> {{ article.section }}</span>{% endif %}
        {% if article.conditional_context %}<span><strong>Context:</strong> {{ article.conditional_context }}</span>{% endif %}
      </div>

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
      {% elif article.raw_text %}
      <div><pre>{{ article.raw_text }}</pre></div>
      {% else %}
      <p>No synthesized clinical points recorded.</p>
      {% endif %}

      {% if article.discrepancies %}
      <div class="variance">
        <strong>Clinical Discrepancies &amp; Staging Variances:</strong>
        <pre>{{ article.discrepancies }}</pre>
      </div>
      {% endif %}
    </div>

    {% if distractor_analysis and distractor_analysis != 'N/A' %}
    <div class="box distractor-box">
      <h2>MCQ &amp; Distractor Rationale</h2>
      <pre>{{ distractor_analysis }}</pre>
    </div>
    {% endif %}

    <div class="box">
      <h2>Verified Reference Sources</h2>
      <ul>
        {% for src, items in search_results.items() %}
          {% for item in items %}
            <li>
              <span class="ref-tag ref-{{ src }}">{{ src }}</span>
              <strong>{{ item.title }}</strong>
              {% if item.heading %} &mdash; <em>{{ item.heading }}</em>{% endif %}
              {% if item.page %} (Page {{ item.page }}){% endif %}
            </li>
          {% endfor %}
        {% endfor %}
      </ul>
    </div>
  </div>
</body>
</html>
"""

_JINJA_ENV = Environment(loader=BaseLoader(), autoescape=True)
_COMPILED_TEMPLATE = _JINJA_ENV.from_string(DOSSIER_TEMPLATE)


def _normalize_article_payload(unified_raw: Any, fallback_topic: str) -> Dict[str, Any]:
    """Parses unified_article whether stored as JSON string, dict, or plain markdown text."""
    if isinstance(unified_raw, dict):
        return unified_raw
    if isinstance(unified_raw, str) and unified_raw.strip():
        try:
            parsed = json.loads(unified_raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {
            "section": "General",
            "subtopic": fallback_topic,
            "conditional_context": "",
            "clinical_points": [],
            "raw_text": unified_raw,
            "discrepancies": "",
        }
    return {
        "section": "General",
        "subtopic": fallback_topic,
        "conditional_context": "",
        "clinical_points": [],
        "raw_text": "",
        "discrepancies": "",
    }


def generate_topic_report(topic: str, search_results: dict, synthesis_output: dict) -> str:
    """
    Compiles a structured HTML dossier for a clinical topic and writes it atomically to disk.
    Returns the path to the generated HTML report.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    slug = _SLUG_REGEX.sub("_", topic.lower()).strip("_")[:100]
    if not slug:
        slug = "unnamed_clinical_topic"

    output_path = REPORTS_DIR / f"{slug}_report.html"
    article_data = _normalize_article_payload(synthesis_output.get("unified_article"), topic)

    html_content = _COMPILED_TEMPLATE.render(
        topic=topic,
        article=article_data,
        distractor_analysis=synthesis_output.get("distractor_analysis", ""),
        search_results=search_results or {},
    )

    temp_path = output_path.with_suffix(".html.tmp")
    try:
        temp_path.write_text(html_content, encoding="utf-8")
        os.replace(temp_path, output_path)
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise e

    return str(output_path)
