from __future__ import annotations

from cs.core.db import Database

# Thirty-day totals per creative and per ad, summed once instead of on every grid request.
# The window ends on each account's newest day with delivery (migration 20260922000001), so
# the totals only move when metric_daily does, and the sync that moves it refreshes them.
ROLLUPS = ("creative_metric_30d", "ad_metric_30d")


async def refresh(db: Database) -> None:
    for name in ROLLUPS:
        # CONCURRENTLY: readers keep the old rows until the new ones are ready.
        await db.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {name}")
