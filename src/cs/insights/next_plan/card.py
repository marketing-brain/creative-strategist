"""One card: a file that is on the wrong side of one of the account read's own findings.

The unit is still an edit to a specific asset -- that was the right half of the v2 design and
it stands. What changed is where the claim comes from. It used to come from three hardcoded
properties of the first shot, contrasted by this package's own statistics, priced in attention
and printed as dollars. It now comes from a graded, cited, money-checked finding that
`/insights` already publishes, and the card points at that finding's own P-id.

The consequence worth stating plainly: a card can now be about what the ad says, not only when
it cuts. `first product shown = never` costs $92 CPA against $45 on org 3, and the previous
generator was recommending the opposite because the same files hold viewers better.

Pricing. A card is priced in the file's own 30-day spend -- money exposed to the finding --
and quotes the finding's measured effect beside it. The two are never multiplied. Multiplying
them would forecast a return, which is out of scope (docs/next-surface.md §3), and the earlier
"recoverable" figure did exactly that one layer down: it priced an attention gap in dollars on
an account where holding viewers at two seconds has no measured relationship with cost per
purchase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cs.insights.account_read.types import Unit
from cs.insights.next_plan.finding import Candidate, Evidence, trade_blocks, trade_note
from cs.insights.next_plan.segment import (
    Asset,
    Control,
    Deficit,
    control_for,
    deepest_deficit,
    eligible,
)
from cs.insights.next_plan.types import item_key

MAX_REFS = 2
MIN_SPEND = 250.0  # below this a file is not a budget decision worth a page slot
# Metrics whose loss the retention curve can place in time. Only the two watching metrics
# qualify: a second is where viewers stopped watching, so it explains a hook or thruplay
# result and explains nothing about a click or a purchase.
#
# Narrower than the first attempt, which keyed off the finding's layer and produced a card
# telling a 111-second file to become a short one while pointing at second 2 -- a timestamp
# attached to a change that is not made at a timestamp.
TIMEABLE_METRICS = ("hook_rate", "thruplay_rate")
# A duration result is never localised: it is a fact about how long the file runs, and no
# second inside it is where that went wrong.
UNTIMEABLE_DIMS = ("duration_s",)

# Dimensions a cut cannot change: you cannot edit a 111-second file into a 40-second one and
# still call it the same file, and you cannot edit a different person onto camera. These get
# the reshoot wording -- brief the next one this way -- rather than "change this file".
#
# Conservative and additive on purpose. A dimension not listed is assumed editable, which is
# the safer error: over-claiming that something needs a reshoot only costs the reader a
# reshoot they did not need, while the reverse tells them to make an edit that cannot be made.
RESHOOT_DIMS = (
    "duration_s",
    "messenger",
    "cast",
    "narration",
    "opening_scene",
    "spoken_language",
)


@dataclass(frozen=True)
class Ref:
    footage_id: str
    sha256: str
    title: str
    rep_creative_id: str | None
    spend_30d: float
    value: float | None  # this file's own value of the card's metric, when it has one


@dataclass(frozen=True)
class Moment:
    """Where in the file the attention loss actually happens, when that can be measured.

    Only ever an addition to a card, never the reason for one. The finding decides that this
    file should change; the curve says where to start looking.
    """

    at_s: int
    splice_at: int
    hold: float
    control_hold: float
    control_kind: str
    control_n: int


@dataclass(frozen=True)
class Card:
    key: str
    own_value: float | None  # this file's own value of the cited metric, when it has one
    footage_id: str
    rep_creative_id: str | None
    title: str
    spend_30d: float
    evidence: Evidence
    money_note: str
    # What the change costs, when it costs something. Empty when it costs nothing measured.
    trade: str | None
    refs: list[Ref]
    moment: Moment | None

    @property
    def verb(self) -> str:
        if self.evidence.dimension in RESHOOT_DIMS:
            return "reshoot"
        return "recut" if self.moment is not None else "change"


def _refs(cand: Candidate, by_sha: dict, carded: set[str], metric: str) -> list[Asset]:
    """Files to copy: on the right side, and best on the metric the card cites.

    Ranking references by spend alone put the account's two biggest spenders on every card,
    which reads as boilerplate and tells the reader nothing about why those two. Ranking on
    the cited metric makes the reference argue for itself, and it varies with the finding.
    Spend still breaks ties and still gates: a file nobody funded is not a model.
    """
    pool = [by_sha[s] for s in cand.right_side if s in by_sha and s not in carded]
    rated = [(a, v) for a in pool if (v := a.metric(metric)) is not None]
    # Funded first, then best. Ranking on the metric alone swung from "the same two biggest
    # spenders on every card" to a $261 file as the thing to copy; a model nobody backed is
    # as unpersuasive as a model chosen only because it was expensive. The floor is the
    # median of the group's own spend, so it moves with the account instead of being a number
    # that ages.
    if len(rated) >= MAX_REFS:
        spends = sorted(a.spend_30d for a, _ in rated)
        floor = spends[len(spends) // 2]
        funded = [(a, v) for a, v in rated if a.spend_30d >= floor] or rated
        better_is_lower = metric == "cpa"
        funded.sort(key=lambda kv: (kv[1] if better_is_lower else -kv[1], -kv[0].spend_30d))
        return [a for a, _ in funded[:MAX_REFS]]
    return sorted(pool, key=lambda a: -a.spend_30d)[:MAX_REFS]


def _moment(asset: Asset, peers: list[Asset]) -> Moment | None:
    control: Control | None = control_for(asset, peers)
    if control is None:
        return None
    d: Deficit | None = deepest_deficit(asset, control)
    if d is None:
        return None
    return Moment(
        at_s=d.at_s,
        splice_at=d.at_s + 1,
        hold=d.asset_hold,
        control_hold=d.control_hold,
        control_kind=control.kind,
        control_n=control.n,
    )


def build(
    candidates: list[Candidate],
    assets: list[Asset],
    units: dict[str, Unit] | None = None,
    limit: int = 3,
) -> tuple[list[Card], str | None]:
    """The highest-spending file on the wrong side of each finding, richest file first.

    Deduplicated both ways. One card per file, because a single video sitting on the wrong
    side of five findings is one conversation, not five. One card per finding, because the
    page is a week of work and five cuts of the same idea is how v1's Make block failed.
    """
    by_sha = {a.sha256: a for a in assets if a.spend_30d >= MIN_SPEND}
    if not by_sha:
        return [], "No file in this account spent enough in the last 30 days to act on."
    if not candidates:
        return [], (
            "The account read has no finding this account can act on: every cited pattern is "
            "either flagged, ungraded, or already true of the files that spend."
        )

    # Best evidence per file: money first, then the smaller p.
    def rank(e: Evidence) -> tuple[int, float]:
        return (0 if e.carries_money else 1, e.p)

    carded_shas: set[str] = set()
    units = units or {}

    def origin_of(sha: str, c: Candidate) -> str | None:
        """Which value of a single-valued dimension this file currently sits on."""
        if not c.single:
            return None
        u = units.get(sha)
        if u is None:
            return None
        return next((v for d, v in u.facets if d == c.evidence.dimension), None)

    picks: dict[str, Candidate] = {}
    for c in candidates:
        for sha in c.wrong_side:
            if sha not in by_sha:
                continue
            # A trade is judged per file, because half of it is where the file starts.
            if trade_blocks(c.evidence, c.rivals, origin_of(sha, c)):
                continue
            held = picks.get(sha)
            if held is None or rank(c.evidence) < rank(held.evidence):
                picks[sha] = c

    ordered = sorted(picks.items(), key=lambda kv: -by_sha[kv[0]].spend_30d)
    usable_peers = [a for a in assets if eligible(a) and a.spend_30d > 0]
    cards: list[Card] = []
    spent_dims: set[str] = set()
    # Two passes: the targets are chosen first so that no card cites a file another card is
    # telling the account to change.
    chosen: list[tuple[str, Candidate]] = []
    for sha, cand in ordered:
        if len(chosen) >= limit:
            break
        # One card per *dimension*, not per finding. Two values of one single-valued dimension
        # are alternatives, and allowing both produced a page that told a long file to become
        # short (P112, CPA) and a short file to become long (P2, link CTR), each citing the
        # other as the example to copy. v1's Make block had this guard; dropping it was a bug.
        if cand.evidence.dimension in spent_dims:
            continue
        spent_dims.add(cand.evidence.dimension)
        chosen.append((sha, cand))
        carded_shas.add(sha)

    for sha, cand in chosen:
        ev = cand.evidence
        trade = trade_note(ev, cand.rivals, origin_of(sha, cand))
        asset = by_sha[sha]
        refs = _refs(cand, by_sha, carded_shas, ev.metric)
        timeable = ev.metric in TIMEABLE_METRICS and ev.dimension not in UNTIMEABLE_DIMS
        moment = _moment(asset, usable_peers) if timeable and eligible(asset) else None
        cards.append(
            Card(
                key=item_key("card", [asset.footage_id, ev.pid]),
                own_value=asset.metric(ev.metric),
                footage_id=asset.footage_id,
                rep_creative_id=asset.rep_creative_id,
                title=asset.title,
                spend_30d=asset.spend_30d,
                evidence=ev,
                money_note=cand.money_note,
                trade=trade,
                refs=[
                    Ref(
                        footage_id=r.footage_id,
                        sha256=r.sha256,
                        title=r.title,
                        rep_creative_id=r.rep_creative_id,
                        spend_30d=r.spend_30d,
                        value=r.metric(ev.metric),
                    )
                    for r in refs
                ],
                moment=moment,
            )
        )
    if not cards:
        return [], "Every file that spends is already on the right side of every graded finding."
    return cards, None


def _fmt(metric: str, v: float) -> str:
    if metric == "cpa":
        return f"${v:,.2f}"
    if metric == "purchases_per_1k":
        return f"{v:.2f}"
    return f"{v * 100:.1f}%"


def phrase(e: Evidence) -> str:
    """`first product shown = never` as something closer to English.

    Only closer. An attribute expression does not become an imperative by substitution, which
    is why the card states the change rather than commanding it and leaves the imperative to
    the writer.
    """
    dim, _, val = e.label.partition(" = ")
    return f"{dim} is {val}" if e.add else f"{dim} is no longer {val}"


def plain_value(e: Evidence) -> str:
    """The attribute's value as a person would say it, with no dimension prefix.

    A tercile carries its own range in parentheses -- "duration s = low (under 51.4s)" -- and
    the range is the only part an editor can act on; "low" alone means nothing outside the
    taxonomy. Everything else is just the value with its underscores taken out.

    This exists because the writer had no way to state a threshold except by pasting the whole
    expression, so "Request a new cut that meets duration s is low (under 51.4s)" was the best
    it could do while obeying the no-invented-numbers rule.
    """
    dim, _, val = e.label.partition(" = ")
    val = val or str(e.value)
    if "(" not in val:
        return val.replace("_", " ")
    rng = val[val.index("(") + 1 : val.rindex(")")]
    # A range that carries its own unit stands alone ("under 51.4s"). One that does not is
    # meaningless without the thing it counts: "2 and up" could be anything, so it becomes
    # "2 and up cuts per 10s". The test is a digit followed immediately by a letter, which
    # "51.4s" passes and "2 and up" does not.
    return rng if re.search(r"\d[a-z%]", rng) else f"{rng} {dim}".strip()


def headline(c: Card) -> str:
    """The fallback instruction, used when the writer's version was refused.

    It used to be the finding's title, which reads well and is the wrong kind of sentence: a
    title is a claim ("Low cut rates match the product's calm positioning"), and the slot it
    sits in is the instruction. A claim there tells the reader nothing to do. So the fallback
    is blunt rather than elegant -- attribute expressions do not linearise into English, and
    pretending otherwise is what v1's Make block shipped for a month -- but it is an
    instruction. The finding's title still appears, under "Why we believe this", where a claim
    belongs.
    """
    lead = "Brief a new cut" if c.verb == "reshoot" else "Change this ad"
    return f"{lead}: {phrase(c.evidence)}"


def position_line(c: Card) -> str:
    """Where this file sits, and how much money sits with it."""
    e = c.evidence
    is_or_not = "is not" if e.add else "is"
    return f"This file {is_or_not} {e.label}, and it spent ${c.spend_30d:,.0f} in the last 30 days."


def evidence_line(c: Card) -> str:
    e = c.evidence
    return (
        f"{e.pid}, graded {e.grade}: {e.metric_name} {_fmt(e.metric, e.cell_value)} for the "
        f"{e.n_cell} files that are {e.label}, against {_fmt(e.metric, e.comp_value)} for the "
        f"{e.n_complement} that are not — {e.change * 100:+.0f}%."
    )


def moment_line(c: Card) -> str | None:
    m = c.moment
    if m is None:
        return None
    # Context, not an instruction. The finding decides what changes; this only says where the
    # watching stops on this particular file.
    return (
        f"For context, this file loses viewers earliest at {m.at_s} seconds — it holds "
        f"{m.hold:.0%} there against {m.control_hold:.0%} for {m.control_n} {m.control_kind}."
    )


def job_line(c: Card) -> str:
    if c.verb == "reshoot":
        return (
            f"This is not an edit: brief the next cut of this concept so that {phrase(c.evidence)}."
        )
    return f"Change this file so that {phrase(c.evidence)}, and leave the rest of the cut alone."


def to_payload(card: Card, narration=None, read_back=None) -> dict:
    """The card as the page draws it. Page-shaped for the reason `next-view.ts` states: the
    API sends what the page renders and the page computes no number of its own."""
    e = card.evidence
    return {
        "key": card.key,
        "verb": card.verb,
        "footage_id": card.footage_id,
        "rep_creative_id": card.rep_creative_id,
        "title": card.title,
        "spend_30d": round(card.spend_30d, 2),
        "pid": e.pid,
        "layer": e.layer,
        "grade": e.grade,
        "confidence": e.confidence,
        "metric": e.metric,
        "metric_name": e.metric_name,
        "change": e.change,
        "label": e.label,
        "add": e.add,
        "plain_value": plain_value(e),
        "carries_money": e.carries_money,
        "at_s": card.moment.at_s if card.moment else None,
        "side_value": _fmt(e.metric, e.cell_value),
        "other_value": _fmt(e.metric, e.comp_value),
        # The wrong-side and right-side group averages, as numbers the page can compare, plus
        # this file's own -- shown only when it clears the purchase floor, because one file's
        # CPA off three purchases is a rounding artefact dressed as a fact.
        "wrong_value": _fmt(e.metric, e.cell_value if not e.add else e.comp_value),
        "right_value": _fmt(e.metric, e.comp_value if not e.add else e.cell_value),
        "own_value": card.own_value,
        "own_value_label": _fmt(e.metric, card.own_value) if card.own_value is not None else None,
        "lines": {
            "headline": headline(card),
            "finding": e.title,
            "position": position_line(card),
            "evidence": evidence_line(card),
            "money": card.money_note,
            "trade": card.trade,
            "mechanism": e.mechanism,
            "moment": moment_line(card),
            "job": job_line(card),
        },
        "written": None
        if narration is None
        else {
            "title": narration.title,
            "bleed": narration.bleed,
            "keep": narration.keep,
            "cause": narration.cause,
            "copy_this": narration.copy_this,
            "job": narration.job,
        },
        "refs": [
            {
                "footage_id": r.footage_id,
                "title": r.title,
                "rep_creative_id": r.rep_creative_id,
                "spend_30d": round(r.spend_30d, 2),
                "value": r.value,
                "value_label": _fmt(e.metric, r.value) if r.value is not None else None,
            }
            for r in card.refs
        ],
        "read_back": None
        if read_back is None
        else {
            "age_days": read_back.age_days,
            "outcome": read_back.outcome,
            "detail": read_back.detail,
            "adoption": read_back.adoption,
        },
    }
