"""Shuffles, computed once for a set of files and read by every comparison over them.

A permutation test asks: if these files were split at random, how often would the gap be
this big? Every comparison over one set of files -- a whole account, or one group -- splits
the *same* files, so each was shuffling the same list a thousand times over. Profiling one
group of 141 files and 211 comparisons put 94% of the time in `random.shuffle`: 1.7 million
random draws to answer questions that could share the answer.

Here the shuffling happens once per set. Each draw walks the permuted files keeping running
totals, so a comparison whose cell holds k files reads its resampled purchases, impressions
and spend straight out of position k. Cost per set falls from `comparisons x draws x files`
to `draws x files + comparisons x draws`.

What this changes: a comparison sees the set's shuffles rather than its own, so its p-value
moves by the usual sampling wobble, and comparisons over the same files share their
randomness. Both were already true of the code this replaces -- every comparison seeded the
same generator with 7 -- and Benjamini-Hochberg holds under this kind of dependence.
"""

from __future__ import annotations

import random
from array import array
from dataclasses import dataclass

from cs.insights.account_read.types import Unit


@dataclass(frozen=True)
class Draws:
    """`draws` random orders of one set of files, as running totals.

    `cum_*[d][k]` is the total over the first k files of draw d, so a cell of k files costs
    one lookup instead of a walk. Each row has n + 1 entries; position 0 is the empty total.

    `members` is who the set is, not merely how many: two scopes of equal size are not
    interchangeable, and reading one set's shuffles for another's comparison would quietly
    answer a different question. Rows are `array`, not `list`: on the 2 GB box a thousand
    draws over 170 files is 4 MB this way and 18 MB as Python objects.
    """

    members: frozenset[str]
    cum_purchases: list[array]
    cum_impressions: list[array]
    cum_spend: list[array]

    @property
    def n(self) -> int:
        return len(self.members)

    def __len__(self) -> int:
        return len(self.cum_purchases)

    def covers(self, units: list[Unit]) -> bool:
        return len(units) == self.n and {u.sha256 for u in units} == self.members


def build(units: list[Unit], draws: int = 1000, seed: int = 7) -> Draws:
    purchases = [u.purchases for u in units]
    impressions = [u.impressions for u in units]
    spend = [float(u.spend) for u in units]
    order = list(range(len(units)))
    rng = random.Random(seed)
    cp: list[array] = []
    ci: list[array] = []
    cs: list[array] = []
    for _ in range(draws):
        rng.shuffle(order)
        rp, ri, rs = array("q", [0]), array("q", [0]), array("d", [0.0])
        ap = ai = 0
        as_ = 0.0
        for i in order:
            ap += purchases[i]
            ai += impressions[i]
            as_ += spend[i]
            rp.append(ap)
            ri.append(ai)
            rs.append(as_)
        cp.append(rp)
        ci.append(ri)
        cs.append(rs)
    return Draws(frozenset(u.sha256 for u in units), cp, ci, cs)


class Cache:
    """The shuffles of one set at a time.

    Comparisons arrive grouped by scope -- all the values of a dimension, then the next --
    so holding the newest set is enough to serve nearly every one of them, and holding more
    would cost megabytes for sets already behind us.
    """

    def __init__(self, draws: int = 1000, seed: int = 7):
        self.draws, self.seed = draws, seed
        self._current: Draws | None = None

    def for_units(self, units: list[Unit]) -> Draws | None:
        if not self.draws or not units:
            return None
        if self._current is None or not self._current.covers(units):
            self._current = build(units, self.draws, self.seed)
        return self._current
