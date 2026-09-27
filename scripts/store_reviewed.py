"""Experiment 5: store reviewed insights for a cluster from a JSON file a person wrote after
checking the numbers (scripts/analyze_cluster.py) and the videos. Creates or updates
experiment_result `profile_read:<cluster>` with the cluster's video list and `reviewed`; a model
read, when one exists, is kept as it is.

Usage:
    uv run python scripts/store_reviewed.py --org <uuid> --cluster "..." --file insights.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments import cluster_why as cw

SLUG = "tag-clusters"


async def run(org: UUID, cluster: str, path: str) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
    b = await cw.build(db, org, exp, cluster, open_=True)
    reviewed = json.load(open(path))
    codes = {v.code for v in b.videos}
    for r in reviewed:
        bad = [c for c in r["videos"] if c not in codes]
        assert not bad, f"unknown videos {bad} in {r['title']!r}"
    key = f"profile_read:{cluster}"
    payload = await db.fetchval("SELECT payload FROM experiment_result WHERE experiment_id=$1 AND key=$2", exp, key) or {}
    payload["videos"] = {v.code: {"id": str(v.id), "name": v.name, "side": v.side,
                                  "cpa": v.sums["spend"] / v.sums["conversions"] if v.sums["conversions"] else None,
                                  "spend": v.sums["spend"]} for v in b.videos}
    payload["reviewed"] = reviewed
    await db.execute(
        "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
        " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()", exp, key, payload)
    print(f"stored {len(reviewed)} insights for {cluster}")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--cluster", required=True)
    p.add_argument("--file", required=True)
    a = p.parse_args()
    asyncio.run(run(a.org, a.cluster, a.file))


if __name__ == "__main__":
    main()
