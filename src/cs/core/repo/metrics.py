from __future__ import annotations

from uuid import UUID

from cs.connectors.base import AudienceRow, MetricRow
from cs.core.db import Database
from cs.core.repo.partitions import ensure_metric_partition

METRIC_CHUNK_SIZE = 1000

# `MetricRow.raw` is not stored: the source row stays in the connector's own schema, and a
# copy on every daily row only widened what each aggregate had to read past.
_UPSERT_METRIC_SQL = """
    INSERT INTO metric_daily (org_id, ad_account_id, creative_id, ad_id, day, placement,
        spend, impressions, clicks, link_clicks, conversions, conversion_value,
        video_3s_views, video_thruplay, video_p50, video_p100,
        reach, video_plays, video_p25, video_p75, video_p95, video_watch_seconds,
        landing_page_views, content_views, add_to_carts, checkouts, purchases,
        post_reactions, comments, saves,
        purchases_click_7d, purchases_view_1d, purchase_value_click_7d, purchase_value_view_1d)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
            $17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34)
    ON CONFLICT (creative_id, ad_id, day, placement) DO UPDATE SET
        spend=EXCLUDED.spend, impressions=EXCLUDED.impressions, clicks=EXCLUDED.clicks,
        link_clicks=EXCLUDED.link_clicks, conversions=EXCLUDED.conversions,
        conversion_value=EXCLUDED.conversion_value,
        video_3s_views=EXCLUDED.video_3s_views, video_thruplay=EXCLUDED.video_thruplay,
        video_p50=EXCLUDED.video_p50, video_p100=EXCLUDED.video_p100,
        reach=EXCLUDED.reach, video_plays=EXCLUDED.video_plays, video_p25=EXCLUDED.video_p25,
        video_p75=EXCLUDED.video_p75, video_p95=EXCLUDED.video_p95,
        video_watch_seconds=EXCLUDED.video_watch_seconds,
        landing_page_views=EXCLUDED.landing_page_views, content_views=EXCLUDED.content_views,
        add_to_carts=EXCLUDED.add_to_carts, checkouts=EXCLUDED.checkouts,
        purchases=EXCLUDED.purchases, post_reactions=EXCLUDED.post_reactions,
        comments=EXCLUDED.comments, saves=EXCLUDED.saves,
        purchases_click_7d=EXCLUDED.purchases_click_7d,
        purchases_view_1d=EXCLUDED.purchases_view_1d,
        purchase_value_click_7d=EXCLUDED.purchase_value_click_7d,
        purchase_value_view_1d=EXCLUDED.purchase_value_view_1d
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
                r.reach,
                r.video_plays,
                r.video_p25,
                r.video_p75,
                r.video_p95,
                r.video_watch_seconds,
                r.landing_page_views,
                r.content_views,
                r.add_to_carts,
                r.checkouts,
                r.purchases,
                r.post_reactions,
                r.comments,
                r.saves,
                r.purchases_click_7d,
                r.purchases_view_1d,
                r.purchase_value_click_7d,
                r.purchase_value_view_1d,
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


_UPSERT_AUDIENCE_SQL = """
    INSERT INTO metric_audience (org_id, ad_account_id, creative_id, ad_id, month, age, gender,
        impressions, reach, spend, link_clicks)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
    ON CONFLICT (creative_id, ad_id, month, age, gender) DO UPDATE SET
        impressions=EXCLUDED.impressions, reach=EXCLUDED.reach, spend=EXCLUDED.spend,
        link_clicks=EXCLUDED.link_clicks
"""


async def upsert_audience(
    db: Database, org_id: UUID, ad_account_id: UUID, rows: list[AudienceRow]
) -> tuple[int, int]:
    return await _upsert_breakdown(
        db,
        org_id,
        ad_account_id,
        rows,
        _UPSERT_AUDIENCE_SQL,
        lambda r: (r.month, r.age, r.gender, r.impressions, r.reach, r.spend, r.link_clicks),
    )


async def _upsert_breakdown(db, org_id, ad_account_id, rows, sql, values) -> tuple[int, int]:
    if not rows:
        return 0, 0
    ids = await db.fetch(
        "SELECT platform_ad_id, id, creative_id FROM ad WHERE org_id=$1 AND ad_account_id=$2",
        org_id,
        ad_account_id,
    )
    by_platform_id = {r["platform_ad_id"]: (r["id"], r["creative_id"]) for r in ids}
    written = skipped = 0
    for start in range(0, len(rows), METRIC_CHUNK_SIZE):
        chunk = rows[start : start + METRIC_CHUNK_SIZE]
        args = []
        for row in chunk:
            known = by_platform_id.get(row.platform_ad_id)
            if known is None:
                skipped += 1
                continue
            ad_id, creative_id = known
            args.append((org_id, ad_account_id, creative_id, ad_id, *values(row)))
        if args:
            await db.executemany(sql, args)
            written += len(args)
    return written, skipped
