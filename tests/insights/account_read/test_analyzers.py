from datetime import date
from decimal import Decimal

import pytest

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


def test_resolve_anchor_best_spend_picks_the_biggest_spender_whatever_the_metric():
    # The high spender is under the purchase floor, so a metric rank would have skipped it.
    low = _unit("low", [], spend=500, purchases=40)
    high = _unit("high", [], spend=9000, purchases=5)
    assert az.resolve_anchor({"best": "spend"}, [low, high], "cpa") is high


def test_resolve_anchor_named_matches_a_slug_typed_with_hyphens_or_spaces():
    a = _unit("a", [("concept_tags", "wrong_supplement_reveal")])
    assert az.resolve_anchor({"named": "wrong-supplement reveal"}, [a], "cpa") is a
    assert az.resolve_anchor({"named": "Wrong Supplement Reveal"}, [a], "cpa") is a


def test_unit_line_and_facet_words():
    u = _unit("u", [], spend=1234, headline="Beware", link_clicks=600)
    line = az.unit_line(u, "link_ctr")
    assert line == '"Beware" | $1,234 spend | 0 copies | link CTR 3.0%'
    below = _unit("b", [], impressions=100)
    assert az.unit_line(below, "link_ctr").endswith("link CTR below floor")
    wrapped = _unit("w", [], headline="Line one\n\nLine two")
    assert az.unit_line(wrapped, "link_ctr").startswith('"Line one Line two" |')
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
    # The flagged effect is kept (heavy spend is what earns the flag) and joins fear's one fact.
    assert out.tests == [fear, flagged, expert]
    assert out.facts == [
        "over-funded | registers = fear | 3 of 10 files | 60% of spend | "
        "link CTR -30% suggestive (P1); link CTR -50% suggestive (P2) | "
        "flags: budget concentration",
        "starved | proof devices = expert credential | 4 of 10 files | 16% of spend | "
        "link CTR +50% suggestive (P3)",
    ]


def test_spend_gap_gives_one_attribute_one_fact_carrying_every_agreeing_metric():
    heavy = [_unit(f"h{i}", [("registers", "fear")], spend=5000) for i in range(3)]
    rest = [_unit(f"r{i}", [], spend=1000) for i in range(7)]
    shas = [u.sha256 for u in heavy]
    ctr = _test("registers", "fear", change=-0.3, p=0.02, cell_shas=shas)
    cpa = _test("registers", "fear", metric="cpa", change=0.4, p=0.01, cell_shas=shas)
    out = az.spend_gap(heavy + rest, [ctr, cpa], ("link_ctr", "cpa"))
    assert out.tests == [cpa, ctr]  # ascending p
    assert out.facts == [
        "over-funded | registers = fear | 3 of 10 files | 68% of spend | "
        "CPA +40% suggestive (P1); link CTR -30% suggestive (P2)"
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


def test_untried_empty_when_every_compatible_combo_is_already_carried():
    A = ("hook_devices", "audience_callout")
    B = ("proof_devices", "expert_credential")
    units = [_unit(f"u{i}", [A, B]) for i in range(6)]  # every file carries both
    shas = [u.sha256 for u in units]
    tests = [_test(*A, change=0.5, cell_shas=shas), _test(*B, change=0.4, cell_shas=shas)]
    out = az.untried(units, tests, [], ("link_ctr",))
    assert out.facts == []
    assert out.empty_reason == (
        "Every compatible pair or triple of proven attributes is already carried by more than "
        "one file."
    )


def test_nearest_finds_underperforming_lookalikes_and_the_diff():
    base = [("messenger", "creator_on_camera"), ("angles", "symptom_relief"), ("setting", "home")]
    d = ("proof_devices", "expert_credential")
    e = ("registers", "urgency")
    anchor = _unit("w", base + [d], link_clicks=1200, headline="Winner")
    better = _unit("z", base + [d], link_clicks=1500)  # outperforms: excluded
    near = [_unit(f"n{i}", base + [e], link_clicks=400) for i in range(6)]  # six, keep five
    far = [_unit(f"f{i}", [("messenger", "graphic_only")], link_clicks=300) for i in range(2)]
    tests = [
        _test(*d, change=0.5, cell_shas=["w", "z"]),
        _test(*d, change=0.9, metric="cpa", cell_shas=["w", "z"]),  # not in layer, ignored
    ]
    out = az.nearest([anchor, better] + near + far, tests, anchor, "link_ctr", ("link_ctr",))
    assert out.empty_reason is None
    assert out.facts[0] == 'anchor | "Winner" | $1,000 spend | 0 copies | link CTR 6.0%'
    neighbours = [f for f in out.facts if f.startswith("neighbour |")]
    assert len(neighbours) == 5
    # Anchor {3 base, d} against neighbour {3 base, e}: 3 shared of 5 -> 60%.
    assert all(f.endswith("| 60% facet overlap") for f in neighbours)
    assert "anchor has, neighbours lack | proof devices = expert credential (P1)" in out.facts
    assert "neighbours have, anchor lacks | registers = urgency" in out.facts
    assert out.tests == [tests[0]]


def test_nearest_ignores_numeric_tercile_facets_in_similarity():
    a = _unit("a", [("duration_s", "high"), ("messenger", "creator_on_camera")], link_clicks=900)
    b = _unit("b", [("duration_s", "low"), ("messenger", "creator_on_camera")], link_clicks=400)
    out = az.nearest([a, b], [], a, "link_ctr", ("link_ctr",))
    assert any(f.endswith("| 100% facet overlap") for f in out.facts)


def test_nearest_empty_when_nothing_underperforms():
    a = _unit("a", [("setting", "home")], link_clicks=400)
    b = _unit("b", [("setting", "home")], link_clicks=900)
    out = az.nearest([a, b], [], a, "link_ctr", ("link_ctr",))
    assert out.empty_reason == 'No file does worse than "Ad a" on link CTR.'


def test_nearest_empty_when_the_anchor_is_below_the_floor():
    anchor = _unit("a", [("setting", "home")], impressions=100, headline="Thin")
    peer = _unit("b", [("setting", "home")], link_clicks=400)
    out = az.nearest([anchor, peer], [], anchor, "link_ctr", ("link_ctr",))
    assert out.empty_reason == '"Thin" is below the floor for link CTR.'
    assert out.facts == [] and out.tests == []


def test_nearest_diff_needs_every_neighbour_when_fewer_than_three():
    base = [("messenger", "creator_on_camera"), ("setting", "home")]
    anchor = _unit("w", base + [("registers", "fear")], link_clicks=1200)
    agree = _unit("n1", base + [("registers", "urgency")], link_clicks=400)
    disagree = _unit("n2", base + [("registers", "fear")], link_clicks=300)
    out = az.nearest([anchor, agree, disagree], [], anchor, "link_ctr", ("link_ctr",))
    neighbours = [f for f in out.facts if f.startswith("neighbour |")]
    assert len(neighbours) == 2
    # Two neighbours: a facet joins the diff only when both disagree with the anchor.
    assert "anchor has, neighbours lack | registers = fear" not in out.facts  # n2 has it
    assert "neighbours have, anchor lacks | registers = urgency" not in out.facts  # only n1
    assert out.facts[-1] == "no facet separates the anchor from its neighbours"


def test_nearest_diff_keeps_twelve_rows_with_the_cited_facets_first():
    shared = [("setting", "home")]
    anchor_only = [("anchor_dims", f"a{i:02d}") for i in range(10)]
    near_only = [("near_dims", f"n{i:02d}") for i in range(10)]
    anchor = _unit("w", shared + anchor_only, link_clicks=1200)
    near = [_unit(f"n{i}", shared + near_only, link_clicks=400) for i in range(3)]
    # Only one facet on each side of the diff carries a test; both must survive the cut.
    cited = [
        _test("anchor_dims", "a09", change=0.5, cell_shas=["w"]),
        _test("near_dims", "n09", change=-0.4, cell_shas=["n0"]),
    ]
    out = az.nearest([anchor] + near, cited, anchor, "link_ctr", ("link_ctr",))
    diff = [f for f in out.facts if f.startswith(("anchor has", "neighbours have"))]
    assert len(diff) == az.MAX_ROWS == 12
    assert diff[0] == "anchor has, neighbours lack | anchor dims = a09 (P1)"
    assert diff[1] == "neighbours have, anchor lacks | near dims = n09 (P2)"
    assert all("(P" not in f for f in diff[2:])
    assert out.tests == cited


def test_recipe_keeps_twelve_ranked_rows():
    facets = [(f"d{i:02d}", "v") for i in range(13)]
    anchor = _unit("w", facets, headline="Winner")
    tests = [_test(d, v, change=0.5 - i * 0.01, cell_shas=["w"]) for i, (d, v) in enumerate(facets)]
    out = az.recipe([anchor], tests, anchor, "link_ctr", ("link_ctr",))
    ranked = [f for f in out.facts if f.startswith("#")]
    assert len(ranked) == az.MAX_ROWS == 12
    assert ranked[0].startswith("#1 | d00 = v |")  # biggest effect first, weakest dropped


def test_controlled_by_spend_yields_one_test_per_tercile_plus_the_overall_reference():
    target = ("messenger", "expert_on_camera")
    units = []
    for i in range(30):
        has = i % 2 == 0
        units.append(
            _unit(
                f"u{i:02d}",
                [target] if has else [],
                spend=100 * (i + 1),  # terciles of ten files, five with and five without
                link_clicks=900 if has else 400,
            )
        )
    overall = _test(*target, change=0.6, grade="strong", cell_shas=[u.sha256 for u in units[::2]])
    out = az.controlled(units, [overall], target, "spend", ("link_ctr",), draws=50)
    assert out.empty_reason is None
    assert out.facts == [
        "spend=low | 5 files with vs 5 without",
        "spend=mid | 5 files with vs 5 without",
        "spend=high | 5 files with vs 5 without",
    ]
    assert out.tests[0] is overall
    strata = [t.group for t in out.tests[1:]]
    assert strata == ["spend=low", "spend=mid", "spend=high"]
    assert all(t.metric == "link_ctr" and t.grade != "ungraded" for t in out.tests[1:])
    # A BH pass over a family of three is not evidence: fresh stratum grades stop at suggestive.
    assert all(t.grade != "strong" for t in out.tests[1:])


def test_controlled_marks_a_stratum_that_never_reached_the_floor():
    target = ("messenger", "expert_on_camera")
    units = []
    for i in range(30):
        has = i in (0, 1) if i < 10 else i % 2 == 0  # low tercile 2/8, mid and high 5/5
        units.append(
            _unit(
                f"u{i:02d}",
                [target] if has else [],
                spend=100 * (i + 1),
                link_clicks=900 if has else 400,
            )
        )
    out = az.controlled(units, [], target, "spend", ("link_ctr",), draws=50)
    assert out.facts[0] == "spend=low | 2 files with vs 8 without | not tested (needs 5 per side)"
    assert out.facts[1] == "spend=mid | 5 files with vs 5 without"
    assert out.facts[2] == "spend=high | 5 files with vs 5 without"
    assert [t.group for t in out.tests] == ["spend=mid", "spend=high"]


def test_controlled_by_partner_attribute_splits_inside_and_outside():
    target = ("proof_devices", "expert_credential")
    partner = ("messenger", "expert_on_camera")
    units = [
        _unit(f"i{i}", [target, partner] if i < 5 else [partner], link_clicks=900 if i < 5 else 400)
        for i in range(10)
    ] + [
        _unit(f"o{i}", [target] if i < 5 else [], link_clicks=700 if i < 5 else 500)
        for i in range(10)
    ]
    out = az.controlled(units, [], target, partner, ("link_ctr",), draws=50)
    assert [t.group for t in out.tests] == [
        "messenger=expert_on_camera",
        "not messenger=expert_on_camera",
    ]


def test_controlled_empty_when_no_stratum_reaches_the_cell_floor():
    target = ("messenger", "expert_on_camera")
    units = [_unit(f"u{i}", [target] if i < 2 else [], spend=100 * i) for i in range(9)]
    out = az.controlled(units, [], target, "spend", ("link_ctr",), draws=50)
    assert out.empty_reason == (
        "No stratum has 5 files on both sides that clear the metric floors "
        "(2,000 impressions for rates, 20 purchases per side for purchase metrics)."
    )


def test_controlled_empty_when_strata_pass_the_cell_floor_but_not_the_purchase_floor():
    target = ("messenger", "expert_on_camera")
    units = [
        _unit(f"u{i:02d}", [target] if i % 2 == 0 else [], spend=100 * (i + 1), purchases=2)
        for i in range(30)
    ]  # every tercile splits 5 with vs 5 without, but 10 purchases a side is under the floor
    out = az.controlled(units, [], target, "spend", ("cpa",), draws=50)
    assert out.tests == []
    assert out.empty_reason == (
        "No stratum has 5 files on both sides that clear the metric floors "
        "(2,000 impressions for rates, 20 purchases per side for purchase metrics)."
    )


def test_recipe_ranks_the_anchor_facets_by_grade_then_change():
    A, B, C = ("messenger", "expert_on_camera"), ("registers", "authority"), ("setting", "home")
    D = ("opening_shot", "face_to_camera")
    anchor = _unit("w", [A, B, C, D], headline="Winner", spend=9000)
    tests = [
        _test(*A, change=0.6, grade="strong", cell_shas=["w"]),
        _test(*B, change=0.3, cell_shas=["w"]),
        _test(*B, change=0.4, grade="strong", group="messenger=expert_on_camera", cell_shas=["w"]),
        _test(
            *B, change=0.9, grade="strong", group="setting=outdoor", cell_shas=["w"]
        ),  # not anchor's
        _test(*C, change=0.2, grade="thin", cell_shas=["w"]),
        _test(*A, change=-0.9, metric="cpa", grade="strong", cell_shas=["w"]),  # not in layer
        # Strong and the biggest effect of all, but it hurts: it ranks behind every helper.
        _test(*D, change=-0.8, grade="strong", cell_shas=["w"]),
    ]
    out = az.recipe([anchor], tests, anchor, "link_ctr", ("link_ctr",))
    assert out.facts[0] == 'anchor | "Winner" | $9,000 spend | 0 copies | link CTR 3.0%'
    assert out.facts[1:] == [
        "#1 | messenger = expert on camera | link CTR +60% strong (P1)",
        "#2 | registers = authority | among files with messenger = expert on camera | "
        "link CTR +40% strong (P2)",
        "#3 | hurts: opening shot = face to camera | link CTR -80% strong (P3)",
    ]
    assert out.tests == [tests[0], tests[2], tests[6]]


def test_recipe_empty_without_graded_facets():
    anchor = _unit("w", [("setting", "home")])
    out = az.recipe([anchor], [], anchor, "link_ctr", ("link_ctr",))
    assert out.empty_reason == (
        "None of this file's attributes has clean graded evidence on these metrics."
    )


def test_run_dispatches_by_kind():
    units = [_unit(f"u{i}", [("angles", "novelty")]) for i in range(6)]
    t = _test("angles", "novelty", change=0.4, cell_shas=[u.sha256 for u in units])
    assert az.run({"kind": "untried"}, units, [t], [], ("link_ctr",), 50).empty_reason
    assert az.run({"kind": "spend_gap"}, units, [t], [], ("link_ctr",), 50).empty_reason
    q = {"kind": "controlled", "target": ("angles", "novelty"), "control": "spend"}
    assert az.run(q, units, [t], [], ("link_ctr",), 50).empty_reason
    out = az.run({"kind": "recipe"}, units, [t], [], ("link_ctr",), 50, anchor=units[0])
    assert out.tests == [t]
    out = az.run({"kind": "nearest"}, units, [t], [], ("link_ctr",), 50, anchor=units[0])
    assert out.empty_reason  # nothing underperforms an identical peer


def test_run_rejects_an_unknown_kind():
    units = [_unit("u0", [("angles", "novelty")])]
    with pytest.raises(ValueError, match="unknown ask kind 'wat'"):
        az.run({"kind": "wat"}, units, [], [], ("link_ctr",), 50, anchor=units[0])


def test_run_passes_the_anchor_metric_through_to_nearest():
    a = _unit("a", [("setting", "home")], spend=3000, purchases=100)  # cpa 30, ppk 5
    b = _unit("b", [("setting", "home")], spend=4000, purchases=100)  # cpa 40, ppk 5
    metrics = ("purchases_per_1k", "cpa")
    by_cpa = az.run({"kind": "nearest"}, [a, b], [], [], metrics, 50, anchor=a, metric="cpa")
    assert by_cpa.empty_reason is None  # b is worse on cpa
    by_default = az.run({"kind": "nearest"}, [a, b], [], [], metrics, 50, anchor=a)
    assert by_default.empty_reason  # equal purchases per 1k: nothing is worse
