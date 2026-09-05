from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id
from cs.insights.types import InsightDraft


async def insert_insight(db: Database, org_id: UUID, d: InsightDraft, explanation: str, suggested_test: str) -> UUID | None:
    row = await db.fetchrow(
        """
        INSERT INTO insight (id, org_id, ad_account_id, kind, dimension, value, metric, creative_id, window_start, window_end,
                             sample_creatives, sample_spend, baseline, observed, lift, explanation, suggested_test)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT DO NOTHING RETURNING id
        """,
        new_id(), org_id, d.ad_account_id, d.kind, d.dimension, d.value, d.metric, d.creative_id, d.window_start,
        d.window_end, d.sample_creatives, d.sample_spend, d.baseline, d.observed, d.lift, explanation, suggested_test)
    return row["id"] if row else None


async def list_insights(db: Database, org_id: UUID, kind: str | None = None, ad_account_id: UUID | None = None,
                        limit: int = 50) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT i.*, c.headline AS creative_headline
        FROM insight i LEFT JOIN creative c ON c.id = i.creative_id
        WHERE i.org_id = $1 AND ($2::text IS NULL OR i.kind = $2) AND ($3::uuid IS NULL OR i.ad_account_id = $3)
        ORDER BY i.created_at DESC, abs(i.lift) DESC LIMIT $4
        """, org_id, kind, ad_account_id, limit)


async def insert_brief(db: Database, org_id: UUID, week_start: date, content_md: str, insight_ids: list[UUID]) -> UUID | None:
    row = await db.fetchrow(
        "INSERT INTO brief (id, org_id, week_start, content_md, insight_ids) VALUES ($1,$2,$3,$4,$5) "
        "ON CONFLICT (org_id, week_start) DO NOTHING RETURNING id", new_id(), org_id, week_start, content_md, insight_ids)
    return row["id"] if row else None


async def list_briefs(db: Database, org_id: UUID, limit: int = 12) -> list[asyncpg.Record]:
    return await db.fetch("SELECT * FROM brief WHERE org_id=$1 ORDER BY week_start DESC LIMIT $2", org_id, limit)
