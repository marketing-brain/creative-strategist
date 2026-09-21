"""The Scale / Fix / Replace block: which live ad gets budget tomorrow.

This is the decision with no home in the product today. `spend_gap` allocates at attribute
level, the diagnosis panel judges one ad at a time and never ranks, and `/fatigue` is a flat
queue with no money on it. Nothing joins "this ad is broken at the click" to "and $46,000 went
through it in the last thirty days".

Pure: the job does the querying and hands the rows in. `diagnose_creative` is several queries
per ad, which is exactly why this may never run on a page load.

One row per footage group, not per creative: a video copied into six campaigns is one decision.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from cs.insights.next_plan.types import AllocationRow, Decline, Stake, item_key

# Per kind, not per page. A flat cap over a fix-first list starves the whole upside: org 3's
# first real run produced 7 fix rows and 3 replace rows, so under one cap of 12 not a single
# scale row would have been shown. Each decision keeps its own slots.
MAX_PER_KIND = 4
MAX_ROWS = 3 * MAX_PER_KIND
# A healthy ad counts as under-funded when it takes less than this share of what the *typical*
# spending ad takes. Self-calibrating: no fixed dollar threshold to age badly.
#
# The typical ad is the median, not the mean. The mean share is 1/n whatever the distribution
# looks like, so in an account where one file holds most of the budget -- common enough that
# `stats._confounds` has a flag for it -- the mean sits far above every ad but the whale and
# almost every healthy ad reads as starved. The median moves with the account instead.
UNDERFUNDED = 0.5

# Beyond this multiple a cost ratio is a thin denominator, not a measurement: the fatigue job
# has a spend floor but no purchase floor, so a trailing week with one or two purchases can
# read as a fifteen-fold rise. Past the cap the row stops quoting a figure, and a click-through
# row is preferred if the same footage has one.
DECLINE_CAP = 5.0


def choose_decline(rows: list[dict]) -> Decline | None:
    """The one decline that represents a footage group.

    A group holds several creatives and the fatigue job writes a row per creative per metric,
    so there can be many. Cost per purchase is the decision metric -- an ad is replaced
    because it stopped paying, not because attention slipped -- so a usable cost row wins.
    When every cost row is past the cap, a click-through row says something trustworthy and a
    capped cost row only says "a lot", so the click-through row is shown instead.
    """
    made = [d for r in rows if (d := _decline(r)) is not None]
    if not made:
        return None
    cpa = [d for d in made if d.metric == "cpa"]
    ctr = [d for d in made if d.metric == "ctr"]
    usable_cpa = [d for d in cpa if not d.capped]
    if usable_cpa:
        return max(usable_cpa, key=lambda d: d.ratio)  # the worst rise
    if ctr:
        return min(ctr, key=lambda d: d.ratio)  # the steepest fall
    return max(cpa, key=lambda d: d.ratio)


def _decline(row: dict) -> Decline | None:
    metric = str(row.get("metric") or "")
    base, obs = row.get("baseline"), row.get("observed")
    if metric not in ("cpa", "ctr") or base in (None, 0) or obs is None:
        return None
    baseline, observed = float(base), float(obs)
    if baseline <= 0:
        return None
    ratio = observed / baseline
    return Decline(
        metric=metric,
        baseline=baseline,
        observed=observed,
        ratio=ratio,
        capped=metric == "cpa" and ratio > DECLINE_CAP,
    )


@dataclass
class DiagnosedAd:
    """What the job loads per footage group, ready for the pure rules below."""

    creative_id: str
    headline: str
    rep_creative_id: str | None
    spend_30d: float
    verdict_kind: str  # "broken" | "healthy" | "too_early"
    verdict_headline: str
    verdict_detail: str
    broken_layer: str | None
    decline: Decline | None  # set when the ad is wearing out; None means it is not


def _decline_verdict(d: Decline | None) -> str:
    """One sentence naming what got worse, in the unit a buyer reads it in."""
    if d is None:
        return "Declining. This creative is wearing out."
    if d.metric == "cpa":
        how = f"more than {DECLINE_CAP:g} times" if d.capped else f"{d.ratio:.1f} times"
        return f"Wearing out. Cost per purchase is {how} what it was in its first two weeks."
    drop = 1 - d.ratio
    return f"Wearing out. Click-through is down {drop:.0%} against its first two weeks."


def _stake(ad: DiagnosedAd, account_spend: float, basis: str) -> Stake:
    return Stake(
        amount=ad.spend_30d,
        share=(ad.spend_30d / account_spend) if account_spend else 0.0,
        window="30d",
        basis=basis,
    )


def build(
    ads: list[DiagnosedAd], account_spend_30d: float
) -> tuple[list[AllocationRow], str | None]:
    """Fix first, then Replace, then Scale, each by spend descending.

    The order is the order money leaks: an ad broken at a measured layer is spending now and
    failing now; a fatigued ad is spending on a declining return; an under-funded healthy ad is
    an opportunity, not a loss. Spend cannot rank the three against each other -- a scale
    candidate is small *because* it has not been scaled -- so each kind is ranked within itself
    rather than pretending one mixed ordering means something.
    """
    spending = [a for a in ads if a.spend_30d > 0]
    if not spending:
        return [], "No ad in this account spent anything in the last thirty days."
    shares = [(a.spend_30d / account_spend_30d) if account_spend_30d else 0.0 for a in spending]
    typical_share = statistics.median(shares)

    fix, replace, scale = [], [], []
    for ad in spending:
        share = (ad.spend_30d / account_spend_30d) if account_spend_30d else 0.0
        if ad.verdict_kind == "broken":
            fix.append(
                AllocationRow(
                    key=item_key("fix", [ad.creative_id]),
                    kind="fix",
                    creative_id=ad.creative_id,
                    headline=ad.headline,
                    rep_creative_id=ad.rep_creative_id,
                    stake=_stake(ad, account_spend_30d, "spending through a broken layer"),
                    verdict=ad.verdict_headline,
                    detail=ad.verdict_detail,
                    broken_layer=ad.broken_layer,
                    decline=None,
                )
            )
        elif ad.decline is not None:
            replace.append(
                AllocationRow(
                    key=item_key("replace", [ad.creative_id]),
                    kind="replace",
                    creative_id=ad.creative_id,
                    headline=ad.headline,
                    rep_creative_id=ad.rep_creative_id,
                    stake=_stake(ad, account_spend_30d, "spending on a declining return"),
                    verdict=_decline_verdict(ad.decline),
                    # Nothing row-specific left to say: the verdict names this ad's own
                    # decline, and why a replace is a replace is the same sentence on every
                    # row, so it belongs once in the group heading rather than four times here.
                    detail="",
                    broken_layer=None,
                    decline=ad.decline,
                )
            )
        elif ad.verdict_kind == "healthy" and share < typical_share * UNDERFUNDED:
            scale.append(
                AllocationRow(
                    key=item_key("scale", [ad.creative_id]),
                    kind="scale",
                    creative_id=ad.creative_id,
                    headline=ad.headline,
                    rep_creative_id=ad.rep_creative_id,
                    stake=_stake(ad, account_spend_30d, "what it is getting now"),
                    verdict=ad.verdict_headline,
                    detail=(
                        f"On par or better at every measured step, on {share:.1%} of account "
                        f"spend against {typical_share:.1%} for the median spending ad."
                    ),
                    broken_layer=None,
                    decline=None,
                )
            )
    for bucket in (fix, replace, scale):
        bucket.sort(key=lambda r: (-r.stake.amount, r.headline))
    rows = fix[:MAX_PER_KIND] + replace[:MAX_PER_KIND] + scale[:MAX_PER_KIND]
    if not rows:
        return [], (
            "Every spending ad is either healthy and already funded, or too young to diagnose."
        )
    return rows, None
