from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_primary_button_dynamic_artistic_hue_css_and_palette_extraction():
    main_css = (ROOT / "ui/css/main.css").read_text(encoding="utf-8")
    app_js = (ROOT / "ui/js/app.js").read_text(encoding="utf-8")

    # Dynamic artistic extraction variables and CSS rules
    assert "--primary-btn-bg" in main_css
    assert "--primary-btn-text" in main_css
    assert "--primary-btn-shadow" in main_css
    assert ".primary-btn" in main_css
    assert "var(--primary-btn-bg" in main_css

    # app.js dynamic artistic hue extraction and WCAG AA contrast calculation
    assert "vividPixels" in app_js
    assert "--primary-btn-bg" in app_js
    assert "--primary-btn-text" in app_js


def test_font_zoom_controls_in_reader_and_csv_explanation():
    index_html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    reader_js = (ROOT / "ui/js/reader.js").read_text(encoding="utf-8")
    quiz_js = (ROOT / "ui/js/quiz.js").read_text(encoding="utf-8")
    main_css = (ROOT / "ui/css/main.css").read_text(encoding="utf-8")

    # Document viewer overlay has font zoom buttons
    assert "reader-font-decrease-btn" in index_html
    assert "reader-font-increase-btn" in index_html
    assert "reader-font-size-label" in index_html

    # reader.js manages reader font size with smooth persistence
    assert "currentReaderFontSize" in reader_js
    assert "--modal-reader-font-size" in reader_js

    # quiz.js manages CSV explanation font size with smooth persistence
    assert "exp-font-decrease-btn" in quiz_js
    assert "exp-font-increase-btn" in quiz_js
    assert "--csv-explanation-font-size" in quiz_js

    # CSS transitions for fluid, natural sizing
    assert "transition: font-size" in main_css
    assert ".font-zoom-btn" in main_css
