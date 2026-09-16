"""Tag a sample of video creatives twice, in memory, and print per-dimension agreement.
Writes nothing: no tag_run, no tag rows, no vocabulary changes.

Usage: uv run python scripts/tag_consistency.py --org <uuid> [--n 12] [--seed 1]
       [--concurrency 4] [--ids <uuid> ...]   (--ids replaces the sampled set)
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
from cs.tagging.consistency import agreement, diff_lines, summarize
from cs.tagging.runner import profile_video
from cs.tagging.tagger import GeminiGatewayTagger
from cs.tagging.taxonomy import load_taxonomy


async def main(org_id: UUID, n: int, seed: int, concurrency: int, only_ids: list[UUID]) -> None:
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
        ids = only_ids or [r["id"] for r in rows]
        if not only_ids:
            random.Random(seed).shuffle(ids)
        taxonomy = load_taxonomy()
        tagger = GeminiGatewayTagger(settings)
        hints = {
            d.name: await vocab.hints(db, org_id, d.name, d.vocab_hint)
            for d in taxonomy.open_dimensions("video")
            if d.vocab_hint
        }
        targets = ids if only_ids else ids[:n]
        sem = asyncio.Semaphore(concurrency)

        async def one(cid):
            # One unreadable asset or one model error must not lose the whole sample:
            # the table is built from whichever creatives got through.
            async with sem:
                try:
                    creative = await ingest_repo.get_creative(db, org_id, cid)
                    a, *_ = await profile_video(ctx, creative, taxonomy, tagger, hints)
                    b, *_ = await profile_video(ctx, creative, taxonomy, tagger, hints)
                except Exception as exc:
                    print(f"{cid}: FAILED {exc}", flush=True)
                    return None
            s = agreement(a, b, taxonomy, "video")
            low = [k for k, v in s.items() if v is not None and v < 1.0]
            print(f"{cid}: disagreements on {', '.join(low) or 'none'}", flush=True)
            for line in diff_lines(a, b, taxonomy, "video"):
                print(f"    {line}", flush=True)
            return s

        results = await asyncio.gather(*(one(cid) for cid in targets))
        scores = [s for s in results if s is not None]
        print(flush=True)
        print(f"{'dimension':<22} {'kind':<8} agreement  (n={len(scores)})", flush=True)
        summary = summarize(scores)
        for d in taxonomy.for_format("video"):
            v = summary.get(d.name)
            below_bar = v is not None and (
                (d.kind in ("single", "numeric", "ordered") and v < 0.8)
                or (d.kind in ("multi", "open") and v < 0.6)
            )
            flag = "  <-- below bar" if below_bar else ""
            print(f"{d.name:<22} {d.kind:<8} {'-' if v is None else f'{v:.2f}'}{flag}", flush=True)
            if d.kind == "ordered":  # same steps, ignoring their order (spec section 9)
                sv = summary.get(f"{d.name}:set")
                s_flag = "  <-- below bar" if sv is not None and sv < 0.6 else ""
                name = f"{d.name}:set"
                shown = "-" if sv is None else f"{sv:.2f}"
                print(f"{name:<22} {'ordered:set':<8} {shown}{s_flag}", flush=True)
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True, type=UUID)
    p.add_argument("--n", type=int, default=12)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--ids", type=UUID, nargs="*", default=[])
    args = p.parse_args()
    asyncio.run(main(args.org, args.n, args.seed, args.concurrency, args.ids))
