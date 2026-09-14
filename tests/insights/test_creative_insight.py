from datetime import date

from cs.insights.creative_insight import (
    evidence_tier,
    grounding_problems,
    is_readable,
    mmss,
    render_brief,
    user_text,
)
from cs.insights.diagnose_creative import DiagnosedCreative
from cs.insights.diagnosis import Sums, diagnose

END = date(2026, 9, 13)
START = date(2026, 8, 15)


def _video_sums(scale: int, *, p25: float, p50: float, thru: float, hook: float) -> Sums:
    plays = int(20_000 * scale * hook)
    return Sums(
        impressions=20_000 * scale,
        reach=9_000 * scale,
        link_clicks=200 * scale,
        video_3s_views=plays,
        video_plays=plays,
        video_p25=int(plays * p25),
        video_p50=int(plays * p50),
        video_p75=int(plays * p50 / 2),
        video_p95=int(plays * p50 / 3),
        video_p100=int(plays * p50 / 4),
        video_thruplay=int(plays * thru),
        video_watch_seconds=plays * 6,
    )


def _diagnosed(
    fmt: str, window: Sums, baseline: Sums, duration_ms: int | None
) -> DiagnosedCreative:
    dx = diagnose(
        fmt=fmt,
        window=window,
        baseline=baseline,
        baseline_creatives=18,
        first14=None,
        trailing7=None,
        days_live=12,
        duration_ms=duration_ms,
        window_start=START,
        window_end=END,
        retention_baseline=baseline if fmt == "video" else None,
        retention_baseline_creatives=18,
    )
    return DiagnosedCreative(dx, window, baseline, None, None, date(2026, 9, 2), 12)


CREATIVE = {
    "format": "video",
    "headline": "Sleep better tonight",
    "primary_text": "50% off this week",
    "description": None,
    "cta_type": "SHOP_NOW",
    "link_url": "https://example.test/p",
}


def weak_retention(duration_ms: int | None = 24_000) -> DiagnosedCreative:
    window = _video_sums(1, p25=0.71, p50=0.29, thru=0.21, hook=0.38)
    baseline = _video_sums(5, p25=0.75, p50=0.54, thru=0.34, hook=0.31)
    return _diagnosed("video", window, baseline, duration_ms)


def test_brief_names_the_drop_window_in_seconds():
    d = weak_retention()
    brief = render_brief(CREATIVE, d, 24_000)
    assert "format: video · duration 00:24" in brief
    assert "<verdict>weakest layer: retention</verdict>" in brief
    assert '<layer name="retention" status="weak" tier="reliable">' in brief
    assert "largest excess drop vs baseline: 00:06-00:12" in brief
    assert "  50% of runtime (00:12): 29% vs 54%" in brief
    assert "hold rate (ThruPlay ÷ 3s views): 21.0% vs 34.0% baseline (-38%)" in brief
    assert '<layer name="attention" status="strong" tier="reliable">' in brief
    assert "volume: 20,000 impressions" in brief
    assert "baseline for this layer: the 18 other videos 15–30s long" in brief
    assert '<layer name="context" status="not_measured" reason="Placement' in brief
    # Model-facing reason text for measured layers is left out: it is interpretation.
    assert "Nothing to change here" not in brief


def test_brief_for_a_video_of_unknown_length_does_not_read_retention():
    brief = render_brief(CREATIVE, weak_retention(None), None)
    assert "duration" not in brief
    assert '<layer name="retention" status="not_measured" reason="This video' in brief
    assert "largest excess drop" not in brief


def test_evidence_tier_follows_volume():
    d = weak_retention()
    attention, retention = d.diagnosis.tiers[0], d.diagnosis.tiers[1]
    assert evidence_tier(attention, d) == "reliable"
    assert evidence_tier(retention, d) == "reliable"
    small = _diagnosed(
        "video",
        Sums(impressions=2_000, link_clicks=20, video_3s_views=700, video_plays=700,
             video_thruplay=100, video_p25=500, video_p50=200),
        _video_sums(5, p25=0.75, p50=0.54, thru=0.34, hook=0.31),
        24_000,
    )  # fmt: skip
    assert small.diagnosis.tiers[0].status in ("strong", "on_par", "weak")
    assert evidence_tier(small.diagnosis.tiers[0], small) == "directional"
    assert evidence_tier(small.diagnosis.tiers[1], small) == "directional"
    assert evidence_tier(small.diagnosis.tiers[4], small) == "insufficient"


def test_unreadable_when_nothing_is_measured():
    d = _diagnosed("image", Sums(impressions=300), Sums(impressions=50_000), None)
    assert not is_readable(d)
    assert is_readable(weak_retention())


def test_grounding_accepts_numbers_and_timestamps_from_the_brief():
    brief = render_brief(CREATIVE, weak_retention(), 24_000)
    paragraph = (
        "Retention is the weak layer: hold rate is 21.0% against 34.0%, and only 29% are still "
        "watching at 00:12. Between 00:06 and 00:12 the video cuts to a slow product pan. Keep "
        "the first 3 seconds and the 50% off line; recut 00:06-00:12 and hold the rest."
    )
    assert grounding_problems(paragraph, brief, "video", 24_000) == []


def test_grounding_flags_invented_or_rerounded_numbers():
    brief = render_brief(CREATIVE, weak_retention(), 24_000)
    problems = grounding_problems(
        "Hold rate is roughly 20% and CTR 1.5%, costing $1,200.", brief, "video", 24_000
    )
    assert problems == ["numbers not in the brief: 20%, 1.5%, $1,200"]


def test_grounding_flags_timestamps_past_the_end_or_on_images():
    brief = render_brief(CREATIVE, weak_retention(), 24_000)
    assert grounding_problems("The CTA lands at 00:31.", brief, "video", 24_000) == [
        "timestamps past the end of the video: 00:31"
    ]
    assert grounding_problems("At 00:02 the logo shows.", brief, "image", None) == [
        "timestamps cited for a non-video ad"
    ]
    assert grounding_problems("   ", brief, "video", 24_000) == ["the paragraph is empty"]


def test_user_text_names_prior_problems_on_retry():
    text = user_text("BRIEF", ["numbers not in the brief: 1.5%"])
    assert text.startswith("BRIEF\n\nBased on the ad and the brief above")
    assert "numbers not in the brief: 1.5%" in text


def test_mmss():
    assert mmss(0) == "00:00"
    assert mmss(6.4) == "00:06"
    assert mmss(83) == "01:23"
