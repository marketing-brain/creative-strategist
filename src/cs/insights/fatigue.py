from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from cs.core.db import Database
from cs.insights.patterns import metrics_from_sums, signed_lift
from cs.insights.types import InsightDraft


async def compute_fatigue(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    today: date,
    *,
    spend_floor: Decimal = Decimal("100"),
    drop_threshold: float = 0.30,
    first_days: int = 14,
    trailing_days: int = 7,
) -> list[InsightDraft]:
    trailing_start = today - timedelta(days=trailing_days - 1)
    rows = await db.fetch(
        """
        SELECT creative_id, day, sum(spend) AS spend, sum(impressions) AS imp,
               sum(clicks) AS clicks, sum(conversions) AS conv, sum(conversion_value) AS val
        FROM metric_daily WHERE org_id=$1 AND ad_account_id=$2 AND day <= $3
          AND creative_id IN (
              SELECT creative_id FROM metric_daily WHERE org_id=$1 AND ad_account_id=$2
              AND day BETWEEN $4 AND $3 GROUP BY creative_id HAVING sum(spend) >= $5)
        GROUP BY creative_id, day ORDER BY creative_id, day
        """,
        org_id,
        ad_account_id,
        today,
        trailing_start,
        spend_floor,
    )
    by_creative: dict[UUID, list] = defaultdict(list)
    for r in rows:
        by_creative[r["creative_id"]].append(r)
    out: list[InsightDraft] = []
    for cid, days in by_creative.items():
        first_day = days[0]["day"]
        first_end = first_day + timedelta(days=first_days - 1)
        if first_end >= trailing_start:
            continue  # too young to compare
        first = [d for d in days if d["day"] <= first_end]
        trailing = [d for d in days if d["day"] >= trailing_start]

        def sums(chunk):
            return metrics_from_sums(
                sum(d["spend"] for d in chunk),
                sum(int(d["imp"]) for d in chunk),
                sum(int(d["clicks"]) for d in chunk),
                sum(d["conv"] for d in chunk),
                sum(d["val"] for d in chunk),
            )

        base, now = sums(first), sums(trailing)
        for metric in ("ctr", "cpa"):
            lift = signed_lift(metric, now[metric], base[metric])
            if lift is not None and lift <= -drop_threshold:
                out.append(
                    InsightDraft(
                        kind="fatigue",
                        dimension="creative",
                        value=str(cid),
                        metric=metric,
                        window_start=trailing_start,
                        window_end=today,
                        sample_creatives=1,
                        sample_spend=Decimal(sum(d["spend"] for d in trailing)),
                        baseline=base[metric],
                        observed=now[metric],
                        lift=lift,
                        creative_id=cid,
                        ad_account_id=ad_account_id,
                    )
                )
    out.sort(key=lambda d: d.lift)
    return out
