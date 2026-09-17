from datetime import date
from decimal import Decimal

from cs.insights.account_read import analyzers as az
from cs.insights.account_read.types import Test, Unit


def _unit(
    sha: str,
    facets,
    *,
    spend: int = 1000,
    impressions: int = 20_000,
    link_clicks: int = 600,
    purchases: int = 30,
    headline: str | None = None,
    concept_summary: str | None = None,
) -> Unit:
    return Unit(
        sha256=sha,
        creative_ids=[],
        rep_creative_id=None,
        headline=headline or f"Ad {sha}",
        duration_s=60.0,
        spend=Decimal(spend),
        impressions=impressions,
        plays=15_000,
        video_3s_views=8_000,
        thruplays=3_000,
        p25=8000,
        p50=5000,
        p75=3000,
        p100=2000,
        link_clicks=link_clicks,
        purchases=purchases,
        first_day=date(2026, 6, 1),
        last_day=date(2026, 6, 30),
        facets=set(facets),
        numeric={},
        first_product_s=None,
        concept_summary=concept_summary,
        hook_verbatim=None,
        insight_paragraph=None,
    )


def _test(
    dim: str,
    val: str,
    *,
    metric: str = "link_ctr",
    change: float = 0.5,
    p: float = 0.01,
    grade: str = "suggestive",
    cell_shas=(),
    group: str | None = None,
    confounds=(),
) -> Test:
    return Test(
        dimension=dim,
        value=val,
        group=group,
        metric=metric,
        kind="pooled" if metric in ("purchases_per_1k", "cpa") else "per_unit",
        n_cell=len(cell_shas),
        n_complement=10,
        cell_value=0.03,
        comp_value=0.02,
        change=change,
        p=p,
        grade=grade,
        confounds=list(confounds),
        cell_shas=list(cell_shas),
    )


def test_is_good_respects_lower_is_better():
    assert az.is_good(_test("a", "b", metric="link_ctr", change=0.2))
    assert not az.is_good(_test("a", "b", metric="link_ctr", change=-0.2))
    assert az.is_good(_test("a", "b", metric="cpa", change=-0.2))
    assert not az.is_good(_test("a", "b", metric="cpa", change=0.2))


def test_clean_main_effects_filters_group_grade_confounds_and_metric():
    keep = _test("a", "b")
    tests = [
        keep,
        _test("a", "c", group="x=y"),
        _test("a", "d", grade="thin"),
        _test("a", "e", confounds=("seasonality",)),
        _test("a", "f", metric="cpa"),
    ]
    assert az.clean_main_effects(tests, ("link_ctr",)) == [keep]


def test_unit_metric_respects_floors():
    u = _unit("u", [], impressions=20_000, link_clicks=600, purchases=30, spend=1500)
    assert az.unit_metric(u, "link_ctr") == 600 / 20_000
    assert az.unit_metric(u, "cpa") == 50.0
    assert az.unit_metric(u, "purchases_per_1k") == 1.5
    low = _unit("l", [], impressions=100, purchases=5)
    assert az.unit_metric(low, "link_ctr") is None
    assert az.unit_metric(low, "cpa") is None


def test_resolve_anchor_best_cpa_ignores_units_below_floor():
    cheap_but_thin = _unit("thin", [], spend=100, purchases=5)  # cpa 20, below purchase floor
    best = _unit("best", [], spend=1200, purchases=40)  # cpa 30
    worse = _unit("worse", [], spend=2000, purchases=40)  # cpa 50
    assert az.resolve_anchor({"best": "cpa"}, [cheap_but_thin, worse, best], "cpa") is best


def test_resolve_anchor_named_matches_headline_summary_and_concept_tags():
    a = _unit("a", [("concept_tags", "pharmacist_warning")], spend=100)
    b = _unit("b", [], headline="The Pharmacist ad", spend=500)
    c = _unit("c", [], concept_summary="A pharmacist explains magnesium.", spend=300)
    d = _unit("d", [], headline="Unrelated")
    assert az.resolve_anchor({"named": "pharmacist"}, [a, b, c, d], "cpa") is b  # tie -> spend
    assert az.resolve_anchor({"named": "nothing here"}, [a, b, c, d], "cpa") is None


def test_unit_line_and_facet_words():
    u = _unit("u", [], spend=1234, headline="Beware", link_clicks=600)
    line = az.unit_line(u, "link_ctr")
    assert line == '"Beware" | $1,234 spend | 0 copies | link CTR 3.0%'
    below = _unit("b", [], impressions=100)
    assert az.unit_line(below, "link_ctr").endswith("link CTR below floor")
    assert az.facet_words(("hook_devices", "audience_callout")) == "hook devices = audience callout"


def test_rank_metric():
    assert az.rank_metric(("hook_rate",)) == "hook_rate"
    assert az.rank_metric(("purchases_per_1k", "cpa")) == "purchases_per_1k"
    assert (
        az.rank_metric(("hook_rate", "thruplay_rate", "link_ctr", "purchases_per_1k", "cpa"))
        == "cpa"
    )


def test_spend_gap_flags_over_funded_and_starved_attributes():
    heavy = [_unit(f"h{i}", [("registers", "fear")], spend=5000) for i in range(3)]
    light = [_unit(f"l{i}", [("proof_devices", "expert_credential")], spend=1000) for i in range(4)]
    rest = [_unit(f"r{i}", [("angles", "novelty")], spend=2000) for i in range(3)]
    units = heavy + light + rest  # total spend 25,000 across 10 files; novelty is proportional
    fear = _test("registers", "fear", change=-0.3, cell_shas=[u.sha256 for u in heavy])
    expert = _test(
        "proof_devices", "expert_credential", change=0.5, cell_shas=[u.sha256 for u in light]
    )
    novelty = _test("angles", "novelty", change=0.4, cell_shas=[u.sha256 for u in rest])
    flagged = _test(
        "registers",
        "fear",
        change=-0.5,
        cell_shas=[u.sha256 for u in heavy],
        confounds=("budget_concentration",),
    )
    out = az.spend_gap(units, [fear, expert, novelty, flagged], ("link_ctr",))
    assert out.empty_reason is None
    assert out.tests == [fear, expert]
    assert out.facts == [
        "over-funded | registers = fear | 3 of 10 files | 60% of spend | "
        "link CTR -30% suggestive (P1)",
        "starved | proof devices = expert credential | 4 of 10 files | 16% of spend | "
        "link CTR +50% suggestive (P2)",
    ]


def test_spend_gap_empty_when_nothing_is_skewed():
    units = [_unit(f"u{i}", [("angles", "novelty")]) for i in range(5)] + [
        _unit(f"v{i}", []) for i in range(5)
    ]
    t = _test("angles", "novelty", change=0.4, cell_shas=[f"u{i}" for i in range(5)])
    out = az.spend_gap(units, [t], ("link_ctr",))
    assert out.tests == [] and out.facts == []
    assert out.empty_reason.startswith("No graded attribute")


def test_single_dims_are_dimensions_no_unit_carries_twice():
    units = [
        _unit(
            "a",
            [("opening_shot", "face_to_camera"), ("registers", "fear"), ("registers", "relief")],
        ),
        _unit("b", [("opening_shot", "scene_broll"), ("registers", "warmth")]),
    ]
    assert az.single_dims(units) == {"opening_shot"}


def test_untried_reports_zero_carrier_combos_and_skips_incompatible_pairs():
    A = ("hook_devices", "audience_callout")
    B = ("proof_devices", "expert_credential")
    C = ("messenger", "expert_on_camera")  # entangled with B
    D = ("opening_shot", "face_to_camera")
    E = ("opening_shot", "scene_broll")  # same single-valued dimension as D
    units = (
        [_unit(f"a{i}", [A, D]) for i in range(3)]  # A+D already combined
        + [_unit(f"b{i}", [B, C, E]) for i in range(3)]
        + [_unit(f"z{i}", []) for i in range(6)]
    )
    tests = [
        _test(*A, change=0.5, cell_shas=["a0", "a1", "a2"]),
        _test(*B, change=0.4, cell_shas=["b0", "b1", "b2"]),
        _test(*C, change=0.3, cell_shas=["b0", "b1", "b2"]),
        _test(*D, change=0.2, cell_shas=["a0", "a1", "a2"]),
        _test(*E, change=0.1, cell_shas=["b0", "b1", "b2"]),
        _test(*A, change=-0.5, metric="cpa", cell_shas=["a0", "a1", "a2"]),  # not in layer
    ]
    overlaps = [{"a": list(B), "b": list(C), "jaccard": 1.0}]
    out = az.untried(units, tests, overlaps, ("link_ctr",))
    assert out.empty_reason is None
    names = [f.split(" | ")[0] for f in out.facts]
    assert "hook devices = audience callout + proof devices = expert credential" in names
    assert (
        "hook devices = audience callout + opening shot = face to camera" not in names
    )  # 3 carry it
    entangled_pair = "messenger = expert on camera + proof devices = expert credential"
    assert not any(entangled_pair in n for n in names)  # entangled, skipped in pairs and triples
    assert not any("face to camera + opening shot" in n for n in names)  # same single dim
    # Highest summed effect first: A (0.5) + B (0.4) + D (0.2), never combined.
    assert out.facts[0] == (
        "hook devices = audience callout + opening shot = face to camera + "
        "proof devices = expert credential | 0 of 12 files carry all 3 | ingredients P1, P2, P3"
    )
    assert [(t.dimension, t.value) for t in out.tests[:3]] == [A, D, B]
    assert all(t.metric == "link_ctr" for t in out.tests)


def test_untried_empty_with_one_candidate():
    units = [_unit(f"u{i}", [("angles", "novelty")]) for i in range(6)]
    t = _test("angles", "novelty", change=0.4, cell_shas=[u.sha256 for u in units])
    out = az.untried(units, [t], [], ("link_ctr",))
    assert out.empty_reason == (
        "Fewer than two attributes have clean positive evidence on these metrics."
    )
