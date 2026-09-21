"""Meta's per-second play curve for one file, against videos of similar length.

Meta returns `video_play_curve_actions` as 22 integers per ad-day: the share of plays still
watching at each second from 0 to 14, then at 15-20, 20-25, 25-30, 30-40, 40-50 and 50-60
seconds, then past 60. Fivetran lands it in the org's schema (`ad_signals`, see
cs.connectors.fivetran.client); the matching day's plays sit in `video_retention_ad`.

The curve is read straight from that schema, the way campaign names are: it is display
evidence for the retention layer, not a number anything else is computed from. Days pool by
plays, so a day with 40,000 plays counts forty times one with 1,000, and the file's copies
pool the same way, like every other number on the page.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

from cs.connectors.fivetran.connect import schema_for
from cs.core.db import Database

# Start and end second of each of Meta's 22 buckets; None is open-ended (past 60s).
BUCKETS: list[tuple[int, int | None]] = [(s, s + 1) for s in range(15)]
BUCKETS += [(15, 20), (20, 25), (25, 30), (30, 40), (40, 50), (50, 60), (60, None)]
# Below this many plays in the window a bucket's share moves by whole points from one day's
# noise (Meta rounds each ad-day to an integer percent), so the page keeps its quartile chart.
PLAYS_FLOOR = 1_000


def shape(
    rows: list[Any], duration_s: float | None, baseline_label: str | None
) -> dict[str, Any] | None:
    """Rows of (mine, bucket, share_pct, plays) -> the payload, or None when this file has
    too few plays to draw. Buckets that start at or after the video's end are dropped: Meta
    reports them as zero and they would read as everyone leaving."""
    own = {r["bucket"]: r for r in rows if r["mine"]}
    base = {r["bucket"]: r for r in rows if not r["mine"]}
    plays = max((int(r["plays"]) for r in own.values()), default=0)
    if plays < PLAYS_FLOOR:
        return None
    base_plays = max((int(r["plays"]) for r in base.values()), default=0)
    points = []
    for i, (start, end) in enumerate(BUCKETS):
        if duration_s is not None and start >= duration_s:
            break
        mine = own.get(i)
        theirs = base.get(i) if base_plays >= PLAYS_FLOOR else None
        points.append(
            {
                "start_s": start,
                "end_s": end,
                "share": float(mine["share"]) / 100 if mine else None,
                "baseline_share": float(theirs["share"]) / 100 if theirs else None,
            }
        )
    return {
        "points": points,
        "plays": plays,
        "baseline_plays": base_plays,
        "baseline_label": baseline_label if base_plays >= PLAYS_FLOOR else None,
        "duration_s": duration_s,
    }


async def play_curve(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    creative_ids: list[UUID],
    since: date,
    until: date,
    length_bucket_s: tuple[int, int | None] | None,
) -> list[Any]:
    """Plays-weighted share per bucket for the file's ads (`mine`) and for every other video
    ad in the account whose length falls in `length_bucket_s` (seconds, low exclusive, high
    inclusive; see diagnosis.length_bucket), the peers the retention layer compares against.
    An org whose schema lacks the report gets no rows."""
    schema = schema_for(org_id)
    lo, hi = length_bucket_s if length_bucket_s is not None else (None, None)
    try:
        return await db.fetch(
            f"""
            WITH own AS (
                SELECT platform_ad_id FROM ad WHERE org_id = $1 AND creative_id = ANY($2)
            ),
            peers AS (
                SELECT a.platform_ad_id FROM ad a
                JOIN creative c ON c.id = a.creative_id
                JOIN asset s ON s.id = c.primary_asset_id
                WHERE a.org_id = $1 AND a.ad_account_id = $3 AND c.format = 'video'
                  AND a.creative_id <> ALL($2)
                  AND $6::bigint IS NOT NULL AND s.duration_ms > $6
                  AND ($7::bigint IS NULL OR s.duration_ms <= $7)
            ),
            plays AS (
                SELECT ad_id, date, sum(value) AS plays
                FROM "{schema}".video_retention_ad_video_play_actions
                WHERE date BETWEEN $4 AND $5
                GROUP BY 1, 2
            ),
            days AS (
                SELECT cv.ad_id IN (SELECT platform_ad_id FROM own) AS mine, cv.value AS curve,
                       p.plays
                FROM "{schema}".ad_signals_video_play_curve_actions cv
                JOIN plays p USING (ad_id, date)
                WHERE cv.date BETWEEN $4 AND $5 AND p.plays > 0
                  AND (cv.ad_id IN (SELECT platform_ad_id FROM own)
                       OR cv.ad_id IN (SELECT platform_ad_id FROM peers))
            )
            SELECT mine, (e.i - 1)::int AS bucket,
                   sum(e.v::float8 * plays) / sum(plays) AS share, sum(plays) AS plays
            FROM days, jsonb_array_elements_text(curve) WITH ORDINALITY e(v, i)
            GROUP BY 1, 2 ORDER BY 1, 2
            """,
            org_id,
            creative_ids,
            ad_account_id,
            since,
            until,
            lo * 1000 if lo is not None else None,
            hi * 1000 if hi is not None else None,
        )
    except (asyncpg.UndefinedTableError, asyncpg.InvalidSchemaNameError):
        return []
