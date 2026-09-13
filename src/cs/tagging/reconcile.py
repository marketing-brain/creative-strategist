from __future__ import annotations

from cs.core import queue
from cs.core.db import Database


async def reenqueue_untagged(db: Database) -> int:
    """Re-enqueue tagging for every creative with a primary asset and no
    completed tag_run. Idempotent: enqueue() no-ops against a job that is
    already pending/running under the same dedup key, and inserts a fresh
    pending row for one that has gone 'dead'."""
    rows = await db.fetch(
        """
        SELECT c.id, c.org_id FROM creative c
        WHERE c.primary_asset_id IS NOT NULL AND c.format IN ('image', 'video', 'carousel')
          AND NOT EXISTS (SELECT 1 FROM tag_run r WHERE r.creative_id = c.id AND r.status = 'done')
        """
    )
    n = 0
    for r in rows:
        jid = await queue.enqueue(
            db,
            r["org_id"],
            "tagging.tag_creative",
            {"creative_id": str(r["id"])},
            dedup_key=f"tag:{r['id']}",
        )
        n += int(jid is not None)
    return n
