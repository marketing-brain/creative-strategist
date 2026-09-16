"""Enqueue tagging.tag_creative for specific creatives (video included, regardless of
VIDEO_TAGGING_ENABLED, which only gates enqueueing at asset download).

Usage: uv run python scripts/enqueue_tagging.py <creative_id> [<creative_id> ...]
"""

import asyncio
import sys
from uuid import UUID

from cs.core import queue
from cs.core.config import get_settings
from cs.core.db import Database

TAGGABLE = ("image", "video", "carousel")


async def main(ids: list[UUID]) -> None:
    db = await Database.connect(get_settings().database_url)
    try:
        for cid in ids:
            row = await db.fetchrow("SELECT org_id, format FROM creative WHERE id=$1", cid)
            if row is None or row["format"] not in TAGGABLE:
                print(f"{cid}: skipped ({'missing' if row is None else row['format']})")
                continue
            jid = await queue.enqueue(
                db,
                row["org_id"],
                "tagging.tag_creative",
                {"creative_id": str(cid)},
                dedup_key=f"tag:{cid}",
            )
            print(f"{cid} ({row['format']}): {'queued ' + str(jid) if jid else 'already queued'}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main([UUID(a) for a in sys.argv[1:]]))
