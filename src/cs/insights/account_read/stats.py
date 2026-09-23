"""Pure-Python comparison statistics for footage units. No numpy, no scipy."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable
from functools import partial

from cs.insights.account_read.permutations import Draws
from cs.insights.account_read.types import RATE_METRICS, Test, Unit

IMP_FLOOR = 2000
PLAYS_FLOOR = 500
MIN_CELL = 5
MIN_PURCHASES = 20


def unit_rates(u: Unit) -> dict[str, float]:
    m: dict[str, float] = {}
    if u.impressions >= IMP_FLOOR:
        m["hook_rate"] = u.video_3s_views / u.impressions
        m["link_ctr"] = u.link_clicks / u.impressions
    if u.plays >= PLAYS_FLOOR:
        m["thruplay_rate"] = u.thruplays / u.plays
    return m


def retention_shares(u: Unit) -> dict[str, float]:
    if u.plays < PLAYS_FLOOR:
        return {}
    return {q: getattr(u, q) / u.plays for q in ("p25", "p50", "p75", "p100")}


def pooled(units: list[Unit], num: str, den: str) -> float | None:
    d = sum(getattr(u, den) for u in units)
    return (float(sum(getattr(u, num) for u in units)) / float(d)) if d else None


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def mann_whitney_p(x: list[float], y: list[float]) -> float:
    """Two-sided Mann-Whitney U, normal approximation with tie correction."""
    n1, n2 = len(x), len(y)
    allv = sorted([(v, 0) for v in x] + [(v, 1) for v in y])
    ranks = [0.0] * len(allv)
    i, tie = 0, 0.0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        t = j - i + 1
        if t > 1:
            tie += t**3 - t
        i = j + 1
    r1 = sum(r for r, (_, g) in zip(ranks, allv, strict=True) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    n = n1 + n2
    sigma2 = n1 * n2 / 12 * ((n + 1) - tie / (n * (n - 1)))
    if sigma2 <= 0:
        return 1.0
    z = (u1 - mu) / math.sqrt(sigma2)
    return max(min(2 * (1 - _phi(abs(z))), 1.0), 0.0)


def permutation_p(
    cell: list[Unit],
    comp: list[Unit],
    stat: Callable[[list[Unit], list[Unit]], float | None],
    draws: int = 1000,
    seed: int = 7,
) -> float:
    obs = stat(cell, comp)
    if obs is None:
        return 1.0
    rng = random.Random(seed)
    pool, n, hits = list(cell) + list(comp), len(cell), 0
    for _ in range(draws):
        rng.shuffle(pool)
        s = stat(pool[:n], pool[n:])
        if s is not None and abs(s) >= abs(obs):
            hits += 1
    return (hits + 1) / (draws + 1)


def money_permutation_p(
    cell: list[Unit],
    comp: list[Unit],
    draws: int = 1000,
    seed: int = 7,
    early_stop_after: int = 200,
    early_stop_rate: float = 0.30,
    shared: Draws | None = None,
) -> tuple[float, float]:
    """Permutation p-values for purchases-per-1,000 and CPA, from one set of shuffles.

    Both metrics partition the same files, and the two separate calls this replaces drew
    from `random.Random(7)` over the same pool -- identical shuffles, computed twice. One
    pass gives the same p-values for half the work.

    The draw loop reads three plain lists instead of the units' attributes, and sums only
    the cell side, taking the complement by subtraction from totals computed once. Same
    arithmetic in the same order; ~99% of this function's old cost was attribute lookups.

    `early_stop_after` / `early_stop_rate`: a comparison whose share of extreme draws is
    already far above every grade boundary (0.25 is the loosest) cannot change grade by
    drawing more, so it stops. Its p-value is then the estimate from the draws taken.

    `shared` hands over the shuffles of the whole set these files came from, so the draw
    loop reads a running total instead of walking the files (see
    cs.insights.account_read.permutations). Shuffling was 94% of this function's cost.
    """
    pc, pk = sum(u.purchases for u in cell), sum(u.purchases for u in comp)
    ic, ik = sum(u.impressions for u in cell), sum(u.impressions for u in comp)
    sc, sk = float(sum(u.spend for u in cell)), float(sum(u.spend for u in comp))
    if not (pc and pk and ic and ik and sc and sk):
        return 1.0, 1.0
    obs_ppk = abs((pc / ic) / (pk / ik) - 1)
    obs_cpa = abs((sc / pc) / (sk / pk) - 1)

    tot_p, tot_i, tot_s = pc + pk, ic + ik, sc + sk
    n = len(cell)
    # The set's shuffles when they are over exactly these files; otherwise this comparison's
    # own, which is what a caller outside a run (a test, the ask box) gets.
    use_shared = shared is not None and shared.covers(cell + comp)
    if not use_shared:
        purchases = [u.purchases for u in cell] + [u.purchases for u in comp]
        impressions = [u.impressions for u in cell] + [u.impressions for u in comp]
        spend = [float(u.spend) for u in cell] + [float(u.spend) for u in comp]
        order = list(range(len(purchases)))
        rng = random.Random(seed)
    hits_ppk = hits_cpa = 0
    drawn = 0
    for d in range(draws if not use_shared else min(draws, len(shared))):
        if use_shared:
            ap = shared.cum_purchases[d][n]
            ai = shared.cum_impressions[d][n]
            as_ = shared.cum_spend[d][n]
        else:
            rng.shuffle(order)
            ap = ai = 0
            as_ = 0.0
            for i in order[:n]:
                ap += purchases[i]
                ai += impressions[i]
                as_ += spend[i]
        drawn += 1
        bp, bi, bs = tot_p - ap, tot_i - ai, tot_s - as_
        if ap and bp and ai and bi:
            if abs((ap / ai) / (bp / bi) - 1) >= obs_ppk:
                hits_ppk += 1
            if as_ and bs and abs((as_ / ap) / (bs / bp) - 1) >= obs_cpa:
                hits_cpa += 1
        if drawn >= early_stop_after and min(hits_ppk, hits_cpa) / drawn > early_stop_rate:
            break
    return (hits_ppk + 1) / (drawn + 1), (hits_cpa + 1) / (drawn + 1)


def benjamini_hochberg(pvals: list[float], q: float = 0.10) -> set[int]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    k = 0
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= q * rank / m:
            k = rank
    return set(order[:k])


def grade(p: float, change: float, passes_bh: bool) -> str:
    if passes_bh:
        return "strong"
    if p < 0.10:
        return "suggestive"
    if p < 0.25 and abs(change) >= 0.15:
        return "directional"
    return "thin"


def _confounds(cell: list[Unit], comp: list[Unit]) -> list[str]:
    out = []
    cell_imp = sum(u.impressions for u in cell)
    if cell_imp and max(u.impressions for u in cell) / cell_imp >= 0.5:
        out.append("budget_concentration")
    mid_cell = statistics.median(u.midpoint() for u in cell)
    mid_comp = statistics.median(u.midpoint() for u in comp)
    if abs(mid_cell - mid_comp) > 15:
        out.append("seasonality")
    return out


def _ppk_stat(a: list[Unit], b: list[Unit]) -> float | None:
    ra, rb = pooled(a, "purchases", "impressions"), pooled(b, "purchases", "impressions")
    return None if not ra or not rb else ra / rb - 1


def _cpa_stat(a: list[Unit], b: list[Unit]) -> float | None:
    ra, rb = pooled(a, "spend", "purchases"), pooled(b, "spend", "purchases")
    return None if not ra or not rb else ra / rb - 1


def compare(
    cell: list[Unit],
    comp: list[Unit],
    *,
    dimension: str,
    value: str,
    group: str | None = None,
    bin_edges: tuple[float, float] | None = None,
    draws: int = 1000,
    shared: Draws | None = None,
) -> list[Test]:
    """Cell vs complement on every metric. Grades stay 'ungraded' until the run's BH pass."""
    if len(cell) < MIN_CELL or len(comp) < MIN_CELL:
        return []
    conf = _confounds(cell, comp)
    make = partial(
        Test,
        dimension=dimension,
        value=value,
        group=group,
        confounds=conf,
        cell_shas=[u.sha256 for u in cell],
        bin_edges=bin_edges,
    )
    out: list[Test] = []
    cell_rates = [unit_rates(u) for u in cell]
    comp_rates = [unit_rates(u) for u in comp]
    for metric in RATE_METRICS:
        x = [r[metric] for r in cell_rates if metric in r]
        y = [r[metric] for r in comp_rates if metric in r]
        if len(x) < MIN_CELL or len(y) < MIN_CELL:
            continue
        mx, my = statistics.median(x), statistics.median(y)
        if not my:
            continue
        out.append(
            make(
                metric=metric,
                kind="per_unit",
                n_cell=len(x),
                n_complement=len(y),
                cell_value=mx,
                comp_value=my,
                change=mx / my - 1,
                p=mann_whitney_p(x, y),
                grade="ungraded",
            )
        )
    pc, pk = sum(u.purchases for u in cell), sum(u.purchases for u in comp)
    if pc >= MIN_PURCHASES and pk >= MIN_PURCHASES:
        pooled_specs = (
            (
                "purchases_per_1k",
                _ppk_stat,
                1000 * (pooled(cell, "purchases", "impressions") or 0.0),
                1000 * (pooled(comp, "purchases", "impressions") or 0.0),
            ),
            (
                "cpa",
                _cpa_stat,
                pooled(cell, "spend", "purchases") or 0.0,
                pooled(comp, "spend", "purchases") or 0.0,
            ),
        )
        # One set of shuffles answers both metrics; they partition the same files.
        p_ppk, p_cpa = (
            money_permutation_p(cell, comp, draws, shared=shared) if draws else (1.0, 1.0)
        )
        for (metric, fn, cv, kv), p in zip(pooled_specs, (p_ppk, p_cpa), strict=True):
            ch = fn(cell, comp)
            if ch is None:
                continue
            out.append(
                make(
                    metric=metric,
                    kind="pooled",
                    n_cell=len(cell),
                    n_complement=len(comp),
                    cell_value=cv,
                    comp_value=kv,
                    change=ch,
                    p=p,
                    grade="ungraded",
                    cell_purchases=pc,
                )
            )
    # `make` hands every test the same list object. Later passes flag one test at a time, so
    # each needs its own copy or a flag on one metric would appear on all of them.
    for t in out:
        t.confounds = list(conf)
    return out
