"""Loads for the `/next` cards. Three queries for a whole account, then everything is pure.

The footage tables exist so this is a join rather than a rebuild: `play_curve_point` already
holds Meta's per-second retention pooled over a file's copies. `sha256` comes back with every
row because it is the key the account read names its files by, and joining a cited test's file
list to current spend is the whole mechanism behind a card.
"""

from __future__ import annotations

from uuid import UUID

from cs.core.db import Database
from cs.insights.next_plan.segment import CURVE_WINDOW, SPEND_WINDOW, Asset

# Title follows the creatives grid and the v1 allocation query: the name of the ad that spent
# the most, falling back to the copy. Half of org 3's top spenders carry no headline at all.
_ASSETS = f"""
WITH ad_spend AS (
  SELECT ad_id, sum(spend) AS spend FROM ad_metric_30d WHERE org_id = $1 GROUP BY ad_id
), named AS (
  SELECT ad.creative_id,
         (array_agg(ad.name ORDER BY COALESCE(s.spend, 0) DESC, ad.first_seen_at, ad.id))[1]
           AS ad_name
  FROM ad LEFT JOIN ad_spend s ON s.ad_id = ad.id
  WHERE ad.org_id = $1 GROUP BY ad.creative_id
), campaign AS (
  -- The campaign a file mostly ran in over the same 30 days, by spend. A file copied into
  -- several campaigns is one decision (footage grouping) but it still has a dominant home,
  -- and that is the delivery context its curve was produced in.
  SELECT footage_id, campaign_id FROM (
    SELECT fc.footage_id, a.campaign_id,
           row_number() OVER (PARTITION BY fc.footage_id ORDER BY sum(m.spend) DESC) rn
    FROM ad a
    JOIN footage_creative fc ON fc.creative_id = a.creative_id
    JOIN metric_daily m ON m.ad_id = a.id AND m.day > current_date - 30
    WHERE a.org_id = $1 AND a.ad_account_id = $2 AND a.campaign_id IS NOT NULL
    GROUP BY 1, 2
  ) ranked WHERE rn = 1
), curve AS (
  SELECT footage_id, max(plays) AS plays FROM play_curve_point
  WHERE window_kind = '{CURVE_WINDOW}' GROUP BY footage_id
)
SELECT f.id, f.sha256, f.rep_creative_id, f.duration_s, f.first_day, curve.plays,
       campaign.campaign_id,
       COALESCE(m.spend, 0) AS spend_30d, COALESCE(m.impressions, 0) AS impressions,
       COALESCE(m.purchases, 0) AS purchases, COALESCE(m.link_clicks, 0) AS link_clicks,
       left(COALESCE(nullif(n.ad_name, ''), nullif(c.headline, ''),
                     nullif(c.primary_text, ''), '(no copy)'), 90) AS title
FROM footage f
JOIN curve ON curve.footage_id = f.id
LEFT JOIN campaign ON campaign.footage_id = f.id
LEFT JOIN footage_metric_window m ON m.footage_id = f.id AND m.window_kind = '{SPEND_WINDOW}'
LEFT JOIN creative c ON c.id = f.rep_creative_id
LEFT JOIN named n ON n.creative_id = f.rep_creative_id
WHERE f.org_id = $1 AND f.ad_account_id = $2 AND f.format = 'video' AND f.duration_s > 0
"""

_CURVES = f"""
SELECT p.footage_id, p.bucket, p.start_s, p.share
FROM play_curve_point p JOIN footage f ON f.id = p.footage_id
WHERE f.org_id = $1 AND f.ad_account_id = $2 AND p.window_kind = '{CURVE_WINDOW}'
ORDER BY p.footage_id, p.bucket
"""


async def load_assets(db: Database, org_id: UUID, ad_account_id: UUID) -> list[Asset]:
    """Every video file in the account that has a 30-day curve, with the counts a card needs.

    `sha256` is the join back to the account read: a cited test names its files by that key,
    so this is what turns "the 15 files on the wrong side of P1" into rows with spend.
    """
    rows = await db.fetch(_ASSETS, org_id, ad_account_id)
    curves: dict[str, dict[int, float]] = {}
    starts: dict[str, dict[int, int]] = {}
    for r in await db.fetch(_CURVES, org_id, ad_account_id):
        fid = str(r["footage_id"])
        curves.setdefault(fid, {})[r["bucket"]] = float(r["share"])
        starts.setdefault(fid, {})[r["bucket"]] = int(r["start_s"])
    out = []
    for r in rows:
        fid = str(r["id"])
        out.append(
            Asset(
                footage_id=fid,
                sha256=r["sha256"],
                rep_creative_id=str(r["rep_creative_id"]) if r["rep_creative_id"] else None,
                title=(r["title"] or "(no copy)").strip(),
                campaign_id=r["campaign_id"],
                first_day=r["first_day"],
                duration_s=float(r["duration_s"]),
                spend_30d=float(r["spend_30d"] or 0),
                impressions=int(r["impressions"] or 0),
                purchases=int(r["purchases"] or 0),
                link_clicks=int(r["link_clicks"] or 0),
                plays=int(r["plays"] or 0),
                curve=curves.get(fid, {}),
                starts=starts.get(fid, {}),
            )
        )
    return out


_PATHS = """
SELECT f.id AS footage_id, a.path, a.mime
FROM footage f
JOIN creative c ON c.id = f.rep_creative_id
JOIN asset a ON a.id = c.primary_asset_id
WHERE f.org_id = $1 AND f.ad_account_id = $2 AND a.path IS NOT NULL
"""


async def load_asset_paths(db: Database, org_id: UUID, ad_account_id: UUID) -> dict[str, str]:
    """Blob paths for the representative copy of each file, for the clipper."""
    return {str(r["footage_id"]): r["path"] for r in await db.fetch(_PATHS, org_id, ad_account_id)}
