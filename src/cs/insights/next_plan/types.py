"""Data shapes for one `/next` plan. Everything here serializes to one small JSON document.

Two different spends appear in a plan and they are not interchangeable, so each row carries
its own window label rather than a bare number:

- Pattern rows (make, and the graded side of a scale row) are priced in **window spend**: a
  `Unit` pools every day `metric_daily` holds for the creative, so its spend is lifetime
  within the read's window, not a trailing 30 days.
- Allocation rows are priced in **30-day spend**, read from `creative_metric_30d`.

Ranking happens inside a block, never across two, for exactly that reason.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from cs.insights.account_read.types import to_json

__all__ = [
    "AllocationRow",
    "Decline",
    "Evidence",
    "ExploreRow",
    "MakeBrief",
    "Plan",
    "Ref",
    "Shape",
    "Stake",
    "Watch",
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
class Evidence:
    """One graded comparison, cited as P<n> in the plan's own numbering."""

    pattern_id: str
    label: str
    metric: str
    metric_name: str
    change: float
    grade: str
    n_cell: int
    n_complement: int
    cell_value: float
    comp_value: float


@dataclass
class Ref:
    """A carrier file: what to watch before writing the next one."""

    sha256: str
    headline: str
    rep_creative_id: str | None
    spend: float
    shows: str  # which ingredient this file is here to demonstrate


@dataclass
class Shape:
    duration_s: float | None
    duration_range: tuple[float, float] | None
    first_product_s: float | None
    captions: str | None
    n_carriers: int


@dataclass
class Watch:
    """The one metric that decides the test, and the number it has to beat."""

    metric: str
    metric_name: str
    account_value: float
    account_label: str  # "median across 47 files" | "pooled across the account"
    direction: str  # "above" | "below"


@dataclass
class MakeBrief:
    key: str
    title: str
    attributes: list[list[str]]  # [[dimension, value], ...]
    stake: Stake
    binding: str
    carriers: int  # files already carrying the whole combo (0 or 1 by construction)
    evidence: list[Evidence]
    downstream: str
    hooks: list[str]
    refs: list[Ref]
    shape: Shape
    hold: str
    watch: Watch
    caveat: str


@dataclass
class Decline:
    """How an ad is wearing out: its first fourteen days against its trailing seven.

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
    headline: str
    rep_creative_id: str | None
    stake: Stake
    verdict: str
    detail: str
    broken_layer: str | None
    decline: Decline | None


@dataclass
class ExploreRow:
    key: str
    dimension: str
    group: str
    value: str
    n_units: int
    status: str  # "never tried" | "too few files to test"


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
    make: list[MakeBrief] = field(default_factory=list)
    make_empty_reason: str | None = None
    explore: list[ExploreRow] = field(default_factory=list)
    explore_empty_reason: str | None = None
    next_tests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return to_json(self)
