"""Footage groups: creatives of one ad account that run the same file.

Meta mints a new creative id (and a new video id) whenever an ad is copied into another
campaign or its file is uploaded again, so one video routinely appears as many creative rows.
The creative row stays the unit of ingest and of the ad/metric joins. A footage group is a
read-side layer over it: same ad account, same format, byte-identical primary file.

Only image and video creatives group. An unresolved creative has no proven format, and its
stored file is often a catalog placeholder shared by unrelated product ads. A carousel is a
sequence of files and its first card says nothing about the rest. Both stay groups of one.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from cs.core.db import Database

FOOTAGE_FORMATS = ("image", "video")

# The 30-day totals here and in the creative list use `day > current_date - 30`: thirty days
# ending today, the same window the diagnosis reads, so a page's header and its diagnosis
# never disagree by a day's delivery.

# Group key for a creative `c` joined to its primary asset `a`. Interpolated into queries;
# it contains no user text.
FOOTAGE_KEY_SQL = """
    CASE WHEN c.format IN ('image', 'video') AND a.sha256 IS NOT NULL
         THEN c.ad_account_id::text || ':' || c.format || ':' || a.sha256
         ELSE c.id::text END
"""


async def footage_member_ids(db: Database, org_id: UUID, creative_id: UUID) -> list[UUID]:
    """Every creative running the same file as this one, itself included. A creative that
    does not group (see module docstring) is a group of one."""
    rows = await db.fetch(
        """
        SELECT peer.id FROM creative c
        JOIN asset a ON a.id = c.primary_asset_id
        JOIN creative peer ON peer.org_id = c.org_id AND peer.ad_account_id = c.ad_account_id
                          AND peer.format = c.format
        JOIN asset pa ON pa.id = peer.primary_asset_id AND pa.sha256 = a.sha256
        WHERE c.org_id = $1 AND c.id = $2 AND c.format = ANY($3)
        ORDER BY peer.id
        """,
        org_id,
        creative_id,
        list(FOOTAGE_FORMATS),
    )
    return [r["id"] for r in rows] or [creative_id]


async def footage_members(
    db: Database, org_id: UUID, creative_ids: list[UUID]
) -> list[asyncpg.Record]:
    """The group's creatives with their copy and 30-day totals, highest spend first."""
    return await db.fetch(
        """
        SELECT c.id, c.platform_creative_id, c.headline, c.primary_text, c.description,
               c.cta_type, c.link_url, c.first_seen_at,
               COALESCE(m.spend, 0) AS spend, COALESCE(m.impressions, 0) AS impressions,
               COALESCE(m.clicks, 0) AS clicks, COALESCE(m.conversions, 0) AS conversions,
               COALESCE(m.conversion_value, 0) AS conversion_value,
               COALESCE(m.purchases, 0) AS purchases
        FROM creative c
        LEFT JOIN (
            SELECT creative_id, spend, impressions, clicks, conversions, conversion_value,
                   purchases
            FROM creative_metric_30d WHERE org_id = $1 AND creative_id = ANY($2)
        ) m ON m.creative_id = c.id
        WHERE c.org_id = $1 AND c.id = ANY($2)
        ORDER BY COALESCE(m.spend, 0) DESC, c.first_seen_at, c.id
        """,
        org_id,
        creative_ids,
    )


async def footage_ads(db: Database, org_id: UUID, creative_ids: list[UUID]) -> list[asyncpg.Record]:
    """Every ad running the group's file, with its own 30-day totals, highest spend first."""
    return await db.fetch(
        """
        SELECT ad.id, ad.creative_id, ad.platform_ad_id, ad.name, ad.status, ad.campaign_id,
               ad.adset_id,
               COALESCE(m.spend, 0) AS spend, COALESCE(m.impressions, 0) AS impressions,
               COALESCE(m.clicks, 0) AS clicks, COALESCE(m.conversions, 0) AS conversions,
               COALESCE(m.conversion_value, 0) AS conversion_value,
               COALESCE(m.purchases, 0) AS purchases
        FROM ad
        LEFT JOIN (
            SELECT ad_id, sum(spend) AS spend, sum(impressions) AS impressions,
                   sum(clicks) AS clicks, sum(conversions) AS conversions,
                   sum(conversion_value) AS conversion_value, sum(purchases) AS purchases
            FROM ad_metric_30d
            WHERE org_id = $1 AND creative_id = ANY($2)
            GROUP BY ad_id
        ) m ON m.ad_id = ad.id
        WHERE ad.org_id = $1 AND ad.creative_id = ANY($2)
        ORDER BY COALESCE(m.spend, 0) DESC, ad.first_seen_at, ad.id
        """,
        org_id,
        creative_ids,
    )
