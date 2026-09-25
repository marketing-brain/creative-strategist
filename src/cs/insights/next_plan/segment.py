"""The segment event study: where one file loses viewers that comparable files keep.

Step 1 of the `/next` v2 build order (docs/next-surface.md §9). No model call, no prose --
the whole point is to find out whether the statistics pick the right assets before anything
is written about them.

One window, for both terms. The curve and the spend are read on `last_30d`, off the same
clause, because a recut changes what happens next and the leak has to be priced against the
money currently paying for it.

`first_7d` was the first choice -- comparing files at equal age rather than by how long the
optimiser kept paying (docs/next-surface.md §4.3) -- and the data rejected it. Org 3's
spending files hold a median of **347 plays** in their first week, only 30 of 202 clear a
usable floor, and the account's largest 30-day spender has no first-week curve at all. Equal
age is the right principle for ranking creative quality across launches; it is the wrong
window for deciding what to edit today.

A deficit is only worth reporting in the seconds a recut can actually change, so the search
is bounded to the opening (`EARLY_BUCKETS`). A drop at second 40 is a different product.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

CURVE_WINDOW = "last_30d"
SPEND_WINDOW = "last_30d"

MIN_PLAYS = 5_000  # below this a curve is a handful of sessions, not a measurement
MIN_PEERS = 8  # fewer than this and the duration-matched control is noise; widen instead
MIN_CONFIRM = 6  # peers the campaign stratum needs before it can confirm or refute a deficit
EARLY_BUCKETS = range(1, 6)  # seconds 1-5: what a new top can change
DURATION_RATIO = 2.0  # a peer's runtime within this factor of the target's, both ways
MIN_GAP = 0.03  # a 3-point hold difference is the floor for calling it a deficit
MIN_REF_PURCHASES = 10  # below this a file's own CPA is a rounding artefact, not a number


@dataclass(frozen=True)
class Asset:
    """One file, with the two windows it is judged on."""

    footage_id: str
    sha256: str  # the account read's unit key, so a cited test's file list joins to a file
    rep_creative_id: str | None
    title: str
    campaign_id: str | None
    first_day: object | None  # date the file first delivered; None when it never has
    duration_s: float
    spend_30d: float
    impressions: int
    purchases: int
    link_clicks: int
    plays: int
    # bucket -> share still watching at that bucket's start second
    curve: dict[int, float] = field(default_factory=dict)
    starts: dict[int, int] = field(default_factory=dict)

    def hold(self, bucket: int) -> float | None:
        return self.curve.get(bucket)

    def metric(self, name: str) -> float | None:
        """This file's own value of an account-read metric, or None when it has too little
        volume to have one. Used to rank a reference on the metric its card cites, rather than
        on spend alone -- which put the same two biggest spenders on every card."""
        if name == "cpa":
            return (
                (self.spend_30d / self.purchases) if self.purchases >= MIN_REF_PURCHASES else None
            )
        if not self.impressions:
            return None
        if name == "purchases_per_1k":
            return (
                1000 * self.purchases / self.impressions
                if self.purchases >= MIN_REF_PURCHASES
                else None
            )
        if name == "link_ctr":
            return self.link_clicks / self.impressions
        return None


@dataclass(frozen=True)
class Control:
    """What comparable files hold, second by second.

    `kind` is on the card because a duration-matched control and an account-wide one are not
    the same claim, and the reader is entitled to know which one the number came from.
    """

    kind: str  # "files of similar length" | "every file in the account"
    n: int
    hold: dict[int, float]


@dataclass(frozen=True)
class Deficit:
    bucket: int
    at_s: int
    asset_hold: float
    control_hold: float

    @property
    def gap(self) -> float:
        return self.control_hold - self.asset_hold

    @property
    def ratio(self) -> float:
        """How many times more viewers survive the opening if it held at the control."""
        return (self.control_hold / self.asset_hold) if self.asset_hold > 0 else 1.0


@dataclass(frozen=True)
class Surplus:
    """The other half of the story: where the file beats its control after the deficit.

    Without it a card reads "this ad is bad at holding attention", which is the opposite of
    what the data says about the file that prompted this module -- worst in the account
    through 3 seconds, best in the account from 6 seconds on. The instruction depends on it:
    replace the opening, keep the body.
    """

    at_s: int
    asset_hold: float
    control_hold: float

    @property
    def gap(self) -> float:
        return self.asset_hold - self.control_hold


@dataclass(frozen=True)
class Recovery:
    amount: float
    ratio: float
    spend: float


def eligible(a: Asset) -> bool:
    return a.plays >= MIN_PLAYS and bool(a.curve)


def confirming_control(target: Asset, peers: list[Asset]) -> Control | None:
    """The same comparison, restricted to files that ran in the target's own campaign.

    A tighter stratum, used to *check* a deficit rather than to replace the one that found it.
    Replacing it would cost more than it buys: on org 3, campaign-and-duration leaves 8 or more
    peers for only 23 of 53 eligible files, so more than half the account would lose its card
    to a stratum that is empty rather than to evidence.

    What it is for is the confound the duration control cannot see. Campaigns differ in who
    they reach, and a file delivered to a colder audience holds worse for reasons a recut
    cannot fix. If the deficit survives inside one campaign it is a property of the file; if it
    disappears, the account-wide version of it was about delivery.

    Placement would be the other stratum worth holding fixed, and it is not available: the
    breakdown was retired, and `metric_daily.placement` is the single value 'all'.
    """
    if target.campaign_id is None:
        return None
    lo, hi = target.duration_s / DURATION_RATIO, target.duration_s * DURATION_RATIO
    same = [
        p
        for p in peers
        if p.footage_id != target.footage_id
        and eligible(p)
        and p.campaign_id == target.campaign_id
        and lo <= p.duration_s <= hi
    ]
    if len(same) < MIN_CONFIRM:
        return None
    return _median_curve("files in the same campaign and of similar length", same)


def at_bucket(a: Asset, c: Control, bucket: int, at_s: int) -> Deficit | None:
    """The gap at one named second, without searching for a new one.

    A confirmation has to answer the question the first measurement asked. Letting it hunt for
    its own worst second would confirm a different claim.
    """
    ah, ch = a.hold(bucket), c.hold.get(bucket)
    if ah is None or ch is None:
        return None
    return Deficit(bucket=bucket, at_s=at_s, asset_hold=ah, control_hold=ch)


def control_for(target: Asset, peers: list[Asset]) -> Control | None:
    """Files this one should be compared against.

    Duration first: a 5-second ad and a 109-second ad answer different questions at the
    8-second mark, and this account runs both (4.1s to 268.7s). A peer also has to be long
    enough to still be running at the second under test, or its "drop" is just the end of the
    file. When too few peers match, the control widens to the account rather than inventing
    precision from three files -- and says so.
    """
    usable = [p for p in peers if p.footage_id != target.footage_id and eligible(p)]
    lo, hi = target.duration_s / DURATION_RATIO, target.duration_s * DURATION_RATIO
    matched = [p for p in usable if lo <= p.duration_s <= hi]
    if len(matched) >= MIN_PEERS:
        return _median_curve("files of similar length", matched)
    return _median_curve("every file in the account", usable) if usable else None


def _median_curve(kind: str, peers: list[Asset]) -> Control:
    by_bucket: dict[int, list[float]] = {}
    for p in peers:
        for b, share in p.curve.items():
            by_bucket.setdefault(b, []).append(share)
    return Control(
        kind=kind, n=len(peers), hold={b: statistics.median(v) for b, v in by_bucket.items()}
    )


def deepest_deficit(a: Asset, c: Control) -> Deficit | None:
    """The worst second of the opening, not the cumulative loss by the end of it.

    Correction, found while building (docs/next-surface.md §4.1): pricing the endpoint returns
    zero for every file that opens badly and then holds well, which is exactly the set worth
    recutting. The trough is the thing to price; what happens after it is the reason to keep
    the rest.
    """
    best: Deficit | None = None
    for b in EARLY_BUCKETS:
        ah, ch = a.hold(b), c.hold.get(b)
        if ah is None or ch is None:
            continue
        d = Deficit(bucket=b, at_s=a.starts.get(b, b), asset_hold=ah, control_hold=ch)
        if d.gap >= MIN_GAP and (best is None or d.gap > best.gap):
            best = d
    return best


def best_surplus(a: Asset, c: Control, after: int) -> Surplus | None:
    """Where the file most beats its control, after the deficit."""
    best: Surplus | None = None
    for b, ah in sorted(a.curve.items()):
        ch = c.hold.get(b)
        if b <= after or ch is None:
            continue
        s = Surplus(at_s=a.starts.get(b, b), asset_hold=ah, control_hold=ch)
        if s.gap > 0 and (best is None or s.gap > best.gap):
            best = s
    return best


def price(a: Asset, d: Deficit) -> Recovery:
    """What the leak costs, in the money already going through this file.

    Hold the file's own downstream retention fixed and give it the control's opening: the same
    spend then buys `ratio` times as many viewers past the trough, so the cost of every viewer
    who does get there falls by (1 - 1/ratio). Nothing here forecasts a gain -- it prices an
    observed loss at the spend that is currently paying for it (docs/next-surface.md §4.2).
    """
    r = d.ratio
    return Recovery(amount=a.spend_30d * (1 - 1 / r) if r > 1 else 0.0, ratio=r, spend=a.spend_30d)
