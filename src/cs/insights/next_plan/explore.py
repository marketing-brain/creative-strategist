"""The Explore block: what this account has never made.

Every other recommendation in the product is an exploitation: `untried` seeds from attributes
that already have positive evidence, and an attribute with no files has no test, so the
highest-value next ad can be one the account simply never tried. Nothing measured can rank
these, and nothing here pretends to -- the block reports absence, in the vocabulary the tagger
would use if the ad existed.

The universe is the closed value lists in `taxonomy.yaml`, single- and multi-valued alike.
Excluded: the dimensions `facets.SKIP_DIMS` keeps out of the read, the numeric terciles, the
open and free-text kinds (no fixed universe, so "never tried" is not defined), and the values
that describe an absence rather than a choice -- "never tried no offer" is not a brief.
"""

from __future__ import annotations

from collections import Counter

from cs.insights.account_read.facets import NUMERIC_DIMS, SKIP_DIMS
from cs.insights.account_read.stats import MIN_CELL
from cs.insights.account_read.types import Unit
from cs.insights.next_plan.policy import explorable
from cs.insights.next_plan.types import ExploreRow, item_key
from cs.tagging.taxonomy import load_taxonomy

CLOSED_KINDS = ("single", "multi")
# Values that name the absence of a choice. Their emptiness is not an opportunity.
ABSENCE_VALUES = {"none", "other", "not_applicable", "unknown", "no"}


def build(units: list[Unit], fmt: str = "video") -> tuple[list[ExploreRow], str | None]:
    counts = Counter(f for u in units for f in u.facets)
    rows: list[ExploreRow] = []
    for d in load_taxonomy().dimensions:
        if fmt not in d.applies_to or d.kind not in CLOSED_KINDS or not d.values:
            continue
        if d.name in SKIP_DIMS or d.name in NUMERIC_DIMS or not explorable(d.name):
            continue
        for value in d.values:
            if value in ABSENCE_VALUES:
                continue
            n = counts.get((d.name, value), 0)
            if n >= MIN_CELL:
                continue
            rows.append(
                ExploreRow(
                    key=item_key("explore", [f"{d.name}={value}"]),
                    dimension=d.name,
                    group=d.group,
                    value=value,
                    n_units=n,
                    status="never tried" if n == 0 else "too few files to test",
                )
            )
    if not rows:
        return [], (
            f"Every value in the taxonomy has at least {MIN_CELL} files in this account, so "
            "there is nothing left untested to propose."
        )
    # Unranked on purpose: taxonomy order, grouped, so the same list reads the same way twice.
    return rows, None
