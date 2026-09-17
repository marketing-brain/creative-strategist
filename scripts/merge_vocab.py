"""Propose (and with --apply, apply) open-vocabulary merges for one org.

Dry run by default: prints the apply list, the review list and the rejected pairs with
the judge's reasons, and writes nothing. --apply applies the apply list, records the
review list for scripts/review_vocab_merges.py, and records rejections so they are never
proposed again.

Usage: uv run python scripts/merge_vocab.py --org <uuid> [--dimension <name>] [--apply]
       [--floor 0.70] [--apply-floor 0.80]
"""

import argparse
import asyncio
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.embeddings import GatewayEmbedder
from cs.tagging import vocab_merge
from cs.tagging.taxonomy import load_taxonomy


async def main(
    org_id: UUID, dimension: str | None, apply: bool, floor: float, apply_floor: float
) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        embedder = GatewayEmbedder(settings)
        judge = vocab_merge.GatewayJudge(settings)
        taxonomy = load_taxonomy()
        dims = [d for d in taxonomy.open_dimensions("video") if dimension in (None, d.name)]
        for d in dims:
            n = await vocab_merge.embed_missing(
                db, embedder, org_id, d.name, settings.embedding_model
            )
            pairs = await vocab_merge.candidate_pairs(
                db, org_id, d.name, settings.embedding_model, floor
            )
            print(
                f"\n== {d.name}: embedded {n} new slug(s), {len(pairs)} candidate pair(s)",
                flush=True,
            )
            if not pairs:
                continue
            slugs = sorted({s for p in pairs for s in (p.a, p.b)})
            context = await vocab_merge.slug_context(db, org_id, d.name, slugs)
            verdicts = await judge.judge(d.name, d.description, pairs, context)
            counts = {s: c.count for s, c in context.items()}
            plan = vocab_merge.plan_merges(verdicts, counts, apply_floor)
            for m in plan.apply:
                print(
                    f"  APPLY   {m.slug} -> {m.into_slug}  ({m.similarity:.2f}) {m.reason}",
                    flush=True,
                )
            for m in plan.review:
                print(
                    f"  REVIEW  {m.slug} -> {m.into_slug}  ({m.similarity:.2f}) {m.reason}",
                    flush=True,
                )
            for v in plan.rejected:
                print(
                    f"  reject  {v.pair.a} | {v.pair.b}  ({v.pair.cosine:.2f}) {v.reason}",
                    flush=True,
                )
            if apply:
                await vocab_merge.apply_merges(db, org_id, d.name, plan.apply, source="auto")
                await vocab_merge.record_review(db, org_id, d.name, plan.review)
                await vocab_merge.record_rejections(db, org_id, d.name, plan.rejected)
                print(
                    f"  applied {len(plan.apply)}, queued {len(plan.review)} for review, "
                    f"recorded {len(plan.rejected)} rejections",
                    flush=True,
                )
        if not apply:
            print(
                "\nDry run: nothing written. Re-run with --apply to apply the APPLY lines "
                "and queue REVIEW lines.",
                flush=True,
            )
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True, type=UUID)
    p.add_argument("--dimension")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--floor", type=float, default=0.70)
    p.add_argument("--apply-floor", type=float, default=0.80)
    a = p.parse_args()
    asyncio.run(main(a.org, a.dimension, a.apply, a.floor, a.apply_floor))
