import random
from datetime import date
from decimal import Decimal

from cs.insights.account_read.patterns import run
from cs.insights.account_read.types import Unit


def synth(seed: int, n: int = 40, planted: str | None = "warning_or_risk", lift: float = 0.35):
    rng = random.Random(seed)
    units = []
    for i in range(n):
        has_plant = i % 2 == 0
        hook = 0.40 * (1 + rng.uniform(-0.15, 0.15))
        if has_plant and planted:
            hook *= 1 + lift
        imp = 20_000
        facets = {
            ("messenger", "creator_on_camera" if i % 3 else "expert_on_camera"),
            ("angles", "convenience"),
            ("first_product_shown", "under 5s" if i % 4 == 0 else "second half"),
        }
        if has_plant:
            facets.add(("hook_devices", "warning_or_risk"))
        if i % 5 == 0:
            facets.add(("registers", "humor"))  # null facet, no planted effect
        units.append(
            Unit(
                sha256=f"u{i}",
                creative_ids=[],
                rep_creative_id=None,
                headline=f"ad {i}",
                duration_s=60.0,
                spend=Decimal(1000),
                impressions=imp,
                plays=15_000,
                video_3s_views=int(hook * imp),
                thruplays=3000,
                p25=8000,
                p50=5000,
                p75=3000,
                p100=2000,
                link_clicks=600,
                purchases=25 + (i % 7),
                first_day=date(2026, 6, 1),
                last_day=date(2026, 6, 30),
                facets=facets,
                numeric={"duration_s": 60.0},
                first_product_s=2.0 if i % 4 == 0 else 40.0,
                concept_summary=f"concept {i}",
                hook_verbatim=f"hook {i}",
                insight_paragraph=None,
            )
        )
    return units


def test_planted_effect_is_found_and_graded():
    r = run(synth(1), {}, grouping_dims=["messenger"], draws=200)
    hits = [
        t
        for t in r.tests
        if t.group is None
        and t.dimension == "hook_devices"
        and t.value == "warning_or_risk"
        and t.metric == "hook_rate"
    ]
    assert len(hits) == 1 and hits[0].grade in ("strong", "suggestive") and hits[0].change > 0.2
    assert r.summary["units"] == 40 and r.summary["by_grade"]["strong"] >= 1
    assert all(t.grade != "ungraded" for t in r.tests)


def test_null_facet_rarely_reaches_suggestive():
    false_hits = 0
    for seed in range(30):
        r = run(synth(seed, planted=None), {}, grouping_dims=[], draws=50)
        false_hits += any(
            t.dimension == "registers" and t.grade in ("strong", "suggestive") for t in r.tests
        )
    assert false_hits <= 6


def test_interactions_only_within_groups_of_ten_or_more():
    r = run(synth(2), {}, grouping_dims=["messenger"], draws=100)
    groups = {t.group for t in r.tests if t.group}
    assert "messenger=creator_on_camera" in groups  # 27 units
    assert all(g.startswith("messenger=") for g in groups)
    # a group of 13 units is allowed (bound is >= 10); a group under 10 never appears
    small = run(synth(2, n=12), {}, grouping_dims=["messenger"], draws=50)
    assert "messenger=expert_on_camera" not in {t.group for t in small.tests if t.group}


def test_entanglement_detects_shared_units_and_stratifies():
    units = synth(3)
    for u in units:  # make 'urgency' ride on the planted attribute
        if u.has("hook_devices", "warning_or_risk"):
            u.facets.add(("registers", "urgency"))
    r = run(units, {}, grouping_dims=[], draws=100)
    pairs = {frozenset({tuple(o["a"]), tuple(o["b"])}) for o in r.overlaps}
    assert frozenset({("hook_devices", "warning_or_risk"), ("registers", "urgency")}) in pairs
    strat = [
        s
        for s in r.stratified
        if tuple(s["attribute"]) == ("hook_devices", "warning_or_risk")
        and s["metric"] == "hook_rate"
    ]
    assert strat and strat[0]["within"]["change"] is None  # nothing left to compare inside


def test_retention_by_reveal_bin_has_rows():
    r = run(synth(4), {}, grouping_dims=[], draws=50)
    assert set(r.retention_by_reveal) == {"under 5s", "second half"}
    row = r.retention_by_reveal["under 5s"]
    assert row["n"] == 10 and 0 < row["p50"] < 1
