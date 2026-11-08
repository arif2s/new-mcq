from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_duplicate_html_and_images_remain_clean():
    assert (ROOT / "ui/index.html").exists()
    assert not (ROOT / "index.html").exists()
    assert not (ROOT / "ui/img/bg-dark.jpg").exists()
    assert not (ROOT / "ui/img/bg-light.jpg").exists()


def test_css_gpu_compositing_reduced_motion_and_katex_fallbacks():
    main_css = (ROOT / "ui/css/main.css").read_text(encoding="utf-8")
    anim_css = (ROOT / "ui/css/animations.css").read_text(encoding="utf-8")
    katex_css = (ROOT / "ui/vendor/katex.min.css").read_text(encoding="utf-8")

    assert "will-change: transform, opacity" in main_css
    assert ".provenance-badge" in main_css
    assert "prefers-reduced-motion: reduce" in anim_css
    assert "Cambria Math" in katex_css
    assert "STIX Two Math" in katex_css


def test_core_js_safe_markdown_abort_controller_and_debounced_state():
    app_js = (ROOT / "ui/js/app.js").read_text(encoding="utf-8")
    state_js = (ROOT / "ui/js/state.js").read_text(encoding="utf-8")
    search_js = (ROOT / "ui/js/search.js").read_text(encoding="utf-8")

    assert "window.renderSafeMarkdown" in app_js
    assert "DOMPurify.sanitize" in app_js
    assert "window.throttleWithRAF" in app_js
    assert "window.computeWsBackoffDelay" in app_js

    assert "window.queueDebouncedStatePatch" in state_js
    assert "localStorage" in state_js

    assert "AbortController" in search_js
    assert "window.executeRaceSafeSearch" in search_js


def test_feature_controllers_upgraded():
    quiz_js = (ROOT / "ui/js/quiz.js").read_text(encoding="utf-8")
    analysis_js = (ROOT / "ui/js/analysis.js").read_text(encoding="utf-8")
    toc_js = (ROOT / "ui/js/analysis_toc.js").read_text(encoding="utf-8")
    hub_js = (ROOT / "ui/js/hub.js").read_text(encoding="utf-8")
    habits_js = (ROOT / "ui/js/habits.js").read_text(encoding="utf-8")

    assert "window.getQuestionResponseTimeMs" in quiz_js
    assert "window.formatProvenanceBadgeHtml" in quiz_js
    assert "createDocumentFragment" in analysis_js
    assert "window.pushTocHistorySnapshot" in toc_js
    assert "window.computeArticleWordBudgetStatus" in hub_js
    assert "window.getLocalHabitDateString" in habits_js
