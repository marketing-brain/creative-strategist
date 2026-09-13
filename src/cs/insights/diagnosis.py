"""Per-creative diagnostic funnel: attention -> retention -> conversion -> fatigue -> context.

Pure functions over additive window sums. No I/O, no LLM. Spec:
docs/superpowers/specs/2026-09-12-creative-diagnosis-panel-design.md
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal
from typing import Any, Literal

LIFT_THRESHOLD = 0.15
FREQUENCY_RISE_THRESHOLD = 0.30
ATTENTION_FLOOR = 1_000
RETENTION_FLOOR = 100
STEP_FLOOR = 100
FATIGUE_FLOOR = 1_000
FATIGUE_MIN_DAYS = 21
CURVE_POINTS = (0, 25, 50, 75, 95, 100)

Status = Literal[
    "strong", "on_par", "weak", "not_applicable", "not_enough_data", "too_young", "not_measured"
]


@dataclass(frozen=True)
class Sums:
    """Additive totals over a window of metric_daily rows."""

    impressions: int = 0
    reach: int = 0
    link_clicks: int = 0
    spend: Decimal = Decimal(0)
    video_3s_views: int = 0
    video_plays: int = 0
    video_p25: int = 0
    video_p50: int = 0
    video_p75: int = 0
    video_p95: int = 0
    video_p100: int = 0
    video_thruplay: int = 0
    video_watch_seconds: Decimal = Decimal(0)
    landing_page_views: int = 0
    content_views: int = 0
    add_to_carts: int = 0
    checkouts: int = 0
    purchases: int = 0
    post_reactions: int = 0
    comments: int = 0
    saves: int = 0
    conversions: Decimal = Decimal(0)
    conversion_value: Decimal = Decimal(0)

    @classmethod
    def from_record(cls, r: Mapping[str, Any]) -> Sums:
        kw: dict[str, Any] = {}
        for f in fields(cls):
            v = r.get(f.name)
            if v is None:
                continue
            kw[f.name] = Decimal(str(v)) if f.type == "Decimal" else int(v)
        return cls(**kw)


@dataclass
class Metric:
    key: str
    label: str
    value: float | None
    baseline: float | None
    lift: float | None
    note: str


@dataclass
class CurvePoint:
    p: int
    share: float | None
    baseline_share: float | None


@dataclass
class Curve:
    points: list[CurvePoint]
    drop_segment: tuple[int, int] | None
    avg_watch_s: float | None
    duration_s: float | None


@dataclass
class Step:
    key: str
    label: str
    rate: float | None
    baseline_rate: float | None
    lift: float | None
    numerator: int
    denominator: int
    readable: bool


@dataclass
class Tier:
    tier: int
    key: str
    name: str
    question: str
    status: Status
    decision: str | None = None
    reason: str | None = None
    metrics: list[Metric] = field(default_factory=list)
    curve: Curve | None = None
    steps: list[Step] | None = None


def ratio(numerator: float | int | Decimal, denominator: float | int | Decimal) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def lift(observed: float | None, baseline: float | None) -> float | None:
    """Relative change vs baseline; higher is better for every metric passed here."""
    if observed is None or baseline is None or baseline == 0:
        return None
    return round((observed - baseline) / baseline, 6)


def status_from_lift(value: float | None) -> Status:
    if value is None:
        return "not_enough_data"
    if value >= LIFT_THRESHOLD:
        return "strong"
    if value <= -LIFT_THRESHOLD:
        return "weak"
    return "on_par"


def _metric(
    key: str,
    label: str,
    value: float | None,
    baseline: float | None,
    note: str,
    *,
    enough: bool,
) -> Metric:
    return Metric(key, label, value, baseline, lift(value, baseline) if enough else None, note)


# ---------------------------------------------------------------- tier 1: attention


def attention_tier(fmt: str, w: Sums, b: Sums) -> Tier:
    enough = w.impressions >= ATTENTION_FLOOR and b.impressions >= ATTENTION_FLOOR
    ctr = _metric(
        "link_ctr",
        "Link CTR",
        ratio(w.link_clicks, w.impressions),
        ratio(b.link_clicks, b.impressions),
        "link clicks ÷ impressions",
        enough=enough,
    )
    if fmt == "video":
        hook = _metric(
            "hook_rate",
            "Hook rate",
            ratio(w.video_3s_views, w.impressions),
            ratio(b.video_3s_views, b.impressions),
            f"3s views ÷ impressions · {w.impressions:,} impressions",
            enough=enough,
        )
        metrics, primary = [hook, ctr], hook
    else:
        metrics, primary = [ctr], ctr
    tier = Tier(
        1,
        "attention",
        "Attention",
        "Is the opening 1–3s stopping the scroll?",
        status_from_lift(primary.lift),
        metrics=metrics,
    )
    if not enough:
        tier.reason = (
            f"Only {w.impressions:,} impressions in the window; "
            f"attention needs {ATTENTION_FLOOR:,}."
            if w.impressions < ATTENTION_FLOOR
            else f"The account baseline has only {b.impressions:,} impressions; "
            f"it needs {ATTENTION_FLOOR:,}."
        )
    elif tier.status == "weak":
        tier.decision = (
            "The first three seconds are not stopping the scroll. "
            "Recut the opening or swap the thumbnail; keep the rest."
            if fmt == "video"
            else "This creative is not stopping the scroll. "
            "Try a different visual or headline; keep the offer."
        )
    else:
        tier.reason = "Nothing to change here. Keep this opening if you recut."
    return tier


# ---------------------------------------------------------------- tier 2: retention

_CURVE_ATTRS = {
    25: "video_p25",
    50: "video_p50",
    75: "video_p75",
    95: "video_p95",
    100: "video_p100",
}


def _curve_points(w: Sums, b: Sums) -> list[CurvePoint]:
    pts = [CurvePoint(0, 1.0 if w.video_plays else None, 1.0 if b.video_plays else None)]
    for p, attr in _CURVE_ATTRS.items():
        pts.append(
            CurvePoint(
                p, ratio(getattr(w, attr), w.video_plays), ratio(getattr(b, attr), b.video_plays)
            )
        )
    return pts


def _drop_segment(points: list[CurvePoint]) -> tuple[int, int] | None:
    best: tuple[float, tuple[int, int]] | None = None
    for a, c in zip(points, points[1:], strict=False):
        shares = (a.share, c.share, a.baseline_share, c.baseline_share)
        if any(x is None for x in shares):
            continue
        assert a.share is not None and c.share is not None
        assert a.baseline_share is not None and c.baseline_share is not None
        excess = (a.share - c.share) - (a.baseline_share - c.baseline_share)
        if best is None or excess > best[0]:
            best = (excess, (a.p, c.p))
    return best[1] if best else None


def retention_tier(fmt: str, w: Sums, b: Sums, duration_ms: int | None) -> Tier:
    tier = Tier(
        2, "retention", "Retention", "Where in the video does attention drop?", "not_applicable"
    )
    if fmt != "video":
        if fmt in ("image", "carousel"):
            tier.reason = (
                f"{fmt.capitalize()} creative. There is no watch-through curve to read; "
                "link CTR carries the scroll-stopping question."
            )
        else:
            tier.reason = (
                "This creative's format is not resolved yet, so there is no watch-through "
                "curve to read."
            )
        return tier
    enough = w.video_plays >= RETENTION_FLOOR and b.video_plays >= RETENTION_FLOOR
    hold = _metric(
        "hold_rate",
        "Hold rate",
        ratio(w.video_thruplay, w.video_3s_views),
        ratio(b.video_thruplay, b.video_3s_views),
        f"ThruPlay ÷ 3s views · {w.video_plays:,} plays",
        enough=enough,
    )
    points = _curve_points(w, b)
    duration_s = duration_ms / 1000 if duration_ms else None
    tier.metrics = [hold]
    tier.curve = Curve(points, None, ratio(w.video_watch_seconds, w.video_plays), duration_s)
    tier.status = status_from_lift(hold.lift)
    if not enough:
        tier.reason = (
            f"Only {w.video_plays:,} plays in the window; retention needs {RETENTION_FLOOR:,}."
        )
        return tier
    if tier.status == "weak":
        seg = _drop_segment(points)
        tier.curve.drop_segment = seg
        if seg:
            where = f"between {seg[0]}% and {seg[1]}%"
            if duration_s:
                s_from = seg[0] / 100 * duration_s
                s_to = seg[1] / 100 * duration_s
                where += f" ({s_from:.0f}s–{s_to:.0f}s)"
            tier.decision = (
                f"Attention drops {where}. Fix pacing or the cut there, or move the CTA earlier."
            )
        else:
            tier.decision = (
                "Viewers leave earlier than the account average. "
                "Tighten the middle section or move the CTA earlier."
            )
    else:
        tier.reason = "Viewers stay with this video at least as long as the account average."
    return tier


# ---------------------------------------------------------------- tier 3: conversion

# Funnel stages in order: (step key, human name, Sums attribute). The chain starts at
# link clicks and ends at purchases; the middle stages are only kept when the account
# actually instruments them (see _instrumented_stages).
_STAGES = (
    ("click", "Click", "link_clicks"),
    ("landing_page", "Landing page", "landing_page_views"),
    ("content_view", "Content view", "content_views"),
    ("add_to_cart", "Add to cart", "add_to_carts"),
    ("checkout", "Checkout", "checkouts"),
    ("purchase", "Purchase", "purchases"),
)


def _instrumented_stages(w: Sums, b: Sums) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Drop middle stages the account does not track.

    A real funnel is non-increasing: no stage can have more events than the one before it.
    Meta only reports the pixel events an advertiser fires, so an account that skips
    `view_content` or `add_to_cart` shows a tiny count there and a far larger count at
    checkout. Such a stage is untracked, not weak, and must not be diagnosed. Counts are
    account-level (creative + baseline) so the shape reflects the account's instrumentation,
    not this creative's volume. First and last stages are always kept.
    """
    counts = [getattr(w, attr) + getattr(b, attr) for _, _, attr in _STAGES]
    kept: list[tuple[str, str, str]] = []
    skipped: list[str] = []
    for i, stage in enumerate(_STAGES):
        first_or_last = i in (0, len(_STAGES) - 1)
        later_max = max(counts[i + 1 :], default=0)
        if first_or_last or (counts[i] > 0 and counts[i] >= later_max):
            kept.append(stage)
        else:
            skipped.append(stage[1])
    return kept, skipped


def conversion_tier(w: Sums, b: Sums) -> Tier:
    stages, skipped = _instrumented_stages(w, b)
    steps: list[Step] = []
    readable = True
    for (_, prev_name, den_attr), (key, name, num_attr) in zip(stages, stages[1:], strict=False):
        label = f"{prev_name} → {name}" if den_attr == "link_clicks" else f"→ {name}"
        den_w, den_b = getattr(w, den_attr), getattr(b, den_attr)
        readable = readable and den_w >= STEP_FLOOR and den_b >= STEP_FLOOR
        rate = ratio(getattr(w, num_attr), den_w) if readable else None
        base = ratio(getattr(b, num_attr), den_b) if readable else None
        steps.append(
            Step(
                key,
                label,
                rate,
                base,
                lift(rate, base) if readable else None,
                getattr(w, num_attr),
                den_w,
                readable,
            )
        )
    skipped_note = (
        f" {' and '.join(skipped)} {'are' if len(skipped) > 1 else 'is'} not tracked "
        "consistently for this account, so that step is left out."
        if skipped
        else ""
    )
    cpa_w, cpa_b = ratio(w.spend, w.conversions), ratio(b.spend, b.conversions)
    cpa_lift = lift(cpa_w, cpa_b)
    cpa_signed = -cpa_lift if cpa_lift is not None else None  # lower CPA is better
    cpa = Metric("cpa", "CPA", cpa_w, cpa_b, cpa_signed, "spend ÷ conversions")
    tier = Tier(
        3,
        "conversion",
        "Conversion",
        "Which funnel step under-delivers?",
        "on_par",
        metrics=[cpa],
        steps=steps,
    )
    scored = [s for s in steps if s.readable and s.lift is not None]
    if not scored:
        tier.status = "not_enough_data"
        tier.reason = (
            f"Steps show only once the previous step has {STEP_FLOOR} events. "
            "Nothing past the click is readable yet." + skipped_note
        )
        return tier
    worst = min(scored, key=lambda s: s.lift or 0.0)
    if worst.lift is not None and worst.lift <= -LIFT_THRESHOLD:
        tier.status = "weak"
        stage_name = worst.label.split("→ ")[-1]
        tier.decision = (
            f"People drop at {stage_name}. "
            "Check that the landing page delivers what the creative promises."
        )
        return tier
    end_to_end = (
        lift(ratio(w.purchases, w.link_clicks), ratio(b.purchases, b.link_clicks))
        if steps[-1].readable
        else None
    )
    tier.status = "strong" if end_to_end is not None and end_to_end >= LIFT_THRESHOLD else "on_par"
    tier.reason = (
        "More of the people who click end up buying than for the account's other creatives."
        if tier.status == "strong"
        else "The people who click convert like everyone else's. "
        "The landing page is not the problem."
    ) + skipped_note
    return tier


# ---------------------------------------------------------------- tier 4: fatigue


def fatigue_tier(
    fmt: str, first14: Sums | None, trailing7: Sums | None, days_live: int | None
) -> Tier:
    tier = Tier(4, "fatigue", "Fatigue", "Is this creative wearing out?", "too_young")
    if days_live is None:
        tier.reason = "No impressions yet."
        return tier
    if days_live < FATIGUE_MIN_DAYS or first14 is None or trailing7 is None:
        tier.reason = (
            f"Live for {days_live} days. Fatigue compares the last 7 days against the first 14, "
            f"so this reads from day {FATIGUE_MIN_DAYS}."
        )
        return tier
    f, t = first14, trailing7
    enough = f.impressions >= FATIGUE_FLOOR and t.impressions >= FATIGUE_FLOOR
    freq = _metric(
        "frequency",
        "Daily frequency",
        ratio(t.impressions, t.reach),
        ratio(f.impressions, f.reach),
        "last 7 days vs first 14",
        enough=enough,
    )
    if fmt == "video":
        attn = _metric(
            "hook_rate",
            "Hook rate trend",
            ratio(t.video_3s_views, t.impressions),
            ratio(f.video_3s_views, f.impressions),
            "same windows",
            enough=enough,
        )
    else:
        attn = _metric(
            "link_ctr",
            "Link CTR trend",
            ratio(t.link_clicks, t.impressions),
            ratio(f.link_clicks, f.impressions),
            "same windows",
            enough=enough,
        )
    eng = _metric(
        "engagement_rate",
        "Engagement rate",
        ratio(t.post_reactions + t.comments + t.saves, t.impressions),
        ratio(f.post_reactions + f.comments + f.saves, f.impressions),
        "reactions, comments, saves ÷ impressions",
        enough=enough,
    )
    tier.metrics = [freq, attn, eng]
    if not enough:
        tier.status = "not_enough_data"
        tier.reason = f"Both windows need {FATIGUE_FLOOR:,} impressions to compare."
        return tier
    rising = freq.lift is not None and freq.lift >= FREQUENCY_RISE_THRESHOLD
    falling = (attn.lift is not None and attn.lift <= -LIFT_THRESHOLD) or (
        eng.lift is not None and eng.lift <= -LIFT_THRESHOLD
    )
    if rising and falling:
        tier.status = "weak"
        tier.decision = (
            "Frequency is climbing faster than the hook is holding. "
            "Refresh the opening or rotate a variant in before efficiency drops."
        )
    else:
        tier.status = "on_par"
        tier.reason = "No decay signal yet. Frequency and response are moving together."
    return tier


# ---------------------------------------------------------------- tier 5: context


def context_tier() -> Tier:
    return Tier(
        5,
        "context",
        "Context",
        "Does it need a native recut per placement?",
        "not_measured",
        reason=(
            "Placement breakdown (Reels vs Feed vs Stories) is not connected for this account "
            "yet. When it is, this row shows hook rate and CTR per placement."
        ),
    )


# ---------------------------------------------------------------- verdict + assembly


@dataclass
class Verdict:
    kind: Literal["broken", "healthy", "too_early"]
    headline: str
    detail: str


@dataclass
class Diagnosis:
    window_start: date
    window_end: date
    format: str
    baseline_creatives: int
    broken_tier: int | None
    verdict: Verdict
    tiers: list[Tier]

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


_UNMEASURED = {"not_applicable", "not_enough_data", "too_young", "not_measured"}


def _verdict(tiers: list[Tier], window: Sums) -> tuple[int | None, Verdict]:
    weak = [t for t in tiers if t.status == "weak"]
    if weak:
        broken = weak[0]
        passed = [
            t.name for t in tiers if t.tier < broken.tier and t.status in ("strong", "on_par")
        ]
        detail = ""
        if passed:
            detail = f"{' and '.join(passed)} {'are' if len(passed) > 1 else 'is'} working. "
        detail += "Fix this layer before touching anything else."
        headline = f"Broken layer: {broken.name}. {broken.decision}"
        return broken.tier, Verdict("broken", headline, detail)
    if all(t.status in _UNMEASURED for t in tiers):
        return None, Verdict(
            "too_early",
            "Too early to diagnose.",
            f"Only {window.impressions:,} impressions in the last 30 days. "
            f"Attention needs {ATTENTION_FLOOR:,} to be worth reading.",
        )
    return None, Verdict(
        "healthy",
        "No broken layer. This creative is on par or better at every measured step.",
        "Scale it, and use it as the reference when you brief the next batch.",
    )


def diagnose(
    *,
    fmt: str,
    window: Sums,
    baseline: Sums,
    baseline_creatives: int,
    first14: Sums | None,
    trailing7: Sums | None,
    days_live: int | None,
    duration_ms: int | None,
    window_start: date,
    window_end: date,
) -> Diagnosis:
    tiers = [
        attention_tier(fmt, window, baseline),
        retention_tier(fmt, window, baseline, duration_ms),
        conversion_tier(window, baseline),
        fatigue_tier(fmt, first14, trailing7, days_live),
        context_tier(),
    ]
    broken, verdict = _verdict(tiers, window)
    return Diagnosis(window_start, window_end, fmt, baseline_creatives, broken, verdict, tiers)


def _plain(obj: Any) -> Any:
    """dataclasses -> dicts, dates -> ISO strings, tuples -> lists, Decimals -> floats."""
    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: _plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list | tuple):
        return [_plain(x) for x in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj
