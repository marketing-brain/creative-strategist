"""What happened after a card was issued. Pure: the job does the querying.

v1 recorded every recommendation and read none of them back, so the page could ask for the
same thing on thirty consecutive nights and never say whether anything came of it.

The measurement changed with the generator. It used to ask "does this file still lose viewers
at second 2", which only ever fitted a recut. A card is now "this file is on the wrong side of
finding P<n>", so the read-back asks the question the card actually posed: **is it still on
the wrong side?** That works for a claim, a cut rate or a duration, and it is answered by the
nightly account read's own tagging rather than by anything this module measures for itself.

Two questions, deliberately separate, because only one of them is about this file:

  1. Has *this file* moved? Its side of the cited attribute, now, against the side recorded
     when the card was issued.
  2. Has the account's newer work moved? Files first delivering after the card, split by which
     side of the same attribute they landed on.

What this must never do is join them into "you took our advice and it worked". Nothing here
establishes that a file changed *because* of a card, and the wording says what moved, not who
moved it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

MIN_AGE_DAYS = 7  # before this, neither question has an answer worth printing
MIN_NEW_FILES = 3  # below this, "the account has started doing X" is one launch, not a trend


@dataclass(frozen=True)
class Baseline:
    """What the card was issued on, frozen at that moment."""

    pid: str
    dimension: str
    value: str
    add: bool  # true when the file should take the attribute on
    label: str
    metric_name: str
    spend_30d: float


@dataclass(frozen=True)
class Now:
    """The same file, re-read against the same attribute."""

    on_right_side: bool | None  # None when the file is no longer in the read at all
    spend_30d: float


@dataclass(frozen=True)
class Adoption:
    n_right: int
    n_wrong: int


@dataclass(frozen=True)
class ReadBack:
    age_days: int
    outcome: str  # too_early | gone | moved | unchanged | unmeasurable
    detail: str
    adoption: str | None


def own_outcome(base: Baseline, now: Now, age_days: int) -> tuple[str, str]:
    if age_days < MIN_AGE_DAYS:
        return "too_early", (
            f"Issued {age_days} days ago. Give it about {MIN_AGE_DAYS} days of delivery before "
            "asking whether anything moved."
        )
    if now.spend_30d <= 0:
        return "gone", ("This file has stopped spending. The decision was made by turning it off.")
    if now.on_right_side is None:
        return "unmeasurable", (
            "This file is no longer in the account read — it has not been tagged since, so its "
            "side of this attribute cannot be checked."
        )
    if now.on_right_side:
        return "moved", (
            f"It is now on the right side of {base.pid}: {base.label} no longer describes it "
            f"the way it did when this card was written."
        )
    return "unchanged", (
        f"Still on the wrong side of {base.pid}. {base.label} describes this file exactly as it "
        f"did when the card was written."
    )


def adoption_line(base: Baseline, a: Adoption | None) -> str | None:
    """What the account's newer work does about the same attribute.

    Stated as a fact about launches, never as a claim that the card caused them.
    """
    if a is None:
        return None
    total = a.n_right + a.n_wrong
    if total < MIN_NEW_FILES:
        return (
            f"{total} new file{'' if total == 1 else 's'} launched since this card. Too few to "
            "say whether the account has moved."
        )
    if a.n_right == 0:
        return f"All {total} files launched since this card are on the wrong side of it too."
    if a.n_wrong == 0:
        return f"All {total} files launched since this card are on the right side of it."
    return (
        f"Of {total} files launched since this card, {a.n_right} are on the right side of "
        f"{base.pid} and {a.n_wrong} are not."
    )


def read_back(
    base: Baseline, now: Now, adoption: Adoption | None, issued: date, today: date
) -> ReadBack:
    age = (today - issued).days
    outcome, detail = own_outcome(base, now, age)
    return ReadBack(
        age_days=age,
        outcome=outcome,
        detail=detail,
        adoption=adoption_line(base, adoption) if age >= MIN_AGE_DAYS else None,
    )


def next_status(outcome: str) -> str:
    """The ledger's own status column. `ran` means the thing the card asked for appears to have
    happened; it is not a claim that the card is why."""
    return {"moved": "ran", "gone": "expired"}.get(outcome, "open")
