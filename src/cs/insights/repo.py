from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id
from cs.insights.types import InsightDraft


async def insert_insight(
    db: Database, org_id: UUID, d: InsightDraft, explanation: str, suggested_test: str
) -> UUID | None:
    row = await db.fetchrow(
        """
        INSERT INTO insight (id, org_id, ad_account_id, kind, dimension, value, metric,
                             creative_id, window_start, window_end, sample_creatives,
                             sample_spend, baseline, observed, lift, explanation, suggested_test)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT DO NOTHING RETURNING id
        """,
        new_id(),
        org_id,
        d.ad_account_id,
        d.kind,
        d.dimension,
        d.value,
        d.metric,
        d.creative_id,
        d.window_start,
        d.window_end,
        d.sample_creatives,
        d.sample_spend,
        d.baseline,
        d.observed,
        d.lift,
        explanation,
        suggested_test,
    )
    return row["id"] if row else None


async def list_insights(
    db: Database,
    org_id: UUID,
    kind: str | None = None,
    ad_account_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT i.*, c.headline AS creative_headline
        FROM insight i LEFT JOIN creative c ON c.id = i.creative_id
        WHERE i.org_id = $1 AND ($2::text IS NULL OR i.kind = $2)
              AND ($3::uuid IS NULL OR i.ad_account_id = $3)
        ORDER BY i.created_at DESC, abs(i.lift) DESC LIMIT $4 OFFSET $5
        """,
        org_id,
        kind,
        ad_account_id,
        limit,
        offset,
    )


async def count_insights(
    db: Database,
    org_id: UUID,
    kind: str | None = None,
    ad_account_id: UUID | None = None,
) -> int:
    return await db.fetchval(
        """
        SELECT count(*) FROM insight i
        WHERE i.org_id = $1 AND ($2::text IS NULL OR i.kind = $2)
              AND ($3::uuid IS NULL OR i.ad_account_id = $3)
        """,
        org_id,
        kind,
        ad_account_id,
    )


async def insert_brief(
    db: Database, org_id: UUID, week_start: date, content_md: str, insight_ids: list[UUID]
) -> UUID | None:
    row = await db.fetchrow(
        "INSERT INTO brief (id, org_id, week_start, content_md, insight_ids) "
        "VALUES ($1,$2,$3,$4,$5) ON CONFLICT (org_id, week_start) DO NOTHING RETURNING id",
        new_id(),
        org_id,
        week_start,
        content_md,
        insight_ids,
    )
    return row["id"] if row else None


async def list_briefs(db: Database, org_id: UUID, limit: int = 12) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT * FROM brief WHERE org_id=$1 ORDER BY week_start DESC LIMIT $2", org_id, limit
    )


async def get_creative_insight(
    db: Database, org_id: UUID, creative_id: UUID
) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT * FROM creative_insight WHERE org_id=$1 AND creative_id=$2", org_id, creative_id
    )


async def insert_creative_insight(
    db: Database,
    org_id: UUID,
    creative_id: UUID,
    *,
    status: str,
    paragraph: str,
    moments: list[dict],
    problems: list[str],
    brief: str,
    window_start: date,
    window_end: date,
    model: str,
    prompt_sha256: str,
    input_tokens: int,
    output_tokens: int,
    creative_profile: dict | None = None,
) -> bool:
    """Write-once: a second insert for the same creative is a no-op. True if this call wrote."""
    row = await db.fetchrow(
        """
        INSERT INTO creative_insight (creative_id, org_id, status, paragraph, creative_profile,
                                      moments, problems, brief, window_start, window_end, model,
                                      prompt_sha256, input_tokens, output_tokens)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
        ON CONFLICT (creative_id) DO NOTHING RETURNING creative_id
        """,
        creative_id,
        org_id,
        status,
        paragraph,
        creative_profile or {},
        moments,
        problems,
        brief,
        window_start,
        window_end,
        model,
        prompt_sha256,
        input_tokens,
        output_tokens,
    )
    return row is not None


async def count_creative_insights(db: Database, org_id: UUID, fmt: str) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM creative_insight ci JOIN creative c ON c.id = ci.creative_id"
        " WHERE ci.org_id = $1 AND c.format = $2",
        org_id,
        fmt,
    )


async def creative_insight_candidates(
    db: Database,
    since: date,
    until: date,
    formats: tuple[str, ...],
    min_impressions: int,
    limit: int,
    limit_per_format: int,
) -> list[asyncpg.Record]:
    """Creatives in active accounts with a stored file and no insight yet, that delivered
    enough in the window to possibly be readable. Highest spend first. Formats an org has
    already reached its insight cap for are left out."""
    return await db.fetch(
        """
        SELECT c.id, c.org_id, c.format FROM creative c
        JOIN ad_account aa ON aa.id = c.ad_account_id AND aa.status = 'active'
        JOIN metric_daily m ON m.creative_id = c.id AND m.day BETWEEN $1 AND $2
        WHERE c.primary_asset_id IS NOT NULL AND c.format = ANY($3)
          AND NOT EXISTS (SELECT 1 FROM creative_insight ci WHERE ci.creative_id = c.id)
          AND (
                SELECT count(*) FROM creative_insight ci
                JOIN creative cf ON cf.id = ci.creative_id
                WHERE ci.org_id = c.org_id AND cf.format = c.format
              ) < $6
        GROUP BY c.id, c.org_id, c.format
        HAVING sum(m.impressions) >= $4
        ORDER BY sum(m.spend) DESC, c.id
        LIMIT $5
        """,
        since,
        until,
        list(formats),
        min_impressions,
        limit,
        limit_per_format,
    )
