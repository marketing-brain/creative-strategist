"""The allocation block decides which live ad gets budget tomorrow. These pin the ordering
and the one judgement in it -- what counts as under-funded."""

from cs.insights.next_plan.allocate import DECLINE_CAP, DiagnosedAd, build, choose_decline
from cs.insights.next_plan.types import Decline


def _decline(metric="cpa", ratio=2.0):
    return Decline(
        metric=metric, baseline=10.0, observed=10.0 * ratio, ratio=ratio, capped=ratio > DECLINE_CAP
    )


def _ad(cid: str, spend: float, *, kind: str = "healthy", fatigued: bool = False, layer=None):
    return DiagnosedAd(
        creative_id=cid,
        headline=f"Ad {cid}",
        rep_creative_id=cid,
        spend_30d=spend,
        verdict_kind=kind,
        verdict_headline="No broken layer." if kind == "healthy" else f"Broken layer: {layer}.",
        verdict_detail="detail",
        broken_layer=layer,
        decline=_decline() if fatigued else None,
    )


def test_money_leaking_now_outranks_an_opportunity():
    """Fix, then replace, then scale -- each ranked inside itself. A scale candidate is small
    precisely because it has not been scaled, so one mixed spend ordering would bury it."""
    ads = [
        _ad("small-healthy", 100.0),
        _ad("broken", 5_000.0, kind="broken", layer="Attention"),
        _ad("tired", 3_000.0, fatigued=True),
    ]
    rows, reason = build(ads, account_spend_30d=8_100.0)
    assert reason is None
    assert [r.kind for r in rows] == ["fix", "replace", "scale"]
    assert rows[0].broken_layer == "Attention"
    assert rows[0].stake.window == "30d"


def test_a_broken_ad_is_a_fix_even_when_it_is_also_fatigued():
    ads = [_ad("x", 900.0, kind="broken", layer="Click", fatigued=True)]
    rows, _ = build(ads, account_spend_30d=900.0)
    assert [r.kind for r in rows] == ["fix"]


def test_a_healthy_ad_already_taking_its_share_is_not_a_scale_row():
    """Three healthy ads splitting the account evenly are funded, not under-funded."""
    ads = [_ad(f"u{i}", 1_000.0) for i in range(3)]
    rows, reason = build(ads, account_spend_30d=3_000.0)
    assert rows == []
    assert reason is not None and "already funded" in reason


def test_a_healthy_ad_well_under_the_average_share_is_a_scale_row():
    ads = [_ad("big", 9_000.0), _ad("starved", 100.0), _ad("mid", 900.0)]
    rows, _ = build(ads, account_spend_30d=10_000.0)
    # shares are 90%, 9%, 1%; the median is 9% and the floor half that, so only 1% clears.
    assert [r.creative_id for r in rows] == ["starved"]
    assert "median spending ad" in rows[0].detail


def test_an_ad_too_young_to_diagnose_is_not_a_row():
    ads = [_ad("new", 4_000.0, kind="too_early")]
    rows, reason = build(ads, account_spend_30d=4_000.0)
    assert rows == []
    assert reason is not None


def test_an_account_with_no_spend_says_so_rather_than_showing_nothing():
    rows, reason = build([_ad("idle", 0.0)], account_spend_30d=0.0)
    assert rows == []
    assert reason is not None and "thirty days" in reason


def test_a_cost_row_is_preferred_because_replacement_is_a_money_decision():
    rows = [
        {"metric": "ctr", "baseline": 0.04, "observed": 0.02},
        {"metric": "cpa", "baseline": 30.0, "observed": 63.0},
    ]
    d = choose_decline(rows)
    assert d is not None and d.metric == "cpa"
    assert round(d.ratio, 2) == 2.1
    assert not d.capped


def test_an_absurd_cost_ratio_defers_to_the_click_through_row():
    """`signed_lift` produced -1386% on org 3. The fatigue job has a spend floor but no
    purchase floor, so one purchase in the trailing week can read as a fifteen-fold rise."""
    rows = [
        {"metric": "cpa", "baseline": 30.0, "observed": 30.0 * 15},
        {"metric": "ctr", "baseline": 0.04, "observed": 0.026},
    ]
    d = choose_decline(rows)
    assert d is not None and d.metric == "ctr"
    assert round(d.ratio, 2) == 0.65


def test_an_absurd_cost_ratio_alone_is_kept_but_capped():
    d = choose_decline([{"metric": "cpa", "baseline": 30.0, "observed": 30.0 * 15}])
    assert d is not None and d.metric == "cpa" and d.capped


def test_a_row_with_no_baseline_is_not_a_decline():
    assert choose_decline([{"metric": "cpa", "baseline": 0, "observed": 5.0}]) is None
    assert choose_decline([]) is None


def test_the_verdict_names_the_metric_that_moved():
    rows, _ = build([_ad("a", 900.0, fatigued=True)], account_spend_30d=900.0)
    assert "Cost per purchase is 2.0 times" in rows[0].verdict
    assert rows[0].decline is not None and rows[0].decline.metric == "cpa"
