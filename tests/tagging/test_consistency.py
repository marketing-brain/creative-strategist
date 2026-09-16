from cs.tagging.consistency import agreement, summarize
from cs.tagging.taxonomy import load_taxonomy


def _r(dimension, kind, value, ordinal=None):
    return {
        "dimension": dimension,
        "kind": kind,
        "value": value,
        "raw_value": None,
        "confidence": 1.0,
        "evidence": "",
        "t_start_s": None,
        "ordinal": ordinal,
    }


def test_agreement_per_kind():
    t = load_taxonomy()
    a = [
        _r("messenger", "single", "expert_on_camera"),
        _r("duration_s", "numeric", "93.2"),
        _r("hook_devices", "multi", "warning_or_risk"),
        _r("hook_devices", "multi", "audience_callout"),
        _r("beats", "ordered", "hook", 0),
        _r("beats", "ordered", "problem", 1),
        _r("concept_tags", "open", "pharmacist_warning"),
        _r("transcript", "text", "words"),
    ]
    b = [
        _r("messenger", "single", "creator_on_camera"),
        _r("duration_s", "numeric", "93.2"),
        _r("hook_devices", "multi", "warning_or_risk"),
        _r("beats", "ordered", "hook", 0),
        _r("beats", "ordered", "problem", 1),
        _r("concept_tags", "open", "pharmacist_warning"),
        _r("concept_tags", "open", "benadryl_swap"),
        _r("transcript", "text", "other words"),
    ]
    got = agreement(a, b, t, "video")
    assert got["messenger"] == 0.0 and got["duration_s"] == 1.0
    assert got["hook_devices"] == 0.5
    assert got["beats"] == 1.0 and got["beats:set"] == 1.0
    assert got["concept_tags"] == 0.5
    assert got["transcript"] is None
    assert got["angles"] == 1.0  # both empty


def test_ordered_disagrees_on_order_and_length():
    t = load_taxonomy()
    a = [_r("beats", "ordered", "hook", 0), _r("beats", "ordered", "problem", 1)]
    b = [_r("beats", "ordered", "problem", 0), _r("beats", "ordered", "hook", 1)]
    got = agreement(a, b, t, "video")
    assert got["beats"] == 0.0
    assert got["beats:set"] == 1.0  # same steps, different order
    shorter = agreement(a, a[:1], t, "video")
    assert shorter["beats"] == 0.0 and shorter["beats:set"] == 0.5


def test_summarize_means_and_ignores_none():
    out = summarize(
        [{"messenger": 1.0, "transcript": None}, {"messenger": 0.0, "transcript": None}]
    )
    assert out == {"messenger": 0.5, "transcript": None}
