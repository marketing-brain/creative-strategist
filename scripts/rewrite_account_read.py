"""Re-writes the account read from statistics already in the database.

The statistics are the cheap half (about 45 seconds) and they do not change unless the
tagging or the metrics do; the writing is the slow, paid half. This runs only the writing,
against the newest stored run, and prints every call it makes: how long it took, what it
cost, and which gate sent the draft back.

Usage:
    uv run python scripts/rewrite_account_read.py --ad-account <uuid>          # dry run
    uv run python scripts/rewrite_account_read.py --ad-account <uuid> --store  # keep it

DRY RUN IS THE DEFAULT, but the model calls are real and are paid for either way; --store
only decides whether the result becomes the read the page serves.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.insights.account_read import repo
from cs.insights.account_read.jobs import _Ledger
from cs.insights.account_read.patterns import ideas
from cs.insights.account_read.types import RunResult, test_from_json, to_json, unit_from_json
from cs.insights.account_read.writer import (
    GeminiGatewayAccountReadWriter,
    build_brief,
    write_read,
)


class Narrating:
    """The real writer, saying what each call cost and how long it took."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[dict] = []

    def model_for(self, call):
        return self.inner.model_for(
            self.inner.correct if call == self.correct else self.inner.draft
        )

    async def draft(self, system: str, user: str):
        return await self._say("draft", self.inner.draft, system, user)

    async def correct(self, system: str, user: str):
        return await self._say("correction", self.inner.correct, system, user)

    async def _say(self, kind, call, system: str, user: str):
        n = len(self.calls) + 1
        model = self.inner.model_for(call)
        print(f"\n  call {n} ({kind} on {model}, prompt {len(user):,} chars)")
        t = time.time()
        try:
            text, ti, to = await call(system, user)
        except Exception as exc:
            print(f"    failed after {time.time() - t:.0f}s: {type(exc).__name__}: {exc}")
            raise
        took = time.time() - t
        print(f"    {took:.0f}s | {ti:,} in / {to:,} out | answer {len(text):,} chars")
        self.calls.append({"kind": kind, "seconds": took, "in": ti, "out": to})
        return text, ti, to


async def main(ad_account_id: UUID, store: bool) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    try:
        row = await db.fetchrow(
            "SELECT id, org_id, result, created_at FROM account_read_run"
            " WHERE ad_account_id=$1 AND status='done' ORDER BY created_at DESC LIMIT 1",
            ad_account_id,
        )
        if row is None:
            raise SystemExit("no stored statistics for that account")
        result = row["result"] if isinstance(row["result"], dict) else json.loads(row["result"])
        run = RunResult(
            units=[unit_from_json(u) for u in result["units"]],
            tests=[test_from_json(t) for t in result["tests"]],
            overlaps=result.get("overlaps") or [],
            stratified=result.get("stratified") or [],
            retention_by_reveal=result.get("retention_by_reveal") or {},
            summary=result["summary"],
            bin_edges={},
        )
        groups = ideas(run.tests)
        print(
            f"statistics from {row['created_at']:%Y-%m-%d %H:%M}: {len(run.units)} files, "
            f"{len(run.tests)} comparisons, {sum(len(g) for g in groups)} results in "
            f"{len(groups)} ideas"
        )
        print(f"brief: {len(build_brief(run)):,} chars")

        writer = Narrating(GeminiGatewayAccountReadWriter(s))
        ledger = _Ledger(db, row["org_id"], ad_account_id, s)
        t = time.time()
        read, problems, ti, to = await write_read(run, writer, ledger)
        print(f"\n{len(writer.calls)} calls, {time.time() - t:.0f}s, {ti:,} in / {to:,} out")

        if read is None:
            print("\nREJECTED. The last draft still broke:")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"\n{len(read.findings)} findings:")
            for i, f in enumerate(read.findings, 1):
                print(f"  {i:>2}. [{f.layer}/{f.confidence}] {f.title}")
                print(f"      cites {', '.join(f.pattern_ids)}")
        if store:
            await repo.insert_run(
                db,
                row["org_id"],
                ad_account_id,
                status="done" if read else "rejected",
                n_units=len(run.units),
                result=result,
                read=to_json(read) if read else None,
                error="; ".join(problems) or None,
                input_tokens=ti,
                output_tokens=to,
            )
            print("\nstored: the page now serves this read")
        else:
            print("\nnot stored (pass --store to keep it)")
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ad-account", type=UUID, required=True)
    p.add_argument("--store", action="store_true", help="make this the read the page serves")
    a = p.parse_args()
    asyncio.run(main(a.ad_account, a.store))
