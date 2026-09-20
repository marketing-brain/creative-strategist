from __future__ import annotations

from cs.core.db import Database

# Thirty-day totals per creative and per ad, summed once instead of on every grid request
# (db/migrations/20260920000002). Stale by design between refreshes: a sync refreshes them
# when it lands, and an hourly schedule keeps the window sliding when nothing syncs.
ROLLUPS = ("creative_metric_30d", "ad_metric_30d")


async def refresh(db: Database) -> None:
    for name in ROLLUPS:
        # CONCURRENTLY: readers keep the old rows until the new ones are ready.
        await db.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {name}")
