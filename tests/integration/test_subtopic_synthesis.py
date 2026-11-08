from agents.tema_q.auditor import filter_novel_secondary_sentences, merge_json_nodes
from agents.tema_q.subtopic_router import route_and_prune_subtopics, sanitize_clinical_prose
from agents.tema_q.topic_extractor import classify_medical_domain


def test_pure_prose_sanitizer_removes_tables_and_captions():
    raw = (
        "# Amyloidosis\n"
        "![Congo red](img01.png)\n"
        "> *Figure 1: Apple-green birefringence under polarized light.*\n"
        "| Type | Precursor | Organ |\n"
        "| --- | --- | --- |\n"
        "| AL | Light chain | Kidney |\n"
        "Amyloidosis shows extracellular deposition of fibrillar proteins with beta-pleated sheet configuration (see Table 1).\n"
    )
    cleaned = sanitize_clinical_prose(raw)
    assert "img01.png" not in cleaned
    assert "Figure 1:" not in cleaned
    assert "| AL |" not in cleaned
    assert "(see Table 1)" not in cleaned
    assert "beta-pleated sheet configuration" in cleaned


def test_seven_domain_classification():
    assert classify_medical_domain("Clostridium tetani") == "microorganism"
    assert classify_medical_domain("Amoxicillin") == "drug_chemical"
    assert classify_medical_domain("Amyloidosis") == "pathology"
    assert classify_medical_domain("Brachial plexus") == "anatomy"
    assert classify_medical_domain("Rigor mortis", "Forensic autopsy time since death") == "medicolegal"
    assert classify_medical_domain("Primary prevention", "National immunization schedule") == "public_health"
    assert classify_medical_domain("Myocardial infarction") == "disease"


def test_cross_article_mismatched_headings_route_to_same_subtopic():
    docs = [
        {
            "source_type": "pdf",
            "source_label": "Harrison",
            "page": 12,
            "raw_text": (
                "# Malaria\n"
                "## Etiology and Transmission\n"
                "Malaria is transmitted by the bite of female Anopheles mosquito carrying Plasmodium sporozoites.\n"
            ),
        },
        {
            "source_type": "zim",
            "source_label": "ZIM: Malaria",
            "page": 1,
            "raw_text": (
                "# Malaria\n"
                "## Vector Lifecycle\n"
                "The definitive host and vector for malaria is the female Anopheles mosquito, which injects sporozoites during a blood meal.\n"
            ),
        },
    ]
    buckets = route_and_prune_subtopics("Malaria", docs)
    etiol_buckets = [b for b in buckets if b.subtopic_id == "etiology_transmission"]
    assert len(etiol_buckets) == 1
    assert "Anopheles" in etiol_buckets[0].primary_text
    assert len(etiol_buckets[0].secondary_entries) == 1


def test_500_and_1000_word_budgets_enforced_in_merge():
    long_pt_1 = " ".join(["clinical_token"] * 300)
    long_pt_2 = " ".join(["diagnostic_token"] * 300)
    base = merge_json_nodes(
        None,
        {"subtopic": "Malaria", "clinical_points": [long_pt_1, long_pt_2]},
        metadata={"source": "Doc1", "page": 1},
        primary_max_words=500,
    )
    base_words = sum(len(p["point"].split()) for p in base["clinical_points"])
    assert base_words <= 500

    sec_pt_1 = " ".join(["treatment_token"] * 350)
    sec_pt_2 = " ".join(["prophylaxis_token"] * 350)
    merged = merge_json_nodes(
        base,
        {"subtopic": "Malaria", "clinical_points": [sec_pt_1, sec_pt_2]},
        metadata={"source": "Doc2", "page": 2},
        max_words=1000,
    )
    total_words = sum(len(p["point"].split()) for p in merged["clinical_points"])
    assert total_words <= 1000
