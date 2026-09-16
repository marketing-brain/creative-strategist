from cs.tagging.prompts import SYSTEM, SYSTEM_V2, user_text, user_text_nested
from cs.tagging.taxonomy import load_taxonomy


def test_system_v2_states_the_rules():
    for phrase in ("first 5 seconds", "credential", "snake_case", "verbatim", "non-decreasing"):
        assert phrase in SYSTEM_V2
    assert SYSTEM_V2 != SYSTEM


def test_user_text_nested_lists_every_model_dimension_and_hints():
    t = load_taxonomy()
    text = user_text_nested(
        t,
        "video",
        {"headline": "H", "primary_text": "P"},
        {"concept_tags": [("pharmacist_warning", 3), ("benadryl_swap", 1)], "topics": []},
    )
    assert "- headline: H" in text and "- primary_text: P" in text
    assert "- messenger (single, messenger):" in text
    assert "Allowed values: none, creator_on_camera" in text
    assert "- hook_devices (multi, hook):" in text and "Each item carries t_start_s" in text
    assert "- angles (multi, message):" in text
    assert "t_start_s" not in text.split("- angles")[1].split("\n")[0]
    assert "- beats (ordered, structure):" in text
    assert "- concept_tags (open, concept):" in text and "At most 6." in text
    assert "Existing slugs (count): pharmacist_warning (3), benadryl_swap (1)" in text
    assert "- topics (open, concept):" in text
    assert "Existing slugs" not in text.split("- topics")[1].split("- comparison_targets")[0]
    assert "- transcript (text):" in text
    for computed in ("duration_s", "cuts_per_10s", "aspect_ratio"):
        assert f"- {computed}" not in text
    assert text.rstrip().endswith("Return JSON matching the schema exactly.")


def test_legacy_user_text_unchanged_for_images():
    text = user_text(load_taxonomy(), "image", {"headline": "H"})
    assert "- hook_type (hook):" in text and "messenger" not in text
