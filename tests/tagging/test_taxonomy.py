from cs.tagging.taxonomy import load_taxonomy


def test_load_and_filter():
    t = load_taxonomy()
    assert t.version == "1.0" and len(t.sha256) == 64
    names = {d.name for d in t.for_format("image")}
    assert "hook_type" in names and "pacing" not in names
    assert "pacing" in {d.name for d in t.for_format("video")}


def test_json_schema_shape():
    schema = load_taxonomy().json_schema("video")
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    item = schema["properties"]["tags"]["items"]
    assert set(item["required"]) == {"dimension", "value", "confidence", "evidence"}
    assert "pacing" in item["properties"]["dimension"]["enum"]


def test_validate_coerces_and_drops():
    t = load_taxonomy()
    out = t.validate(
        [
            {"dimension": "hook_type", "value": "question", "confidence": 1.4, "evidence": "e"},
            {"dimension": "hook_type", "value": "made_up", "confidence": 0.5, "evidence": "e"},
            {"dimension": "pacing", "value": "fast", "confidence": 0.9, "evidence": "e"},
            {"dimension": "nope", "value": "x", "confidence": 0.9, "evidence": "e"},
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
