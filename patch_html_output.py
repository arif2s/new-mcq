with open('agents/tema_q/pipeline.py', 'r') as f:
    content = f.read()

old_html = r"""    html_content = f\"\"\"<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Tema_Q Synthesis Report - {topic}</title>
<style>
  body {{ font-family: Arial, sans-serif; line-height: 1.6; margin: 20px; }}
  h1 {{ color: #2c3e50; }}
  h2, h3 {{ color: #34495e; }}
  .content {{ max-width: 800px; margin: auto; }}
  .section {{ margin-bottom: 20px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }}
  .discrepancies {{ border-color: #e74c3c; background-color: #fadbd8; }}
</style>
</head>
<body>
<div class="content">
  <h1>Clinical Synthesis: {topic}</h1>
  <div class="section">
    <h2>Pass 1: Base Note</h2>
    <pre style="white-space: pre-wrap; font-family: inherit;">{base_note_json}</pre>
  </div>
  <div class="section">
    <h2>Pass 2: Unified Article</h2>
    <pre style="white-space: pre-wrap; font-family: inherit;">{final_article}</pre>
  </div>
  <div class="section">
    <h2>Distractor Analysis</h2>
    <pre style="white-space: pre-wrap; font-family: inherit;">{distractor_output}</pre>
  </div>
</div>
</body>
</html>
\"\"\""""

new_html = r"""
    # Generate structured HTML from final_article JSON
    subtopic = final_article.get("subtopic", "")
    section = final_article.get("section", "")
    cond_context = final_article.get("conditional_context", "")
    clinical_points = final_article.get("clinical_points", [])
    discrepancies = final_article.get("discrepancies", "")

    html_points = ""
    for pt in clinical_points:
        point_text = pt.get("point", "")
        source = pt.get("source", "")
        page = pt.get("page", "")

        # Clickable reference badge
        if source:
            badge = f'<span class="badge" title="Source: {source} (Page {page})"><a href="#" onclick="alert(\'Open: {source} Page: {page}\'); return false;">[Ref: {source} p.{page}]</a></span>'
        else:
            badge = ""

        html_points += f"<li>{point_text} {badge}</li>\n"

    html_content = f\"\"\"<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Tema_Q Synthesis Report - {topic}</title>
<style>
  body {{ font-family: Arial, sans-serif; line-height: 1.6; margin: 20px; }}
  h1 {{ color: #2c3e50; }}
  h2, h3 {{ color: #34495e; }}
  .content {{ max-width: 800px; margin: auto; }}
  .section {{ margin-bottom: 20px; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }}
  .discrepancies {{ border-color: #e74c3c; background-color: #fadbd8; padding: 10px; margin-top: 10px; font-weight: bold; }}
  .badge {{ font-size: 0.85em; background-color: #e2e8f0; padding: 2px 6px; border-radius: 12px; margin-left: 8px; }}
  .badge a {{ text-decoration: none; color: #3182ce; }}
</style>
</head>
<body>
<div class="content">
  <h1>Clinical Synthesis: {topic}</h1>
  <div class="section">
    <h2>Unified Article: {subtopic}</h2>
    <p><strong>Section:</strong> {section}</p>
    <p><strong>Context:</strong> {cond_context}</p>
    <ul>
      {html_points}
    </ul>
"""

new_html += """
    """
new_html += r"""    {f'<div class="discrepancies">{discrepancies}</div>' if discrepancies else ''}
  </div>
  <div class="section">
    <h2>Distractor Analysis</h2>
    <pre style="white-space: pre-wrap; font-family: inherit;">{distractor_output}</pre>
  </div>
</div>
</body>
</html>
\"\"\""""

if "html_content = f\"\"\"<!DOCTYPE html>" in content:
    import re
    content = re.sub(r'    html_content = f"""<!DOCTYPE html>.*?</html>\n"""', new_html, content, flags=re.DOTALL)

with open('agents/tema_q/pipeline.py', 'w') as f:
    f.write(content)
