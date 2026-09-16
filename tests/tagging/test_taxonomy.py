import pytest

from cs.tagging.taxonomy import load_taxonomy

LEGACY_IMAGE_DIMENSIONS = [
    "hook_type",
    "hook_text_present",
    "message_angle",
    "offer_type",
    "urgency",
    "proof_type",
    "claim_specificity",
    "cta_style",
    "cta_verb",
    "visual_style",
    "color_palette",
    "text_density",
    "layout",
    "brand_presence",
    "product_visible",
    "people_present",
    "face_closeup",
    "demographic_signal",
    "tone",
    "humor_present",
    "aspect_ratio",
    "format_subtype",
    "hook_summary",
    "message_summary",
]

VIDEO_DIMENSIONS = {
    "numeric": ["duration_s", "cuts_per_10s"],
    # file order: brand_presence and aspect_ratio live in the shared legacy block
    "single": [
        "brand_presence",
        "aspect_ratio",
        "messenger",
        "narration",
        "captions",
        "opening_shot",
        "production",
        "setting",
        "product_reveal",
        "ending",
        "music",
    ],
    "multi": [
        "hook_devices",
        "proof_devices",
        "angles",
        "offer_elements",
        "cta_modes",
        "registers",
    ],
    "ordered": ["beats"],
    "open": [
        "concept_tags",
        "topics",
        "comparison_targets",
        "audience_callouts",
        "claims",
        "visual_motifs",
    ],
    "text": ["hook_verbatim", "concept_summary", "transcript"],
}


def test_version_and_nested_formats():
    t = load_taxonomy()
    assert t.version == "2.0" and len(t.sha256) == 64
    assert t.nested_formats == ("video",)
    assert t.is_nested("video") and not t.is_nested("image") and not t.is_nested("carousel")


def test_image_and_carousel_keep_the_legacy_dimensions():
    t = load_taxonomy()
    assert [d.name for d in t.for_format("image")] == LEGACY_IMAGE_DIMENSIONS
    carousel = [d.name for d in t.for_format("carousel")]
    assert carousel == LEGACY_IMAGE_DIMENSIONS
    assert all(d.kind in ("single", "text") for d in t.for_format("image"))


def test_video_dimensions_by_kind():
    t = load_taxonomy()
    by_kind: dict[str, list[str]] = {}
    for d in t.for_format("video"):
        by_kind.setdefault(d.kind, []).append(d.name)
    assert by_kind == VIDEO_DIMENSIONS
    names = {d.name: d for d in t.for_format("video")}
    assert names["duration_s"].computed and names["aspect_ratio"].computed
    assert not names["messenger"].computed
    assert names["hook_devices"].timestamped and not names["angles"].timestamped
    assert names["concept_tags"].max_values == 6 and names["topics"].max_values is None
    assert names["concept_tags"].vocab_hint == 40
    assert [d.name for d in t.open_dimensions("video")] == VIDEO_DIMENSIONS["open"]


def test_legacy_flat_schema_is_unchanged_for_images():
    schema = load_taxonomy().json_schema("image")
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    item = schema["properties"]["tags"]["items"]
    assert set(item["required"]) == {"dimension", "value", "confidence", "evidence"}
    assert item["properties"]["dimension"]["enum"] == LEGACY_IMAGE_DIMENSIONS
    assert item["properties"]["value"] == {"type": "string"}


def test_nested_schema_enforces_every_enum_and_skips_computed():
    schema = load_taxonomy().json_schema("video")
    assert list(schema["properties"]) == ["single", "multi", "ordered", "open", "text"]
    assert schema["required"] == ["single", "multi", "ordered", "open", "text"]
    single = schema["properties"]["single"]
    assert "aspect_ratio" not in single["properties"]
    messenger = single["properties"]["messenger"]
    assert messenger["properties"]["value"]["enum"][0] == "none"
    assert messenger["required"] == ["value", "confidence", "evidence"]
    hook = schema["properties"]["multi"]["properties"]["hook_devices"]["items"]
    assert hook["required"] == ["value", "evidence", "t_start_s"]
    assert "audience_callout" in hook["properties"]["value"]["enum"]
    angles = schema["properties"]["multi"]["properties"]["angles"]["items"]
    assert "t_start_s" not in angles["properties"]
    beats = schema["properties"]["ordered"]["properties"]["beats"]["items"]
    assert beats["required"] == ["value", "t_start_s"]
    concept = schema["properties"]["open"]["properties"]["concept_tags"]["items"]
    assert concept["required"] == ["slug", "evidence"]
    assert schema["properties"]["text"]["properties"]["transcript"] == {"type": "string"}
    # strict mode: every object lists all of its properties as required
    for section in schema["properties"].values():
        assert section["required"] == list(section["properties"])
        assert section["additionalProperties"] is False


def _payload() -> dict:
    single = {
        "messenger": ("expert_on_camera", 0.9, "coat"),
        "narration": ("on_camera_speech", 1.0, "speaks"),
        "captions": ("text_cards", 0.9, "overlays"),
        "opening_shot": ("face_to_camera", 1.0, "face"),
        "production": ("phone_ugc", 0.8, "handheld"),
        "setting": ("home", 0.9, "kitchen"),
        "product_reveal": ("after_10s", 1.0, "0:55"),
        "brand_presence": ("prominent", 1.0, "logo"),
        "ending": ("cta_card", 1.0, "card"),
        "music": ("none", 1.0, "silent"),
    }
    return {
        "single": {
            k: {"value": v, "confidence": c, "evidence": e} for k, (v, c, e) in single.items()
        },
        "multi": {
            "hook_devices": [
                {"value": "warning_or_risk", "t_start_s": 0.0, "evidence": "a"},
                {"value": "audience_callout", "t_start_s": 1.5, "evidence": "b"},
                {"value": "warning_or_risk", "t_start_s": 2.0, "evidence": "dup"},
            ],
            "proof_devices": [{"value": "expert_credential", "t_start_s": 3.0, "evidence": "c"}],
            "angles": [{"value": "risk_avoidance", "evidence": "d"}],
            "offer_elements": [],
            "cta_modes": [{"value": "end_card", "t_start_s": 88.0, "evidence": "e"}],
            "registers": [
                {"value": "fear", "evidence": "f"},
                {"value": "authority", "evidence": "g"},
            ],
        },
        "ordered": {
            "beats": [
                {"value": "hook", "t_start_s": 0.0},
                {"value": "problem", "t_start_s": 5.0},
                {"value": "proof", "t_start_s": 5.0},
                {"value": "cta", "t_start_s": 85.0},
            ]
        },
        "open": {
            "concept_tags": [
                {"slug": "Pharmacist Warning", "evidence": "h"},
                {"slug": "pharmacist-warning", "evidence": "same after normalization"},
                {"slug": "otc sleep aid risks", "evidence": "i"},
            ],
            "topics": [{"slug": "sleep", "evidence": "j"}],
            "comparison_targets": [],
            "audience_callouts": [],
            "claims": [],
            "visual_motifs": [{"slug": "  Pharmacy--Coat  ", "evidence": "k"}],
        },
        "text": {
            "hook_verbatim": "Worst OTC Sleep Aids!! / As a pharmacist",
            "concept_summary": "A pharmacist warns. She recommends gummies.",
            "transcript": "As a pharmacist with ten years",
        },
    }


def test_validate_nested_flattens_every_kind():
    rows = load_taxonomy().validate(_payload(), "video")
    by_dim: dict[str, list[dict]] = {}
    for r in rows:
        by_dim.setdefault(r["dimension"], []).append(r)
    assert set(rows[0]) == {
        "dimension",
        "kind",
        "value",
        "raw_value",
        "confidence",
        "evidence",
        "t_start_s",
        "ordinal",
    }
    m = by_dim["messenger"][0]
    assert (m["kind"], m["value"], m["confidence"], m["t_start_s"], m["ordinal"]) == (
        "single",
        "expert_on_camera",
        0.9,
        None,
        None,
    )
    assert [(r["value"], r["t_start_s"]) for r in by_dim["hook_devices"]] == [
        ("warning_or_risk", 0.0),
        ("audience_callout", 1.5),
    ]
    assert all(r["confidence"] == 1.0 for r in by_dim["hook_devices"])
    assert by_dim["angles"][0]["t_start_s"] is None
    assert "offer_elements" not in by_dim
    assert [(r["value"], r["ordinal"], r["t_start_s"]) for r in by_dim["beats"]] == [
        ("hook", 0, 0.0),
        ("problem", 1, 5.0),
        ("proof", 2, 5.0),
        ("cta", 3, 85.0),
    ]
    assert [(r["value"], r["raw_value"]) for r in by_dim["concept_tags"]] == [
        ("pharmacist_warning", "Pharmacist Warning"),
        ("otc_sleep_aid_risks", "otc sleep aid risks"),
    ]
    assert by_dim["visual_motifs"][0]["value"] == "pharmacy_coat"
    assert "comparison_targets" not in by_dim
    assert by_dim["transcript"][0] == {
        "dimension": "transcript",
        "kind": "text",
        "value": "As a pharmacist with ten years",
        "raw_value": None,
        "confidence": 1.0,
        "evidence": "",
        "t_start_s": None,
        "ordinal": None,
    }
    # computed dimensions are never produced by the validator
    assert not {"duration_s", "cuts_per_10s", "aspect_ratio"} & set(by_dim)


def test_validate_nested_caps_concept_tags_at_six():
    p = _payload()
    p["open"]["concept_tags"] = [{"slug": f"idea_{i}", "evidence": ""} for i in range(9)]
    rows = load_taxonomy().validate(p, "video")
    assert [r["value"] for r in rows if r["dimension"] == "concept_tags"] == [
        f"idea_{i}" for i in range(6)
    ]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: p["single"]["messenger"].update(value="wizard"), "messenger"),
        (lambda p: p["single"].pop("music"), "music"),
        (lambda p: p["multi"]["angles"].append({"value": "nope", "evidence": ""}), "angles"),
        (lambda p: p["ordered"].update(beats=[]), "beats"),
        (
            lambda p: p["ordered"].update(
                beats=[{"value": "hook", "t_start_s": 3.0}, {"value": "cta", "t_start_s": 1.0}]
            ),
            "beats",
        ),
        (lambda p: p["multi"].update(registers=["fear"]), "registers"),
        (lambda p: p.update(multi=[]), "multi"),
    ],
)
def test_validate_nested_rejects_rule_breaks(mutate, message):
    p = _payload()
    mutate(p)
    with pytest.raises(ValueError, match=message):
        load_taxonomy().validate(p, "video")


def test_validate_nested_clamps_confidence_and_truncates_evidence():
    p = _payload()
    p["single"]["messenger"].update(confidence=7, evidence="x" * 600)
    rows = load_taxonomy().validate(p, "video")
    m = next(r for r in rows if r["dimension"] == "messenger")
    assert m["confidence"] == 1.0 and len(m["evidence"]) == 500


def test_legacy_validate_still_coerces_and_dedupes_for_images():
    t = load_taxonomy()
    out = t.validate(
        [
            {"dimension": "hook_type", "value": "question", "confidence": 1.4, "evidence": "e"},
            {"dimension": "hook_type", "value": "made_up", "confidence": 0.5, "evidence": "e"},
            {"dimension": "pacing", "value": "fast", "confidence": 0.9, "evidence": "e"},
            {
                "dimension": "hook_summary",
                "value": "Opens with a question",
                "confidence": 0.9,
                "evidence": "e",
            },
        ],
        "image",
    )
    assert [(o["dimension"], o["value"], o["confidence"]) for o in out] == [
        ("hook_type", "question", 1.0),
        ("hook_summary", "Opens with a question", 0.9),
    ]


def test_validate_dedupes_repeated_dimension_keeping_highest_confidence():
    t = load_taxonomy()
    out = t.validate(
        [
            {"dimension": "hook_type", "value": "question", "confidence": 0.4, "evidence": "e1"},
            {"dimension": "tone", "value": "warm", "confidence": 0.8, "evidence": "e2"},
            {"dimension": "hook_type", "value": "statistic", "confidence": 0.9, "evidence": "e3"},
        ],
        "image",
    )
    assert [(o["dimension"], o["value"], o["confidence"]) for o in out] == [
        ("hook_type", "statistic", 0.9),
        ("tone", "warm", 0.8),
    ]
