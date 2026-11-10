import pytest
from core.reporter import _render_markdown_to_html, _sanitize_rendered_html


def test_recursive_html_sanitizer_offline():
    payload = (
        '<scr<script>ipt>alert("xss")</scr</script>ipt>'
        '<a href="javascript:alert(1)" onclick="evil()">Link</a>'
        'Normal clinical text with K+ < 3.5 mEq/L.'
    )
    sanitized = _render_markdown_to_html(payload)
    assert "<script" not in sanitized.lower()
    assert "javascript:" not in sanitized.lower()
    assert "onclick" not in sanitized.lower()
    assert "3.5 mEq/L" in sanitized


def test_recursive_html_sanitizer_all_dangerous_tags_and_schemes():
    # Test stripping form, svg, math, style tags, and schemes vbscript / data:text/html
    raw = (
        '<svg onload="alert(1)"><circle /></svg>'
        '<math><mtext>test</mtext></math>'
        '<style>body { display: none; }</style>'
        '<form action="javascript:steal()"><input type="text"/></form>'
        '<a href="vbscript:msgbox(1)">VBS</a>'
        '<a href="data:text/html:evil">Data</a>'
        '<img src="javascript:evil" onerror="hack()">'
    )
    sanitized = _sanitize_rendered_html(raw)
    assert "<svg" not in sanitized.lower()
    assert "<math" not in sanitized.lower()
    assert "<style" not in sanitized.lower()
    assert "<form" not in sanitized.lower()
    assert "vbscript:" not in sanitized.lower()
    assert "data:text/html" not in sanitized.lower()
    assert "onload" not in sanitized.lower()
    assert "onerror" not in sanitized.lower()
    assert "javascript:" not in sanitized.lower()


def test_sanitize_empty_or_none():
    assert _sanitize_rendered_html("") == ""
    assert _sanitize_rendered_html(None) == ""
