from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from cs.core.db import Database

# Thirty-day totals per creative and per ad, summed once instead of on every grid request.
# The window ends on each account's newest day with delivery (migration 20260922000001), so
# the totals only move when metric_daily does, and the sync that moves it refreshes them.
ROLLUPS = ("creative_metric_30d", "ad_metric_30d")


async def refresh(db: Database) -> None:
    for name in ROLLUPS:
        # CONCURRENTLY: readers keep the old rows until the new ones are ready.
        await db.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {name}")


async def rollup_window(
    db: Database, org_id: UUID, ad_account_id: UUID | None = None
) -> tuple[date, date] | None:
    """The days the rollups cover: the thirty ending on the newest day with delivery, per
    account (the same `last` the views use). Across several accounts, the widest span."""
    rows = await db.fetch(
        "SELECT max(day) AS day FROM metric_daily WHERE org_id = $1 AND impressions > 0"
        " AND ($2::uuid IS NULL OR ad_account_id = $2) GROUP BY ad_account_id",
        org_id,
        ad_account_id,
    )
    ends = [r["day"] for r in rows if r["day"]]
    if not ends:
        return None
    return min(ends) - timedelta(days=29), max(ends)
