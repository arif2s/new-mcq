from parsers.heading_toc_calibrator import (
    calibrate_page_blocks,
    extract_hsv_color_profile,
    is_distinct_heading_color,
)


def test_hsv_color_hue_differentiates_chromatic_headings_from_grayscale_body():
    # Dark gray body text (#222222) should NOT be treated as a chromatic heading color
    distinct_gray, _ = is_distinct_heading_color("#222222", body_mode_color=(0, 0, 0))
    assert distinct_gray is False

    # Medical textbook dark blue (#004B87) and maroon (#8B0000) headings have distinct chromatic hues
    distinct_blue, blue_bucket = is_distinct_heading_color("#004B87", body_mode_color=(0, 0, 0))
    distinct_maroon, maroon_bucket = is_distinct_heading_color("#8B0000", body_mode_color=(0, 0, 0))
    assert distinct_blue is True
    assert distinct_maroon is True
    assert blue_bucket != maroon_bucket

    # White text on a shaded banner box
    distinct_banner, _ = is_distinct_heading_color("#FFFFFF", body_mode_color=(0, 0, 0), has_bg_fill=True, bg_color="#1E3A8A")
    assert distinct_banner is True


def test_hard_exclusion_and_prose_demotion_vetoes():
    raw_blocks = [
        {"text": "Running Chapter Header", "raw_label": "Page-Header", "font_size": 14.0, "is_bold": True},
        {"text": "Table 1.2: Antibiotic Dosages", "raw_label": "Caption", "font_size": 12.0, "is_bold": True},
        {
            "text": "The patient presented with acute retrosternal chest pain radiating to the left arm and was immediately administered aspirin in the emergency department.",
            "raw_label": "Section-Header",  # Accidentially mislabeled by order.onnx
            "font_size": 10.0,
            "is_bold": False,
            "text_color": "#000000",
        },
        {
            "text": "Normal body paragraph explaining pathophysiology with sufficient character length to anchor the 10pt median baseline.",
            "raw_label": "Text",
            "font_size": 10.0,
            "is_bold": False,
            "text_color": "#000000",
        },
    ]
    calibrated = calibrate_page_blocks(raw_blocks, page_height=800.0)
    assert calibrated[0]["label"] == "Page-Header"
    assert calibrated[0]["is_heading"] is False
    assert calibrated[1]["label"] == "Caption"
    assert calibrated[1]["is_heading"] is False
    # Mislabeled prose sentence must be force-demoted to Text
    assert calibrated[2]["label"] == "Text"
    assert calibrated[2]["is_heading"] is False
    assert calibrated[2]["heading_level"] is None


def test_situation_banner_and_sibling_sequence_consistency():
    raw_blocks = [
        {
            "text": "Standard clinical prose paragraph establishing the 10pt black body baseline across the textbook page for testing.",
            "raw_label": "Text",
            "font_size": 10.0,
            "is_bold": False,
            "text_color": "#000000",
        },
        {
            "text": "Situation 1: At Secondary Hospital/ Non-Metro situation: Optimal Standards of Treatment in Situations where technology and resources are limited",
            "raw_label": "Text",
            "font_size": 11.0,
            "is_bold": True,
            "has_bg_fill": True,
            "bg_color": "#D9E2F3",
        },
        {"text": "a) Clinical Diagnosis:", "raw_label": "List-Group", "font_size": 10.5, "is_bold": True},
        {"text": "b) Investigations:", "raw_label": "List-Group", "font_size": 10.5, "is_bold": True},
        {"text": "c) Treatment:", "raw_label": "List-Group", "font_size": 10.5, "is_bold": True},
        # Missing trailing colon on d), but belongs to the a)..d) sibling sequence
        {"text": "d) Referral criteria", "raw_label": "List-Group", "font_size": 10.5, "is_bold": True},
        # Plain unbolded list item should stay List-Group
        {
            "text": "1. Administer oral rehydration therapy and monitor urine output every four hours.",
            "raw_label": "List-Group",
            "font_size": 10.0,
            "is_bold": False,
        },
    ]
    calibrated = calibrate_page_blocks(raw_blocks, page_height=800.0)

    # Situation 1 banner promoted to Section-Header
    assert calibrated[1]["label"] == "Section-Header"
    assert calibrated[1]["is_heading"] is True
    assert calibrated[1]["heading_level"] in (1, 2)

    # Sibling sequence a), b), c) Treatment:, d) Referral criteria ALL promoted to identical heading level
    sibling_levels = [calibrated[i]["heading_level"] for i in (2, 3, 4, 5)]
    assert all(calibrated[i]["label"] == "Section-Header" for i in (2, 3, 4, 5))
    assert len(set(sibling_levels)) == 1
    assert sibling_levels[0] is not None

    # Unbolded numbered sentence retained as List-Group
    assert calibrated[6]["label"] == "List-Group"
    assert calibrated[6]["is_heading"] is False
    assert calibrated[6]["heading_level"] is None
