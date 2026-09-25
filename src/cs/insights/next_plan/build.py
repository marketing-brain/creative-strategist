"""Assemble one `/next` plan from a stored account-read run. Pure: no database, no model.

Neither block on the page is built here. The allocation block needs a diagnosis per live ad
and the recut block needs every file's retention curve, so both are produced by the job and
merged in -- never computed on a page load. See docs/next-surface.md §6.
"""

from __future__ import annotations

from cs.insights.next_plan.types import Plan


def build_plan(result: dict, read: dict | None = None) -> Plan:
    """The frame the job fills: the window the account read covered, and the tests it named.

    Make and Explore used to be built from `result` here. They were removed in step 5 of the
    v2 build order: Make enumerated proven attributes the account had *not* combined, which
    selects for rarity rather than opportunity, and Explore listed taxonomy values with no
    files behind them, which is vocabulary browsing. What replaced both is a card anchored to
    a file that is already spending. `result` is still read for the window and the unit count
    that head the page.
    """
    # `next_tests` is deliberately not carried. It was the read's own prose suggestions,
    # rendered ungraded, uncited and unpriced beneath cards that carry a P-id, a grade and a
    # CPA -- and on org 3 it shipped "Test a 90s+ expert deep-dive" directly under a card
    # saying "Brief a new cut under 51.4s", plus "using a solo male messenger", which
    # `policy.NOT_PRESCRIBED` bars everywhere else on this page. The findings behind those
    # sentences now reach the page through the cards, graded and cited.
    units = result.get("units") or []
    summary = result.get("summary") or {}
    return Plan(
        account_spend=float(sum(float(u.get("spend") or 0) for u in units)),
        window_start=summary.get("first_day"),
        window_end=summary.get("last_day"),
        n_units=len(units),
    )
