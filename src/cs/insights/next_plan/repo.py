"""Storage for the `/next` plan, and the candidate query the allocation block runs on."""

from __future__ import annotations

from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id
from cs.core.repo.footage import FOOTAGE_FORMATS, FOOTAGE_KEY_SQL

# One row per footage group: the highest-spend copy represents the group, and the group's
# 30-day totals are summed over every copy. A video in six campaigns is one decision, so
# reading it copy by copy would both repeat the row and split the spend that ranks it.
# `title` follows the creatives grid: the name of the ad that spent the most, falling back to
# the copy. Half of org 3's top spenders carry no headline at all, so titling rows by headline
# alone printed "(no copy)" on five of twelve budget decisions.
_CANDIDATES = f"""
WITH ad_spend AS (
  SELECT ad_id, sum(spend) AS spend FROM ad_metric_30d WHERE org_id = $1 GROUP BY ad_id
), grouped AS (
  SELECT {FOOTAGE_KEY_SQL} AS group_key, c.id, c.headline, c.primary_text, c.format,
         COALESCE(m.spend, 0) AS spend, n.ad_name
  FROM creative c
  JOIN asset a ON a.id = c.primary_asset_id
  LEFT JOIN creative_metric_30d m ON m.creative_id = c.id AND m.org_id = c.org_id
  LEFT JOIN (
      SELECT ad.creative_id,
             (array_agg(ad.name ORDER BY COALESCE(s.spend, 0) DESC, ad.first_seen_at, ad.id))[1]
               AS ad_name
      FROM ad LEFT JOIN ad_spend s ON s.ad_id = ad.id
      WHERE ad.org_id = $1 GROUP BY ad.creative_id
  ) n ON n.creative_id = c.id
  WHERE c.org_id = $1 AND c.ad_account_id = $2 AND c.format = ANY($3)
), totals AS (
  SELECT group_key, sum(spend) AS spend FROM grouped GROUP BY group_key
), rep AS (
  SELECT DISTINCT ON (g.group_key) g.group_key, g.id,
         left(COALESCE(nullif(g.ad_name, ''), nullif(g.headline, ''),
                       nullif(g.primary_text, ''), '(no copy)'), 90) AS title
  FROM grouped g ORDER BY g.group_key, g.spend DESC, g.id
)
SELECT rep.id, rep.title, totals.spend
FROM totals JOIN rep ON rep.group_key = totals.group_key
WHERE totals.spend >= $4
ORDER BY totals.spend DESC
LIMIT $5
"""


async def allocation_candidates(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    *,
    spend_floor: float,
    limit: int,
) -> list:
    """Footage groups worth diagnosing, biggest spender first. Bounded twice -- by a spend
    floor and by `limit` -- because each row costs a `diagnose_creative` call."""
    return await db.fetch(
        _CANDIDATES, org_id, ad_account_id, list(FOOTAGE_FORMATS), spend_floor, limit
    )


async def account_spend_30d(db: Database, org_id: UUID, ad_account_id: UUID) -> float:
    v = await db.fetchval(
        "SELECT COALESCE(sum(m.spend), 0) FROM creative_metric_30d m "
        "JOIN creative c ON c.id = m.creative_id "
        "WHERE m.org_id = $1 AND c.ad_account_id = $2",
        org_id,
        ad_account_id,
    )
    return float(v or 0)


async def fatigue_rows(db: Database, org_id: UUID, ad_account_id: UUID) -> dict[UUID, list[dict]]:
    """Fatigue comparisons by creative. One creative can hold a row per metric, and one
    footage group several creatives, so the caller picks (see allocate.choose_decline)."""
    rows = await db.fetch(
        "SELECT creative_id, metric, baseline, observed, lift, window_end, created_at "
        "FROM insight WHERE org_id = $1 AND ad_account_id = $2 AND kind = 'fatigue' "
        "AND creative_id IS NOT NULL",
        org_id,
        ad_account_id,
    )
    out: dict[UUID, list[dict]] = {}
    for row in rows:
        out.setdefault(row["creative_id"], []).append(dict(row))
    return out


async def insert_run(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    *,
    status: str,
    plan: dict,
    account_read_run_id: UUID | None,
    error: str | None = None,
) -> UUID:
    rid = new_id()
    await db.execute(
        "INSERT INTO next_plan_run (id, org_id, ad_account_id, account_read_run_id, status, "
        "plan, error) VALUES ($1,$2,$3,$4,$5,$6,$7)",
        rid,
        org_id,
        ad_account_id,
        account_read_run_id,
        status,
        plan,
        error,
    )
    return rid


async def latest_run(db: Database, org_id: UUID, ad_account_id: UUID) -> dict | None:
    """The newest done plan, else the newest of any status.

    `plan` is bounded by MAX_ROWS / MAX_COMBOS and the explore list, so unlike the account
    read's `result` it is read whole in one query with no page slice.
    """
    row = await db.fetchrow(
        "SELECT id, status, plan, error, created_at FROM next_plan_run "
        "WHERE org_id = $1 AND ad_account_id = $2 AND format = 'video' "
        "ORDER BY (status = 'done') DESC, created_at DESC LIMIT 1",
        org_id,
        ad_account_id,
    )
    return dict(row) if row else None


async def upsert_items(
    db: Database, org_id: UUID, ad_account_id: UUID, items: list[tuple[str, str, dict]]
) -> None:
    """Record every recommendation this run emitted, keeping the identity of one it has
    emitted before. `last_seen_at` is what phase 2 uses to expire an item whose supporting
    pattern stopped grading."""
    for key, kind, spec in items:
        await db.execute(
            "INSERT INTO next_item (id, org_id, ad_account_id, key, kind, spec) "
            "VALUES ($1,$2,$3,$4,$5,$6) "
            "ON CONFLICT (ad_account_id, key) DO UPDATE "
            "SET last_seen_at = now(), spec = EXCLUDED.spec",
            new_id(),
            org_id,
            ad_account_id,
            key,
            kind,
            spec,
        )


async def purge_runs(db: Database, days: int = 30) -> int:
    res = await db.execute(
        "DELETE FROM next_plan_run WHERE created_at < now() - make_interval(days => $1)", days
    )
    return int(res.split()[-1])
