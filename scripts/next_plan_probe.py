"""Build the `/next` plan from one ad account's latest stored account-read run and print it.

This is the real-data gate for the plan builders: read the output by hand and check that each
brief is something you could actually hand to an editor, and that no number outruns its grade.

Usage: uv run python scripts/next_plan_probe.py --ad-account <uuid> [--json]
"""

import argparse
import asyncio
import json
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.insights.account_read import repo
from cs.insights.account_read.writer import fmt_val
from cs.insights.next_plan import jobs as plan_jobs
from cs.insights.next_plan.allocate import build as build_allocation
from cs.insights.next_plan.build import build_plan
from cs.insights.next_plan.make import watch_line


def money(v: float) -> str:
    return f"${v:,.0f}"


def show(plan) -> None:
    print(
        f"\n{plan.n_units} files | {money(plan.account_spend)} over "
        f"{plan.window_start} to {plan.window_end}\n"
    )
    print("=" * 78)
    print("ALLOCATE")
    print("=" * 78)
    if plan.allocate_empty_reason:
        print(f"  (none) {plan.allocate_empty_reason}")
    for r in plan.allocate:
        print(
            f"\n  [{r.kind.upper():<7}] {money(r.stake.amount)} ({r.stake.share:.1%}) — "
            f"{r.stake.basis}\n            {r.headline[:70]!r}\n            {r.verdict}"
        )
        if r.detail:
            print(f"            {r.detail}")

    print("\n" + "=" * 78)
    print("MAKE")
    print("=" * 78)
    if plan.make_empty_reason:
        print(f"  (none) {plan.make_empty_reason}")
    for i, b in enumerate(plan.make, start=1):
        print(f"\n{i}. {b.title}")
        print(
            f"   Stake     {money(b.stake.amount)} ({b.stake.share:.0%} of window spend) — "
            f"{b.stake.basis}\n"
            f"             binding ingredient: {b.binding}"
        )
        print(f"   Carriers  {b.carriers} file(s) already hold the whole combination")
        for e in b.evidence:
            print(
                f"   {e.pattern_id:<9} {e.label}: {e.metric_name} {e.change * 100:+.0f}% "
                f"{e.grade} ({e.n_cell} vs {e.n_complement} files, "
                f"{fmt_val(e.metric, e.cell_value)} vs {fmt_val(e.metric, e.comp_value)})"
            )
        print(f"   Down      {b.downstream}")
        for h in b.hooks:
            print(f'   Hook      "{h}"')
        for r in b.refs:
            print(f"   Ref       {r.headline!r} ({money(r.spend)}) — shows {r.shows}")
        s = b.shape
        bits = []
        if s.duration_s:
            lo, hi = s.duration_range or (s.duration_s, s.duration_s)
            bits.append(f"length ~{s.duration_s:.0f}s (carriers {lo:.0f}-{hi:.0f}s)")
        if s.first_product_s is not None:
            bits.append(f"product on screen by {s.first_product_s:.0f}s")
        if s.captions:
            bits.append(f"captions: {s.captions}")
        print(f"   Shape     {' · '.join(bits) or '(no shared shape)'} [{s.n_carriers} carriers]")
        print(f"   Hold      {b.hold}")
        print(f"   Watch     {watch_line(b.watch)}")
        if b.caveat:
            print(f"   Caveat    {b.caveat}")

    print("\n" + "=" * 78)
    print("EXPLORE")
    print("=" * 78)
    if plan.explore_empty_reason:
        print(f"  (none) {plan.explore_empty_reason}")
    by_dim: dict[tuple[str, str], list] = {}
    for r in plan.explore:
        by_dim.setdefault((r.group, r.dimension), []).append(r)
    for (group, dim), rows in by_dim.items():
        never = [r.value for r in rows if r.status == "never tried"]
        few = [f"{r.value} ({r.n_units})" for r in rows if r.status != "never tried"]
        print(f"\n  {group} / {dim}")
        if never:
            print(f"    never tried:   {', '.join(never)}")
        if few:
            print(f"    too few files: {', '.join(few)}")

    if plan.next_tests:
        print("\n" + "=" * 78)
        print("NEXT TESTS (from the stored read)")
        print("=" * 78)
        for t in plan.next_tests:
            print(f"  - {t}")


async def main(ad_account_id: UUID, as_json: bool, allocate: bool, limit: int) -> None:
    db = await Database.connect(get_settings().database_url)
    try:
        row = await db.fetchrow("SELECT org_id FROM ad_account WHERE id=$1", ad_account_id)
        if row is None:
            raise SystemExit(f"no ad_account {ad_account_id}")
        run = await repo.latest_run(db, row["org_id"], ad_account_id)
        if run is None:
            raise SystemExit("no account read run for this account")
        result = await repo.run_result(db, run["id"])
        plan = build_plan(result, run.get("read"))
        if allocate:
            # Read-only: runs the job's loaders and the pure rules, writes nothing.
            plan_jobs.MAX_DIAGNOSED = limit
            ads, spend_30d = await plan_jobs._diagnose_all(db, row["org_id"], ad_account_id)
            print(f"(diagnosed {len(ads)} footage groups; 30-day account spend {spend_30d:,.0f})")
            plan.allocate, plan.allocate_empty_reason = build_allocation(ads, spend_30d)
    finally:
        await db.close()
    if as_json:
        print(json.dumps(plan.to_dict(), indent=2))
    else:
        show(plan)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ad-account", required=True, type=UUID)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--allocate", action="store_true", help="also run the allocation block (read-only)"
    )
    ap.add_argument("--limit", type=int, default=12, help="footage groups to diagnose")
    a = ap.parse_args()
    asyncio.run(main(a.ad_account, a.json, a.allocate, a.limit))
