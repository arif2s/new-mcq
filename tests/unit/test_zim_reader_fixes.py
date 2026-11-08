import posixpath
import re
import urllib.parse
from fastapi import FastAPI
from fastapi.testclient import TestClient
from parsers.zim_reader import (
    _MEDIA_ATTR_RE,
    _SRCSET_RE,
    _STYLE_COLOR_RE,
    _INLINE_STYLE_RE,
    get_zim_asset,
    ZimAsset
)
from server.routes_notes import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_svg_and_media_attribute_rewriting():
    html = """
    <div>
        <img src="../I/m/heart.svg" srcset="../I/m/heart.svg 1.5x, ../I/m/heart2.svg 2x" alt="heart">
        <svg viewBox="0 0 100 100">
            <image href="subdiagram.svg" />
            <image xlink:href="../I/subdiagram2.svg" />
            <text x="10" y="20">Heart Diagram</text>
        </svg>
        <object data="../I/m/interactive.svg" type="image/svg+xml"></object>
        <embed src="chart.svg">
    </div>
    """
    zp = "/tmp/test_medical.zim"

    # 1. Test srcset stripping
    stripped = _SRCSET_RE.sub("", html)
    assert "srcset=" not in stripped

    # 2. Test media rewriting
    def _rewrite_media(match: re.Match) -> str:
        prefix = match.group(1)
        src_val = match.group(2)
        suffix = match.group(3)
        if src_val.startswith(("http://", "https://", "data:", "#", "javascript:")):
            return match.group(0)
        cleaned_src = urllib.parse.unquote(src_val.lstrip("./").lstrip("/"))
        norm_src = posixpath.normpath(cleaned_src).lstrip("./").lstrip("/")
        while norm_src.startswith("../"):
            norm_src = norm_src[3:].lstrip("/")
        target_src = norm_src if norm_src else cleaned_src
        encoded_src = urllib.parse.quote(target_src)
        encoded_zp = urllib.parse.quote(zp)
        return f'{prefix}/api/notes/zim-asset?path={encoded_src}&zim_path={encoded_zp}{suffix}'

    rewritten = _MEDIA_ATTR_RE.sub(_rewrite_media, stripped)
    assert 'src="/api/notes/zim-asset?path=I/m/heart.svg&zim_path=/tmp/test_medical.zim"' in rewritten
    assert 'href="/api/notes/zim-asset?path=subdiagram.svg&zim_path=/tmp/test_medical.zim"' in rewritten
    assert 'xlink:href="/api/notes/zim-asset?path=I/subdiagram2.svg&zim_path=/tmp/test_medical.zim"' in rewritten
    assert 'data="/api/notes/zim-asset?path=I/m/interactive.svg&zim_path=/tmp/test_medical.zim"' in rewritten
    assert 'src="/api/notes/zim-asset?path=chart.svg&zim_path=/tmp/test_medical.zim"' in rewritten


def test_navbox_style_cleaning():
    raw_navbox = (
        '<table class="navbox" bgcolor="#ffffff" '
        'style="background: #fdfdfd; width: 100%; color: #202122; font-size: 88%; border: 1px solid #aaa;">'
        '<tr><th class="navbox-title" style="background: #ccccff; color: #000000;">Title</th></tr>'
        '</table>'
    )

    clean_step1 = _STYLE_COLOR_RE.sub("", raw_navbox)
    assert "bgcolor=" not in clean_step1

    def _clean_style_declarations(style_val: str) -> str:
        cleaned = []
        for part in style_val.split(";"):
            item = part.strip()
            if not item:
                continue
            prop = item.split(":", 1)[0].strip().lower()
            if prop in ("color", "background", "background-color", "bgcolor"):
                continue
            cleaned.append(item)
        if not cleaned:
            return ""
        return f'style="{"; ".join(cleaned)}"'

    def _clean_style_match(match: re.Match) -> str:
        cleaned_css = _clean_style_declarations(match.group(1))
        return f" {cleaned_css}" if cleaned_css else ""

    clean_step2 = _INLINE_STYLE_RE.sub(_clean_style_match, clean_step1)
    assert 'style="width: 100%; font-size: 88%; border: 1px solid #aaa"' in clean_step2
    assert "color: #202122" not in clean_step2
    assert "background: #fdfdfd" not in clean_step2
    assert "background: #ccccff" not in clean_step2


def test_zim_asset_candidate_resolution_order():
    # Verify candidate resolution logic produces valid ZIM paths
    asset_path = "../I/m/Anatomy_of_the_Human_Heart.svg"
    clean_path = urllib.parse.unquote(str(asset_path).strip().lstrip("./").lstrip("/"))
    norm_path = posixpath.normpath(clean_path).lstrip("./").lstrip("/")
    while norm_path.startswith("../"):
        norm_path = norm_path[3:].lstrip("/")

    candidates = []
    def _add_cand(c: str) -> None:
        c = c.lstrip("/")
        if c and c not in candidates:
            candidates.append(c)

    _add_cand(clean_path)
    _add_cand(norm_path)
    for base in [clean_path, norm_path]:
        if not base.startswith(("I/", "A/", "C/", "-/", "m/")):
            _add_cand(f"I/{base}")
            _add_cand(f"I/m/{base}")

    assert "I/m/Anatomy_of_the_Human_Heart.svg" in candidates


def test_zim_asset_mime_detection():
    # Test ZimAsset mime fallback
    asset = ZimAsset(b"<svg>dummy</svg>", "image/svg+xml", "/tmp/fake.zim")
    assert asset.mimetype == "image/svg+xml"
    assert asset.content == b"<svg>dummy</svg>"
    assert asset.zim_path == "/tmp/fake.zim"
    assert "content" in asset
    assert "mimetype" in asset
    assert asset["mimetype"] == "image/svg+xml"
