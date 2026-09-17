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
