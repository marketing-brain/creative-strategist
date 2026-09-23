"""One account-read run: main effects, bounded interactions, entanglement, retention by reveal."""

from __future__ import annotations

import statistics
from itertools import combinations

from cs.insights.account_read import permutations
from cs.insights.account_read.facets import DERIVED_SINGLE_DIMS, NUMERIC_DIMS, REVEAL_BINS
from cs.insights.account_read.reliability import FLAG, MIN_AGREEMENT, Reliability
from cs.insights.account_read.stats import (
    MIN_PURCHASES,
    benjamini_hochberg,
    compare,
    grade,
    pooled,
    retention_shares,
    unit_rates,
)
from cs.insights.account_read.types import RATE_METRICS, RunResult, Test, Unit
from cs.tagging.taxonomy import load_taxonomy

GRADE_RANK = {"strong": 0, "suggestive": 1, "directional": 2, "thin": 3}
MIN_GROUP = 10
# Two results over the same files are one idea, however many metrics measured it: on the
# 170-file panel, 22 surviving results were 11 ideas, seven of them one format (a person
# talking to camera, branded, ending on a CTA card) counted seven ways.
SAME_FILES = 0.6
SECOND_SEED = 8  # the run grades itself twice; a grade that moves between them is not one


def dimension_kinds() -> dict[str, str]:
    """dimension -> 'single' for taxonomy single-value dimensions, the derived single facets
    and the numeric tercile facets (complement = units that have the dimension at all);
    anything else is 'multi' (complement = every other unit)."""
    kinds = {d.name: "single" for d in load_taxonomy().dimensions if d.kind == "single"}
    for d in DERIVED_SINGLE_DIMS + NUMERIC_DIMS:
        kinds[d] = "single"
    return kinds


def _all_facets(units: list[Unit]) -> list[tuple[str, str]]:
    return sorted({f for u in units for f in u.facets})


def scope_units(units: list[Unit], dim: str, kinds: dict[str, str]) -> list[Unit]:
    if kinds.get(dim) == "single":
        return [u for u in units if any(d == dim for d, _ in u.facets)]
    return list(units)


def main_effects(
    units: list[Unit], bin_edges: dict, kinds: dict[str, str], draws: int, seed: int = 7
) -> list[Test]:
    out: list[Test] = []
    # Facets arrive grouped by dimension, and a dimension's values all compare against the
    # same scope, so one set of shuffles serves every value of it.
    cache = permutations.Cache(draws, seed)
    for dim, val in _all_facets(units):
        cell = [u for u in units if u.has(dim, val)]
        scope = scope_units(units, dim, kinds)
        comp = [u for u in scope if not u.has(dim, val)]
        out += compare(
            cell,
            comp,
            dimension=dim,
            value=val,
            bin_edges=bin_edges.get(dim),
            draws=draws,
            shared=cache.for_units(scope),
        )
    return out


def interactions(
    units: list[Unit],
    grouping_dims: list[str],
    kinds: dict[str, str],
    draws: int,
    seed: int = 7,
) -> list[Test]:
    out: list[Test] = []
    for gdim in grouping_dims:
        for gval in sorted({v for u in units for d, v in u.facets if d == gdim}):
            group = [u for u in units if u.has(gdim, gval)]
            if len(group) < MIN_GROUP:
                continue
            label = f"{gdim}={gval}"
            cache = permutations.Cache(draws, seed)
            for dim, val in _all_facets(group):
                if dim == gdim:
                    continue
                cell = [u for u in group if u.has(dim, val)]
                scope = scope_units(group, dim, kinds)
                comp = [u for u in scope if not u.has(dim, val)]
                out += compare(
                    cell,
                    comp,
                    dimension=dim,
                    value=val,
                    group=label,
                    draws=draws,
                    shared=cache.for_units(scope),
                )
    return out


def flag_tagger_disagreement(tests: list[Test], reliability: Reliability) -> None:
    """Mark every test whose attribute, or whose grouping value, the tagger flips on. The test
    still runs: a label applied at random blurs a real difference and seldom invents one, so
    the account-level number is if anything understated. What the flag rules out is the claim
    about one ad, "this video has it" or "lacks it", and `repo.patterns_for_file` drops
    flagged rows for that reason. Everywhere else it rides with the other confound flags."""
    for t in tests:
        attrs = [(t.dimension, t.value)]
        if t.group:
            attrs.append(tuple(t.group.split("=", 1)))
        if not all(reliability.reliable(*a) for a in attrs):
            t.confounds.append(FLAG)


def apply_grades(tests: list[Test]) -> None:
    passing = benjamini_hochberg([t.p for t in tests])
    for i, t in enumerate(tests):
        t.grade = grade(t.p, t.change, i in passing)


def _key(t: Test) -> tuple:
    return (t.group, t.dimension, str(t.value), t.metric)


def flag_unstable_grades(tests: list[Test], second: list[Test]) -> int:
    """Mark every result the run grades differently under a second set of shuffles.

    A permutation p-value is an estimate, and the grade boundaries are hard edges, so a
    result sitting on one is decided by the draw. Re-grading under another seed moved 13 of
    1,175 grades with no other change (2026-09-23) -- those are the ones this marks, and
    they are not evidence of anything.
    """
    other = {_key(t): t.grade for t in second}
    n = 0
    for t in tests:
        if other.get(_key(t), t.grade) != t.grade:
            t.grade_unstable = True
            n += 1
    return n


def _change_within(cell: list[Unit], comp: list[Unit], metric: str) -> tuple[float | None, str]:
    if metric in RATE_METRICS:
        x = [unit_rates(u)[metric] for u in cell if metric in unit_rates(u)]
        y = [unit_rates(u)[metric] for u in comp if metric in unit_rates(u)]
        if len(x) >= 3 and len(y) >= 3 and statistics.median(y):
            return statistics.median(x) / statistics.median(y) - 1, f"{len(x)} vs {len(y)}"
        return None, f"{len(x)} vs {len(y)}"
    if metric == "purchases_per_1k":
        num, den = "purchases", "impressions"
    else:
        num, den = "spend", "purchases"
    ok = (
        len(cell) >= 3
        and len(comp) >= 3
        and sum(u.purchases for u in cell) >= MIN_PURCHASES
        and sum(u.purchases for u in comp) >= MIN_PURCHASES
    )
    ra, rb = pooled(cell, num, den), pooled(comp, num, den)
    change = (ra / rb - 1) if ok and ra and rb else None
    return change, f"{len(cell)} vs {len(comp)}"


def entanglement(units: list[Unit], tests: list[Test]) -> tuple[list[dict], list[dict]]:
    """Jaccard >= 0.6 between graded, unflagged main-effect attributes with 8..48 units, and the
    effect of each on its graded metrics within / outside the partner."""
    graded: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for t in tests:
        if (
            t.group is None
            and t.grade in ("strong", "suggestive")
            and not t.confounds
            and 8 <= t.n_cell <= 48
        ):
            graded.setdefault((t.dimension, t.value), []).append((t.metric, t.change))
    members = {a: {u.sha256 for u in units if u.has(*a)} for a in graded}
    by_sha = {u.sha256: u for u in units}
    overlaps: list[dict] = []
    strat: list[dict] = []
    for a, b in combinations(sorted(graded), 2):
        inter, union = len(members[a] & members[b]), len(members[a] | members[b])
        j = inter / union if union else 0.0
        if j < 0.6:
            continue
        overlaps.append(
            {
                "a": list(a),
                "b": list(b),
                "jaccard": round(j, 2),
                "shared": inter,
                "n_a": len(members[a]),
                "n_b": len(members[b]),
            }
        )
        for attr, partner in ((a, b), (b, a)):
            inside = [by_sha[s] for s in members[partner]]
            outside = [u for u in units if u.sha256 not in members[partner]]
            for metric, overall in graded[attr]:
                w_ch, w_n = _change_within(
                    [u for u in inside if u.has(*attr)],
                    [u for u in inside if not u.has(*attr)],
                    metric,
                )
                o_ch, o_n = _change_within(
                    [u for u in outside if u.has(*attr)],
                    [u for u in outside if not u.has(*attr)],
                    metric,
                )
                strat.append(
                    {
                        "attribute": list(attr),
                        "controlling_for": list(partner),
                        "metric": metric,
                        "overall_change": overall,
                        "within": {"change": w_ch, "n": w_n},
                        "outside": {"change": o_ch, "n": o_n},
                    }
                )
    overlaps.sort(key=lambda o: -o["jaccard"])
    return overlaps, strat


def retention_by_reveal(units: list[Unit]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for b in REVEAL_BINS:
        grp = [u for u in units if u.has("first_product_shown", b)]
        if len(grp) < 3:
            continue
        reveals = [u.first_product_s for u in grp if u.first_product_s is not None]
        row: dict = {
            "n": len(grp),
            "median_dur_s": round(statistics.median(u.duration_s or 0 for u in grp)),
            "median_reveal_s": round(statistics.median(reveals)) if reveals else None,
        }
        shares = [retention_shares(u) for u in grp]
        for q in ("p25", "p50", "p75", "p100"):
            vals = [s[q] for s in shares if q in s]
            row[q] = round(statistics.median(vals), 3) if vals else None
        hooks = [unit_rates(u)["hook_rate"] for u in grp if "hook_rate" in unit_rates(u)]
        row["hook_rate"] = round(statistics.median(hooks), 3) if hooks else None
        enough = sum(u.purchases for u in grp) >= MIN_PURCHASES
        cpa = pooled(grp, "spend", "purchases")
        row["cpa"] = round(cpa) if enough and cpa else None
        ppk = pooled(grp, "purchases", "impressions")
        row["ppk"] = round(1000 * ppk, 2) if ppk else None
        out[b] = row
    return out


def _jaccard(a: list[str], b: list[str]) -> float:
    x, y = set(a), set(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def survivors(tests: list[Test]) -> list[Test]:
    """Account-wide results that clear every bar this run applies: they pass the
    false-discovery correction, carry no confound flag, and kept their grade when the run
    graded itself a second time on different shuffles."""
    return [
        t
        for t in tests
        if t.group is None and t.grade == "strong" and not t.confounds and not t.grade_unstable
    ]


def ideas(tests: list[Test]) -> list[list[Test]]:
    """The surviving results, grouped by the files they describe. Biggest effect first,
    within a group and between them."""
    out: list[list[Test]] = []
    for t in sorted(survivors(tests), key=lambda t: -abs(t.change)):
        for g in out:
            if _jaccard(t.cell_shas, g[0].cell_shas) >= SAME_FILES:
                g.append(t)
                break
        else:
            out.append([t])
    return out


def _tagger_summary(units: list[Unit], reliability: Reliability | None) -> dict | None:
    """The attributes this run flagged, worst first, for the page to own up to."""
    if reliability is None:
        return None
    flagged = []
    for dim, val in sorted({f for u in units for f in u.facets}):
        s = reliability.score(dim, val)
        if s is not None and s.agreement < MIN_AGREEMENT:
            flagged.append(
                {
                    "dimension": dim,
                    "value": val,
                    "agreement": s.agreement,
                    "files": s.files,
                    "own_score": s is reliability.values.get((dim, val)),
                    "n_units": sum(1 for u in units if u.has(dim, val)),
                }
            )
    flagged.sort(key=lambda r: (r["agreement"], -r["n_units"]))
    return {"min_agreement": MIN_AGREEMENT, "flagged": flagged}


def run(
    units: list[Unit],
    bin_edges: dict[str, tuple[float, float]],
    *,
    grouping_dims: list[str],
    draws: int,
    reliability: Reliability | None = None,
    with_interactions: bool = False,
) -> RunResult:
    kinds = dimension_kinds()
    tests = main_effects(units, bin_edges, kinds, draws)
    if with_interactions:
        tests += interactions(units, grouping_dims, kinds, draws)
    apply_grades(tests)
    # The same run, graded again on different shuffles. Only the pooled metrics move -- the
    # rank tests are exact -- but the correction spans every test, so the whole set is
    # re-graded and compared.
    if draws:
        second = main_effects(units, bin_edges, kinds, draws, seed=SECOND_SEED)
        if with_interactions:
            second += interactions(units, grouping_dims, kinds, draws, seed=SECOND_SEED)
        apply_grades(second)
        unstable = flag_unstable_grades(tests, second)
    else:
        unstable = 0
    if reliability is not None:
        flag_tagger_disagreement(tests, reliability)
    main = [t for t in tests if t.group is None]
    main.sort(key=lambda t: (GRADE_RANK[t.grade], len(t.confounds), -abs(t.change)))
    inter = [t for t in tests if t.group is not None]
    inter.sort(key=lambda t: (t.group or "", GRADE_RANK[t.grade], t.p))
    tests = main + inter
    overlaps, strat = entanglement(units, tests)
    summary = {
        "units": len(units),
        "creatives": sum(len(u.creative_ids) for u in units),
        "spend": round(float(sum(u.spend for u in units))),
        "purchases": sum(u.purchases for u in units),
        "tests": len(main),
        "interaction_tests": len(inter),
        "by_grade": {g: sum(1 for t in main if t.grade == g) for g in GRADE_RANK},
        "unstable_grades": unstable,
        "dimensions": sorted({t.dimension for t in main}),
        "first_day": min(u.first_day for u in units).isoformat() if units else None,
        "last_day": max(u.last_day for u in units).isoformat() if units else None,
        "tagger_agreement": _tagger_summary(units, reliability),
    }
    return RunResult(
        units=units,
        tests=tests,
        overlaps=overlaps,
        stratified=strat,
        retention_by_reveal=retention_by_reveal(units),
        summary=summary,
        bin_edges=bin_edges,
    )
