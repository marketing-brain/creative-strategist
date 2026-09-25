"""Experiment 5: an Insights-style read inside each cluster (cs.experiments.cluster_insights).
Clusters with more suggestive results than chance get a paid write-up by the Insights tab's own
writer and gates; the rest are stored as "nothing beyond chance yet" with no model call.

Usage:
    uv run python scripts/write_cluster_reads.py --org <uuid> --dry-run   # free: which clusters qualify
    uv run python scripts/write_cluster_reads.py --org <uuid>             # paid for qualifying clusters only
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import date
from uuid import UUID

from cs.core import model_calls
from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments import cluster_insights as ci
from cs.experiments import tag_clusters as tc
from cs.insights.account_read.page import build_page
from cs.insights.account_read.patterns import run as run_patterns
from cs.insights.account_read.reliability import measure
from cs.insights.account_read.types import to_json, unit_from_json
from cs.insights.account_read.writer import GeminiGatewayAccountReadWriter, write_read

SLUG = "tag-clusters"
PURPOSE = "experiments.cluster_read"


class _Ledger:
    def __init__(self, db, org, key):
        self.db, self.org, self.key = db, org, f"{key}:{date.today().isoformat()}"

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
    src = await db.fetchval(
        "SELECT id FROM account_read_run WHERE org_id=$1 AND format='video' AND status='done' ORDER BY created_at DESC LIMIT 1",
        org,
    )
    res = await db.fetchval("SELECT result FROM account_read_run WHERE id=$1", src)
    units = [unit_from_json(u) for u in res["units"]]
    edges = {k: tuple(v) for k, v in (res.get("bin_edges") or {}).items()}
    labels: dict[str, dict[str, str]] = {}
    for r in await db.fetch(
        "SELECT f.sha256, l.axis, l.value FROM experiment_label l JOIN footage f ON f.id = l.footage_id WHERE l.experiment_id = $1",
        exp,
    ):
        labels.setdefault(r["sha256"], {})[r["axis"]] = r["value"]
    # "Other" is a mixed bag, not a kind of ad: no read of its own.
    cluster_of = tc.cluster_map(labels)
    by: dict[str, list] = defaultdict(list)
    for u in units:
        if cluster_of.get(u.sha256, tc.OTHER) != tc.OTHER:
            by[cluster_of[u.sha256]].append(u)
    reliability = await measure(db, org)
    for cluster, us in sorted(by.items(), key=lambda kv: -len(kv[1])):
        key = f"cluster_read:{cluster}"
        if len(us) < ci.MIN_VIDEOS:
            payload = {"status": "insufficient", "n_units": len(us), "source_run": str(src)}
            print(f"{cluster}: {len(us)} videos, too few")
        else:
            result = run_patterns(us, edges, grouping_dims=[], draws=settings.account_read_permutation_draws,
                                  reliability=reliability, with_interactions=False)
            stats = ci.beyond_chance(result.tests)
            ideas = ci.loose_ideas(result.tests)
            print(f"{cluster}: {len(us)} videos, {stats['suggestive']} of {stats['clean']} clean suggestive "
                  f"(chance {stats['chance']}, p={stats['p']:.4f}) -> {'WRITE' if stats['beyond'] and ideas else 'chance'}, {len(ideas)} ideas")
            if dry:
                continue
            if not (stats["beyond"] and ideas):
                payload = {"status": "chance", "n_units": len(us), "stats": stats, "source_run": str(src)}
            else:
                read, problems, ti, to = await write_read(
                    result, GeminiGatewayAccountReadWriter(settings), _Ledger(db, org, f"{exp}:{cluster}"),
                    brief=ci.brief(result, cluster, ideas), system=ci.system(cluster, stats),
                )
                rj = to_json(read) if read else None
                payload = {
                    "status": "done" if read else "rejected", "n_units": len(us), "stats": stats, "source_run": str(src),
                    "read": rj, "page": build_page(to_json(result), rj), "error": "; ".join(problems) or None, "tokens": [ti, to],
                }
                print(f"   {payload['status']}: {len(read.findings) if read else 0} findings, tokens {ti:,}/{to:,}"
                      + (f" | {payload['error'][:300]}" if not read else ""))
        if dry:
            continue
        await db.execute(
            "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
            " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()",
            exp, key, payload,
        )
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.org, a.dry_run))


if __name__ == "__main__":
    main()
