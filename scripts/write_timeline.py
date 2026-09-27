"""Time-coded logs for videos (cs.experiments.timeline). One paid call per video, stored as
experiment_result key `timeline:<footage_id>` on the tag-clusters experiment.

Usage:
    uv run python scripts/write_timeline.py --org <uuid> --footage <id> [<id> ...] --dry-run   # free: prints the prompt
    uv run python scripts/write_timeline.py --org <uuid> --footage <id> [<id> ...]             # paid
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

import openai

from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments import timeline as tl

SLUG = "tag-clusters"


async def run(org: UUID, ids: list[UUID], dry: bool) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    blobs = blob_store_from_settings(s)
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
    client = openai.AsyncOpenAI(api_key=s.ai_gateway_api_key, base_url=f"{s.ai_gateway_base_url}/v1",
                                max_retries=0, timeout=900)
    model = s.account_read_model
    for fid in ids:
        f = await db.fetchrow(
            "SELECT f.duration_s, a.path FROM footage f JOIN creative c ON c.id = f.rep_creative_id"
            " JOIN asset a ON a.id = c.primary_asset_id WHERE f.id=$1 AND f.org_id=$2",
            fid, org,
        )
        dur = float(f["duration_s"])
        print(f"{fid}: {dur:g}s, {f['path']}")
        if dry:
            print(tl.SYSTEM + "\n\n[video]\n" + tl.user_text(dur))
            continue
        video = await blobs.get(f["path"])
        log, usage = await tl.call(db, client, model, org, fid, video, dur)
        problems = await tl.check(db, fid, log, dur)
        payload = {"version": tl.VERSION, "model": model, "usage": usage, "duration_s": dur,
                   "log": log, "derived": tl.derived(log, dur), "problems": problems}
        await db.execute(
            "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
            " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()",
            exp, f"timeline:{fid}", payload,
        )
        print(f"  tokens {usage}; {sum(len(log[k]) for k in ('shots', 'speech', 'on_screen_text', 'audio', 'product'))} entries;"
              f" problems: {problems or 'none'}")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--footage", type=UUID, nargs="+", required=True)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.org, a.footage, a.dry_run))


if __name__ == "__main__":
    main()
