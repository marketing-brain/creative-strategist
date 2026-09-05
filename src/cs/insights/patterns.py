from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from cs.core.db import Database
from cs.insights.types import InsightDraft

METRICS = ("ctr", "cpa", "roas")


def metrics_from_sums(spend: Decimal, impressions: int, clicks: int, conversions: Decimal, conversion_value: Decimal) -> dict:
    spend_f, conv_f, val_f = float(spend), float(conversions), float(conversion_value)
    return {
        "ctr": round(clicks / impressions, 6) if impressions else None,
        "cpa": round(spend_f / conv_f, 4) if conv_f else None,
        "roas": round(val_f / spend_f, 4) if spend_f else None,
    }


def signed_lift(metric: str, observed: float | None, baseline: float | None) -> float | None:
    if observed is None or baseline is None or baseline == 0:
        return None
    raw = (observed - baseline) / baseline
    return round(-raw if metric == "cpa" else raw, 6)


async def compute_pattern_insights(
    db: Database, org_id: UUID, ad_account_id: UUID, window_start: date, window_end: date, *,
    min_creatives: int = 5, min_spend: Decimal = Decimal("1000"), lift_threshold: float = 0.15,
    min_confidence: float = 0.5,
) -> list[InsightDraft]:
    rows = await db.fetch(
        """
        WITH m AS (
          SELECT creative_id, sum(spend) AS spend, sum(impressions) AS imp, sum(clicks) AS clicks,
                 sum(conversions) AS conv, sum(conversion_value) AS val
          FROM metric_daily WHERE org_id=$1 AND ad_account_id=$2 AND day BETWEEN $3 AND $4
          GROUP BY creative_id
        ), base AS (
          SELECT sum(spend) AS spend, sum(imp) AS imp, sum(clicks) AS clicks, sum(conv) AS conv, sum(val) AS val FROM m
        ), seg AS (
          SELECT lt.dimension, lt.value, count(*) AS n, sum(m.spend) AS spend, sum(m.imp) AS imp,
                 sum(m.clicks) AS clicks, sum(m.conv) AS conv, sum(m.val) AS val
          FROM m JOIN creative_latest_tag lt ON lt.creative_id = m.creative_id AND lt.org_id = $1
          WHERE lt.confidence >= $7
          GROUP BY lt.dimension, lt.value
        )
        SELECT seg.dimension, seg.value, seg.n, seg.spend, seg.imp, seg.clicks, seg.conv, seg.val,
               base.spend AS b_spend, base.imp AS b_imp, base.clicks AS b_clicks, base.conv AS b_conv, base.val AS b_val
        FROM seg, base
        WHERE seg.n >= $5 AND seg.spend >= $6
        """, org_id, ad_account_id, window_start, window_end, min_creatives, min_spend, min_confidence)
    out: list[InsightDraft] = []
    for r in rows:
        seg = metrics_from_sums(r["spend"], int(r["imp"]), int(r["clicks"]), r["conv"], r["val"])
        base = metrics_from_sums(r["b_spend"], int(r["b_imp"]), int(r["b_clicks"]), r["b_conv"], r["b_val"])
        for metric in METRICS:
            lift = signed_lift(metric, seg[metric], base[metric])
            if lift is None or abs(lift) < lift_threshold:
                continue
            out.append(InsightDraft(
                kind="pattern", dimension=r["dimension"], value=r["value"], metric=metric,
                window_start=window_start, window_end=window_end, sample_creatives=int(r["n"]),
                sample_spend=Decimal(r["spend"]), baseline=base[metric], observed=seg[metric], lift=lift,
                ad_account_id=ad_account_id))
    out.sort(key=lambda d: -abs(d.lift))
    return out
