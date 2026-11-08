from pathlib import Path
from parsers.obsidian_reader import (
    ObsidianVaultGraph,
    convert_obsidian_markdown_to_html_ready,
)


def test_small_single_note_internal_subheading_and_synonym_slicing(tmp_path: Path):
    vault = tmp_path / "CompactVault"
    topics_dir = vault / "Davidson_Medicine" / "Topics"
    topics_dir.mkdir(parents=True, exist_ok=True)

    # Small topic: 'pericarditis.md' has NO separate treatment.md child file;
    # instead it has '## Management' as an internal H2 subheading with nested H3 items.
    (topics_dir / "pericarditis.md").write_text(
        "# Pericarditis\n"
        "## Clinical Features\n"
        "Pleuritic chest pain relieved by sitting forward.\n"
        "## Management\n"
        "High-dose NSAIDs (ibuprofen or aspirin) plus colchicine for 3 months.\n"
        "### Refractory Cases\n"
        "Low-dose corticosteroids if NSAIDs are contraindicated.\n"
        "## Complications\n"
        "Cardiac tamponade and constrictive pericarditis.\n",
        encoding="utf-8",
    )

    # Another note to ensure ranking specificity
    (topics_dir / "myocarditis.md").write_text(
        "# Myocarditis\n"
        "## Management\n"
        "Supportive heart failure therapy and arrhythmia monitoring.\n",
        encoding="utf-8",
    )

    graph = ObsidianVaultGraph(vault)
    graph.build()

    # Query asks for "treatment of pericarditis" even though the internal subheading is "## Management"
    hits = graph.search_notes_and_sections("treatment of pericarditis", top_k=3)
    assert len(hits) >= 1
    top_hit = hits[0]
    assert top_hit["record"].clean_title == "pericarditis"
    assert top_hit["matched_section"] is not None
    assert top_hit["matched_section"]["title"] == "Management"
    assert top_hit["anchor_slug"] == "heading-management"

    # Verify the sliced section content includes '## Management' and nested '### Refractory Cases',
    # but stops before '## Complications'
    sliced = top_hit["matched_content"]
    assert "colchicine for 3 months" in sliced
    assert "Refractory Cases" in sliced
    assert "constrictive pericarditis" not in sliced

    # Verify HTML rendering injects deterministic heading id="heading-management"
    html_out = convert_obsidian_markdown_to_html_ready(
        top_hit["record"].raw_markdown,
        book_name="Davidson_Medicine",
    )
    assert 'id="heading-management"' in html_out
    assert 'id="heading-refractory-cases"' in html_out


def test_large_split_topic_parent_child_linking(tmp_path: Path):
    vault = tmp_path / "SplitVault"
    book_dir = vault / "Harrison_Internal_Medicine"
    book_dir.mkdir(parents=True, exist_ok=True)

    # Parent note
    (book_dir / "heart attack.md").write_text(
        "# Heart Attack\n"
        "Also known as myocardial infarction.\n"
        "See [[treatment(1)|Treatment of Heart Attack]]\n",
        encoding="utf-8",
    )

    # Child note
    (book_dir / "treatment(1).md").write_text(
        "# Treatment of Heart Attack\n"
        "Immediate aspirin, nitroglycerin, and emergency PCI.\n",
        encoding="utf-8",
    )

    graph = ObsidianVaultGraph(vault)
    graph.build()

    # Child note should be linked to parent
    child_records = [r for r in graph.notes_by_id.values() if r.raw_title == "treatment(1)"]
    assert len(child_records) == 1
    child_rec = child_records[0]
    assert "heart attack" in [p.lower() for p in child_rec.parent_titles]

    # Query matching treatment of heart attack should rank treatment(1) or heart attack highly
    hits = graph.search_notes_and_sections("treatment of heart attack", top_k=3)
    assert len(hits) >= 1
    top_clean_titles = [h["record"].clean_title for h in hits]
    assert "treatment" in top_clean_titles or "heart attack" in top_clean_titles
