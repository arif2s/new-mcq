from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from core.reporter import generate_topic_report, sanitize_report_path
from parsers.obsidian_reader import read_markdown_body


def test_path_confinement_offline(tmp_path: Path):
    report_path = sanitize_report_path("../../etc/passwd", reports_dir=tmp_path)
    assert report_path is not None
    assert report_path.is_relative_to(tmp_path.resolve())
    assert report_path.name == "etc_passwd_report.html"

    # Non-markdown file read should be rejected by read_markdown_body
    secret_file = tmp_path / "config.py"
    secret_file.write_text("SECRET = 123", encoding="utf-8")
    assert read_markdown_body(str(secret_file)) == ""


def test_obsidian_reader_confinement_and_rejection(tmp_path: Path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    valid_note = vault_dir / "clinical_note.md"
    valid_note.write_text("# Note\nClinical content", encoding="utf-8")

    # Valid read with allowed_root
    assert read_markdown_body(str(valid_note), allowed_root=vault_dir) == "# Note\nClinical content"
    assert read_markdown_body(valid_note, allowed_root=vault_dir) == "# Note\nClinical content"

    # Outside file read with allowed_root
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("outside", encoding="utf-8")
    assert read_markdown_body(str(outside_file), allowed_root=vault_dir) == ""
    assert read_markdown_body(outside_file, allowed_root=vault_dir) == ""

    # Null byte rejection
    assert read_markdown_body(f"{valid_note}\x00") == ""
    assert read_markdown_body("\x00") == ""
    assert read_markdown_body(None) == ""

    # Non-markdown extensions rejection
    txt_file = vault_dir / "notes.txt"
    txt_file.write_text("plain text", encoding="utf-8")
    assert read_markdown_body(str(txt_file)) == ""

    # Non-existent file rejection
    assert read_markdown_body(vault_dir / "nonexistent.md") == ""


def test_generate_topic_report_path_confinement(tmp_path: Path):
    with patch("core.reporter.REPORTS_DIR", tmp_path):
        out = generate_topic_report("Acute Pericarditis")
        assert out is not None
        assert Path(out).is_relative_to(tmp_path.resolve())

        # When slug or topic would attempt traversal out of reports dir
        mock_regex = MagicMock()
        mock_regex.sub.return_value = "../../escaped"
        with patch("core.reporter._SLUG_REGEX", mock_regex):
            with pytest.raises(ValueError, match="Path traversal blocked"):
                generate_topic_report("Acute Pericarditis")
