"""Experiment 5: the cluster read from blind profiles (cs.experiments.profile_read).
Measures each video (free, cached), builds the feature table and counts, then one paid call.
Stored as experiment_result `profile_read:<cluster>`.

Usage:
    uv run python scripts/write_profile_read.py --org <uuid> --cluster "Talking head · Late reveal (>10s)" --dry-run
    uv run python scripts/write_profile_read.py --org <uuid> --cluster "..."
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

import openai

from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments import cluster_why as cw
from cs.experiments import profile_read as pr

SLUG = "tag-clusters"


async def run(org: UUID, cluster: str, dry: bool) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    blobs = blob_store_from_settings(s)
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
    b = await cw.build(db, org, exp, cluster, open_=True)
    profiles, rows, missing = {}, {}, []
    for v in b.videos:
        p = await db.fetchval("SELECT payload FROM experiment_result WHERE experiment_id=$1 AND key=$2", exp, f"profile:{v.id}")
        if not p:
            missing.append(v.code)
        profiles[v.code] = p
        rows[v.code] = pr.features(p, await pr.measure(db, blobs, exp, v))
    table = pr.counts(b.videos, rows, b.twin_groups)
    text = pr.brief(b, profiles, rows, table)
    print(f"{cluster}: {len(b.videos)} videos, profiles missing {missing or 'none'}, {len(table)} feature values, "
          f"brief {len(text):,} chars")
    print("twins:", [[v.code for v in g] for g in b.twin_groups])
    for t in table:
        print(f"  {t['feature']}={t['value']}: {len(t['videos_with'])}v/{t['scripts_with']}s with vs {len(t['videos_without'])}v/{t['scripts_without']}s without; "
              f"median ${t['median_cpa_with']} vs ${t['median_cpa_without']}; pooled ${t['cpa_with']} vs ${t['cpa_without']}")
    if dry:
        await db.close()
        return
    client = openai.AsyncOpenAI(api_key=s.ai_gateway_api_key, base_url=f"{s.ai_gateway_base_url}/v1", max_retries=0, timeout=900)
    out, usage = await pr.explain(db, client, s.account_read_model, org, exp, cluster, text)
    findings = pr.check(out, table, {v.code for v in b.videos})
    payload = {"version": pr.VERSION, "model": s.account_read_model, "usage": usage, "rows": rows, "table": table,
               "videos": {v.code: {"id": str(v.id), "name": v.name, "side": v.side,
                                   "cpa": v.sums["spend"] / v.sums["conversions"] if v.sums["conversions"] else None,
                                   "spend": v.sums["spend"]} for v in b.videos},
               "answer": {**out, "findings": findings}}
    await db.execute(
        "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
        " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()", exp, f"profile_read:{cluster}", payload)
    print(f"\ntokens {usage}\n\nREAD:\n{out['read']}\n")
    for f in findings:
        print(f"- [{f['strength']}] {f['claim']}  ({f['feature']}={f['value']}, favours {f['favours']})")
        if f["counts"]:
            c = f["counts"]
            print(f"    counts: {len(c['videos_with'])} videos/{c['scripts_with']} scripts with {c['videos_with']}; "
                  f"median ${c['median_cpa_with']} vs ${c['median_cpa_without']}; pooled ${c['cpa_with']} vs ${c['cpa_without']}")
        for e in f["evidence"]:
            print(f"    {e['video']} {e['at']}: {e['what']}")
        if f["flags"]:
            print(f"    FLAGS: {f['flags']}")
    print("\nUNEXPLAINED:")
    for u in out["unexplained"]:
        print(f"  {u['video']}: {u['why']}")
    print("\nCAVEATS:")
    for c in out["caveats"]:
        print(f"  - {c}")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--cluster", required=True)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.org, a.cluster, a.dry_run))


if __name__ == "__main__":
    main()
