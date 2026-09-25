"""Experiment 5: the Insights tab's read, redone with every comparison made within clusters
(cs.experiments.within_read), written by the Insights tab's own writer and gates, and stored
for the experiment page. The Insights tab's own run is read, never changed.

Usage:
    uv run python scripts/write_within_read.py --org <uuid> --dry-run   # free: build, print the brief size
    uv run python scripts/write_within_read.py --org <uuid>             # paid: one draft + up to 3 corrections
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter
from datetime import date
from uuid import UUID

from cs.core import model_calls
from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments import tag_clusters as tc
from cs.experiments import within_read as wr
from cs.insights.account_read.page import build_page
from cs.insights.account_read.patterns import ideas
from cs.insights.account_read.types import RunResult, test_from_json, to_json, unit_from_json
from cs.insights.account_read.writer import GeminiGatewayAccountReadWriter, write_read

SLUG = "tag-clusters"
KEY = "within_read"
PURPOSE = "experiments.within_read"
TITLE = "Write the Insights read, like with like"
DETAIL = (
    "Every comparison the Insights tab makes, redone within clusters, then written up by the "
    "Insights tab's own writer and checked by its gates. Paid: one model draft plus up to three "
    "corrections."
)


class _Ledger:
    def __init__(self, db, org, exp):
        self.db, self.org, self.key = db, org, f"{exp}:{date.today().isoformat()}"

    async def claim(self, model):
        c = await model_calls.claim(self.db, self.org, PURPOSE, self.key, model)
        db = self.db

        class _Call:
            async def done(self, ti, to):
                await model_calls.finish(db, c.id, input_tokens=ti, output_tokens=to)

            async def failed(self, error):
                await model_calls.fail(db, c.id, error)

        return _Call()


async def run(org: UUID, dry: bool) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
    src = await db.fetchrow(
        "SELECT id, created_at FROM account_read_run WHERE org_id=$1 AND format='video' AND status='done'"
        " ORDER BY created_at DESC LIMIT 1",
        org,
    )
    res = await db.fetchval("SELECT result FROM account_read_run WHERE id=$1", src["id"])
    base = RunResult(
        units=[unit_from_json(u) for u in res["units"]],
        tests=[test_from_json(t) for t in res["tests"]],
        overlaps=res.get("overlaps") or [],
        stratified=res.get("stratified") or [],
        retention_by_reveal=res.get("retention_by_reveal") or {},
        summary=res.get("summary") or {},
        bin_edges={k: tuple(v) for k, v in (res.get("bin_edges") or {}).items()},
    )
    labels: dict[str, dict[str, str]] = {}
    for r in await db.fetch(
        "SELECT f.sha256, l.axis, l.value FROM experiment_label l JOIN footage f ON f.id = l.footage_id"
        " WHERE l.experiment_id = $1",
        exp,
    ):
        labels.setdefault(r["sha256"], {})[r["axis"]] = r["value"]
    # "Other" is a mixed bag, not a kind of ad, so it is no stratum.
    cluster_of = {sha: c for sha, c in tc.cluster_map(labels).items() if c != tc.OTHER}
    t0 = time.time()
    within, extra = wr.build_run(base, cluster_of)
    brief = wr.build_brief_within(within, extra, cluster_of)
    main = [t for t in base.tests if t.group is None]
    print(f"source run {src['id']} ({src['created_at']:%Y-%m-%d}): {len(base.units)} files, {len(main)} account-wide tests")
    print(f"within: {len(within.tests)} tests, {len(extra['lost'])} not comparable, {time.time() - t0:.0f}s")
    print("grades within:", dict(Counter(t.grade for t in within.tests)), "unstable", within.summary["unstable_grades"])
    print("grades account-wide:", dict(Counter(t.grade for t in main)))
    print("ideas within:", len(ideas(within.tests)), "| ideas account-wide:", len(ideas(base.tests)))
    print(f"brief: {len(brief):,} chars (~{len(brief) // 4:,} tokens)")
    if dry:
        open("/tmp/within_brief.txt", "w").write(brief)
        print("brief written to /tmp/within_brief.txt")
        await db.close()
        return

    ordinal = await db.fetchval(
        "SELECT COALESCE(max(ordinal) + 1, 0) FROM experiment_step WHERE experiment_id=$1 AND key <> $2", exp, KEY
    )
    await db.execute(
        "INSERT INTO experiment_step (experiment_id, ordinal, key, title, detail, status, started_at)"
        " VALUES ($1,$2,$3,$4,$5,'running', now()) ON CONFLICT (experiment_id, key) DO UPDATE"
        " SET title=$4, detail=$5, status='running', started_at=now(), finished_at=NULL",
        exp, ordinal, KEY, TITLE, DETAIL,
    )
    read, problems, ti, to = await write_read(
        within, GeminiGatewayAccountReadWriter(settings), _Ledger(db, org, exp), brief=brief, system=wr.SYSTEM_WITHIN
    )
    result = to_json(within)
    payload = {
        "status": "done" if read else "rejected",
        "source_run": str(src["id"]),
        "n_units": len(within.units),
        "read": to_json(read) if read else None,
        "page": build_page(result, to_json(read) if read else None),
        "error": "; ".join(problems) or None,
        "tokens": [ti, to],
    }
    await db.execute(
        "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
        " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()",
        exp, KEY, payload,
    )
    n = len(read.findings) if read else 0
    note = f"{n} findings written and checked." if read else f"The draft failed its checks: {payload['error'][:300]}"
    await db.execute(
        "UPDATE experiment_step SET status=$3, progress_done=1, progress_total=1, finished_at=now(), result_note=$4"
        " WHERE experiment_id=$1 AND key=$2",
        exp, KEY, "done" if read else "failed", note,
    )
    print(payload["status"], note, f"tokens in {ti:,} out {to:,}")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.org, a.dry_run))


if __name__ == "__main__":
    main()
