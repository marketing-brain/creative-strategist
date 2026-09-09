from __future__ import annotations

from uuid import UUID

from cs.connectors.base import MetricRow
from cs.core.db import Database
from cs.core.repo.partitions import ensure_metric_partition

METRIC_CHUNK_SIZE = 1000

_UPSERT_METRIC_SQL = """
    INSERT INTO metric_daily (org_id, ad_account_id, creative_id, ad_id, day, placement,
        spend, impressions, clicks, link_clicks, conversions, conversion_value,
        video_3s_views, video_thruplay, video_p50, video_p100, raw)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
    ON CONFLICT (creative_id, ad_id, day, placement) DO UPDATE SET
        spend=EXCLUDED.spend, impressions=EXCLUDED.impressions, clicks=EXCLUDED.clicks,
        link_clicks=EXCLUDED.link_clicks, conversions=EXCLUDED.conversions,
        conversion_value=EXCLUDED.conversion_value,
        video_3s_views=EXCLUDED.video_3s_views, video_thruplay=EXCLUDED.video_thruplay,
        video_p50=EXCLUDED.video_p50, video_p100=EXCLUDED.video_p100, raw=EXCLUDED.raw
"""


async def upsert_metrics(
    db: Database, org_id: UUID, ad_account_id: UUID, rows: list[MetricRow]
) -> tuple[int, int]:
    """Upsert metric rows, returning (written, skipped).

    Rows whose ad is unknown to us are skipped: Meta insights can name ads that the ad
    listing did not return (deleted or out of the page window).
    """
    if not rows:
        return 0, 0
    ads = await db.fetch(
        "SELECT id, platform_ad_id, creative_id FROM ad WHERE org_id=$1 AND ad_account_id=$2",
        org_id,
        ad_account_id,
    )
    by_pid = {a["platform_ad_id"]: a for a in ads}
    params: list[tuple] = []
    skipped = 0
    for r in rows:
        a = by_pid.get(r.platform_ad_id)
        if not a:
            skipped += 1
            continue
        params.append(
            (
                org_id,
                ad_account_id,
                a["creative_id"],
                a["id"],
                r.day,
                r.placement,
                r.spend,
                r.impressions,
                r.clicks,
                r.link_clicks,
                r.conversions,
                r.conversion_value,
                r.video_3s_views,
                r.video_thruplay,
                r.video_p50,
                r.video_p100,
                r.raw,
            )
        )
    if not params:
        return 0, skipped
    for month in {t[4].replace(day=1) for t in params}:
        await ensure_metric_partition(db, month)
    for i in range(0, len(params), METRIC_CHUNK_SIZE):
        async with db.transaction() as tx:
            await tx.executemany(_UPSERT_METRIC_SQL, params[i : i + METRIC_CHUNK_SIZE])
    return len(params), skipped
