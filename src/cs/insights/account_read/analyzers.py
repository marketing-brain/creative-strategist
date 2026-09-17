"""Deterministic analyzers behind the ask box's typed query kinds. Each turns the stored run
(units, graded tests, overlaps) into tests to cite and fact lines to state. No model calls."""

from __future__ import annotations

from dataclasses import dataclass, field

from cs.insights.account_read.stats import MIN_PURCHASES, pooled, unit_rates
from cs.insights.account_read.types import LOWER_IS_BETTER, METRICS, RATE_METRICS, Test, Unit
from cs.insights.account_read.writer import METRIC_NAME, fmt_val

GOOD_GRADES = ("strong", "suggestive")


@dataclass
class Analysis:
    tests: list[Test] = field(default_factory=list)  # cited as P1.. in this order
    facts: list[str] = field(default_factory=list)  # cited as F1.. in this order
    empty_reason: str | None = None


def is_good(t: Test) -> bool:
    return t.change < 0 if t.metric in LOWER_IS_BETTER else t.change > 0


def clean_main_effects(tests: list[Test], metrics: tuple[str, ...]) -> list[Test]:
    return [
        t
        for t in tests
        if t.group is None and t.metric in metrics and t.grade in GOOD_GRADES and not t.confounds
    ]


def unit_metric(u: Unit, metric: str) -> float | None:
    """The unit's own value of a metric, or None when it sits below the metric's floor."""
    if metric in RATE_METRICS:
        return unit_rates(u).get(metric)
    if u.purchases < MIN_PURCHASES:
        return None
    if metric == "purchases_per_1k":
        v = pooled([u], "purchases", "impressions")
        return 1000 * v if v is not None else None
    return pooled([u], "spend", "purchases")


def rank_metric(metrics: tuple[str, ...]) -> str:
    """The metric a kind ranks by: the layer's first metric, or CPA for the whole set."""
    return "cpa" if len(metrics) == len(METRICS) else metrics[0]


def resolve_anchor(anchor: dict, units: list[Unit], metric: str) -> Unit | None:
    if "named" in anchor:
        needle = str(anchor["named"]).lower()
        hits = [
            u
            for u in units
            if needle in u.headline.lower()
            or needle in (u.concept_summary or "").lower()
            or any(d == "concept_tags" and needle in v.lower() for d, v in u.facets)
        ]
        return max(hits, key=lambda u: u.spend) if hits else None
    scored = [(v, u) for u in units if (v := unit_metric(u, metric)) is not None]
    if not scored:
        return None
    pick = min if metric in LOWER_IS_BETTER else max
    return pick(scored, key=lambda x: x[0])[1]


def unit_line(u: Unit, metric: str) -> str:
    v = unit_metric(u, metric)
    val = fmt_val(metric, v) if v is not None else "below floor"
    n = len(u.creative_ids)
    return (
        f'"{u.headline}" | ${float(u.spend):,.0f} spend | {n} cop{"y" if n == 1 else "ies"} | '
        f"{METRIC_NAME[metric]} {val}"
    )


def facet_words(f: tuple[str, str]) -> str:
    return f"{f[0].replace('_', ' ')} = {str(f[1]).replace('_', ' ')}"
