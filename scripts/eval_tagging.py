"""Tag hand-labeled creatives with the real model and report agreement per dimension.

Usage: AI_GATEWAY_API_KEY=... uv run python scripts/eval_tagging.py --min-agreement 0.7
Requires DATABASE_URL to point at a database containing the labeled creatives and their blobs.
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from uuid import UUID

import httpx

from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.queue import Job
from cs.jobs.registry import JobContext
from cs.tagging import repo, runner
from cs.tagging.tagger import GeminiGatewayTagger
from cs.tagging.video import GatewayTranscriber

LABELS = Path("tests/evals/labeled.jsonl")


async def main(min_agreement: float) -> int:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    blobs = blob_store_from_settings(settings)
    rows = [json.loads(line) for line in LABELS.read_text().splitlines() if line.strip()]
    if not rows:
        print("no labeled rows")
        return 0
    hits, totals = defaultdict(int), defaultdict(int)
    async with httpx.AsyncClient() as http:
        tagger, tr = GeminiGatewayTagger(settings), GatewayTranscriber(settings, http)
        for r in rows:
            cid = UUID(r["creative_id"])
            org_id = await db.fetchval("SELECT org_id FROM creative WHERE id=$1", cid)
            await db.execute("DELETE FROM tag WHERE creative_id=$1", cid)
            await db.execute("DELETE FROM tag_run WHERE creative_id=$1", cid)
            ctx = JobContext(db, blobs, settings, Job(UUID(int=0), org_id, "eval", {}, 1, 1))
            await runner.tag_creative(ctx, cid, tagger, tr)
            got = {t["dimension"]: t["value"] for t in await repo.latest_tags(db, org_id, cid)}
            for dim, want in r["labels"].items():
                totals[dim] += 1
                hits[dim] += int(got.get(dim) == want)
    overall = sum(hits.values()) / max(1, sum(totals.values()))
    for dim in sorted(totals):
        print(f"{dim:28s} {hits[dim]}/{totals[dim]}  {hits[dim] / totals[dim]:.2f}")
    print(f"\noverall agreement: {overall:.3f} (min {min_agreement})")
    await db.close()
    return 0 if overall >= min_agreement else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-agreement", type=float, default=0.7)
    sys.exit(asyncio.run(main(ap.parse_args().min_agreement)))
