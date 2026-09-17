"""Deterministic analyzers behind the ask box's typed query kinds. Each turns the stored run
(units, graded tests, overlaps) into tests to cite and fact lines to state. No model calls."""

from __future__ import annotations

from dataclasses import dataclass, field

from cs.insights.account_read.stats import MIN_PURCHASES, pooled, unit_rates
from cs.insights.account_read.types import LOWER_IS_BETTER, METRICS, RATE_METRICS, Test, Unit
from cs.insights.account_read.writer import METRIC_NAME, fmt_val, label

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


OVER_FUNDED, STARVED = 1.5, 0.67  # spend share against file share


def spend_gap(units: list[Unit], tests: list[Test], metrics: tuple[str, ...]) -> Analysis:
    """Graded attributes whose share of spend is out of line with their share of files and
    their effect: over-funded when heavy and bad, starved when light and good."""
    total = float(sum(u.spend for u in units)) or 1.0
    n = len(units)
    by_sha = {u.sha256: u for u in units}
    rows: list[tuple[str, float, Test]] = []
    for t in clean_main_effects(tests, metrics):
        cell_spend = sum(float(by_sha[s].spend) for s in t.cell_shas if s in by_sha)
        spend_share, file_share = cell_spend / total, len(t.cell_shas) / n
        if spend_share >= OVER_FUNDED * file_share and not is_good(t):
            rows.append(("over-funded", spend_share, t))
        elif spend_share <= STARVED * file_share and is_good(t):
            rows.append(("starved", spend_share, t))
    rows.sort(key=lambda r: (r[0] != "over-funded", -r[1]))
    out = Analysis()
    for verdict, share, t in rows:
        out.tests.append(t)
        out.facts.append(
            f"{verdict} | {label(t)} | {len(t.cell_shas)} of {n} files | {share * 100:.0f}% of "
            f"spend | {METRIC_NAME[t.metric]} {t.change * 100:+.0f}% {t.grade} (P{len(out.tests)})"
        )
    if not rows:
        out.empty_reason = (
            "No graded attribute is over- or under-funded relative to its share of files."
        )
    return out
