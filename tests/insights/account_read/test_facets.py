from cs.insights.account_read.facets import extract, reveal_bin, tercile_labels


def row(d, k, v, raw=None, t=None, o=None):
    return {"dimension": d, "kind": k, "value": v, "raw_value": raw, "t_start_s": t, "ordinal": o}


def test_extract_closed_open_numeric_text_and_derived():
    rows = [
        row("messenger", "single", "expert_on_camera"),
        row("hook_devices", "multi", "warning_or_risk", t=0),
        row("claims", "open", "promotes_restful_sleep"),
        row("duration_s", "numeric", "93.2"),
        row("scenes", "ordered", "talking_head", raw="face", t=0, o=0),
        row("scenes", "ordered", "product_closeup", raw="face|product", t=27, o=1),
        row("concept_summary", "text", "A pharmacist warns about OTC sleep aids."),
        row("hook_verbatim", "text", "Worst OTC sleep aids"),
        row("beats", "ordered", "hook", t=0, o=0),
        row("transcript", "text", "long transcript"),
    ]
    x = extract(rows, duration_s=93.2)
    assert ("messenger", "expert_on_camera") in x.facets
    assert ("hook_devices", "warning_or_risk") in x.facets
    assert ("claims", "promotes_restful_sleep") in x.facets
    assert ("opening_scene", "talking_head") in x.facets
    assert ("first_product_shown", "10s to midpoint") in x.facets
    assert x.numeric == {"duration_s": 93.2}
    assert x.first_product_s == 27
    assert x.concept_summary.startswith("A pharmacist")
    assert x.hook_verbatim == "Worst OTC sleep aids"
    assert not any(d in ("beats", "transcript", "scenes") for d, _ in x.facets)


def test_reveal_bins():
    assert reveal_bin(None, 60) == "never"
    assert reveal_bin(3, 60) == "under 5s"
    assert reveal_bin(7, 60) == "5 to 10s"
    assert reveal_bin(20, 60) == "10s to midpoint"
    assert reveal_bin(40, 60) == "second half"
    assert reveal_bin(12, None) == "second half"


def test_tercile_labels_and_edges():
    values = {f"u{i}": float(v) for i, v in enumerate([10, 20, 30, 40, 50, 60, 70, 80, 90])}
    labels, edges = tercile_labels(values)
    assert edges == (40.0, 70.0)
    assert labels["u0"] == "low" and labels["u4"] == "mid" and labels["u8"] == "high"
    assert tercile_labels({"a": 1.0, "b": 2.0}) == ({}, None)


def test_opening_shows_reads_the_scenes_and_replaces_opening_shot():
    # opening_shot asks for one of face_to_camera and product_in_hand for an opening that is
    # both, and the tagger splits on it. The scene flags say both, so both are recorded.
    rows = [
        row("opening_shot", "single", "face_to_camera"),
        row("scenes", "ordered", "talking_head", raw="face|product", t=0, o=0),
        row("scenes", "ordered", "talking_head", raw="face", t=8, o=1),
    ]
    x = extract(rows, duration_s=60.0)
    assert ("opening_shows", "face") in x.facets
    assert ("opening_shows", "product") in x.facets
    assert not any(d == "opening_shot" for d, _ in x.facets)


def test_opening_shows_ignores_what_starts_after_the_hook_window():
    rows = [
        row("scenes", "ordered", "talking_head", raw="face", t=0, o=0),
        row("scenes", "ordered", "product_closeup", raw="product", t=9, o=1),
    ]
    x = extract(rows, duration_s=60.0)
    assert {v for d, v in x.facets if d == "opening_shows"} == {"face"}
