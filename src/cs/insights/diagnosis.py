"""Per-creative diagnostic funnel: attention -> retention -> conversion -> fatigue -> context.

Pure functions over additive window sums. No I/O, no LLM. Spec:
docs/superpowers/specs/2026-09-12-creative-diagnosis-panel-design.md
"""

from __future__ import annotations

import math
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
# Retention compares a video only with videos of similar length. ThruPlay is 15s, or the
# whole video when it is shorter, so a short and a long video's hold rates measure different
# things, and a curve at "50% of runtime" is 5s into one ad and 30s into another. Buckets
# are (low, high] seconds; the first boundary is the ThruPlay rule.
LENGTH_BUCKETS_S: tuple[tuple[int, int | None], ...] = ((0, 15), (15, 30), (30, 60), (60, None))
# Context tier. A placement is only rated once both this creative and its siblings clear the
# floor there; only placements carrying a real share of delivery can break the tier.
PLACEMENT_FLOOR = 1_000
PLACEMENT_SHARE_FLOOR = 0.20
AUDIENCE_SLICES = 6
# Below this the remainder is not worth a row of its own.
AUDIENCE_REMAINDER_FLOOR = 0.005

_PLATFORM_LABELS = {
    "facebook": "Facebook",
    "instagram": "Instagram",
    "messenger": "Messenger",
    "audience_network": "Audience Network",
    "threads": "Threads",
    "whatsapp": "WhatsApp",
    "unknown": "Unknown",
}
_POSITION_LABELS = {
    "feed": "Feed",
    "reels": "Reels",
    "stories": "Stories",
    "story": "Stories",
    "explore": "Explore",
    "explore_home": "Explore home",
    "profile_feed": "Profile feed",
    "profile_reels": "Profile reels",
    "reels_overlay": "Reels overlay",
    "video_feeds": "Video feeds",
    "marketplace": "Marketplace",
    "search": "Search",
    "right_hand_column": "Right column",
    "instream_video": "In-stream video",
    "facebook_stories": "Stories",
    "biz_inbox": "Business inbox",
    "unknown": "Unknown",
}

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
    baseline_label: str | None = None


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
class Placement:
    platform: str
    position: str
    label: str
    impressions: int
    share: float | None
    hook_rate: float | None
    hook_fit: float | None
    link_ctr: float | None
    ctr_fit: float | None
    readable: bool


@dataclass
class AudienceSlice:
    label: str
    age: str
    gender: str
    share: float
    baseline_share: float


@dataclass
class Siblings:
    """The creatives this one is measured against, and how they were chosen. Placement and
    audience are set by Meta's optimiser, not by the advertiser, so the only fair comparison
    is creatives that ran under the same targeting: same ad set first, then same campaign,
    and the whole account only as a last resort."""

    scope: Literal["ad set", "campaign", "account"]
    creatives: int

    @property
    def label(self) -> str:
        noun = "creative" if self.creatives == 1 else "creatives"
        where = "the account" if self.scope == "account" else f"the same {self.scope}"
        return f"{self.creatives} other {noun} in {where}"


# Per-placement and per-age/gender window sums, keyed the way the breakdown tables are.
Key = tuple[str, str]
PlacementSums = Mapping[Key, Sums]
AudienceSums = Mapping[Key, int]


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
    placements: list[Placement] | None = None
    audience: list[AudienceSlice] | None = None
    comparison: str | None = None


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


def length_bucket(duration_ms: int | None) -> tuple[int, int | None] | None:
    if not duration_ms:
        return None
    seconds = duration_ms / 1000
    for lo, hi in LENGTH_BUCKETS_S:
        if hi is None or seconds <= hi:
            return lo, hi
    return None


def bucket_label(bucket: tuple[int, int | None]) -> str:
    lo, hi = bucket
    if lo == 0:
        return f"up to {hi}s long"
    if hi is None:
        return f"over {lo}s long"
    return f"{lo}–{hi}s long"


def retention_tier(
    fmt: str, w: Sums, b: Sums, duration_ms: int | None, baseline_creatives: int = 0
) -> Tier:
    """`b` must be the same-length baseline: other videos in the account whose length falls
    in this video's LENGTH_BUCKETS_S bucket."""
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
    bucket = length_bucket(duration_ms)
    if bucket is None:
        tier.status = "not_enough_data"
        tier.reason = (
            "This video's length is not known yet. Hold rate and the watch curve are only "
            "compared with videos of similar length."
        )
        return tier
    label = bucket_label(bucket)
    others = f"{baseline_creatives} other {'video' if baseline_creatives == 1 else 'videos'}"
    enough = w.video_plays >= RETENTION_FLOOR and b.video_plays >= RETENTION_FLOOR
    hold = _metric(
        "hold_rate",
        "Hold rate",
        ratio(w.video_thruplay, w.video_3s_views),
        ratio(b.video_thruplay, b.video_3s_views),
        f"ThruPlay ÷ 3s views · {w.video_plays:,} plays · vs videos {label}",
        enough=enough,
    )
    points = _curve_points(w, b)
    duration_s = duration_ms / 1000 if duration_ms else None
    tier.metrics = [hold]
    tier.curve = Curve(
        points,
        None,
        ratio(w.video_watch_seconds, w.video_plays),
        duration_s,
        f"{others} {label}",
    )
    tier.status = status_from_lift(hold.lift)
    if not enough:
        tier.reason = (
            f"Only {w.video_plays:,} plays in the window; retention needs {RETENTION_FLOOR:,}."
            if w.video_plays < RETENTION_FLOOR
            else f"The same-length baseline ({others} {label}) has only {b.video_plays:,} "
            f"plays; it needs {RETENTION_FLOOR:,}."
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
                f"Viewers leave earlier than on other videos {label}. "
                "Tighten the middle section or move the CTA earlier."
            )
    else:
        tier.reason = f"Viewers stay with this video at least as long as other videos {label}."
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


def placement_label(platform: str, position: str) -> str:
    """'instagram' + 'instagram_reels' -> 'Instagram · Reels'. Meta repeats the platform in
    most position values and spells the rest in snake case."""
    plat = _PLATFORM_LABELS.get(platform, platform.replace("_", " ").title())
    pos = position
    for prefix in (platform + "_", "facebook_", "instagram_"):
        if pos.startswith(prefix):
            pos = pos[len(prefix) :]
            break
    pos = _POSITION_LABELS.get(pos, pos.replace("_", " ").title())
    return f"{plat} · {pos}"


def _rate(s: Sums, metric: str) -> float | None:
    if metric == "hook_rate":
        return ratio(s.video_3s_views, s.impressions)
    return ratio(s.link_clicks, s.impressions)


def _fit(
    metric: str, placements: PlacementSums, siblings: PlacementSums, comparable: list[Key]
) -> dict[Key, float]:
    """How much better or worse this creative does in each placement than its own placement
    average, after the siblings' own pattern for that placement is taken out.

    Meta's optimiser decides where a creative runs, and it buys the impressions it expects to
    perform, so a plain level comparison partly measures the auction's choice. Dividing by the
    siblings' rate in the same placement removes what that placement is worth to everyone;
    dividing by the creative's own average across placements removes how good the creative is
    overall. What is left is the interaction -- whether this creative travels to that placement
    -- which is the only part a recut can change. It is a ratio of ratios, so it is zero-sum by
    construction: read it as fit, never as performance.

    The creative's own average is unweighted on purpose. Weighting it by delivery would let the
    placement Meta favoured set the reference it is then judged against, which puts the
    optimiser's allocation back into a number whose whole job is to be free of it."""
    ratios: dict[Key, float] = {}
    for key in comparable:
        ad, sib = _rate(placements[key], metric), _rate(siblings[key], metric)
        if ad and sib:
            ratios[key] = ad / sib
    # One placement cannot separate fit from level: the average would be the single ratio.
    if len(ratios) < 2:
        return {}
    mean_log = sum(math.log(r) for r in ratios.values()) / len(ratios)
    return {k: r / math.exp(mean_log) - 1 for k, r in ratios.items()}


def _placement_rows(
    fmt: str, placements: PlacementSums, siblings: PlacementSums
) -> list[Placement]:
    total = sum(s.impressions for s in placements.values())
    comparable = [
        k
        for k, w in placements.items()
        if w.impressions >= PLACEMENT_FLOOR
        and siblings.get(k, Sums()).impressions >= PLACEMENT_FLOOR
    ]
    hook = _fit("hook_rate", placements, siblings, comparable) if fmt == "video" else {}
    ctr = _fit("link_ctr", placements, siblings, comparable)
    rows: list[Placement] = []
    for key, w in placements.items():
        # Meta delivers a long tail of placements with a few dozen impressions each. They
        # still count towards the share denominator, but they are not rows anyone can read.
        if w.impressions < PLACEMENT_FLOOR:
            continue
        rows.append(
            Placement(
                platform=key[0],
                position=key[1],
                label=placement_label(*key),
                impressions=w.impressions,
                share=ratio(w.impressions, total),
                hook_rate=_rate(w, "hook_rate") if fmt == "video" else None,
                hook_fit=hook.get(key),
                link_ctr=_rate(w, "link_ctr"),
                ctr_fit=ctr.get(key),
                readable=key in comparable,
            )
        )
    rows.sort(key=lambda r: r.impressions, reverse=True)
    return rows


def _audience_slices(audience: AudienceSums, siblings: AudienceSums) -> list[AudienceSlice]:
    """The ad's age x gender mix beside its siblings', largest slices first, with whatever the
    listed rows leave out gathered into a final row so both columns still read as a full 100%.

    Like the placement mix, this is where Meta took the creative, not who anyone targeted."""
    total = sum(audience.values())
    sib_total = sum(siblings.values())
    if not total or not sib_total:
        return []
    slices = [
        AudienceSlice(
            label=f"{age} {gender}",
            age=age,
            gender=gender,
            share=impressions / total,
            baseline_share=siblings.get((age, gender), 0) / sib_total,
        )
        for (age, gender), impressions in audience.items()
    ]
    slices.sort(key=lambda s: s.share, reverse=True)
    shown = slices[:AUDIENCE_SLICES]
    rest = 1 - sum(s.share for s in shown)
    sib_rest = 1 - sum(s.baseline_share for s in shown)
    if rest > AUDIENCE_REMAINDER_FLOOR or sib_rest > AUDIENCE_REMAINDER_FLOOR:
        shown.append(
            AudienceSlice("Everyone else", "other", "other", max(rest, 0), max(sib_rest, 0))
        )
    return shown


def context_tier(
    fmt: str,
    placements: PlacementSums | None,
    siblings: PlacementSums | None,
    sibling_set: Siblings | None = None,
    audience: AudienceSums | None = None,
    audience_siblings: AudienceSums | None = None,
) -> Tier:
    """Whether the creative travels across the placements Meta chose for it.

    Under Advantage+ the advertiser picks neither the placement mix nor the audience -- the
    optimiser does, per creative -- so this layer is not a targeting diagnosis and never names
    a targeting fix. It reports fit (see _fit), and the only lever it can call for is a native
    asset for the placement. The audience mix is shown as context and never sets the status."""
    tier = Tier(
        5,
        "context",
        "Context",
        "Does the creative travel across the placements Meta chose for it?",
        "not_measured",
    )
    if not placements:
        tier.reason = (
            "Placement breakdown (Reels vs Feed vs Stories) is not connected for this account "
            "yet. When it is, this row shows how the creative travels between placements."
        )
        return tier
    tier.comparison = sibling_set.label if sibling_set else None
    tier.placements = _placement_rows(fmt, placements, siblings or {})
    tier.audience = _audience_slices(audience or {}, audience_siblings or {})
    primary = "hook_fit" if fmt == "video" else "ctr_fit"
    rated = [r for r in tier.placements if getattr(r, primary) is not None]
    if len(rated) < 2:
        tier.status = "not_enough_data"
        against = tier.comparison or "the baseline"
        tier.reason = (
            f"Fit needs two placements where both this creative and {against} cleared "
            f"{PLACEMENT_FLOOR:,} impressions; "
            f"{'only one' if len(rated) == 1 else 'none'} did."
        )
        return tier

    def fit(row: Placement) -> float:
        return getattr(row, primary)

    metric = "hook rate" if primary == "hook_fit" else "link CTR"
    carrying = [r for r in rated if (r.share or 0) >= PLACEMENT_SHARE_FLOOR]
    failing = [r for r in carrying if fit(r) <= -LIFT_THRESHOLD]
    if failing:
        row = min(failing, key=fit)
        tier.status = "weak"
        tier.decision = (
            f"{row.label} takes {row.share:.0%} of delivery, and the ad's {metric} there runs "
            f"{abs(fit(row)):.0%} below its own placement average -- a gap {tier.comparison} "
            f"do not have. Supply a native {row.label} cut instead of the master."
        )
        return tier
    main = rated[0]
    if fit(main) >= LIFT_THRESHOLD:
        tier.status = "strong"
        tier.decision = (
            f"Meta put {main.share:.0%} of delivery into {main.label} and the ad's {metric} "
            f"there runs {fit(main):.0%} above its own placement average, beyond what "
            f"{tier.comparison} get from that placement. Brief the next batch for {main.label}."
        )
        return tier
    tier.status = "on_par"
    tier.reason = (
        f"The creative travels: no placement carrying {PLACEMENT_SHARE_FLOOR:.0%} or more of "
        f"delivery is {LIFT_THRESHOLD:.0%} off its own average once the placement pattern of "
        f"{tier.comparison} is taken out."
    )
    return tier


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
    # Context is advisory, not a defect layer. Under Advantage+ the advertiser chooses neither
    # the placement mix nor the audience, so a poor fit is a brief for the next asset, never a
    # broken step to fix before the others -- and it must not hold back a healthy verdict.
    tiers = [t for t in tiers if t.key != "context"]
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
    retention_baseline: Sums | None = None,
    retention_baseline_creatives: int = 0,
    placements: PlacementSums | None = None,
    placement_siblings: PlacementSums | None = None,
    siblings: Siblings | None = None,
    audience: AudienceSums | None = None,
    audience_siblings: AudienceSums | None = None,
) -> Diagnosis:
    """`baseline` is every other creative of the same format; `retention_baseline` narrows it
    to videos of the same length bucket (see retention_tier). Without it, retention reads as
    not enough data rather than comparing unlike videos."""
    tiers = [
        attention_tier(fmt, window, baseline),
        retention_tier(
            fmt,
            window,
            retention_baseline if retention_baseline is not None else Sums(),
            duration_ms if retention_baseline is not None else None,
            retention_baseline_creatives,
        ),
        conversion_tier(window, baseline),
        fatigue_tier(fmt, first14, trailing7, days_live),
        context_tier(fmt, placements, placement_siblings, siblings, audience, audience_siblings),
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
