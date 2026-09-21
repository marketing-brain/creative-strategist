from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from cs.core.db import Database
from cs.core.metrics import derived_metrics as metrics_from_sums
from cs.insights.types import InsightDraft


def signed_lift(metric: str, observed: float | None, baseline: float | None) -> float | None:
    if observed is None or baseline is None or baseline == 0:
        return None
    raw = (observed - baseline) / baseline
    return round(-raw if metric == "cpa" else raw, 6)


async def compute_fatigue(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    today: date,
    *,
    spend_floor: Decimal = Decimal("100"),
    drop_threshold: float = 0.30,
    prior_days: int = 14,
    trailing_days: int = 7,
) -> list[InsightDraft]:
    trailing_start = today - timedelta(days=trailing_days - 1)
    prior_start = trailing_start - timedelta(days=prior_days)
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
        # Against the fortnight just before the trailing week, not the ad's first fortnight:
        # most ads trickle on a test budget before they scale, and a cost measured off that
        # trickle is a tiny denominator, not a baseline.
        if days[0]["day"] > prior_start:
            continue  # too young to compare
        first = [d for d in days if prior_start <= d["day"] < trailing_start]
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
