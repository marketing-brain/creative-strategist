from __future__ import annotations

from uuid import UUID

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
