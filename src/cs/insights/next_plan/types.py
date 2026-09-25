"""Data shapes for one `/next` plan. Everything here serializes to one small JSON document.

Two different spends appear in a plan and they are not interchangeable, so each row carries
its own window label rather than a bare number:

- Allocation rows are priced in **30-day spend**, read from `creative_metric_30d`.
- Recut cards are priced in recoverable media cost over the same 30 days, and carry their own
  page-shaped payload (`recut.to_payload`) rather than a dataclass here.

Ranking happens inside a block, never across two, because the two answer different questions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from cs.insights.account_read.types import to_json

__all__ = [
    "AllocationRow",
    "Decline",
    "Plan",
    "Stake",
    "item_key",
    "to_json",
]


def item_key(kind: str, parts: list[str]) -> str:
    """A stable identity for one recommendation, so the ledger can follow it across runs.
    Sorted, so the same combo keyed in a different order is the same item."""
    digest = hashlib.sha256("|".join([kind, *sorted(parts)]).encode()).hexdigest()
    return f"{kind}:{digest[:16]}"


@dataclass
class Stake:
    """Money on the wrong side of a graded difference, and what window it covers."""

    amount: float
    share: float  # of the same window's account spend
    window: str  # "window" | "30d"
    basis: str  # one clause naming whose spend this is


@dataclass
class Decline:
    """How an ad is wearing out: its trailing seven days against the fourteen before them.

    `ratio` is observed / baseline, which for a cost is the natural reading -- "cost per
    purchase is 2.1x what it was". The stored `lift` is not used for display: `signed_lift`
    divides by the baseline, so a cost that rose off a small early denominator produces
    numbers like -1386%, which is an artefact of the divisor rather than a measurement. Past
    `DECLINE_CAP` the row says "more than Nx" and stops quoting a figure.
    """

    metric: str  # "cpa" | "ctr"
    baseline: float
    observed: float
    ratio: float
    capped: bool


@dataclass
class AllocationRow:
    """One live ad and the decision its diagnosis implies."""

    key: str
    kind: str  # "fix" | "replace" | "scale"
    creative_id: str
    # The file this ad runs. The page folds a budget row into the creative card for the same
    # file rather than showing one video twice under two names.
    footage_id: str | None
    headline: str
    rep_creative_id: str | None
    stake: Stake
    verdict: str
    detail: str
    broken_layer: str | None
    decline: Decline | None


@dataclass
class Plan:
    """One account's standing answer. `blocks` that came back empty carry their reason."""

    account_spend: float
    window_start: str | None
    window_end: str | None
    n_units: int
    # The newest day of delivery the account actually has. When the sync is behind, "the last
    # 30 days" is 30 days of calendar over fewer days of data, and the page has to say so.
    data_through: str | None = None
    allocate: list[AllocationRow] = field(default_factory=list)
    allocate_empty_reason: str | None = None
    # Already page-shaped (`recut.to_payload`), because a card carries template wording, an
    # optional written version and a ledger read-back, and the page picks between them.
    recut: list[dict] = field(default_factory=list)
    recut_empty_reason: str | None = None

    def to_dict(self) -> dict:
        return to_json(self)
