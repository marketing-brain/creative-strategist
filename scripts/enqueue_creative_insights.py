"""Enqueue insights.creative_insight for specific creatives, highest priority first.

The job itself re-checks the per-format cap (CREATIVE_INSIGHT_LIMIT_PER_FORMAT), so
raise it on the worker before running this or the extra jobs finish with nothing.

Usage: uv run python scripts/enqueue_creative_insights.py <creative_id> [<creative_id> ...]
"""

import asyncio
import sys
from uuid import UUID

from cs.core import queue
from cs.core.config import get_settings
from cs.core.db import Database
from cs.insights.jobs import (
    CREATIVE_INSIGHT_JOB,
    CREATIVE_INSIGHT_MAX_ATTEMPTS,
    creative_insight_dedup_key,
)


async def main(ids: list[UUID]) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        for cid in ids:
            row = await db.fetchrow("SELECT org_id, format FROM creative WHERE id=$1", cid)
            if row is None:
                print(f"{cid}: no such creative")
                continue
            jid = await queue.enqueue(
                db,
                row["org_id"],
                CREATIVE_INSIGHT_JOB,
                {"creative_id": str(cid)},
                dedup_key=creative_insight_dedup_key(cid),
                max_attempts=CREATIVE_INSIGHT_MAX_ATTEMPTS,
            )
            print(f"{cid} ({row['format']}): {'queued ' + str(jid) if jid else 'already queued'}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main([UUID(a) for a in sys.argv[1:]]))
