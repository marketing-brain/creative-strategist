"""Rebuilds the durable footage tables from what the app already has.

`footage` and its companions are a materialised view of `creative`, `asset`, `tag`,
`metric_daily` and the org's Fivetran schema: one row per file per ad account, its copies,
its tags, its scenes, its metrics per window and its retention curve. Every statement is an
idempotent upsert, so a refresh is safe to run at any time and the tables can be dropped and
rebuilt from scratch.

They go stale the moment a sync lands -- new creatives, another day of delivery, a finished
tag run -- so `footage.nightly_refresh` runs this every night and `scripts/backfill_footage.py`
runs it by hand. Nothing else writes these tables.
"""

from __future__ import annotations

import logging
from uuid import UUID

from cs.connectors.fivetran.connect import schema_for
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.ids import new_id
from cs.jobs.scheduler import register_schedule

log = logging.getLogger("cs.footage")
NIGHTLY_SCHEDULE = "footage.nightly_refresh"

FORMATS = ("image", "video")
# The tagger writes one row per scene with kind 'ordered'; these are the facet kinds.
FACET_KINDS = ("single", "multi", "open", "numeric")

# Candidate groups, straight from the same key cs.core.repo.footage groups on.
_GROUPS = """
SELECT c.ad_account_id, c.org_id, c.format, a.sha256,
       max(a.duration_ms) FILTER (WHERE a.duration_ms > 0) AS duration_ms,
       count(*) AS n_creatives,
       (array_agg(c.id ORDER BY coalesce(m.spend, 0) DESC, c.id))[1] AS rep_creative_id,
       min(m.first_day) AS first_day, max(m.last_day) AS last_day
FROM creative c
JOIN asset a ON a.id = c.primary_asset_id AND a.sha256 IS NOT NULL
LEFT JOIN (
    SELECT creative_id, sum(spend) AS spend, min(day) AS first_day, max(day) AS last_day
    FROM metric_daily GROUP BY creative_id
) m ON m.creative_id = c.id
WHERE c.format = ANY($1::text[]) AND ($2::uuid IS NULL OR c.org_id = $2)
  AND ((c.format = 'video' AND a.kind = 'original_video')
    OR (c.format = 'image' AND a.kind = 'original_image'))
GROUP BY c.ad_account_id, c.org_id, c.format, a.sha256
"""

_UPSERT_FOOTAGE = """
INSERT INTO footage (id, org_id, ad_account_id, format, sha256, duration_s, n_creatives,
                     rep_creative_id, first_day, last_day, refreshed_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10, now())
ON CONFLICT (ad_account_id, format, sha256) DO UPDATE SET
  duration_s = EXCLUDED.duration_s, n_creatives = EXCLUDED.n_creatives,
  rep_creative_id = EXCLUDED.rep_creative_id, first_day = EXCLUDED.first_day,
  last_day = EXCLUDED.last_day, refreshed_at = now()
"""

_MAP_CREATIVES = """
INSERT INTO footage_creative (creative_id, footage_id)
SELECT c.id, f.id
FROM creative c
JOIN asset a ON a.id = c.primary_asset_id AND a.sha256 IS NOT NULL
JOIN footage f ON f.ad_account_id = c.ad_account_id AND f.format = c.format
              AND f.sha256 = a.sha256
WHERE ($1::uuid IS NULL OR c.org_id = $1)
ON CONFLICT (creative_id) DO UPDATE SET footage_id = EXCLUDED.footage_id
"""

# One statement per window: the window's days are chosen in the CTE, the sums are identical.
_METRICS = """
WITH days AS (
    SELECT fc.footage_id, m.*
    FROM footage_creative fc
    JOIN footage f ON f.id = fc.footage_id
    JOIN metric_daily m ON m.creative_id = fc.creative_id
    WHERE ($1::uuid IS NULL OR f.org_id = $1) AND {window}
)
INSERT INTO footage_metric_window (
    footage_id, window_kind, day_from, day_to, spend, impressions, plays, views_3s,
    thruplays, p25, p50, p75, p100, watch_seconds, link_clicks, landing_page_views,
    add_to_carts, purchases, purchase_value, n_days)
SELECT footage_id, '{kind}', min(day), max(day), sum(spend), sum(impressions),
       sum(video_plays), sum(video_3s_views), sum(video_thruplay), sum(video_p25),
       sum(video_p50), sum(video_p75), sum(video_p100), sum(video_watch_seconds),
       sum(link_clicks), sum(landing_page_views), sum(add_to_carts), sum(purchases),
       sum(purchase_value_click_7d) + sum(purchase_value_view_1d), count(DISTINCT day)
FROM days GROUP BY footage_id
ON CONFLICT (footage_id, window_kind) DO UPDATE SET
  day_from = EXCLUDED.day_from, day_to = EXCLUDED.day_to, spend = EXCLUDED.spend,
  impressions = EXCLUDED.impressions, plays = EXCLUDED.plays, views_3s = EXCLUDED.views_3s,
  thruplays = EXCLUDED.thruplays, p25 = EXCLUDED.p25, p50 = EXCLUDED.p50,
  p75 = EXCLUDED.p75, p100 = EXCLUDED.p100, watch_seconds = EXCLUDED.watch_seconds,
  link_clicks = EXCLUDED.link_clicks, landing_page_views = EXCLUDED.landing_page_views,
  add_to_carts = EXCLUDED.add_to_carts, purchases = EXCLUDED.purchases,
  purchase_value = EXCLUDED.purchase_value, n_days = EXCLUDED.n_days
"""

WINDOWS = {
    "lifetime": "TRUE",
    "first_7d": "m.day BETWEEN f.first_day AND f.first_day + 6",
    "last_30d": "m.day > current_date - 30",
}

# The copy whose tags stand for the file: highest lifetime spend among copies with a finished
# run, ties on id, and for video only the v2+ profile (the image dimensions never changed).
_REP_RUNS = """
SELECT DISTINCT ON (fc.footage_id)
       fc.footage_id, r.id AS run_id, split_part(v.version, '.', 1)::int AS major
FROM footage_creative fc
JOIN footage f ON f.id = fc.footage_id
JOIN tag_run r ON r.creative_id = fc.creative_id AND r.status = 'done'
JOIN taxonomy_version v ON v.id = r.taxonomy_version_id
LEFT JOIN (SELECT creative_id, sum(spend) AS spend FROM metric_daily GROUP BY creative_id) m
       ON m.creative_id = fc.creative_id
WHERE ($1::uuid IS NULL OR f.org_id = $1)
  AND (f.format <> 'video' OR split_part(v.version, '.', 1)::int >= 2)
ORDER BY fc.footage_id, coalesce(m.spend, 0) DESC, fc.creative_id, r.finished_at DESC
"""

_ATTRIBUTES = """
INSERT INTO footage_attribute (footage_id, dimension, value, kind, numeric_value,
                               source_tag_run_id, taxonomy_major)
SELECT DISTINCT ON (t.dimension, t.value)
       $1, t.dimension, t.value, t.kind,
       CASE WHEN t.kind = 'numeric' THEN nullif(t.value, '')::double precision END,
       t.tag_run_id, $3
FROM tag t
WHERE t.tag_run_id = $2 AND t.kind = ANY($4::text[])
ORDER BY t.dimension, t.value, t.id
ON CONFLICT (footage_id, dimension, value) DO UPDATE SET
  kind = EXCLUDED.kind, numeric_value = EXCLUDED.numeric_value,
  source_tag_run_id = EXCLUDED.source_tag_run_id, taxonomy_major = EXCLUDED.taxonomy_major
"""

# Scene text carries what is on screen; 'face' and 'product'/'packaging' are read off it the
# same way cs.insights.account_read.facets does, so both stay one rule in two places at most.
_SCENES = """
INSERT INTO scene (footage_id, ordinal, t_start_s, t_end_s, label, raw, shows_face,
                   shows_product)
SELECT $1, t.ordinal, t.t_start_s,
       lead(t.t_start_s) OVER (ORDER BY t.ordinal),
       t.value, t.raw_value,
       coalesce(t.raw_value, '') ILIKE '%face%',
       coalesce(t.raw_value, '') ILIKE '%product%' OR coalesce(t.raw_value, '') ILIKE '%packaging%'
FROM (
    SELECT DISTINCT ON (ordinal) ordinal, t_start_s, value, raw_value
    FROM tag
    WHERE tag_run_id = $2 AND dimension = 'scenes' AND kind = 'ordered'
      AND ordinal IS NOT NULL AND t_start_s IS NOT NULL
    ORDER BY ordinal, id
) t
ON CONFLICT (footage_id, ordinal) DO UPDATE SET
  t_start_s = EXCLUDED.t_start_s, t_end_s = EXCLUDED.t_end_s, label = EXCLUDED.label,
  raw = EXCLUDED.raw, shows_face = EXCLUDED.shows_face,
  shows_product = EXCLUDED.shows_product
"""

# Meta's 22 curve buckets, plays-weighted over the file's ads and days, exactly as
# cs.insights.play_curve reads them live. `value` is a JSON array of 22 integer percents.
_CURVE = """
mine AS (
    SELECT a.platform_ad_id, fc.footage_id, f.first_day
    FROM ad a
    JOIN footage_creative fc ON fc.creative_id = a.creative_id
    JOIN footage f ON f.id = fc.footage_id
    WHERE a.org_id = $1 AND f.format = 'video'
),
plays AS (
    SELECT ad_id, date, sum(value) AS plays
    FROM "{schema}".video_retention_ad_video_play_actions GROUP BY 1, 2
),
days AS (
    SELECT m.footage_id, cv.value AS curve, p.plays
    FROM "{schema}".ad_signals_video_play_curve_actions cv
    JOIN mine m ON m.platform_ad_id = cv.ad_id
    JOIN plays p ON p.ad_id = cv.ad_id AND p.date = cv.date
    WHERE p.plays > 0 AND {window}
)
INSERT INTO play_curve_point (footage_id, window_kind, bucket, start_s, end_s, share, plays)
SELECT d.footage_id, '{kind}', (e.i - 1)::int, b.start_s, b.end_s,
       sum(e.v::float8 * d.plays) / sum(d.plays) / 100.0, sum(d.plays)
FROM days d, jsonb_array_elements_text(d.curve) WITH ORDINALITY e(v, i)
JOIN buckets b ON b.bucket = (e.i - 1)::int
GROUP BY 1, 3, 4, 5
ON CONFLICT (footage_id, window_kind, bucket) DO UPDATE SET
  share = EXCLUDED.share, plays = EXCLUDED.plays
"""

# Start and end second of Meta's 22 buckets: one per second to 15s, then widening. This is
# the first CTE of the curve statement, so the rest of it starts at `mine`.
_BUCKETS = "WITH buckets (bucket, start_s, end_s) AS (VALUES {rows}),"
BUCKET_EDGES = [(s, s + 1) for s in range(15)]
BUCKET_EDGES += [(15, 20), (20, 25), (25, 30), (30, 40), (40, 50), (50, 60), (60, None)]
CURVE_WINDOWS = {
    "lifetime": "TRUE",
    "first_7d": "cv.date BETWEEN m.first_day AND m.first_day + 6",
}


def _bucket_values() -> str:
    rows = [
        f"({i}, {lo}, {'NULL' if hi is None else hi})" for i, (lo, hi) in enumerate(BUCKET_EDGES)
    ]
    return _BUCKETS.format(rows=", ".join(rows))


async def _footage(db: Database, org: UUID | None) -> int:
    rows = await db.fetch(_GROUPS, list(FORMATS), org)
    await db.executemany(
        _UPSERT_FOOTAGE,
        [
            (
                new_id(),
                r["org_id"],
                r["ad_account_id"],
                r["format"],
                r["sha256"],
                (r["duration_ms"] / 1000.0) if r["duration_ms"] else None,
                r["n_creatives"],
                r["rep_creative_id"],
                r["first_day"],
                r["last_day"],
            )
            for r in rows
        ],
    )
    await db.execute(_MAP_CREATIVES, org)
    return len(rows)


async def _metrics(db: Database, org: UUID | None) -> None:
    for kind, clause in WINDOWS.items():
        await db.execute(_METRICS.format(window=clause, kind=kind), org)


async def _tags(db: Database, org: UUID | None) -> tuple[int, int]:
    runs = await db.fetch(_REP_RUNS, org)
    scenes = 0
    for r in runs:
        await db.execute(_ATTRIBUTES, r["footage_id"], r["run_id"], r["major"], list(FACET_KINDS))
        if r["major"] >= 2:
            await db.execute(_SCENES, r["footage_id"], r["run_id"])
            scenes += 1
    return len(runs), scenes


async def _curves(db: Database, org_ids: list[UUID]) -> int:
    done = 0
    for org_id in org_ids:
        schema = schema_for(org_id)
        exists = await db.fetchval(
            "SELECT to_regclass($1) IS NOT NULL",
            f'"{schema}".ad_signals_video_play_curve_actions',
        )
        if not exists:
            log.info("no play-curve report for org %s", org_id)
            continue
        for kind, clause in CURVE_WINDOWS.items():
            sql = _bucket_values() + _CURVE.format(schema=schema, window=clause, kind=kind)
            await db.execute(sql, org_id)
        done += 1
    return done


async def _counts(db: Database) -> dict[str, int]:
    tables = (
        "footage",
        "footage_creative",
        "footage_metric_window",
        "footage_attribute",
        "scene",
        "play_curve_point",
    )
    return {t: await db.fetchval(f"SELECT count(*) FROM {t}") for t in tables}


async def refresh(db: Database, org: UUID | None = None) -> dict[str, int]:
    """Bring every footage table up to date. Returns the row counts afterwards."""
    org_ids = [
        r["id"]
        for r in await db.fetch(
            "SELECT id FROM org WHERE $1::uuid IS NULL OR id = $1 ORDER BY id", org
        )
    ]
    groups = await _footage(db, org)
    await _metrics(db, org)
    runs, scenes = await _tags(db, org)
    orgs_with_curves = await _curves(db, org_ids)
    counts = await _counts(db)
    log.info(
        "footage refreshed",
        extra={
            "groups": groups,
            "tag_runs": runs,
            "scene_runs": scenes,
            "orgs_with_curves": orgs_with_curves,
        },
    )
    return counts


@register_schedule(NIGHTLY_SCHEDULE, every_seconds=86400)
async def nightly_refresh(db: Database, settings: Settings) -> None:
    await refresh(db)
