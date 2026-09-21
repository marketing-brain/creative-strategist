"""The Make block: proven ingredients the account has never combined, rendered as a brief.

`analyzers.untried` already finds the combinations. What it returns is a fact line naming
attributes, which is a test to run, not something an editor can shoot. This module keeps the
same selection rules and adds the material a brief needs -- which files already carry each
ingredient, the hooks they actually say, how long they run, when the product appears, and the
one number the result has to beat -- all of it read off the stored run, none of it written by
a model.

The evidence bar is the writer's, not a looser one: clean (unflagged) main effects, grade
strong or suggestive, and a `downstream` line on every brief so an attention lift is never
read as a purchase lift.
"""

from __future__ import annotations

import statistics
from itertools import combinations

from cs.insights.account_read.analyzers import (
    MAX_CARRIERS,
    MAX_COMBOS,
    clean_main_effects,
    is_good,
    single_dims,
)
from cs.insights.account_read.stats import pooled, unit_rates
from cs.insights.account_read.types import (
    LOWER_IS_BETTER,
    METRICS,
    POOLED_METRICS,
    RATE_METRICS,
    Test,
    Unit,
)
from cs.insights.account_read.writer import METRIC_NAME, fmt_val, label
from cs.insights.next_plan.policy import prescribable
from cs.insights.next_plan.stake import stake
from cs.insights.next_plan.types import (
    Evidence,
    MakeBrief,
    Ref,
    Shape,
    Watch,
    item_key,
)

MAX_HOOKS = 3
MAX_REFS = 3
CAPTION_AGREEMENT = 0.8  # share of carriers that must agree before a brief prescribes captions
MIN_SHAPE_FILES = 3  # below this a "shape" is one file, not a central tendency


def _listed(names: list[str]) -> str:
    """ "A, B and C" -- three ingredients joined with "and" twice read as one run-on clause."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def candidates(tests: list[Test], metrics: tuple[str, ...]) -> dict[tuple[str, str], Test]:
    """Best (lowest p) clean, positive main effect per attribute a brief may prescribe."""
    out: dict[tuple[str, str], Test] = {}
    for t in clean_main_effects(tests, metrics):
        if not is_good(t) or not prescribable(t.dimension):
            continue
        key = (t.dimension, t.value)
        if key not in out or t.p < out[key].p:
            out[key] = t
    return out


def _entangled(overlaps: list[dict]) -> set[frozenset]:
    return {frozenset((tuple(o["a"]), tuple(o["b"]))) for o in overlaps}


def _carriers(units: list[Unit], key: tuple[str, str]) -> list[Unit]:
    return sorted((u for u in units if u.has(*key)), key=lambda u: (-float(u.spend), u.sha256))


def _downstream(tests: list[Test], combo: tuple[tuple[str, str], ...]) -> tuple[str, str]:
    """(downstream line, caveat) for a combo, from the pooled purchase tests on its attributes.

    Follows the writer's rule: a lift that is not graded on purchases does not reach the
    purchase, and a graded worse line has to be said out loud.
    """
    keys = set(combo)
    pooled_tests = [
        t
        for t in tests
        if t.group is None and t.metric in POOLED_METRICS and (t.dimension, t.value) in keys
    ]
    graded = [t for t in pooled_tests if t.grade in ("strong", "suggestive")]
    bad = [t for t in graded if not is_good(t)]
    good = [t for t in graded if is_good(t)]
    if bad:
        worst = min(bad, key=lambda t: t.p)
        return (
            f"{label(worst)} is graded worse downstream: "
            f"{METRIC_NAME[worst.metric]} {worst.change * 100:+.0f}% ({worst.grade}).",
            "One ingredient already costs money at the purchase. Run this as an attention "
            "test only, and stop it on CPA rather than on the attention metric.",
        )
    if good:
        best = min(good, key=lambda t: t.p)
        return (
            f"{label(best)} also reads better downstream: "
            f"{METRIC_NAME[best.metric]} {best.change * 100:+.0f}% ({best.grade}).",
            "",
        )
    if pooled_tests:
        return (
            "Downstream, the same files show no clear difference in purchases or CPA.",
            "Nothing here is graded on purchases. This is an attention test, not a CPA test.",
        )
    return (
        "Purchases could not be measured for these files: too few to compare.",
        "Nothing here is graded on purchases. This is an attention test, not a CPA test.",
    )


def _watch(units: list[Unit], t: Test) -> Watch:
    """The metric the deciding comparison used, and the account's own value of it."""
    if t.metric in RATE_METRICS:
        vals = [r[t.metric] for r in (unit_rates(u) for u in units) if t.metric in r]
        value = statistics.median(vals) if vals else 0.0
        note = f"median across {len(vals)} files"
    elif t.metric == "purchases_per_1k":
        value = 1000 * (pooled(units, "purchases", "impressions") or 0.0)
        note = "pooled across the account"
    else:
        value = pooled(units, "spend", "purchases") or 0.0
        note = "pooled across the account"
    return Watch(
        metric=t.metric,
        metric_name=METRIC_NAME[t.metric],
        account_value=value,
        account_label=note,
        direction="below" if t.metric in LOWER_IS_BETTER else "above",
    )


def _duration_bounds(combo: tuple[tuple[str, str], ...], edges: dict) -> tuple[float, float] | None:
    """The seconds a duration tercile actually prescribes. `bin_edges` holds the two tercile
    boundaries; which side of them the value means is the value's own name (see writer.label)."""
    for dim, value in combo:
        if dim != "duration_s" or not edges.get(dim):
            continue
        lo, hi = edges[dim]
        return {"low": (0.0, lo), "mid": (lo, hi), "high": (hi, float("inf"))}.get(str(value))
    return None


def shape_units(units: list[Unit], combo: tuple[tuple[str, str], ...]) -> list[Unit]:
    """The files closest to the combination: those carrying the most of its ingredients.

    Averaging every file that carries any one ingredient describes the account, not the brief,
    and it contradicts the brief whenever an ingredient is itself a constraint -- a combination
    naming "duration under 51.5s" was printing a 76-second median, because the long files
    carrying the *other* ingredients dragged it there.

    A combination is untried precisely because few files come close to it, so the nearest set
    is often one file, and one file is an example, not a central tendency. Widen a step at a
    time until there are enough to average; `Shape.n_carriers` reports how many it took.
    """
    overlap = {u.sha256: sum(u.has(*k) for k in combo) for u in units}
    best = max(overlap.values(), default=0)
    for level in range(best, 0, -1):
        near = [u for u in units if overlap[u.sha256] >= level]
        if len(near) >= MIN_SHAPE_FILES:
            return near
    return [u for u in units if overlap[u.sha256] >= 1]


def _shape(carriers: list[Unit], combo: tuple[tuple[str, str], ...], edges: dict) -> Shape:
    """Central tendency of the files nearest the combination. Not a measured property of the
    combination itself -- no file carries the whole thing, which is why it is being made.

    A numeric ingredient states its own range, so the brief prescribes the tercile's edges
    rather than whatever the nearest files happen to average.
    """
    bounds = _duration_bounds(combo, edges)
    if bounds:
        lo, hi = bounds
        inside = [u for u in carriers if u.duration_s and lo <= u.duration_s <= hi]
        carriers = inside or carriers
    durations = sorted(u.duration_s for u in carriers if u.duration_s)
    reveals = sorted(u.first_product_s for u in carriers if u.first_product_s is not None)
    caption_vals = [v for u in carriers for d, v in u.facets if d == "captions"]
    captions = None
    if caption_vals:
        top, n = statistics.mode(caption_vals), len(caption_vals)
        if sum(1 for v in caption_vals if v == top) / n >= CAPTION_AGREEMENT:
            captions = top
    return Shape(
        duration_s=statistics.median(durations) if durations else None,
        duration_range=(durations[0], durations[-1]) if durations else None,
        first_product_s=statistics.median(reveals) if reveals else None,
        captions=captions,
        n_carriers=len(carriers),
    )


def build(
    units: list[Unit],
    tests: list[Test],
    overlaps: list[dict],
    account_spend: float,
    kinds: dict[str, str],
    bin_edges: dict | None = None,
    metrics: tuple[str, ...] = METRICS,
) -> tuple[list[MakeBrief], str | None]:
    bin_edges = bin_edges or {}
    cands = candidates(tests, metrics)
    if len(cands) < 2:
        return [], (
            "Fewer than two attributes have clean positive evidence, so there is no proven "
            "combination to propose."
        )
    entangled, single = _entangled(overlaps), single_dims(units)
    carriers_by_key = {k: {u.sha256 for u in units if u.has(*k)} for k in cands}
    stakes = {k: stake(t, units, kinds, account_spend) for k, t in cands.items()}

    def compatible(a: tuple[str, str], b: tuple[str, str]) -> bool:
        if a[0] == b[0] and a[0] in single:
            return False  # two values of one single-valued dimension are alternatives
        return frozenset((a, b)) not in entangled

    scored = []
    for r in (2, 3):
        for combo in combinations(sorted(cands), r):
            if not all(compatible(a, b) for a, b in combinations(combo, 2)):
                continue
            held = len(set.intersection(*(carriers_by_key[k] for k in combo)))
            if held > MAX_CARRIERS:
                continue
            # The combination needs every ingredient, so the ingredient with the least
            # addressable spend bounds what acting on it can move.
            binding = min(combo, key=lambda k: stakes[k].amount)
            # Addressable spend orders the list, but on an account where every proven
            # ingredient is rare the complements are nearly the same size and the amounts come
            # out within a few points of each other. The strength of the weakest ingredient's
            # evidence breaks those near-ties, so the better-evidenced combination wins.
            weakest_p = max(cands[k].p for k in combo)
            scored.append((stakes[binding].amount, weakest_p, held, binding, combo))
    if not scored:
        return [], (
            "Every pair of proven attributes is either already combined, entangled, or two "
            "values of the same dimension."
        )
    # Most addressable spend, then best-evidenced, then fewest carriers, then name for a
    # stable order between two combinations that tie on all three.
    scored.sort(key=lambda x: (-x[0], x[1], x[2], x[4]))

    briefs: list[MakeBrief] = []
    spent: set[tuple[str, str]] = set()
    for _, _, held, binding, combo in scored:
        if len(briefs) >= MAX_COMBOS:
            break
        # Every brief has to introduce an ingredient no earlier brief used. Without it the list
        # fills with one idea re-cut: a pair, then the same pair plus a third attribute, then
        # the same pair again with a fourth -- five rows the reader reads as five tests.
        if not set(combo) - spent:
            continue
        spent |= set(combo)
        evidence, refs, hooks, seen = [], [], [], set()
        for i, k in enumerate(combo, start=1):
            t = cands[k]
            evidence.append(
                Evidence(
                    pattern_id=f"P{i}",
                    label=label(t),
                    metric=t.metric,
                    metric_name=METRIC_NAME[t.metric],
                    change=t.change,
                    grade=t.grade,
                    n_cell=t.n_cell,
                    n_complement=t.n_complement,
                    cell_value=t.cell_value,
                    comp_value=t.comp_value,
                )
            )
            carriers = _carriers(units, k)
            ref_u = next((u for u in carriers if u.sha256 not in seen), None)
            if ref_u is not None and len(refs) < MAX_REFS:
                seen.add(ref_u.sha256)
                refs.append(
                    Ref(
                        sha256=ref_u.sha256,
                        headline=" ".join(ref_u.headline.split()),
                        rep_creative_id=(
                            str(ref_u.rep_creative_id) if ref_u.rep_creative_id else None
                        ),
                        spend=float(ref_u.spend),
                        shows=label(t),
                    )
                )
            # The highest-spend carrier need not be the one with a recorded hook, so the hook
            # is picked on its own rather than taken from whichever file became the reference.
            verbatim = next((u.hook_verbatim for u in carriers if u.hook_verbatim), None)
            if verbatim and len(hooks) < MAX_HOOKS:
                line = " ".join(verbatim.split())
                if line not in hooks:
                    hooks.append(line)
        nearest = shape_units(units, combo)
        decider = min((cands[k] for k in combo), key=lambda t: t.p)
        downstream, caveat = _downstream(tests, combo)
        names = [label(cands[k]) for k in combo]
        briefs.append(
            MakeBrief(
                key=item_key("make", [f"{d}={v}" for d, v in combo]),
                title=" + ".join(names),
                attributes=[[d, v] for d, v in combo],
                stake=stakes[binding],
                binding=label(cands[binding]),
                carriers=held,
                evidence=evidence,
                downstream=downstream,
                hooks=hooks,
                refs=refs,
                shape=_shape(nearest, combo, bin_edges),
                hold=(
                    "Keep the offer, the CTA and the landing page as they are. The only "
                    f"{'change is' if len(names) == 1 else 'changes are'} {_listed(names)}."
                ),
                watch=_watch(units, decider),
                caveat=caveat,
            )
        )
    return briefs, None


def watch_line(w: Watch) -> str:
    """One sentence for the page and for the verification script."""
    return (
        f"{w.metric_name} must come {w.direction} {fmt_val(w.metric, w.account_value)} "
        f"({w.account_label})."
    )
