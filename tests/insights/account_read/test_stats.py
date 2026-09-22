from datetime import date
from decimal import Decimal

from cs.insights.account_read.stats import (
    benjamini_hochberg,
    compare,
    grade,
    mann_whitney_p,
    permutation_p,
    pooled,
    unit_rates,
)
from cs.insights.account_read.types import Unit


def unit(
    sha,
    imp=10_000,
    v3=4_000,
    plays=8_000,
    thru=1_200,
    lc=300,
    purch=30,
    spend=1_000,
    first=date(2026, 6, 1),
    last=date(2026, 6, 30),
    facets=(),
):
    return Unit(
        sha256=sha,
        creative_ids=[],
        rep_creative_id=None,
        headline=sha,
        duration_s=60.0,
        spend=Decimal(spend),
        impressions=imp,
        plays=plays,
        video_3s_views=v3,
        thruplays=thru,
        p25=plays // 2,
        p50=plays // 3,
        p75=plays // 4,
        p100=plays // 5,
        link_clicks=lc,
        purchases=purch,
        first_day=first,
        last_day=last,
        facets=set(facets),
        numeric={},
        first_product_s=None,
        concept_summary=None,
        hook_verbatim=None,
    )


def test_mann_whitney_matches_known_value():
    # scipy two-sided p for [1..5] vs [6..10] is ~0.008 exact, ~0.009 asymptotic
    p = mann_whitney_p([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
    assert 0.005 < p < 0.02
    assert mann_whitney_p([1, 2, 3], [1, 2, 3]) == 1.0


def test_benjamini_hochberg_passes_only_small_p():
    assert benjamini_hochberg([0.001, 0.02, 0.04, 0.5, 0.9], q=0.10) == {0, 1, 2}
    assert benjamini_hochberg([0.5, 0.6]) == set()


def test_grade_boundaries():
    assert grade(0.5, 0.5, True) == "strong"
    assert grade(0.05, 0.01, False) == "suggestive"
    assert grade(0.2, 0.2, False) == "directional"
    assert grade(0.2, 0.05, False) == "thin"
    assert grade(0.5, 0.5, False) == "thin"


def test_permutation_detects_planted_difference():
    cell = [unit(f"c{i}", purch=60) for i in range(8)]
    comp = [unit(f"k{i}", purch=20) for i in range(8)]

    def stat(a, b):
        ra, rb = pooled(a, "purchases", "impressions"), pooled(b, "purchases", "impressions")
        return None if not ra or not rb else ra / rb - 1

    assert permutation_p(cell, comp, stat, draws=400) < 0.05


def test_unit_rates_respects_floors():
    assert unit_rates(unit("a")) == {"hook_rate": 0.4, "thruplay_rate": 0.15, "link_ctr": 0.03}
    assert "hook_rate" not in unit_rates(unit("b", imp=1_999))
    assert "thruplay_rate" not in unit_rates(unit("c", plays=499))


def test_compare_emits_rate_and_pooled_tests_with_confounds():
    cell = [unit(f"c{i}", lc=600, imp=10_000 if i else 200_000, purch=40) for i in range(6)]
    comp = [unit(f"k{i}", lc=300, first=date(2026, 8, 1), last=date(2026, 8, 30)) for i in range(6)]
    tests = compare(cell, comp, dimension="angles", value="novelty", draws=200)
    by_metric = {t.metric: t for t in tests}
    assert set(by_metric) == {"hook_rate", "thruplay_rate", "link_ctr", "purchases_per_1k", "cpa"}
    ctr = by_metric["link_ctr"]
    assert ctr.n_cell == 6 and ctr.n_complement == 6 and ctr.change > 0
    assert ctr.kind == "per_unit" and ctr.group is None and ctr.grade == "ungraded"
    assert "budget_concentration" in ctr.confounds and "seasonality" in ctr.confounds
    assert by_metric["cpa"].kind == "pooled"


def test_compare_returns_nothing_below_minimums():
    assert compare([unit("a")] * 4, [unit("b")] * 6, dimension="d", value="v") == []
