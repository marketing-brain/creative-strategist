"""Assemble one `/next` plan from a stored account-read run. Pure: no database, no model.

The allocation block (scale / fix / replace) is not here. It needs a diagnosis per live ad,
which is several queries each, so it is produced by the job and merged in -- never computed on
a page load. See docs/next-surface.md §6.
"""

from __future__ import annotations

from cs.insights.account_read.patterns import dimension_kinds
from cs.insights.account_read.types import Unit, test_from_json, unit_from_json
from cs.insights.next_plan import explore, make
from cs.insights.next_plan.types import Plan


def build_plan(result: dict, read: dict | None = None, fmt: str = "video") -> Plan:
    units: list[Unit] = [unit_from_json(u) for u in result.get("units") or []]
    tests = [test_from_json(t) for t in result.get("tests") or []]
    overlaps = result.get("overlaps") or []
    summary = result.get("summary") or {}
    account_spend = float(sum(u.spend for u in units))
    kinds = dimension_kinds()

    edges = {d: tuple(e) for d, e in (result.get("bin_edges") or {}).items() if e}
    briefs, make_empty = make.build(units, tests, overlaps, account_spend, kinds, edges)
    rows, explore_empty = explore.build(units, fmt)
    return Plan(
        account_spend=account_spend,
        window_start=summary.get("first_day"),
        window_end=summary.get("last_day"),
        n_units=len(units),
        make=briefs,
        make_empty_reason=make_empty,
        explore=rows,
        explore_empty_reason=explore_empty,
        next_tests=[str(x) for x in ((read or {}).get("next_tests") or [])],
    )
