"""Spend on the wrong side of a graded difference: how the plan ranks.

Ranking by p-value ranks by confidence, not by consequence, and predicting a return is out of
scope for this product. What is left is money already committed and already measured.

For a graded difference, the wrong side is the side the evidence argues against: the files
*lacking* an attribute that does better, or the files *carrying* one that does worse. Their
spend is the addressable amount -- what could move if the account acted on the pattern. An
attribute already carried by most of the spend scores low (little room left); a proven
attribute starved of budget scores high. That is the same reading `spend_gap` makes of one
attribute, applied as an ordering.

The spend in a `Unit` pools every day `metric_daily` holds for its creatives, so these are
window totals, not trailing 30 days. `types.Stake` carries the window so a row never prints a
bare number the reader could take for something else.
"""

from __future__ import annotations

from cs.insights.account_read.analyzers import is_good
from cs.insights.account_read.patterns import scope_units
from cs.insights.account_read.types import Test, Unit
from cs.insights.next_plan.types import Stake


def wrong_side(t: Test, units: list[Unit], kinds: dict[str, str]) -> list[Unit]:
    """The units the evidence argues against.

    The complement is rebuilt the way `patterns.main_effects` built it -- for a single-valued
    dimension only the units that carry that dimension at all count, so "messenger is not
    expert" never silently includes files with no messenger tag.
    """
    if is_good(t):
        return [
            u for u in scope_units(units, t.dimension, kinds) if not u.has(t.dimension, t.value)
        ]
    cell = set(t.cell_shas)
    return [u for u in units if u.sha256 in cell]


def window_spend(units: list[Unit]) -> float:
    return float(sum(u.spend for u in units))


def stake(t: Test, units: list[Unit], kinds: dict[str, str], account_spend: float) -> Stake:
    amount = window_spend(wrong_side(t, units, kinds))
    side = "lack it" if is_good(t) else "carry it"
    return Stake(
        amount=amount,
        share=(amount / account_spend) if account_spend else 0.0,
        window="window",
        basis=f"spend on the files that {side}",
    )
