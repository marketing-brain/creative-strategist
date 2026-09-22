"""Intervals of every file's retention curve, with the scenes that play inside them.

Reads the durable footage tables (migration 20260922000006), so one query covers every file
in an account instead of a per-file fan-out.
"""

from __future__ import annotations

from uuid import UUID

from cs.core.db import Database
from cs.insights.event_study.types import Interval

# Meta reports the curve at 22 points: every second to 15s, then widening to 20, 25, 30, 40,
# 50, 60. An interval runs from one point to the next, so the last point (past 60s) opens no
# interval -- nothing bounds it, and 243 of this account's scenes start out there, beyond
# anything the curve can measure.
_INTERVALS = """
WITH pt AS (
  SELECT p.footage_id, p.bucket, p.start_s, p.share, p.plays,
         lead(p.start_s) OVER w AS next_s, lead(p.share) OVER w AS next_share
  FROM play_curve_point p
  WHERE p.window_kind = $3
  WINDOW w AS (PARTITION BY p.footage_id ORDER BY p.bucket)
)
SELECT f.id, pt.bucket, pt.start_s, pt.next_s, pt.share, pt.next_share, pt.plays,
       f.duration_s,
       count(s.*) FILTER (WHERE s.ordinal > 0) AS cuts,
       count(s.*) FILTER (WHERE s.ordinal > 0 AND s.shows_product) AS cuts_to_product,
       count(s.*) FILTER (WHERE s.ordinal > 0 AND s.shows_face) AS cuts_to_face
FROM footage f
JOIN pt ON pt.footage_id = f.id
LEFT JOIN scene s ON s.footage_id = f.id
                 AND s.t_start_s >= pt.start_s AND s.t_start_s < pt.next_s
WHERE f.org_id = $1 AND f.ad_account_id = $2 AND f.format = 'video'
  AND pt.next_s IS NOT NULL
  -- Only files whose scenes are known. An untagged file has no scene rows, and counting it
  -- as a file where nothing happens on screen would fill the control group with unknowns.
  AND EXISTS (SELECT 1 FROM scene s2 WHERE s2.footage_id = f.id)
GROUP BY f.id, pt.bucket, pt.start_s, pt.next_s, pt.share, pt.next_share, pt.plays,
         f.duration_s
ORDER BY f.id, pt.bucket
"""


async def intervals(
    db: Database, org_id: UUID, ad_account_id: UUID, window_kind: str = "lifetime"
) -> list[Interval]:
    rows = await db.fetch(_INTERVALS, org_id, ad_account_id, window_kind)
    return [
        Interval(
            footage_id=r["id"],
            bucket=r["bucket"],
            start_s=float(r["start_s"]),
            end_s=float(r["next_s"]),
            share_from=float(r["share"]),
            share_to=float(r["next_share"]),
            cuts=r["cuts"],
            cuts_to_product=r["cuts_to_product"],
            cuts_to_face=r["cuts_to_face"],
            duration_s=float(r["duration_s"] or 0),
            plays=int(r["plays"]),
        )
        for r in rows
    ]
