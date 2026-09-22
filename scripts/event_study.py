"""Within-video retention study: does what happens on screen move the exit rate?

Each video's curve is cut into the intervals Meta reports, and each interval is described by
what plays inside it (a cut, a cut to the product, a cut to a face). The file and the
position in the curve are held constant, so an effect is what happens inside videos, not a
difference between them.

Usage:
    uv run python scripts/event_study.py --account <uuid>
    uv run python scripts/event_study.py --account <uuid> --window first_7d
    uv run python scripts/event_study.py --selftest      # no database, synthetic curves

Read the output as exit rate: viewers lost per second, as a rate. 0.10 means about 10% of
those still watching leave during that second. An effect is the change in that rate, and the
interval is a 95% confidence interval from resampling whole files.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.insights.event_study import repo
from cs.insights.event_study.model import Effect, fit_effect, usable
from cs.insights.event_study.types import Interval

TREATMENTS = (
    ("a cut", lambda r: r.cuts_per_second),
    ("a cut to the product", lambda r: r.cuts_to_product / r.seconds),
    ("a cut to a face", lambda r: r.cuts_to_face / r.seconds),
)


def _line(e: Effect) -> str:
    mark = "no clear effect" if e.crosses_zero else "clear"
    return (
        f"  {e.name:<24} {e.estimate:+.4f} per cut/second "
        f"[{e.ci_low:+.4f}, {e.ci_high:+.4f}] {mark}\n"
        f"    {e.n_treated_obs} of {e.n_obs} intervals over {e.n_files} files | "
        f"raw exit rate {e.treated_mean:.3f} with vs {e.control_mean:.3f} without"
    )


def report(rows: list[Interval], label: str) -> None:
    print(f"\n{label}: {len(rows)} intervals, {len({r.footage_id for r in rows})} files")
    if not rows:
        return
    for name, fn in TREATMENTS:
        e = fit_effect(rows, fn, name=name)
        print(_line(e) if e else f"  {name:<24} too few files or no variation")


async def run(account_id: UUID, window: str) -> None:
    db = await Database.connect(get_settings().database_url)
    try:
        org_id = await db.fetchval("SELECT org_id FROM ad_account WHERE id=$1", account_id)
        if org_id is None:
            raise SystemExit(f"no ad account {account_id}")
        rows = usable(await repo.intervals(db, org_id, account_id, window))
    finally:
        await db.close()
    fine = [r for r in rows if r.seconds <= 1]
    print(f"window {window}")
    report(rows, "every interval")
    report(fine, "first 15 seconds only (1-second resolution)")
    starts = sum(r.cuts for r in rows)
    print(f"\ncuts inside measurable intervals: {starts}")


def selftest() -> None:
    """Synthetic curves with a known effect: the estimator should find it, and should not
    find one in the same curves with the cuts shuffled."""
    rng = random.Random(3)
    effect = 0.05
    rows: list[Interval] = []
    for f in range(40):
        fid = UUID(int=f)
        base = 0.04 + 0.03 * rng.random()  # this file's own drop-off level
        share = 1.0
        for bucket in range(15):
            cuts = 1 if rng.random() < 0.25 else 0
            position = 0.25 if bucket < 3 else 0.0  # everyone leaves fastest at the start
            rate = base + position + effect * cuts + rng.gauss(0, 0.004)
            nxt = share * math.exp(-max(rate, 0.001))
            rows.append(
                Interval(fid, bucket, bucket, bucket + 1, share, nxt, cuts, 0, 0, 60, 50_000)
            )
            share = nxt
    got = fit_effect(rows, lambda r: r.cuts_per_second, name="a cut")
    assert got is not None
    print(f"planted {effect:+.4f} -> {_line(got)}")
    assert abs(got.estimate - effect) < 0.01, got
    assert not got.crosses_zero, got

    shuffled = [r.cuts for r in rows]
    rng.shuffle(shuffled)
    placebo_rows = [
        Interval(**{**r.__dict__, "cuts": c}) for r, c in zip(rows, shuffled, strict=True)
    ]
    placebo = fit_effect(placebo_rows, lambda r: r.cuts_per_second, name="shuffled cuts")
    assert placebo is not None
    print(f"placebo      -> {_line(placebo)}")
    assert placebo.crosses_zero, placebo
    print("\nself-test passed")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account", type=UUID, help="ad account id")
    p.add_argument("--window", default="lifetime", choices=("lifetime", "first_7d"))
    p.add_argument("--selftest", action="store_true", help="synthetic check, no database")
    args = p.parse_args()
    if args.selftest:
        selftest()
        return
    if not args.account:
        raise SystemExit("--account is required (or --selftest)")
    asyncio.run(run(args.account, args.window))


if __name__ == "__main__":
    main()
