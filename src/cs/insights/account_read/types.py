"""Data shapes for the account read. Everything here serializes to one JSON document."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

RATE_METRICS = ("hook_rate", "thruplay_rate", "link_ctr")
POOLED_METRICS = ("purchases_per_1k", "cpa")
METRICS = RATE_METRICS + POOLED_METRICS
LOWER_IS_BETTER = {"cpa"}


@dataclass
class Unit:
    """A footage unit: every creative in the account sharing one video file."""

    sha256: str
    creative_ids: list[UUID]
    rep_creative_id: UUID | None
    headline: str
    duration_s: float | None
    spend: Decimal
    impressions: int
    plays: int
    video_3s_views: int
    thruplays: int
    p25: int
    p50: int
    p75: int
    p100: int
    link_clicks: int
    purchases: int
    first_day: date
    last_day: date
    facets: set[tuple[str, str]]
    numeric: dict[str, float]
    first_product_s: float | None
    # Qualitative record for the writer.
    concept_summary: str | None
    hook_verbatim: str | None

    def has(self, dimension: str, value: str) -> bool:
        return (dimension, value) in self.facets

    def midpoint(self) -> float:
        a, b = self.first_day.toordinal(), self.last_day.toordinal()
        return a + (b - a) / 2


@dataclass
class Test:
    __test__ = False  # not a pytest test class

    dimension: str
    value: str
    group: str | None  # "audience_callouts=magnesium_users" for an interaction, else None
    metric: str
    kind: Literal["per_unit", "pooled"]
    n_cell: int
    n_complement: int
    cell_value: float
    comp_value: float
    change: float
    p: float
    grade: str
    confounds: list[str]
    cell_shas: list[str]
    bin_edges: tuple[float, float] | None = None
    cell_purchases: int | None = None
    # True when this grade depends on which random shuffles the permutation test drew: the
    # run grades itself twice, under two seeds, and disagrees with itself here. About 1% of
    # results sit that close to a threshold, and calling one of them "strong" without
    # saying so would dress up a coin toss (measured 2026-09-23).
    grade_unstable: bool = False


@dataclass
class Finding:
    title: str
    layer: str
    paragraph: str
    mechanism: str
    exemplars: list[str]
    exception: str
    why_non_obvious: str
    pattern_ids: list[str]
    confidence: str


@dataclass
class Read:
    overview: str
    findings: list[Finding]
    entangled: str
    no_evidence: str
    next_tests: list[str]
    data_needed: list[str]


@dataclass
class RunResult:
    units: list[Unit]
    tests: list[Test]  # main effects then interactions; P<n> is tests[n-1]
    overlaps: list[dict[str, Any]]
    stratified: list[dict[str, Any]]
    retention_by_reveal: dict[str, dict[str, Any]]
    summary: dict[str, Any]
    bin_edges: dict[str, tuple[float, float]] = field(default_factory=dict)


def to_json(obj: Any) -> Any:
    """dataclasses / Decimal / date / UUID / set / tuple → JSON-safe values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {
            ("|".join(k) if isinstance(k, tuple) else str(k)): to_json(v) for k, v in obj.items()
        }
    if isinstance(obj, set):
        return [to_json(v) for v in sorted(obj)]
    if isinstance(obj, (list, tuple)):
        return [to_json(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    return obj


def unit_from_json(d: dict) -> Unit:
    return Unit(
        sha256=d["sha256"],
        creative_ids=[UUID(x) for x in d.get("creative_ids") or []],
        rep_creative_id=UUID(d["rep_creative_id"]) if d.get("rep_creative_id") else None,
        headline=d["headline"],
        duration_s=d.get("duration_s"),
        spend=Decimal(str(d.get("spend", 0))),
        impressions=int(d.get("impressions", 0)),
        plays=int(d.get("plays", 0)),
        video_3s_views=int(d.get("video_3s_views", 0)),
        thruplays=int(d.get("thruplays", 0)),
        p25=int(d.get("p25", 0)),
        p50=int(d.get("p50", 0)),
        p75=int(d.get("p75", 0)),
        p100=int(d.get("p100", 0)),
        link_clicks=int(d.get("link_clicks", 0)),
        purchases=int(d.get("purchases", 0)),
        first_day=date.fromisoformat(d["first_day"]),
        last_day=date.fromisoformat(d["last_day"]),
        facets={(f[0], f[1]) for f in d.get("facets") or []},
        numeric=d.get("numeric") or {},
        first_product_s=d.get("first_product_s"),
        concept_summary=d.get("concept_summary"),
        hook_verbatim=d.get("hook_verbatim"),
    )


def test_from_json(d: dict) -> Test:
    d = dict(d)
    d.pop("pid", None)  # added for the page, not part of the record
    if d.get("bin_edges") is not None:
        d["bin_edges"] = tuple(d["bin_edges"])
    return Test(**d)
