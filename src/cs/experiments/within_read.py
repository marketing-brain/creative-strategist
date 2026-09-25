"""The Insights tab's read, redone with every comparison made within clusters.

Same pipeline as the Insights tab (cs.insights.account_read), one change: each account-wide
test is re-run comparing a video only with videos of its own cluster. Per cluster, the gap
between videos with and without the attribute is a log ratio (of the typical video for rates, of
pooled numbers for cost and purchases); the gaps are pooled across clusters weighted by
n_with * n_without / n (a cluster where every video has it, or none does, drops out); and the
permutation test shuffles the attribute only within clusters, so a gap between kinds of ad can
never pass. Everything after that is the Insights tab's own code:

- the re-run tests are graded with the same Benjamini-Hochberg pass and grade words, twice on
  different shuffles, and a grade that moves is flagged unstable;
- each test keeps the original's confound flags (budget concentration, seasonality, tagger
  disagreement) and files;
- the ideas are the Insights tab's own ideas (its findings), each shown with its account-wide
  result beside its within-cluster one: at the Insights tab's bar (strong, unflagged, stable)
  nothing within clusters qualifies on its own, so the read re-checks what the tab says rather
  than looking for new findings (decided 2026-09-25);
- the brief, the writer, its gates and retries, and the page slice are the Insights tab's code,
  called unchanged. The brief's IDEAS section is replaced, two sections are added (the clusters,
  and the strong results that vanish within them), and the writer gets one paragraph of context.

A within test reports `cell_value` as what the videos with the trait actually did (in the
clusters that could be compared) and `comp_value` as what the same kinds of video without it
did, i.e. cell_value / (1 + change). Tests that cannot be compared within any cluster are
left out and listed in the brief instead.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from dataclasses import replace

from cs.insights.account_read.patterns import (
    GRADE_RANK,
    apply_grades,
    entanglement,
    ideas,
    retention_by_reveal,
)
from cs.insights.account_read.stats import MIN_PURCHASES, unit_rates
from cs.insights.account_read.types import RATE_METRICS, RunResult, Test, Unit
from cs.insights.account_read.writer import (
    METRIC_NAME,
    SYSTEM,
    build_brief,
    fmt_val,
    label,
    pattern_line,
)

DRAWS = 1000
# A test still at the floor after DRAWS keeps drawing, so its p-value is measured rather than
# capped at 1/1001: with ~1,000 tests the correction needs p far below that to pass.
MAX_DRAWS = 20000
EARLY_STOP_AFTER, EARLY_STOP_RATE = 200, 0.30  # the Insights tab's own early stop
SEEDS = (7, 8)


def _values(u: Unit, metric: str):
    """What one video contributes: its rate, or (spend, purchases, impressions) to pool."""
    if metric in RATE_METRICS:
        return unit_rates(u).get(metric)
    return (float(u.spend), u.purchases, u.impressions)


def _side(vals: list, metric: str) -> float | None:
    if metric in RATE_METRICS:
        v = [x for x in vals if x is not None]
        return statistics.median(v) if v else None
    spend = sum(x[0] for x in vals)
    purchases = sum(x[1] for x in vals)
    imps = sum(x[2] for x in vals)
    if metric == "cpa":
        return spend / purchases if purchases else None
    return purchases / imps * 1000 if imps else None


def _stat(strata: list[tuple[list, list[bool]]], metric: str) -> float | None:
    num = den = 0.0
    for vals, flags in strata:
        a = [v for v, f in zip(vals, flags, strict=True) if f]
        b = [v for v, f in zip(vals, flags, strict=True) if not f]
        if len(a) < 2 or len(b) < 2:
            continue
        va, vb = _side(a, metric), _side(b, metric)
        if not va or not vb:
            continue
        w = len(a) * len(b) / (len(a) + len(b))
        num += w * math.log(va / vb)
        den += w
    return num / den if den else None


def _within(t: Test, groups: list[list[Unit]], seed: int) -> Test | None:
    """One account-wide test redone within clusters, or None when no cluster can compare it."""
    attr = (t.dimension, t.value)
    strata, used = [], []
    for us in groups:
        flags = [u.has(*attr) for u in us]
        vals = [_values(u, t.metric) for u in us]
        if t.metric in RATE_METRICS:
            keep = [i for i, v in enumerate(vals) if v is not None]
            flags, vals, us = [flags[i] for i in keep], [vals[i] for i in keep], [us[i] for i in keep]
        if sum(flags) >= 2 and len(flags) - sum(flags) >= 2:
            strata.append((vals, flags))
            used.append((us, flags))
    if not strata:
        return None
    with_units = [u for us, fl in used for u, f in zip(us, fl, strict=True) if f]
    without_units = [u for us, fl in used for u, f in zip(us, fl, strict=True) if not f]
    if t.metric not in RATE_METRICS and (
        sum(u.purchases for u in with_units) < MIN_PURCHASES
        or sum(u.purchases for u in without_units) < MIN_PURCHASES
    ):
        return None
    obs = _stat(strata, t.metric)
    if obs is None:
        return None
    rng = random.Random(seed)
    hits = drawn = 0
    while drawn < MAX_DRAWS:
        if drawn >= DRAWS and hits > 2:
            break
        shuffled = []
        for vals, flags in strata:
            fl = flags[:]
            rng.shuffle(fl)
            shuffled.append((vals, fl))
        s = _stat(shuffled, t.metric)
        drawn += 1
        if s is not None and abs(s) >= abs(obs):
            hits += 1
        if drawn >= EARLY_STOP_AFTER and hits / drawn > EARLY_STOP_RATE:
            break
    change = math.exp(obs) - 1
    cell = _side([_values(u, t.metric) for u in with_units], t.metric)
    if not cell:
        return None
    return replace(
        t,
        n_cell=len(with_units),
        n_complement=len(without_units),
        cell_value=cell,
        comp_value=cell / (1 + change),
        change=change,
        p=(hits + 1) / (drawn + 1),
        grade="ungraded",
        cell_shas=[u.sha256 for u in with_units],
        grade_unstable=False,
        confounds=list(t.confounds),
    )


def within_tests(run: RunResult, cluster_of: dict[str, str]) -> tuple[list[Test], list[Test], int]:
    """Every account-wide test redone within clusters and graded the Insights tab's way.
    Returns (within tests, account-wide tests that could not be compared, unstable count)."""
    by_cluster: dict[str, list[Unit]] = defaultdict(list)
    for u in run.units:
        if u.sha256 in cluster_of:
            by_cluster[cluster_of[u.sha256]].append(u)
    groups = list(by_cluster.values())
    main = [t for t in run.tests if t.group is None]
    graded_runs = []
    for seed in SEEDS:
        out, lost = [], []
        for t in main:
            w = _within(t, groups, seed)
            (out if w else lost).append(w or t)
        apply_grades(out)
        graded_runs.append((out, lost))
    (tests, lost), (second, _) = graded_runs
    other = {(t.dimension, str(t.value), t.metric): t.grade for t in second}
    unstable = 0
    for t in tests:
        if other.get((t.dimension, str(t.value), t.metric), t.grade) != t.grade:
            t.grade_unstable = True
            unstable += 1
    tests.sort(key=lambda t: (GRADE_RANK[t.grade], len(t.confounds), -abs(t.change)))
    return tests, lost, unstable


def build_run(run: RunResult, cluster_of: dict[str, str]) -> tuple[RunResult, dict]:
    """The Insights run with its tests replaced by their within-cluster versions, plus what the
    brief needs about what changed."""
    tests, lost, unstable = within_tests(run, cluster_of)
    overlaps, strat = entanglement(run.units, tests)
    summary = {
        **run.summary,
        "tests": len(tests),
        "interaction_tests": 0,
        "by_grade": {g: sum(1 for t in tests if t.grade == g) for g in GRADE_RANK},
        "unstable_grades": unstable,
        "within_clusters": True,
    }
    within = RunResult(
        units=run.units,
        tests=tests,
        overlaps=overlaps,
        stratified=strat,
        retention_by_reveal=retention_by_reveal(run.units),
        summary=summary,
        bin_edges=run.bin_edges,
    )
    return within, {"lost": lost, "original": [t for t in run.tests if t.group is None]}


SYSTEM_WITHIN = (
    SYSTEM
    + """
CONTEXT FOR THIS READ
This read re-checks the account's existing Insights read. The account-wide read compared every
video with an attribute against every video without it, so an attribute that mostly lives in
one kind of ad can just be that kind of ad talking. Here the account's videos were sorted into
clusters by who carries the ad and when the product first appears (all AI-made videos are one
cluster; THE CLUSTERS lists them), and every comparison was redone within clusters: a video is
compared only with videos of its own cluster, and the gaps are pooled. The "with vs without"
numbers on a P line read: what the videos with the attribute did, against what the same kinds of
video without it did.

THE IDEAS are the account-wide read's own findings, in its order, each shown with its
account-wide result and its within-cluster P line. For each idea, the finding says whether it
holds among ads of the same kind, holds but weaker, or was the format (the within change is under
a third of the account-wide one, or flips), and what that means for the next ad. Put that verdict
in the title in plain words, and word it for this account (for example "Opening on a face was
the talking-head format, not the opening"; do not reuse this sentence). Cite the within P
line for every quantitative claim about the re-check; the account-wide numbers are in the brief
and may be quoted as context. An idea marked "not comparable within clusters" gets a finding that
says so and why, with no pattern ids. The overview says how much of the account-wide read
survives like for like.
"""
)


def build_brief_within(within: RunResult, extra: dict, cluster_of: dict[str, str]) -> str:
    """The Insights tab's brief for the within run, with the clusters and the vanished results."""
    brief = build_brief(within)
    brief = brief.replace(
        "Rate metrics compare per-file medians (Mann-Whitney U); pooled metrics use a permutation test.",
        "Every comparison is made within clusters: a file is compared only with files of its own "
        "cluster, the per-cluster gaps are pooled (weighted by n_with x n_without / n), and a "
        "permutation test shuffles the attribute only within clusters. 'With vs without' is what "
        "the files with the attribute did vs what the same kinds of file without it did.",
    )
    counts: dict[str, int] = defaultdict(int)
    for u in within.units:
        counts[cluster_of.get(u.sha256, "no cluster")] += 1
    clusters = "\n".join(f"  {name}: {n} files" for name, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    ids = {(t.dimension, str(t.value), t.metric): f"P{i}" for i, t in enumerate(within.tests, start=1)}
    by_key = {(t.dimension, str(t.value), t.metric): t for t in within.tests}
    vanished = []
    for o in extra["original"]:
        if o.grade != "strong" or o.confounds:
            continue
        k = (o.dimension, str(o.value), o.metric)
        w = by_key.get(k)
        if w is None or (w.change * o.change > 0 and abs(math.log1p(w.change)) >= abs(math.log1p(o.change)) / 3):
            continue
        vanished.append(
            f"  {label(o)} on {METRIC_NAME[o.metric]}: account-wide {fmt_val(o.metric, o.cell_value)} with vs "
            f"{fmt_val(o.metric, o.comp_value)} without ({o.change * 100:+.0f}%, strong); within clusters "
            f"{w.change * 100:+.0f}% ({ids[k]}, {w.grade})"
        )
    lost = sorted({label(t) for t in extra["lost"] if t.grade in ("strong", "suggestive")})
    # THE IDEAS: the account-wide read's own ideas, each re-checked, in its order.
    by_sha = {u.sha256: u for u in within.units}
    blocks = []
    for n, group in enumerate(ideas(extra["original"]), start=1):
        lines = []
        for o in group:
            k = (o.dimension, str(o.value), o.metric)
            acct = (
                f"account-wide {fmt_val(o.metric, o.cell_value)} with vs {fmt_val(o.metric, o.comp_value)} without "
                f"({o.change * 100:+.0f}%, {o.grade}, {o.n_cell} vs {o.n_complement} files)"
            )
            w = by_key.get(k)
            if w is None:
                lines.append(f"  {label(o)} on {METRIC_NAME[o.metric]}: {acct}; not comparable within clusters")
                continue
            la, lw = math.log1p(o.change), math.log1p(w.change)
            verdict = (
                "was the format" if la * lw <= 0 or abs(lw) < abs(la) / 3
                else "holds" if w.grade in ("strong", "suggestive") else "weakens past the bar"
            )
            lines.append(f"  {pattern_line(ids[k], w)}\n    {acct}; re-check: {verdict}")
        files = sorted((by_sha[x] for x in group[0].cell_shas if x in by_sha), key=lambda u: -u.spend)
        examples = "; ".join(f'"{u.headline}"' for u in files[:3])
        blocks.append(f"\nIDEA {n} ({len(group)} result{'s' if len(group) > 1 else ''}):\n" + "\n".join(lines) + f"\n    files: {examples}")
    start, end = brief.index("=== THE IDEAS"), brief.index("=== GRADED PATTERNS")
    brief = (
        brief[:start]
        + f"=== THE IDEAS: the account-wide read's findings, re-checked within clusters ({len(blocks)}) ===\n"
        "Each is one of the account-wide read's ideas, in its order, with its account-wide result and its\n"
        "within-cluster P line. Write one finding per idea, in order.\n"
        + "\n".join(blocks)
        + "\n\n"
        + brief[end:]
    )
    return (
        brief
        + f"""
=== THE CLUSTERS (every comparison above is made inside these) ===
{clusters}

=== WHAT WAS THE FORMAT: account-wide strong results that vanish within clusters ===
{chr(10).join(vanished) or "  none"}

=== COULD NOT BE COMPARED WITHIN A CLUSTER (the attribute is the cluster, or too few files on one side) ===
  {"; ".join(lost) or "none"}
"""
    )
