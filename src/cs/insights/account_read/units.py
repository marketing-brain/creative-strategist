"""Footage units for one ad account: video creatives grouped by primary-asset sha256, metrics
pooled across copies, facets from the highest-spend copy's latest done v2 tag run."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from cs.core.db import Database
from cs.insights.account_read.facets import NUMERIC_DIMS, extract, tercile_labels
from cs.insights.account_read.types import Unit

_CREATIVES = """
WITH latest AS (
  SELECT DISTINCT ON (r.creative_id) r.creative_id, r.id AS run_id
  FROM tag_run r JOIN taxonomy_version v ON v.id = r.taxonomy_version_id
  WHERE r.org_id = $1 AND r.status = 'done' AND split_part(v.version, '.', 1)::int >= 2
  ORDER BY r.creative_id, r.finished_at DESC
), m AS (
  SELECT creative_id, sum(spend) spend, sum(impressions) imp, sum(video_plays) plays,
         sum(video_3s_views) v3, sum(video_thruplay) thru, sum(video_p25) p25, sum(video_p50) p50,
         sum(video_p75) p75, sum(video_p100) p100, sum(link_clicks) lc, sum(purchases) purch,
         min(day) first_day, max(day) last_day
  FROM metric_daily WHERE org_id = $1 AND ad_account_id = $2 GROUP BY creative_id
)
SELECT c.id, a.sha256, a.duration_ms, l.run_id,
       left(coalesce(nullif(c.headline, ''), nullif(c.primary_text, ''), '(no copy)'), 90)
         AS headline,
       coalesce(m.spend, 0) spend, coalesce(m.imp, 0) imp, coalesce(m.plays, 0) plays,
       coalesce(m.v3, 0) v3, coalesce(m.thru, 0) thru, coalesce(m.p25, 0) p25,
       coalesce(m.p50, 0) p50, coalesce(m.p75, 0) p75, coalesce(m.p100, 0) p100,
       coalesce(m.lc, 0) lc, coalesce(m.purch, 0) purch, m.first_day, m.last_day
FROM creative c
JOIN asset a ON a.id = c.primary_asset_id
LEFT JOIN latest l ON l.creative_id = c.id
LEFT JOIN m ON m.creative_id = c.id
WHERE c.org_id = $1 AND c.ad_account_id = $2 AND c.format = 'video'
  AND a.kind = 'original_video'
"""

_TAGS = (
    "SELECT dimension, kind, value, raw_value, t_start_s, ordinal FROM tag WHERE tag_run_id = $1"
)


async def build_units(
    db: Database, org_id: UUID, ad_account_id: UUID
) -> tuple[list[Unit], dict[str, tuple[float, float]]]:
    rows = await db.fetch(_CREATIVES, org_id, ad_account_id)
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r["sha256"]].append(r)
    units: list[Unit] = []
    for sha, rs in groups.items():
        tagged = [r for r in rs if r["run_id"] is not None]
        with_metrics = [r for r in rs if r["first_day"] is not None]
        if not tagged or not with_metrics:
            continue
        # Ties break on id so `tagging.repo.footage_latest_tags` picks the same copy.
        rep = max(tagged, key=lambda r: (r["spend"], r["id"]))
        tag_rows = [dict(t) for t in await db.fetch(_TAGS, rep["run_id"])]
        duration_ms = max((r["duration_ms"] or 0) for r in rs)
        duration_s = duration_ms / 1000.0 if duration_ms else None
        x = extract(tag_rows, duration_s)
        units.append(
            Unit(
                sha256=sha,
                creative_ids=[r["id"] for r in rs],
                rep_creative_id=rep["id"],
                headline=rep["headline"],
                duration_s=duration_s,
                spend=Decimal(sum(r["spend"] for r in rs)),
                impressions=int(sum(r["imp"] for r in rs)),
                plays=int(sum(r["plays"] for r in rs)),
                video_3s_views=int(sum(r["v3"] for r in rs)),
                thruplays=int(sum(r["thru"] for r in rs)),
                p25=int(sum(r["p25"] for r in rs)),
                p50=int(sum(r["p50"] for r in rs)),
                p75=int(sum(r["p75"] for r in rs)),
                p100=int(sum(r["p100"] for r in rs)),
                link_clicks=int(sum(r["lc"] for r in rs)),
                purchases=int(sum(r["purch"] for r in rs)),
                first_day=min(r["first_day"] for r in with_metrics),
                last_day=max(r["last_day"] for r in with_metrics),
                facets=x.facets,
                numeric=x.numeric,
                first_product_s=x.first_product_s,
                concept_summary=x.concept_summary,
                hook_verbatim=x.hook_verbatim,
            )
        )
    edges: dict[str, tuple[float, float]] = {}
    for dim in NUMERIC_DIMS:
        labels, e = tercile_labels({u.sha256: u.numeric[dim] for u in units if dim in u.numeric})
        if e:
            edges[dim] = e
            for u in units:
                if u.sha256 in labels:
                    u.facets.add((dim, labels[u.sha256]))
    return units, edges
