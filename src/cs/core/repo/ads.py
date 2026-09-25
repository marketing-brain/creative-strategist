from __future__ import annotations

from uuid import UUID

from cs.connectors.base import PlatformAd
from cs.core.db import Database
from cs.core.ids import new_id


async def upsert_ad(
    db: Database, org_id: UUID, ad_account_id: UUID, a: PlatformAd, creative_id: UUID
) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO ad (id, org_id, ad_account_id, platform_ad_id, creative_id, adset_id,
                        campaign_id, name, status, campaign_name, adset_name,
                        effective_status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (ad_account_id, platform_ad_id) DO UPDATE
          SET status = EXCLUDED.status, name = EXCLUDED.name, creative_id = EXCLUDED.creative_id,
              -- A sync that could not read a name keeps the last one it did.
              campaign_name = COALESCE(EXCLUDED.campaign_name, ad.campaign_name),
              adset_name = COALESCE(EXCLUDED.adset_name, ad.adset_name),
              effective_status = COALESCE(EXCLUDED.effective_status, ad.effective_status),
              last_seen_at = now()
        RETURNING id
        """,
        new_id(),
        org_id,
        ad_account_id,
        a.platform_ad_id,
        creative_id,
        a.adset_id,
        a.campaign_id,
        a.name,
        a.status,
        a.campaign_name,
        a.adset_name,
        a.effective_status,
    )
    return row["id"]
