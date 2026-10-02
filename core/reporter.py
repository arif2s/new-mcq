import re
import os
from pathlib import Path
from jinja2 import Environment, BaseLoader
from config import REPORTS_DIR

# 1. Pre-compile the regex at the module level to avoid recompiling on every function call
_SLUG_REGEX = re.compile(r'[^a-z0-9]+')

DOSSIER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{{ topic }} - Dossier</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #121316; color: #e1e4e8; padding: 2rem; line-height: 1.6; }
    h1, h2, h3 { color: #58a6ff; }
    .box { background: #1c2128; border: 1px solid #30363d; padding: 1.5rem; border-radius: 6px; margin-bottom: 1.5rem; }
    .variance { border-left: 4px solid #d29922; background: #272115; }
    ul { padding-left: 1.2rem; }
  </style>
</head>
<body>
  <h1>Clinical Synthesis: {{ topic }}</h1>
  <div class="box">
    <h2>Unified Synthesis</h2>
    <div>{{ synthesis.unified_article }}</div>
  </div>
  <div class="box variance">
    <h2>Distractor Rationale</h2>
    <p>{{ synthesis.distractor_analysis }}</p>
  </div>
  <div class="box">
    <h2>Reference Sources</h2>
    <ul>
      {% for src, items in search_results.items() %}
        {% for item in items %}
          <li><strong>[{{ src|upper }}]</strong> {{ item.title }} (Page/Ref: {{ item.page }})</li>
        {% endfor %}
      {% endfor %}
    </ul>
  </div>
</body>
</html>
"""

# 2. Use a Jinja2 Environment instead of a bare Template for better memory management
# and extensibility. (Autoescape is False assuming synthesis contains pre-formatted HTML).
_JINJA_ENV = Environment(loader=BaseLoader(), autoescape=False)
_COMPILED_TEMPLATE = _JINJA_ENV.from_string(DOSSIER_TEMPLATE)

def generate_topic_report(topic: str, search_results: dict, synthesis_output: dict):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 3. Truncate slug length to prevent Windows MAX_PATH (260 chars) exceptions
    slug = _SLUG_REGEX.sub('_', topic.lower()).strip('_')[:100]

    # Fallback for edge cases where the topic consists entirely of special characters
    if not slug:
        slug = "unnamed_clinical_topic"

    output_path = REPORTS_DIR / f"{slug}_report.html"

    html_content = _COMPILED_TEMPLATE.render(
        topic=topic,
        search_results=search_results,
        synthesis=synthesis_output
    )

    # 4. Atomic File Write for Windows
    # Prevents file corruption if the offline desktop worker is forcefully closed mid-write.
    temp_path = output_path.with_suffix(".html.tmp")
    try:
        temp_path.write_text(html_content, encoding="utf-8")
        # os.replace provides an atomic swap on NTFS file systems
        os.replace(temp_path, output_path)
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise e
