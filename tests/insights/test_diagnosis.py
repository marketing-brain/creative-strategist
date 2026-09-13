import json
from datetime import date
from decimal import Decimal

import pytest

from cs.insights.diagnosis import (
    Sums,
    attention_tier,
    context_tier,
    conversion_tier,
    diagnose,
    fatigue_tier,
    lift,
    retention_tier,
    status_from_lift,
)


def sums(**kw) -> Sums:
    return Sums(**kw)


def test_lift_and_status():
    assert lift(0.38, 0.31) == pytest.approx(0.225806, abs=1e-6)
    assert lift(None, 0.3) is None and lift(0.3, None) is None and lift(0.3, 0.0) is None
    assert status_from_lift(0.2) == "strong"
    assert status_from_lift(0.15) == "strong"
    assert status_from_lift(0.1) == "on_par"
    assert status_from_lift(-0.15) == "weak"
    assert status_from_lift(None) == "not_enough_data"


def test_sums_from_record_coerces_nulls_and_decimals():
    s = Sums.from_record(
        {"impressions": None, "spend": Decimal("1.5"), "video_watch_seconds": None}
    )
    assert s.impressions == 0 and s.spend == Decimal("1.5")
    assert s.video_watch_seconds == Decimal(0)


def test_attention_video_uses_hook_rate_and_reports_ctr_too():
    w = sums(impressions=10_000, video_3s_views=3_800, link_clicks=140)
    b = sums(impressions=100_000, video_3s_views=31_000, link_clicks=1_200)
    t = attention_tier("video", w, b)
    assert t.tier == 1 and t.key == "attention" and t.status == "strong"
    assert [m.key for m in t.metrics] == ["hook_rate", "link_ctr"]
    hook = t.metrics[0]
    assert hook.value == pytest.approx(0.38) and hook.baseline == pytest.approx(0.31)
    assert hook.lift == pytest.approx(0.2258, abs=1e-3)
    assert t.decision is None


def test_attention_image_uses_link_ctr_only_and_weak_gets_decision():
    w = sums(impressions=5_000, link_clicks=40)  # 0.8%
    b = sums(impressions=50_000, link_clicks=600)  # 1.2%
    t = attention_tier("image", w, b)
    assert [m.key for m in t.metrics] == ["link_ctr"]
    assert t.status == "weak"
    assert "stopping the scroll" in t.decision


def test_attention_floor_applies_to_creative_and_baseline():
    big = sums(impressions=50_000, video_3s_views=10_000)
    t = attention_tier("video", sums(impressions=900, video_3s_views=400), big)
    assert t.status == "not_enough_data" and "1,000" in t.reason
    small = sums(impressions=500, video_3s_views=100)
    t = attention_tier("video", sums(impressions=5_000, video_3s_views=400), small)
    assert t.status == "not_enough_data"


def test_retention_not_applicable_for_images_and_carousels():
    for fmt in ("image", "carousel"):
        t = retention_tier(fmt, sums(), sums(), None)
        assert t.tier == 2 and t.status == "not_applicable" and t.curve is None
        assert t.reason


def _retention_pair():
    w = sums(
        video_3s_views=1_000, video_thruplay=210, video_plays=1_000, video_p25=710,
        video_p50=290, video_p75=180, video_p95=140, video_p100=130,
        video_watch_seconds=Decimal("7100"),
    )  # fmt: skip
    b = sums(
        video_3s_views=10_000, video_thruplay=3_400, video_plays=10_000, video_p25=7_500,
        video_p50=5_400, video_p75=4_200, video_p95=3_500, video_p100=3_200,
        video_watch_seconds=Decimal("110000"),
    )  # fmt: skip
    return w, b


def test_retention_curve_hold_rate_and_drop_segment():
    w, b = _retention_pair()
    t = retention_tier("video", w, b, 24_000)
    assert t.status == "weak"
    hold = t.metrics[0]
    assert hold.key == "hold_rate"
    assert hold.value == pytest.approx(0.21) and hold.baseline == pytest.approx(0.34)
    assert [p.p for p in t.curve.points] == [0, 25, 50, 75, 95, 100]
    assert t.curve.points[0].share == 1.0 and t.curve.points[0].baseline_share == 1.0
    assert t.curve.points[2].share == pytest.approx(0.29)
    # Biggest excess drop vs baseline is p25 -> p50 (0.42 drop vs 0.21 baseline drop).
    assert t.curve.drop_segment == (25, 50)
    assert t.curve.avg_watch_s == pytest.approx(7.1) and t.curve.duration_s == 24.0
    assert "between 25% and 50%" in t.decision and "6s" in t.decision and "12s" in t.decision


def test_retention_strong_has_no_drop_segment_and_floor_applies():
    _, b = _retention_pair()
    w = sums(
        video_3s_views=1_000, video_thruplay=500, video_plays=1_000, video_p25=900,
        video_p50=700, video_p75=600, video_p95=550, video_p100=500,
    )  # fmt: skip
    t = retention_tier("video", w, b, None)
    assert t.status == "strong" and t.curve.drop_segment is None and t.decision is None
    assert t.curve.duration_s is None
    few = sums(video_plays=50, video_3s_views=60, video_thruplay=10)
    t = retention_tier("video", few, b, None)
    assert t.status == "not_enough_data" and "100" in t.reason


def test_conversion_steps_readable_chain_and_on_par():
    w = sums(
        link_clicks=5_800, landing_page_views=3_700, content_views=1_780, add_to_carts=196,
        checkouts=80, purchases=30, spend=Decimal("1230"), conversions=Decimal(30),
    )  # fmt: skip
    b = sums(
        link_clicks=58_000, landing_page_views=35_400, content_views=15_900,
        add_to_carts=1_900, checkouts=740, purchases=270, spend=Decimal("11880"),
        conversions=Decimal(270),
    )  # fmt: skip
    t = conversion_tier(w, b)
    assert t.tier == 3 and t.status == "on_par"
    keys = ["landing_page", "content_view", "add_to_cart", "checkout", "purchase"]
    assert [s.key for s in t.steps] == keys
    lp = t.steps[0]
    assert lp.rate == pytest.approx(3_700 / 5_800)
    assert lp.baseline_rate == pytest.approx(35_400 / 58_000)
    assert lp.numerator == 3_700 and lp.denominator == 5_800 and lp.readable
    # checkouts=80 < 100 -> purchase step unreadable, everything before it readable.
    assert [s.readable for s in t.steps] == [True, True, True, True, False]
    assert t.steps[4].rate is None and t.steps[4].denominator == 80
    cpa = t.metrics[0]
    assert cpa.key == "cpa" and cpa.value == pytest.approx(41.0)
    assert cpa.baseline == pytest.approx(44.0)
    assert cpa.lift == pytest.approx(0.068, abs=1e-3)  # lower CPA is better: sign flipped


def test_conversion_weak_names_worst_step():
    w = sums(
        link_clicks=5_000, landing_page_views=3_000, content_views=600, add_to_carts=300,
        checkouts=150, purchases=60,
    )  # fmt: skip
    b = sums(
        link_clicks=50_000, landing_page_views=30_000, content_views=14_000,
        add_to_carts=3_000, checkouts=1_500, purchases=600,
    )  # fmt: skip
    t = conversion_tier(w, b)
    assert t.status == "weak" and "Content view" in t.decision


def test_conversion_strong_requires_end_to_end_lift_and_not_enough_data():
    w = sums(
        link_clicks=5_000, landing_page_views=3_500, content_views=2_000, add_to_carts=600,
        checkouts=400, purchases=200,
    )  # fmt: skip
    b = sums(
        link_clicks=50_000, landing_page_views=30_000, content_views=15_000,
        add_to_carts=3_000, checkouts=1_500, purchases=600,
    )  # fmt: skip
    assert conversion_tier(w, b).status == "strong"
    t = conversion_tier(sums(link_clicks=40), b)
    assert t.status == "not_enough_data" and all(not s.readable for s in t.steps)


def test_fatigue_too_young_and_floors():
    t = fatigue_tier("video", None, None, 9)
    assert t.tier == 4 and t.status == "too_young" and "day 21" in t.reason
    assert fatigue_tier("video", None, None, None).status == "too_young"
    t = fatigue_tier("video", sums(impressions=500), sums(impressions=5_000), 30)
    assert t.status == "not_enough_data"


def test_fatigue_early_decay_and_stable():
    first = sums(
        impressions=20_000, reach=14_300, video_3s_views=8_200, link_clicks=300,
        post_reactions=300, comments=60, saves=60,
    )  # fmt: skip
    trailing = sums(
        impressions=10_000, reach=4_350, video_3s_views=3_300, link_clicks=120,
        post_reactions=120, comments=30, saves=40,
    )  # fmt: skip
    t = fatigue_tier("video", first, trailing, 40)
    assert t.status == "weak"
    freq, hook, eng = t.metrics
    assert freq.key == "frequency" and freq.value == pytest.approx(10_000 / 4_350)
    assert freq.baseline == pytest.approx(20_000 / 14_300)
    assert freq.lift > 0.3
    assert hook.key == "hook_rate" and hook.lift < -0.15
    assert eng.key == "engagement_rate" and eng.value == pytest.approx(190 / 10_000)
    assert "Frequency is climbing" in t.decision
    # Frequency up but hook and engagement holding -> stable.
    steady = sums(
        impressions=10_000, reach=4_350, video_3s_views=4_100, link_clicks=150,
        post_reactions=150, comments=30, saves=30,
    )  # fmt: skip
    assert fatigue_tier("video", first, steady, 40).status == "on_par"
    # Image creatives use link CTR as the attention trend.
    assert fatigue_tier("image", first, trailing, 40).metrics[1].key == "link_ctr"


def test_context_is_always_locked():
    t = context_tier()
    assert t.tier == 5 and t.status == "not_measured"
    assert "not connected" in t.reason and t.metrics == []


def _video_window_weak_retention():
    w = sums(
        impressions=412_000, link_clicks=5_800, video_3s_views=156_000, video_thruplay=32_800,
        video_plays=156_000, video_p25=110_800, video_p50=45_200, video_p75=28_100,
        video_p95=21_800, video_p100=20_300, landing_page_views=3_700, content_views=1_780,
        add_to_carts=196, checkouts=80, purchases=30, spend=Decimal("1230"),
        conversions=Decimal(30),
    )  # fmt: skip
    b = sums(
        impressions=4_000_000, link_clicks=48_000, video_3s_views=1_240_000,
        video_thruplay=421_600, video_plays=1_240_000, video_p25=930_000, video_p50=670_000,
        video_p75=520_000, video_p95=434_000, video_p100=397_000, landing_page_views=29_300,
        content_views=13_200, add_to_carts=1_580, checkouts=616, purchases=222,
        spend=Decimal("9800"), conversions=Decimal(222),
    )  # fmt: skip
    return w, b


def _diagnose(**over):
    w, b = _video_window_weak_retention()
    kw = dict(
        fmt="video", window=w, baseline=b, baseline_creatives=41, first14=None, trailing7=None,
        days_live=12, duration_ms=24_000, window_start=date(2026, 8, 14),
        window_end=date(2026, 9, 12),
    )  # fmt: skip
    kw.update(over)
    return diagnose(**kw)


def test_diagnose_broken_layer_is_first_weak_tier():
    d = _diagnose()
    keys = ["attention", "retention", "conversion", "fatigue", "context"]
    assert [t.key for t in d.tiers] == keys
    assert d.tiers[0].status == "strong" and d.tiers[1].status == "weak"
    assert d.tiers[3].status == "too_young"
    assert d.broken_tier == 2
    assert d.verdict.kind == "broken"
    assert d.verdict.headline.startswith("Broken layer: Retention.")
    assert "Attention is working" in d.verdict.detail
    out = d.to_dict()
    assert out["window_start"] == "2026-08-14" and out["format"] == "video"
    assert out["baseline_creatives"] == 41
    assert out["tiers"][1]["curve"]["drop_segment"] == [25, 50]
    assert out["verdict"]["kind"] == "broken"
    json.dumps(out)  # every value must be JSON-native


def test_diagnose_healthy_and_too_early():
    w, _ = _video_window_weak_retention()
    better = {"video_thruplay": 60_000, "video_p50": 90_000, "video_p75": 70_000}
    strong = Sums(**{**w.__dict__, **better})
    d = _diagnose(window=strong, duration_ms=None)
    assert d.broken_tier is None and d.verdict.kind == "healthy"
    d = _diagnose(fmt="image", window=sums(impressions=640), days_live=3, duration_ms=None)
    assert d.verdict.kind == "too_early" and "640" in d.verdict.detail
    assert d.broken_tier is None
