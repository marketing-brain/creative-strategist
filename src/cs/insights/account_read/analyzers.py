"""Deterministic analyzers behind the ask box's typed query kinds. Each turns the stored run
(units, graded tests, overlaps) into tests to cite and fact lines to state. No model calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from cs.insights.account_read.facets import NUMERIC_DIMS
from cs.insights.account_read.patterns import GRADE_RANK
from cs.insights.account_read.stats import (
    IMP_FLOOR,
    MIN_CELL,
    MIN_PURCHASES,
    benjamini_hochberg,
    compare,
    grade,
    pooled,
    unit_rates,
)
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


MAX_COMBOS = 5
MAX_CARRIERS = 1  # a combo counts as untried when at most this many files carry it


def single_dims(units: list[Unit]) -> set[str]:
    """Dimensions no unit carries two values of: their values are alternatives, not additions."""
    dims = {d for u in units for d, _ in u.facets}
    return {d for d in dims if all(sum(1 for dd, _ in u.facets if dd == d) <= 1 for u in units)}


def untried(
    units: list[Unit], tests: list[Test], overlaps: list[dict], metrics: tuple[str, ...]
) -> Analysis:
    """Pairs and triples of attributes with clean positive evidence that no file, or one file,
    carries together. Two values of one single-valued dimension and entangled pairs are skipped."""
    cands: dict[tuple[str, str], Test] = {}
    for t in clean_main_effects(tests, metrics):
        if not is_good(t):
            continue
        key = (t.dimension, t.value)
        if key not in cands or t.p < cands[key].p:
            cands[key] = t
    if len(cands) < 2:
        return Analysis(
            empty_reason="Fewer than two attributes have clean positive evidence on these metrics."
        )
    entangled = {frozenset((tuple(o["a"]), tuple(o["b"]))) for o in overlaps}
    single = single_dims(units)
    carriers_by_key = {k: {u.sha256 for u in units if u.has(*k)} for k in cands}

    def compatible(a: tuple[str, str], b: tuple[str, str]) -> bool:
        if a[0] == b[0] and a[0] in single:
            return False
        return frozenset((a, b)) not in entangled

    scored: list[tuple[float, int, tuple[tuple[str, str], ...]]] = []
    for r in (2, 3):
        for combo in combinations(sorted(cands), r):
            if not all(compatible(a, b) for a, b in combinations(combo, 2)):
                continue
            carriers = len(set.intersection(*(carriers_by_key[k] for k in combo)))
            if carriers <= MAX_CARRIERS:
                scored.append((sum(abs(cands[k].change) for k in combo), carriers, combo))
    # Sort by highest summed effect first (descending), then by fewest carriers (ascending)
    # so a zero-carrier combo ranks ahead of a one-carrier combo with the same effect.
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = Analysis()
    for _, carriers, combo in scored[:MAX_COMBOS]:
        pids = []
        for k in combo:
            t = cands[k]
            if t not in out.tests:
                out.tests.append(t)
            pids.append(f"P{out.tests.index(t) + 1}")
        names = " + ".join(label(cands[k]) for k in combo)
        out.facts.append(
            f"{names} | {carriers} of {len(units)} files carry all {len(combo)} | "
            f"ingredients {', '.join(pids)}"
        )
    if not out.facts:
        out.empty_reason = (
            "Every compatible pair of proven attributes is already carried by at least two files."
        )
    return out


NEIGHBOURS = 5
DIFF_MIN = 3  # a facet joins the diff when at least this many neighbours disagree with the anchor


def _choices(u: Unit) -> set[tuple[str, str]]:
    """Facets a maker chose; tercile bins are outcomes of the run, not choices."""
    return {f for f in u.facets if f[0] not in NUMERIC_DIMS}


def nearest(
    units: list[Unit], tests: list[Test], anchor: Unit, metric: str, metrics: tuple[str, ...]
) -> Analysis:
    """The files most like the anchor by facet overlap that do worse than it on the metric,
    and the facets that separate them."""
    av = unit_metric(anchor, metric)
    if av is None:
        return Analysis(
            empty_reason=f'"{anchor.headline}" is below the floor for {METRIC_NAME[metric]}.'
        )
    choice = _choices(anchor)
    scored: list[tuple[float, float, Unit]] = []
    for u in units:
        if u.sha256 == anchor.sha256:
            continue
        v = unit_metric(u, metric)
        if v is None:
            continue
        worse = v > av if metric in LOWER_IS_BETTER else v < av
        if not worse:
            continue
        uf = _choices(u)
        j = len(choice & uf) / len(choice | uf) if (choice | uf) else 0.0
        scored.append((j, v, u))
    scored.sort(key=lambda x: (-x[0], x[2].sha256))
    near = scored[:NEIGHBOURS]
    if not near:
        return Analysis(
            empty_reason=f'No file does worse than "{anchor.headline}" on {METRIC_NAME[metric]}.'
        )
    out = Analysis()
    out.facts.append(f"anchor | {unit_line(anchor, metric)}")
    for j, _, u in near:
        out.facts.append(f"neighbour | {unit_line(u, metric)} | {j * 100:.0f}% facet overlap")
    sets = [_choices(u) for _, _, u in near]
    need = min(DIFF_MIN, len(near))
    only_anchor = sorted(f for f in choice if sum(1 for s in sets if f not in s) >= need)
    only_near = sorted(
        f for f in set().union(*sets) - choice if sum(1 for s in sets if f in s) >= need
    )
    best: dict[tuple[str, str], Test] = {}
    for t in tests:
        if t.group is None and t.metric in metrics:
            k = (t.dimension, t.value)
            if k not in best or t.p < best[k].p:
                best[k] = t

    def cite(f: tuple[str, str]) -> str:
        t = best.get(f)
        if t is None:
            return ""
        if t not in out.tests:
            out.tests.append(t)
        return f" (P{out.tests.index(t) + 1})"

    for f in only_anchor:
        out.facts.append(f"anchor has, neighbours lack | {facet_words(f)}{cite(f)}")
    for f in only_near:
        out.facts.append(f"neighbours have, anchor lacks | {facet_words(f)}{cite(f)}")
    if not only_anchor and not only_near:
        out.facts.append("no facet separates the anchor from its neighbours")
    return out


def _strata(units: list[Unit], control: tuple[str, str] | str) -> list[tuple[str, list[Unit]]]:
    if control == "spend":
        ordered = sorted(units, key=lambda u: (u.spend, u.sha256))
        n = len(ordered)
        return [
            ("spend=low", ordered[: n // 3]),
            ("spend=mid", ordered[n // 3 : (2 * n) // 3]),
            ("spend=high", ordered[(2 * n) // 3 :]),
        ]
    cd, cv = control
    return [
        (f"{cd}={cv}", [u for u in units if u.has(cd, cv)]),
        (f"not {cd}={cv}", [u for u in units if not u.has(cd, cv)]),
    ]


def controlled(
    units: list[Unit],
    tests: list[Test],
    target: tuple[str, str],
    control: tuple[str, str] | str,
    metrics: tuple[str, ...],
    draws: int,
) -> Analysis:
    """The target's comparison rerun inside each stratum of the control. The stored overall
    main effect rides first as the reference."""
    dim, val = target
    out = Analysis()
    fresh: list[Test] = []
    for lbl, grp in _strata(units, control):
        cell = [u for u in grp if u.has(dim, val)]
        comp = [u for u in grp if not u.has(dim, val)]
        out.facts.append(f"{lbl} | {len(cell)} files with vs {len(comp)} without")
        found = compare(cell, comp, dimension=dim, value=val, group=lbl, draws=draws)
        fresh += [t for t in found if t.metric in metrics]
    if not fresh:
        return Analysis(
            empty_reason=(
                f"No stratum has {MIN_CELL} files on both sides that clear the metric floors "
                f"({IMP_FLOOR:,} impressions for rates, {MIN_PURCHASES} purchases per side for "
                "purchase metrics)."
            )
        )
    passing = benjamini_hochberg([t.p for t in fresh])
    for i, t in enumerate(fresh):
        t.grade = grade(t.p, t.change, i in passing)
    overall = [
        t
        for t in tests
        if t.group is None and (t.dimension, t.value) == target and t.metric in metrics
    ]
    out.tests = overall + fresh
    return out


def recipe(
    units: list[Unit], tests: list[Test], anchor: Unit, metric: str, metrics: tuple[str, ...]
) -> Analysis:
    """The anchor's facets ranked by their best clean graded effect: a main effect, or an
    interaction whose group is another facet the anchor carries."""
    groups = {f"{d}={v}" for d, v in anchor.facets}
    ranked: list[tuple[int, float, Test]] = []
    for f in sorted(anchor.facets):
        cands = [
            t
            for t in tests
            if (t.dimension, t.value) == f
            and t.metric in metrics
            and t.grade in GOOD_GRADES
            and not t.confounds
            and (t.group is None or t.group in groups)
        ]
        if not cands:
            continue
        best = min(cands, key=lambda t: (GRADE_RANK[t.grade], -abs(t.change)))
        ranked.append((GRADE_RANK[best.grade], -abs(best.change), best))
    if not ranked:
        return Analysis(
            empty_reason=(
                "None of this file's attributes has clean graded evidence on these metrics."
            )
        )
    ranked.sort(key=lambda r: (r[0], r[1]))
    out = Analysis()
    out.facts.append(f"anchor | {unit_line(anchor, metric)}")
    for i, (_, _, t) in enumerate(ranked, start=1):
        out.tests.append(t)
        within = f"within {t.group} | " if t.group else ""
        out.facts.append(
            f"#{i} | {label(t)} | {within}{METRIC_NAME[t.metric]} {t.change * 100:+.0f}% "
            f"{t.grade} (P{i})"
        )
    return out


def run(
    query: dict,
    units: list[Unit],
    tests: list[Test],
    overlaps: list[dict],
    metrics: tuple[str, ...],
    draws: int,
    anchor: Unit | None = None,
) -> Analysis:
    """Dispatch a parsed non-compare query. `anchor` must be resolved by the caller for the
    nearest and recipe kinds."""
    kind = query["kind"]
    if kind == "spend_gap":
        return spend_gap(units, tests, metrics)
    if kind == "untried":
        return untried(units, tests, overlaps, metrics)
    if kind == "controlled":
        return controlled(units, tests, tuple(query["target"]), query["control"], metrics, draws)
    if anchor is None:
        return Analysis(empty_reason="No file matches that description.")
    metric = rank_metric(metrics)
    if kind == "nearest":
        return nearest(units, tests, anchor, metric, metrics)
    return recipe(units, tests, anchor, metric, metrics)
