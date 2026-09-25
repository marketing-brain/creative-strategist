"""Turn the account read's findings into per-file candidates. Pure: no database, no model.

This replaces the contrast machinery `/next` used to run for itself, and it exists because
that machinery was a worse copy of something the read already does. A cited test carries its
grade, its p-value, its confound flags, its effect size, the metric it was measured on, and --
the part that made the parallel engine pointless -- `cell_shas`, the list of files on each
side. Asset anchoring was in the payload all along.

The rule this module enforces, which the hand-rolled version could not, is the read's own:
**follow every attribute to the purchase.** A card pushes a file from one side of an attribute
to the other, so before it ships, the money tests for that attribute are checked in the
direction of the push. On org 3 the previous generator told the account to take the product
out of the opening because files without it hold 23 points more at two seconds, while the same
run's strongest conversion finding said files that never show the product run $92 CPA against
$45 and called that retention "commercially hollow". Nothing in a generator built on three
hardcoded scene properties could see that. `money_blocks` sees it.

What is *not* here is the second. The read is account-level and has no time resolution; only
the retention curve can say where in a file the loss happens. That stays in `segment.py` and
is attached to a card when the finding's layer is one the curve can localise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cs.insights.account_read.analyzers import GOOD_GRADES, is_good
from cs.insights.account_read.patterns import dimension_kinds, scope_units
from cs.insights.account_read.types import Test, Unit
from cs.insights.account_read.writer import (
    METRIC_NAME,
    MONEY_METRICS,
    costs,
    label,
    money_siblings,
    pays,
)
from cs.insights.next_plan.policy import prescribable

_PID = re.compile(r"^P(\d+)$")

# A finding may cite a test the run graded as a coin toss. `grade_unstable` means the run
# graded itself twice under different random draws and disagreed with itself; that is not
# something to put a budget behind.
MIN_GRADES = GOOD_GRADES


@dataclass(frozen=True)
class Evidence:
    """One cited test, with the finding that cites it. `pid` is the run's own numbering, so a
    card points at the same P-id `/insights` prints instead of minting a colliding one."""

    pid: str
    dimension: str
    value: str
    label: str
    metric: str
    metric_name: str
    change: float
    grade: str
    p: float
    n_cell: int
    n_complement: int
    cell_value: float
    comp_value: float
    cell_is_good: bool
    # From the finding that cited it.
    layer: str
    title: str
    mechanism: str
    why_non_obvious: str
    confidence: str

    @property
    def carries_money(self) -> bool:
        return self.metric in MONEY_METRICS

    @property
    def add(self) -> bool:
        """True when the file should take the attribute on, false when it should drop it."""
        return self.cell_is_good


@dataclass(frozen=True)
class Rival:
    """Another graded result on the same dimension.

    Only single-valued dimensions have these, and they exist because the values of one are
    alternatives: a file cannot be both `duration_s = high` and `duration_s = low`, so a card
    that moves it to one necessarily takes it off the other. `money_blocks` checks the money
    tests for the value being moved *to*; it cannot see the value being moved *from*, and on
    org 3 that blind spot produced a card asking a 111-second file to become a short one --
    trading away link CTR +71% and thruplay +23%, both strong, and walking into link CTR -27%,
    also strong, to gain a *suggestive* 12% on CPA.
    """

    pid: str
    value: str
    metric: str
    metric_name: str
    change: float
    grade: str
    good: bool


@dataclass(frozen=True)
class Candidate:
    evidence: Evidence
    wrong_side: list[str]  # sha256 of files the evidence argues against
    right_side: list[str]  # sha256 of files to copy
    money_note: str  # what the purchase tests for this attribute say, in words
    single: bool  # the dimension holds one value at a time, so its values are alternatives
    rivals: list[Rival]  # every other graded result on the same dimension


def cited_tests(result: dict, read: dict | None) -> list[tuple[dict, Test, str]]:
    """(finding, test, pid) for every `P<n>` a finding cites.

    `page.build_page` fixes the numbering: P<n> is `tests[n-1]` of the run's full list, and
    that is the list to resolve against -- the page's copy is trimmed. Ids that are not `P<n>`
    (the read also cites retention bins as `R-never`) have no test and are skipped.
    """
    from cs.insights.account_read.types import test_from_json

    tests = result.get("tests") or []
    out: list[tuple[dict, Test, str]] = []
    for f in (read or {}).get("findings") or []:
        for pid in f.get("pattern_ids") or []:
            m = _PID.fullmatch(str(pid))
            if not m:
                continue
            i = int(m.group(1))
            if 1 <= i <= len(tests):
                out.append((f, test_from_json(tests[i - 1]), f"P{i}"))
    return out


def usable(t: Test) -> bool:
    """Main effect, cleanly graded, and something the product is allowed to prescribe."""
    return (
        t.group is None
        and t.grade in MIN_GRADES
        and not t.confounds
        and not t.grade_unstable
        and prescribable(t.dimension)
    )


def money_blocks(ev: Evidence, siblings: dict[tuple, list[Test]]) -> str | None:
    """Why this card must not ship, in the reader's words, or None if it may.

    The card moves a file toward one side of an attribute. The question is what the purchase
    and CPA tests for that same attribute say about the side it is being moved toward.
    """
    if ev.carries_money:
        return None  # the cited test is itself the money evidence
    for s in siblings.get((None, ev.dimension, str(ev.value)), []):
        if ev.add and costs(s):
            return (
                f"{ev.label} is graded worse downstream: {METRIC_NAME[s.metric]} "
                f"{s.change * 100:+.0f}% ({s.grade}). Adding it would buy attention and cost "
                f"money."
            )
        if not ev.add and pays(s):
            return (
                f"{ev.label} is graded better downstream: {METRIC_NAME[s.metric]} "
                f"{s.change * 100:+.0f}% ({s.grade}). Dropping it would cost money."
            )
    return None


def money_note(ev: Evidence, siblings: dict[tuple, list[Test]]) -> str:
    """What the purchase tests say, said out loud even when they say nothing.

    Silence here is what let an attention card read as a money card, so the absence of a
    downstream result is stated rather than left out.
    """
    if ev.carries_money:
        return f"This is measured on {ev.metric_name}, so the finding is about money directly."
    graded = [s for s in siblings.get((None, ev.dimension, str(ev.value)), []) if pays(s)]
    if graded:
        s = min(graded, key=lambda x: x.p)
        return (
            f"It also reads better downstream: {METRIC_NAME[s.metric]} {s.change * 100:+.0f}% "
            f"({s.grade})."
        )
    if siblings.get((None, ev.dimension, str(ev.value))):
        return (
            "Downstream, the same files show no clear difference in purchases or cost per "
            "purchase. This is an attention result, not a money result."
        )
    return (
        "Purchases could not be measured for these files. This is an attention result, not a "
        "money result."
    )


GRADE_ORDER = {"strong": 2, "suggestive": 1}


# Money is the outcome; clicks and views are proxies for it. On org 3 the proxy is measurably
# disconnected from the outcome -- early hold correlates +0.55 with link CTR and +0.22 with
# CPA, in the wrong direction -- so a strong result on a proxy must not veto a weaker result
# on the outcome. It earns a caveat on the card instead.
def _tier(metric: str) -> int:
    return 2 if metric in MONEY_METRICS else 1


def rivals_for(dimension: str, exclude_pid: str, tests: list[Test]) -> list[Rival]:
    """Every graded, unflagged main effect on one dimension, bar the card's own."""
    out: list[Rival] = []
    for i, t in enumerate(tests, start=1):
        pid = f"P{i}"
        if t.dimension != dimension or pid == exclude_pid or not usable(t):
            continue
        out.append(
            Rival(
                pid=pid,
                value=str(t.value),
                metric=t.metric,
                metric_name=METRIC_NAME[t.metric],
                change=t.change,
                grade=t.grade,
                good=is_good(t),
            )
        )
    return out


def _against(ev: Evidence, rivals: list[Rival], origin: str | None) -> list[Rival]:
    """Graded results that argue against this change: a bad one where it is going, or a good
    one where it is leaving. Only a single-valued dimension has the second kind, which is why
    this is decided per file rather than per finding."""
    out = []
    for r in rivals:
        if r.value == ev.value and ev.add and not r.good:
            out.append(r)
        elif origin is not None and r.value == origin and r.good:
            out.append(r)
    return out


def trade_blocks(ev: Evidence, rivals: list[Rival], origin: str | None) -> str | None:
    """Why this change is a bad trade, or None if it is only a trade worth naming.

    A rival stops a card only when it is at least as well graded *and* at least as far down
    the funnel. Grade alone is not enough: link CTR graded strong would otherwise veto CPA
    graded suggestive, which is a proxy overruling the outcome it stands in for.

    What that leaves is a card that is worth making and costs something. `trade_note` says so
    on the card rather than the product quietly picking a side of a real tension -- the read
    names it too, in the title "Short-form content is click-poor but suggestively
    purchase-efficient".
    """
    for r in _against(ev, rivals, origin):
        if GRADE_ORDER.get(r.grade, 0) < GRADE_ORDER.get(ev.grade, 0):
            continue
        if _tier(r.metric) < _tier(ev.metric):
            continue
        if r.value == ev.value:
            return (
                f"{r.pid} grades the same side worse on {r.metric_name} "
                f"({r.change * 100:+.0f}%, {r.grade}). A {ev.grade} gain on {ev.metric_name} "
                f"does not pay for it."
            )
        return (
            f"This file is currently {ev.dimension.replace('_', ' ')} = {origin}, which "
            f"{r.pid} grades better on {r.metric_name} ({r.change * 100:+.0f}%, {r.grade}). "
            f"Moving it would give that up for a {ev.grade} result."
        )
    return None


def trade_note(ev: Evidence, rivals: list[Rival], origin: str | None) -> str | None:
    """What this change costs, when it costs something the card is still worth doing."""
    worst = [r for r in _against(ev, rivals, origin) if GRADE_ORDER.get(r.grade, 0) > 0]
    if not worst:
        return None
    r = max(worst, key=lambda x: (GRADE_ORDER.get(x.grade, 0), abs(x.change)))
    where = "this one" if r.value == ev.value else "the length it runs now"
    return (
        f"It costs something: {r.pid} grades {where} {r.change * 100:+.0f}% on "
        f"{r.metric_name} ({r.grade}). This card follows {ev.metric_name}, which is the "
        f"outcome; {r.metric_name} is a step on the way to it."
    )


def _sides(t: Test, units: list[Unit], kinds: dict[str, str]) -> tuple[list[str], list[str]]:
    """(wrong side, right side) as sha256 lists.

    The complement is rebuilt the way `patterns.main_effects` built it: for a single-valued
    dimension only files that carry that dimension at all count, so "messenger is not expert"
    never silently includes files with no messenger tag.
    """
    scoped = scope_units(units, t.dimension, kinds)
    carriers = [u.sha256 for u in scoped if u.has(t.dimension, t.value)]
    others = [u.sha256 for u in scoped if not u.has(t.dimension, t.value)]
    return (others, carriers) if is_good(t) else (carriers, others)


def candidates(result: dict, read: dict | None) -> tuple[list[Candidate], list[str]]:
    """Every finding that can be acted on, and the reasons the others cannot.

    One candidate per attribute: a finding often cites the same attribute on two metrics, and
    the strongest of those is the one worth putting a file behind.
    """
    from cs.insights.account_read.types import test_from_json, unit_from_json

    units = [unit_from_json(u) for u in result.get("units") or []]
    all_tests = [test_from_json(t) for t in result.get("tests") or []]
    siblings = money_siblings(all_tests)
    kinds = dimension_kinds()

    best: dict[tuple[str, str], tuple[Test, Evidence]] = {}
    skipped: list[str] = []
    for f, t, pid in cited_tests(result, read):
        if not usable(t):
            continue
        ev = Evidence(
            pid=pid,
            dimension=t.dimension,
            value=str(t.value),
            label=label(t),
            metric=t.metric,
            metric_name=METRIC_NAME[t.metric],
            change=t.change,
            grade=t.grade,
            p=t.p,
            n_cell=t.n_cell,
            n_complement=t.n_complement,
            cell_value=t.cell_value,
            comp_value=t.comp_value,
            cell_is_good=is_good(t),
            layer=str(f.get("layer") or ""),
            title=str(f.get("title") or ""),
            mechanism=str(f.get("mechanism") or ""),
            why_non_obvious=str(f.get("why_non_obvious") or ""),
            confidence=str(f.get("confidence") or t.grade),
        )
        blocked = money_blocks(ev, siblings)
        if blocked:
            skipped.append(f"{ev.pid} {ev.label}: {blocked}")
            continue
        key = (t.dimension, str(t.value))
        # Money first, then the smaller p. An attribute graded on CPA is worth more than the
        # same attribute graded on hook rate, whatever their p-values.
        rank = (0 if ev.carries_money else 1, t.p)
        if key not in best or rank < (0 if best[key][1].carries_money else 1, best[key][0].p):
            best[key] = (t, ev)

    out: list[Candidate] = []
    for t, ev in best.values():
        wrong, right = _sides(t, units, kinds)
        if not wrong or not right:
            continue
        out.append(
            Candidate(
                evidence=ev,
                wrong_side=wrong,
                right_side=right,
                money_note=money_note(ev, siblings),
                single=kinds.get(t.dimension) == "single",
                rivals=rivals_for(t.dimension, ev.pid, all_tests),
            )
        )
    return out, skipped
