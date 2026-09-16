"""Tag a sample of video creatives twice, in memory, and print per-dimension agreement.
Writes nothing: no tag_run, no tag rows, no vocabulary changes.

Usage: uv run python scripts/tag_consistency.py --org <uuid> [--n 12] [--seed 1]
Needs AI_GATEWAY_API_KEY and DATABASE_URL in the environment (or .env).
"""

import argparse
import asyncio
import random
from uuid import UUID

from cs.core import repo as ingest_repo
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.queue import Job
from cs.jobs.registry import JobContext
from cs.tagging import vocab
from cs.tagging.consistency import agreement, summarize
from cs.tagging.runner import profile_video
from cs.tagging.tagger import GeminiGatewayTagger
from cs.tagging.taxonomy import load_taxonomy


async def main(org_id: UUID, n: int, seed: int) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        ctx = JobContext(
            db=db,
            blobs=blob_store_from_settings(settings),
            settings=settings,
            job=Job(UUID(int=0), org_id, "tagging.consistency", {}, 1, 1),
        )
        rows = await db.fetch(
            "SELECT c.id FROM creative c WHERE c.org_id=$1 AND c.format='video' "
            "AND c.primary_asset_id IS NOT NULL ORDER BY c.id",
            org_id,
        )
        ids = [r["id"] for r in rows]
        random.Random(seed).shuffle(ids)
        taxonomy = load_taxonomy()
        tagger = GeminiGatewayTagger(settings)
        hints = {
            d.name: await vocab.hints(db, org_id, d.name, d.vocab_hint)
            for d in taxonomy.open_dimensions("video")
            if d.vocab_hint
        }
        scores = []
        for cid in ids[:n]:
            creative = await ingest_repo.get_creative(db, org_id, cid)
            a, *_ = await profile_video(ctx, creative, taxonomy, tagger, hints)
            b, *_ = await profile_video(ctx, creative, taxonomy, tagger, hints)
            s = agreement(a, b, taxonomy, "video")
            scores.append(s)
            low = [k for k, v in s.items() if v is not None and v < 1.0]
            print(f"{cid}: disagreements on {', '.join(low) or 'none'}")
        print()
        print(f"{'dimension':<22} {'kind':<8} agreement  (n={len(scores)})")
        summary = summarize(scores)
        for d in taxonomy.for_format("video"):
            v = summary.get(d.name)
            below_bar = v is not None and (
                (d.kind in ("single", "numeric", "ordered") and v < 0.8)
                or (d.kind in ("multi", "open") and v < 0.6)
            )
            flag = "  <-- below bar" if below_bar else ""
            print(f"{d.name:<22} {d.kind:<8} {'-' if v is None else f'{v:.2f}'}{flag}")
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True, type=UUID)
    p.add_argument("--n", type=int, default=12)
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()
    asyncio.run(main(args.org, args.n, args.seed))
