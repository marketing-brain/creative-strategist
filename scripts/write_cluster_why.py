"""Experiment 5: why the best videos in a cluster beat the worst (cs.experiments.cluster_why).
One paid call per cluster, stored as experiment_result key `cluster_why:<cluster>`.

Usage:
    uv run python scripts/write_cluster_why.py --org <uuid> --cluster "Talking head · Late reveal (>10s)" --dry-run
    uv run python scripts/write_cluster_why.py --org <uuid> --cluster "..."      # paid, one call
    uv run python scripts/write_cluster_why.py --org <uuid> --list               # clusters and sizes, free
    uv run python scripts/write_cluster_why.py --org <uuid> --cluster "..." --recheck   # re-apply checks, free
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

import openai

from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments import cluster_why as cw

SLUG = "tag-clusters"


async def run(org: UUID, cluster: str | None, dry: bool, show: bool, recheck: bool = False, open_: bool = False) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
    if cluster is None:
        span, clusters = await cw.cluster_members(db, org, exp)
        print(f"window {span[0]} to {span[1]}")
        for c, vs in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
            print(f"{len(vs):3}  {sum(v.sums['link_clicks'] >= cw.MIN_CLICKS for v in vs):3} rankable  {c}")
        await db.close()
        return
    brief = await cw.build(db, org, exp, cluster, open_=open_)
    sides = {k: sum(v.side == k for v in brief.videos) for k in ("best", "worst", "middle", "unrankable")}
    print(f"{cluster}: {len(brief.videos)} videos {sides}, twins {[[v.code for v in g] for g in brief.twin_groups]}")
    print(f"brief {len(brief.text):,} chars (~{len(brief.text) // 4:,} tokens)")
    if show or dry:
        print("\n" + brief.text)
    if dry:
        await db.close()
        return
    if recheck:
        row = await db.fetchval(
            "SELECT payload FROM experiment_result WHERE experiment_id=$1 AND key=$2", exp, f"cluster_why:{cluster}"
        )
        kept, dropped = cw.check(brief, row["raw"])
        payload = cw.payload(brief, row["raw"], kept, dropped, row["usage"], row["model"])
        await db.execute(
            "UPDATE experiment_result SET payload=$3, created_at=now() WHERE experiment_id=$1 AND key=$2",
            exp, f"cluster_why:{cluster}", payload,
        )
        print(f"rechecked, no call: kept {len(kept['videos'])} videos, {len(kept['patterns'])} patterns, "
              f"{len(kept['twins'])} twins; dropped {dropped}")
        await db.close()
        return
    model = s.account_read_model
    client = openai.AsyncOpenAI(api_key=s.ai_gateway_api_key, base_url=f"{s.ai_gateway_base_url}/v1",
                                max_retries=0, timeout=s.account_read_timeout_s)
    if open_:
        raw, usage = await cw.explain_open(db, client, model, org, exp, brief)
        flags = cw.flag_open(brief, raw)
        codes = {v.code: {"id": str(v.id), "name": v.name, "cpa": v.sums["spend"] / v.sums["conversions"] if v.sums["conversions"] else None,
                          "spend": v.sums["spend"], "side_by_cpa": v.side} for v in brief.videos}
        await db.execute(
            "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
            " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()",
            exp, f"cluster_why_open:{cluster}",
            {"model": model, "usage": usage, "span": [brief.span[0].isoformat(), brief.span[1].isoformat()],
             "videos": codes, "twins": [[v.code for v in g] for g in brief.twin_groups], "answer": raw, "flags": flags},
        )
        print(f"tokens {usage}; {len(flags)} flags")
        await db.close()
        return
    raw, usage = await cw.explain(db, client, model, org, exp, brief)
    kept, dropped = cw.check(brief, raw)
    payload = cw.payload(brief, raw, kept, dropped, usage, model)
    await db.execute(
        "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
        " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()",
        exp, f"cluster_why:{cluster}", payload,
    )
    print(f"\ntokens {usage}; kept {len(kept['videos'])} videos, {len(kept['patterns'])} patterns, "
          f"{len(kept['twins'])} twins; dropped {len(dropped)}")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--cluster")
    p.add_argument("--list", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--show", action="store_true")
    p.add_argument("--recheck", action="store_true", help="re-apply the checks to the stored answer; no call")
    p.add_argument("--open", action="store_true", help="one open comparison: every video, the model judges, code only flags")
    a = p.parse_args()
    asyncio.run(run(a.org, None if a.list else a.cluster, a.dry_run, a.show, a.recheck, a.open))


if __name__ == "__main__":
    main()
