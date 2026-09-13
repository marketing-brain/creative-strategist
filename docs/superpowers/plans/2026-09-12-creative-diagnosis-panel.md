# Creative Diagnosis Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a five-tier diagnostic funnel (attention → retention → conversion → fatigue → context) on the creative detail page that names which layer of a creative is broken, backed by real ad-level data.

**Architecture:** Widen `metric_daily` with retention, step-conversion, reach and engagement counts and fold them from the Fivetran tables that already exist. A pure `diagnose()` function in `cs.insights.diagnosis` turns window sums plus an account baseline into per-tier verdicts. One new endpoint serves it; one server component renders it.

**Tech Stack:** Python 3.12 / FastAPI / asyncpg / dbmate migrations / pytest (backend); Next.js App Router / React / Tailwind v4 / vitest + testing-library (web). Lint: `ruff` (backend), `npm run lint` + `tsc --noEmit` (web).

**Spec:** `docs/superpowers/specs/2026-09-12-creative-diagnosis-panel-design.md`

## Global Constraints

- No new LLM calls of any kind. Verdict text is templated.
- No proxies: Context is always `not_measured`. Retention on non-video is `not_applicable`.
- Window = trailing 30 days ending today (UTC). Baseline = other creatives, same `ad_account_id`, same `format`, same window.
- Lift threshold `0.15`; frequency-rise threshold `0.30`; floors: attention 1,000 impressions, retention 100 plays, conversion 100 events on each step's denominator, fatigue 1,000 impressions per window and 21 live days.
- New `metric_daily` columns are `NOT NULL DEFAULT 0`. New `MetricRow` fields are appended **after** `raw` with default `0` so existing positional constructors keep working.
- Backend tests run against `DATABASE_URL_TEST` (Supabase, see `.env`); `dbmate` is at `~/.local/bin/dbmate`. Run backend tests with `uv run pytest -q <path>`; lint with `make lint`.
- Web tests: `cd web && npm test -- <file>`; types: `cd web && npx tsc --noEmit`; lint: `cd web && npm run lint`.
- Commit after every task with the attribution trailer:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01QXg6915bNSsyjaShpMX3Co
  ```
- The working tree already carries an uncommitted change to `src/cs/ingest/fivetran/insights.py` and `tests/ingest/test_fivetran_insights.py` (folds `video_view` into `video_3s_views`). Task 2 subsumes it; keep it, do not revert it.

---

## File map

| File | Responsibility |
|---|---|
| `db/migrations/20260912000001_diagnosis_metrics.sql` | New `metric_daily` columns |
| `src/cs/connectors/base.py` | `MetricRow` gains the new fields |
| `src/cs/core/repo/metrics.py` | Upsert writes the new columns |
| `src/cs/ingest/fivetran/schema.py` | Retention table names |
| `src/cs/ingest/fivetran/fixtures.py` | Test schema gains `reach` and retention tables |
| `src/cs/ingest/fivetran/insights.py` | Folds steps, engagement, reach, retention |
| `src/cs/insights/diagnosis.py` | Pure tier model + verdict + serialization |
| `src/cs/core/repo/creatives.py` | Window-sum, baseline, first-day queries; duration on `get_creative` |
| `src/cs/api/routers/creatives.py` | `GET /creatives/{id}/diagnosis` |
| `web/src/lib/types.ts`, `web/src/lib/api.ts` | Types + client |
| `web/src/components/diagnosis-panel.tsx` | The panel |
| `web/src/app/(app)/creatives/[id]/page.tsx` | Mounts the panel |

---

### Task 1: Widen `metric_daily` and `MetricRow`

**Files:**
- Create: `db/migrations/20260912000001_diagnosis_metrics.sql`
- Modify: `src/cs/connectors/base.py:37-51` (`MetricRow`)
- Modify: `src/cs/core/repo/metrics.py` (upsert SQL + params)
- Test: `tests/ingest/test_repo.py`

**Interfaces:**
- Produces: `MetricRow` fields `reach, video_plays, video_p25, video_p75, video_p95, video_watch_seconds, landing_page_views, content_views, add_to_carts, checkouts, purchases, post_reactions, comments, saves` (all default `0`; `video_watch_seconds: Decimal`). `upsert_metrics` persists them.

- [ ] **Step 1: Write the migration**

```sql
-- migrate:up
ALTER TABLE metric_daily
  ADD COLUMN reach BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN video_plays BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN video_p25 BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN video_p75 BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN video_p95 BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN video_watch_seconds NUMERIC(14,4) NOT NULL DEFAULT 0,
  ADD COLUMN landing_page_views BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN content_views BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN add_to_carts BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN checkouts BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN purchases BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN post_reactions BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN comments BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN saves BIGINT NOT NULL DEFAULT 0;

-- migrate:down
ALTER TABLE metric_daily
  DROP COLUMN reach, DROP COLUMN video_plays, DROP COLUMN video_p25, DROP COLUMN video_p75,
  DROP COLUMN video_p95, DROP COLUMN video_watch_seconds, DROP COLUMN landing_page_views,
  DROP COLUMN content_views, DROP COLUMN add_to_carts, DROP COLUMN checkouts,
  DROP COLUMN purchases, DROP COLUMN post_reactions, DROP COLUMN comments, DROP COLUMN saves;
```

- [ ] **Step 2: Write the failing test** (append to `tests/ingest/test_repo.py`; check its existing imports first and reuse `db`, `org_id` fixtures)

```python
from datetime import date
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import repo


async def test_upsert_metrics_persists_diagnosis_columns(db, org_id):
    acct = await repo.upsert_ad_account(db, org_id, "77", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db, org_id, acct,
        PlatformCreative("cr", "video", "H", "P", None, None, "u", None, None, {}), "raw",
    )
    await repo.upsert_ad(db, org_id, acct, PlatformAd("ad77", "A", "ACTIVE", None, None, "cr"), cid)
    row = MetricRow(
        "ad77", date(2026, 9, 1), "all", Decimal("10"), 1000, 20, 20, Decimal("2"),
        Decimal("100"), 300, 90, 50, 30, {},
        reach=700, video_plays=400, video_p25=280, video_p75=60, video_p95=40,
        video_watch_seconds=Decimal("2840.5"), landing_page_views=15, content_views=9,
        add_to_carts=4, checkouts=3, purchases=2, post_reactions=12, comments=3, saves=5,
    )
    written, skipped = await repo.upsert_metrics(db, org_id, acct, [row])
    assert (written, skipped) == (1, 0)
    r = await db.fetchrow("SELECT * FROM metric_daily WHERE creative_id=$1", cid)
    assert r["reach"] == 700 and r["video_plays"] == 400
    assert (r["video_p25"], r["video_p50"], r["video_p75"], r["video_p95"], r["video_p100"]) == (280, 50, 60, 40, 30)
    assert r["video_watch_seconds"] == Decimal("2840.5")
    assert (r["landing_page_views"], r["content_views"], r["add_to_carts"], r["checkouts"], r["purchases"]) == (15, 9, 4, 3, 2)
    assert (r["post_reactions"], r["comments"], r["saves"]) == (12, 3, 5)

    # Re-upsert with changed values must overwrite, not keep the old row.
    row2 = MetricRow(
        "ad77", date(2026, 9, 1), "all", Decimal("10"), 1000, 20, 20, Decimal("2"),
        Decimal("100"), 300, 90, 50, 30, {}, reach=900, purchases=6,
    )
    await repo.upsert_metrics(db, org_id, acct, [row2])
    r = await db.fetchrow("SELECT reach, purchases, video_plays FROM metric_daily WHERE creative_id=$1", cid)
    assert (r["reach"], r["purchases"], r["video_plays"]) == (900, 6, 0)


async def test_metric_row_positional_construction_still_works():
    r = MetricRow("a", date(2026, 9, 1), "all", Decimal(1), 1, 1, 1, Decimal(0), Decimal(0), 0, 0, 0, 0, {})
    assert r.reach == 0 and r.purchases == 0 and r.video_watch_seconds == 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest -q tests/ingest/test_repo.py -k "diagnosis_columns or positional"`
Expected: FAIL — `TypeError: MetricRow.__init__() got an unexpected keyword argument 'reach'`

- [ ] **Step 4: Extend `MetricRow`** (`src/cs/connectors/base.py`), appending after `raw`:

```python
@dataclass
class MetricRow:
    platform_ad_id: str
    day: date
    placement: str
    spend: Decimal
    impressions: int
    clicks: int
    link_clicks: int
    conversions: Decimal
    conversion_value: Decimal
    video_3s_views: int
    video_thruplay: int
    video_p50: int
    video_p100: int
    raw: dict = field(default_factory=dict)
    # Diagnosis-panel columns (spec 2026-09-12). Appended after `raw` so positional
    # constructors in older code and tests keep working.
    reach: int = 0
    video_plays: int = 0
    video_p25: int = 0
    video_p75: int = 0
    video_p95: int = 0
    video_watch_seconds: Decimal = Decimal(0)
    landing_page_views: int = 0
    content_views: int = 0
    add_to_carts: int = 0
    checkouts: int = 0
    purchases: int = 0
    post_reactions: int = 0
    comments: int = 0
    saves: int = 0
```

- [ ] **Step 5: Extend the upsert** (`src/cs/core/repo/metrics.py`). Replace `_UPSERT_METRIC_SQL` and the `params.append(...)` tuple:

```python
_UPSERT_METRIC_SQL = """
    INSERT INTO metric_daily (org_id, ad_account_id, creative_id, ad_id, day, placement,
        spend, impressions, clicks, link_clicks, conversions, conversion_value,
        video_3s_views, video_thruplay, video_p50, video_p100, raw,
        reach, video_plays, video_p25, video_p75, video_p95, video_watch_seconds,
        landing_page_views, content_views, add_to_carts, checkouts, purchases,
        post_reactions, comments, saves)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
            $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31)
    ON CONFLICT (creative_id, ad_id, day, placement) DO UPDATE SET
        spend=EXCLUDED.spend, impressions=EXCLUDED.impressions, clicks=EXCLUDED.clicks,
        link_clicks=EXCLUDED.link_clicks, conversions=EXCLUDED.conversions,
        conversion_value=EXCLUDED.conversion_value,
        video_3s_views=EXCLUDED.video_3s_views, video_thruplay=EXCLUDED.video_thruplay,
        video_p50=EXCLUDED.video_p50, video_p100=EXCLUDED.video_p100, raw=EXCLUDED.raw,
        reach=EXCLUDED.reach, video_plays=EXCLUDED.video_plays, video_p25=EXCLUDED.video_p25,
        video_p75=EXCLUDED.video_p75, video_p95=EXCLUDED.video_p95,
        video_watch_seconds=EXCLUDED.video_watch_seconds,
        landing_page_views=EXCLUDED.landing_page_views, content_views=EXCLUDED.content_views,
        add_to_carts=EXCLUDED.add_to_carts, checkouts=EXCLUDED.checkouts,
        purchases=EXCLUDED.purchases, post_reactions=EXCLUDED.post_reactions,
        comments=EXCLUDED.comments, saves=EXCLUDED.saves
"""
```

and in `upsert_metrics`, the tuple becomes:

```python
        params.append(
            (
                org_id, ad_account_id, a["creative_id"], a["id"], r.day, r.placement,
                r.spend, r.impressions, r.clicks, r.link_clicks, r.conversions,
                r.conversion_value, r.video_3s_views, r.video_thruplay, r.video_p50,
                r.video_p100, r.raw,
                r.reach, r.video_plays, r.video_p25, r.video_p75, r.video_p95,
                r.video_watch_seconds, r.landing_page_views, r.content_views,
                r.add_to_carts, r.checkouts, r.purchases, r.post_reactions, r.comments,
                r.saves,
            )
        )
```

- [ ] **Step 6: Run the tests** (the `settings` fixture applies migrations automatically)

Run: `uv run pytest -q tests/ingest/test_repo.py`
Expected: PASS

- [ ] **Step 7: Run the whole backend suite and lint** to catch anything else that constructs `MetricRow`

Run: `uv run pytest -q && make lint`
Expected: PASS, lint clean

- [ ] **Step 8: Commit**

```bash
git add db/migrations/20260912000001_diagnosis_metrics.sql src/cs/connectors/base.py src/cs/core/repo/metrics.py tests/ingest/test_repo.py
git commit -m "feat(metrics): widen metric_daily with retention, step-conversion, reach and engagement counts"
```

---

### Task 2: Fold the new columns from Fivetran

**Files:**
- Modify: `src/cs/ingest/fivetran/schema.py`
- Modify: `src/cs/ingest/fivetran/fixtures.py` (`create_fixture_schema`, `insert_daily_insight`)
- Modify: `src/cs/ingest/fivetran/insights.py`
- Test: `tests/ingest/test_fivetran_insights.py`

**Interfaces:**
- Consumes: `MetricRow` keyword fields from Task 1.
- Produces: `sync_insights(...)` returns `{"written", "skipped", "retention_tables": bool}`. `fixtures.insert_retention(db, schema, *, ad_id, day, plays, p25, p50, p75, p95, p100, thruplay, avg_time_watched)` for tests.

- [ ] **Step 1: Add table names** (`src/cs/ingest/fivetran/schema.py`, append)

```python
VIDEO_RETENTION_AD = "video_retention_ad"

# Fivetran unpivots each list<AdsActionStats> field of the custom report into its own
# child table named <report>_<field>. Maps child-table suffix -> metric_daily column.
VIDEO_RETENTION_CHILD_COLUMNS = {
    "video_play_actions": "video_plays",
    "video_p_25_watched_actions": "video_p25",
    "video_p_50_watched_actions": "video_p50",
    "video_p_75_watched_actions": "video_p75",
    "video_p_95_watched_actions": "video_p95",
    "video_p_100_watched_actions": "video_p100",
    "video_thruplay_watched_actions": "video_thruplay",
    "video_avg_time_watched_actions": "video_avg_time_watched",  # seconds, per-day average
}
```

- [ ] **Step 2: Extend the fixture schema** (`src/cs/ingest/fivetran/fixtures.py`)

Change the `basic_ad` create to include `reach`, add retention tables, and extend `insert_daily_insight`:

```python
from cs.ingest.fivetran.schema import (
    AD_HISTORY, AD_VIDEO_HISTORY, BASIC_AD, CAMPAIGN_HISTORY, CREATIVE_HISTORY,
    INSIGHTS_ACTION_TABLES, VIDEO_RETENTION_AD, VIDEO_RETENTION_CHILD_COLUMNS,
)

# inside create_fixture_schema, replace the BASIC_AD create:
    await db.execute(
        f'CREATE TABLE "{schema}".{BASIC_AD} (ad_id TEXT, date DATE, '
        f"spend NUMERIC, impressions INT, inline_link_clicks INT, reach INT)"
    )
    for t in INSIGHTS_ACTION_TABLES:
        await db.execute(
            f'CREATE TABLE "{schema}".{t} '
            f"(ad_id TEXT, date DATE, action_type TEXT, index INT, value NUMERIC)"
        )
    if with_retention:
        await create_retention_tables(db, schema)


async def create_retention_tables(db: Database, schema: str) -> None:
    await db.execute(
        f'CREATE TABLE "{schema}".{VIDEO_RETENTION_AD} (ad_id TEXT, date DATE, '
        f"account_id BIGINT, adset_id BIGINT, campaign_id BIGINT, ad_name TEXT)"
    )
    for suffix in VIDEO_RETENTION_CHILD_COLUMNS:
        await db.execute(
            f'CREATE TABLE "{schema}".{VIDEO_RETENTION_AD}_{suffix} '
            f"(ad_id TEXT, date DATE, index INT, value DOUBLE PRECISION, action_type TEXT)"
        )
```

Give `create_fixture_schema` the signature `async def create_fixture_schema(db, schema, *, with_retention: bool = True)`.

Extend `insert_daily_insight` with `reach: int = 0` and insert it as the sixth value:

```python
async def insert_daily_insight(db, schema, *, ad_id, day, spend, impressions, link_clicks, reach: int = 0) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".basic_ad VALUES ($1, $2, $3, $4, $5, $6)',
        ad_id, date.fromisoformat(day), spend, impressions, link_clicks, reach,
    )
```

Add:

```python
async def insert_retention(
    db: Database, schema: str, *, ad_id: str, day: str, plays: float, p25: float, p50: float,
    p75: float, p95: float, p100: float, thruplay: float, avg_time_watched: float,
) -> None:
    d = date.fromisoformat(day)
    await db.execute(
        f'INSERT INTO "{schema}".{VIDEO_RETENTION_AD} VALUES ($1, $2, 1, 1, 1, $3)', ad_id, d, ad_id
    )
    values = {
        "video_play_actions": plays, "video_p_25_watched_actions": p25,
        "video_p_50_watched_actions": p50, "video_p_75_watched_actions": p75,
        "video_p_95_watched_actions": p95, "video_p_100_watched_actions": p100,
        "video_thruplay_watched_actions": thruplay,
        "video_avg_time_watched_actions": avg_time_watched,
    }
    for suffix, v in values.items():
        await db.execute(
            f'INSERT INTO "{schema}".{VIDEO_RETENTION_AD}_{suffix} VALUES ($1, $2, 0, $3, $4)',
            ad_id, d, v, "video_view",
        )
```

Search the repo for other callers of `create_fixture_schema` / `insert_daily_insight` (`grep -rn "create_fixture_schema\|insert_daily_insight" src tests scripts`) and confirm they still work with the new defaults (they should: both additions are keyword-defaulted).

- [ ] **Step 3: Write the failing tests** (`tests/ingest/test_fivetran_insights.py`). Replace `_seed` and the first test, and add two tests:

```python
from cs.ingest.fivetran.fixtures import (
    create_fixture_schema, insert_daily_insight, insert_retention,
)


async def _seed(db, org_id, *, with_retention=True):
    await create_fixture_schema(db, SCHEMA, with_retention=with_retention)
    await insert_daily_insight(
        db, SCHEMA, ad_id="ad1", day="2026-09-01", spend=10.0, impressions=1000,
        link_clicks=20, reach=800,
    )
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".basic_ad_actions VALUES
        ('ad1', '2026-09-01', 'omni_purchase', 0, 2),
        ('ad1', '2026-09-01', 'purchase', 0, 2),
        ('ad1', '2026-09-01', 'video_view', 0, 30),
        ('ad1', '2026-09-01', 'omni_landing_page_view', 0, 15),
        ('ad1', '2026-09-01', 'landing_page_view', 0, 15),
        ('ad1', '2026-09-01', 'view_content', 0, 9),
        ('ad1', '2026-09-01', 'omni_add_to_cart', 0, 4),
        ('ad1', '2026-09-01', 'add_to_cart', 0, 3),
        ('ad1', '2026-09-01', 'initiate_checkout', 0, 3),
        ('ad1', '2026-09-01', 'post_reaction', 0, 12),
        ('ad1', '2026-09-01', 'comment', 0, 3),
        ('ad1', '2026-09-01', 'onsite_conversion.post_save', 0, 5)
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".basic_ad_action_values VALUES
        ('ad1', '2026-09-01', 'omni_purchase', 0, 100)
    """)
    if with_retention:
        await insert_retention(
            db, SCHEMA, ad_id="ad1", day="2026-09-01", plays=400, p25=280, p50=120,
            p75=60, p95=40, p100=30, thruplay=90, avg_time_watched=7.1,
        )

    acct = await repo.upsert_ad_account(db, org_id, "999", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db, org_id, acct,
        PlatformCreative("cr1", "video", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
        "",
    )
    await repo.upsert_ad(db, org_id, acct, PlatformAd("ad1", "A", "ACTIVE", None, None, "cr1"), cid)
    return acct


async def test_sync_insights_folds_action_breakdowns_into_metric_daily(db, blobs, settings, org_id):
    acct = await _seed(db, org_id)
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_insights.sync_insights(ctx, org_id, connection)
    assert summary == {"written": 1, "skipped": 0, "retention_tables": True}

    rows = await db.fetch("SELECT * FROM metric_daily WHERE org_id=$1", org_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["ad_account_id"] == acct
    assert row["placement"] == "all"
    assert row["spend"] == 10 and row["impressions"] == 1000 and row["clicks"] == 20
    assert row["reach"] == 800
    assert row["conversions"] == 2 and row["conversion_value"] == 100
    # video_view is Meta's 3-second-view action_type -> hook-rate numerator.
    assert row["video_3s_views"] == 30
    # Step conversions: omni_* wins when present, plain action_type otherwise.
    assert row["landing_page_views"] == 15 and row["content_views"] == 9
    assert row["add_to_carts"] == 4 and row["checkouts"] == 3 and row["purchases"] == 2
    assert row["post_reactions"] == 12 and row["comments"] == 3 and row["saves"] == 5
    # Retention from the video_retention_ad custom report child tables.
    assert row["video_plays"] == 400
    assert (row["video_p25"], row["video_p50"], row["video_p75"], row["video_p95"], row["video_p100"]) == (280, 120, 60, 40, 30)
    assert row["video_thruplay"] == 90
    # avg_time_watched is a per-day average; stored as avg * plays so it sums across days.
    assert float(row["video_watch_seconds"]) == pytest.approx(7.1 * 400)


async def test_sync_insights_without_retention_report_writes_zeros(db, blobs, settings, org_id):
    """Org 1's Fivetran schema has no video_retention_ad tables at all. The sync must
    still land everything else and say so in the summary, never raise."""
    await _seed(db, org_id, with_retention=False)
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_insights.sync_insights(ctx, org_id, connection)
    assert summary == {"written": 1, "skipped": 0, "retention_tables": False}
    row = await db.fetchrow("SELECT * FROM metric_daily WHERE org_id=$1", org_id)
    assert row["video_3s_views"] == 30 and row["purchases"] == 2
    assert row["video_plays"] == 0 and row["video_p25"] == 0 and row["video_thruplay"] == 0
    assert row["video_watch_seconds"] == 0
```

Add `import pytest` at the top if it is not already imported. Keep `test_bigint_ad_id_still_joins_against_our_text_ad_table` as is, but its hand-built `basic_ad` table has no `reach` column — the implementation must tolerate that (Step 5 handles it).

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest -q tests/ingest/test_fivetran_insights.py`
Expected: FAIL — `ImportError: cannot import name 'insert_retention'` or assertion failures on the new columns.

- [ ] **Step 5: Implement the fold** (`src/cs/ingest/fivetran/insights.py`). Full replacement of the module body after the imports:

```python
from cs.ingest.fivetran.schema import (
    BASIC_AD, BASIC_AD_ACTION_VALUES, BASIC_AD_ACTIONS, VIDEO_RETENTION_AD,
    VIDEO_RETENTION_CHILD_COLUMNS,
)

CONVERSION_ACTION_TYPES = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "lead",
    "complete_registration",
    "onsite_conversion.lead_grouped",
)

# Meta's 3-second video view, the hook-rate numerator. Lands in basic_ad_actions like any
# other action_type. ThruPlay and p25-p100 are separate Graph API fields that only reach
# us through the video_retention_ad custom report (see _retention_totals).
VIDEO_3S_ACTION_TYPES = ("video_view",)

# Step-conversion and engagement action_types. First match wins; omni_* is preferred
# because Meta dedups it across web/app/onsite channels.
LANDING_PAGE_VIEW_TYPES = ("omni_landing_page_view", "landing_page_view")
CONTENT_VIEW_TYPES = ("omni_view_content", "view_content", "offsite_conversion.fb_pixel_view_content")
ADD_TO_CART_TYPES = ("omni_add_to_cart", "add_to_cart", "offsite_conversion.fb_pixel_add_to_cart")
CHECKOUT_TYPES = ("omni_initiated_checkout", "initiate_checkout", "offsite_conversion.fb_pixel_initiate_checkout")
PURCHASE_TYPES = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
POST_REACTION_TYPES = ("post_reaction",)
COMMENT_TYPES = ("comment",)
SAVE_TYPES = ("onsite_conversion.post_save",)


def _q(schema: str, table: str) -> str:
    return f'"{schema}".{table}'


def _pid(value: object) -> str | None:
    return None if value is None else str(value)


async def _action_totals(db, schema: str, table: str) -> dict[tuple[str, date], dict[str, Decimal]]:
    rows = await db.fetch(f"""
        SELECT ad_id, date, action_type, sum(value) AS total
        FROM {_q(schema, table)} GROUP BY ad_id, date, action_type
    """)
    out: dict[tuple[str, date], dict[str, Decimal]] = {}
    for r in rows:
        bucket = out.setdefault((_pid(r["ad_id"]), r["date"]), {})
        bucket[r["action_type"]] = Decimal(str(r["total"]))
    return out


async def _table_exists(db, schema: str, table: str) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=$1 AND table_name=$2",
            schema,
            table,
        )
    )


async def _column_exists(db, schema: str, table: str, column: str) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema=$1 AND table_name=$2 AND column_name=$3",
            schema,
            table,
            column,
        )
    )


async def _retention_totals(db, schema: str) -> dict[tuple[str, date], dict[str, float]] | None:
    """Per (ad_id, day) totals from the video_retention_ad child tables, or None when the
    custom report has not been configured for this connector (org 1 today)."""
    if not await _table_exists(db, schema, VIDEO_RETENTION_AD):
        return None
    out: dict[tuple[str, date], dict[str, float]] = {}
    for suffix, column in VIDEO_RETENTION_CHILD_COLUMNS.items():
        table = f"{VIDEO_RETENTION_AD}_{suffix}"
        if not await _table_exists(db, schema, table):
            continue
        rows = await db.fetch(f"""
            SELECT ad_id, date, sum(value) AS total FROM {_q(schema, table)}
            WHERE action_type = 'video_view' GROUP BY ad_id, date
        """)
        for r in rows:
            out.setdefault((_pid(r["ad_id"]), r["date"]), {})[column] = float(r["total"] or 0)
    return out


def _first_matching(by_type: dict[str, Decimal] | None, types: tuple[str, ...]) -> Decimal:
    if not by_type:
        return Decimal(0)
    for t in types:
        if t in by_type:
            return by_type[t]
    return Decimal(0)


def _count(by_type: dict[str, Decimal] | None, types: tuple[str, ...]) -> int:
    return int(_first_matching(by_type, types))


async def sync_insights(
    ctx: JobContext, org_id: UUID, connection: asyncpg.Record
) -> dict[str, Any]:
    schema = connection["schema_name"]
    db = ctx.db

    actions = await _action_totals(db, schema, BASIC_AD_ACTIONS)
    action_values = await _action_totals(db, schema, BASIC_AD_ACTION_VALUES)
    retention = await _retention_totals(db, schema)
    has_reach = await _column_exists(db, schema, BASIC_AD, "reach")

    base_rows = await db.fetch(f"SELECT * FROM {_q(schema, BASIC_AD)}")
    metric_rows: list[MetricRow] = []
    for r in base_rows:
        key = (_pid(r["ad_id"]), r["date"])
        link_clicks = int(r["inline_link_clicks"] or 0)
        acts = actions.get(key)
        ret = (retention or {}).get(key, {})
        plays = int(ret.get("video_plays", 0))
        metric_rows.append(
            MetricRow(
                platform_ad_id=_pid(r["ad_id"]),
                day=r["date"],
                placement="all",
                spend=Decimal(str(r["spend"] or 0)),
                impressions=int(r["impressions"] or 0),
                clicks=link_clicks,
                link_clicks=link_clicks,
                conversions=_first_matching(acts, CONVERSION_ACTION_TYPES),
                conversion_value=_first_matching(action_values.get(key), CONVERSION_ACTION_TYPES),
                video_3s_views=_count(acts, VIDEO_3S_ACTION_TYPES),
                video_thruplay=int(ret.get("video_thruplay", 0)),
                video_p50=int(ret.get("video_p50", 0)),
                video_p100=int(ret.get("video_p100", 0)),
                raw=dict(r),
                reach=int(r["reach"] or 0) if has_reach else 0,
                video_plays=plays,
                video_p25=int(ret.get("video_p25", 0)),
                video_p75=int(ret.get("video_p75", 0)),
                video_p95=int(ret.get("video_p95", 0)),
                # avg_time_watched is a per-day mean in seconds; multiply by plays so the
                # column sums across days (avg = sum(seconds) / sum(plays) downstream).
                video_watch_seconds=Decimal(str(round(ret.get("video_avg_time_watched", 0.0) * plays, 4))),
                landing_page_views=_count(acts, LANDING_PAGE_VIEW_TYPES),
                content_views=_count(acts, CONTENT_VIEW_TYPES),
                add_to_carts=_count(acts, ADD_TO_CART_TYPES),
                checkouts=_count(acts, CHECKOUT_TYPES),
                purchases=_count(acts, PURCHASE_TYPES),
                post_reactions=_count(acts, POST_REACTION_TYPES),
                comments=_count(acts, COMMENT_TYPES),
                saves=_count(acts, SAVE_TYPES),
            )
        )

    by_account: dict[UUID, list[MetricRow]] = {}
    ad_rows = await db.fetch(
        "SELECT id, ad_account_id, platform_ad_id FROM ad WHERE org_id=$1", org_id
    )
    account_by_platform_ad_id = {a["platform_ad_id"]: a["ad_account_id"] for a in ad_rows}
    for m in metric_rows:
        acct_id = account_by_platform_ad_id.get(m.platform_ad_id)
        if acct_id is not None:
            by_account.setdefault(acct_id, []).append(m)

    written = skipped = 0
    for acct_id, rows in by_account.items():
        w, s = await repo.upsert_metrics(db, org_id, acct_id, rows)
        written += w
        skipped += s
    skipped += len(metric_rows) - sum(len(v) for v in by_account.values())
    return {"written": written, "skipped": skipped, "retention_tables": retention is not None}
```

Note `raw=dict(r)` — `asyncpg.Record` values may include `Decimal`/`date`; this was already the case before, so JSON encoding is handled by the existing `Database` codec. Do not change it.

- [ ] **Step 6: Run tests**

Run: `uv run pytest -q tests/ingest/`
Expected: PASS (including the bigint test, whose `basic_ad` lacks `reach`)

- [ ] **Step 7: Check the sync job's log line still works.** `src/cs/ingest/jobs.py:96` logs `{**entities, **metrics}`; the new `retention_tables` key is a bool and logs fine. Run the job tests:

Run: `uv run pytest -q tests/ingest/test_jobs.py && make lint`
Expected: PASS, lint clean

- [ ] **Step 8: Commit**

```bash
git add src/cs/ingest/fivetran/schema.py src/cs/ingest/fivetran/fixtures.py src/cs/ingest/fivetran/insights.py tests/ingest/test_fivetran_insights.py
git commit -m "feat(ingest): fold retention curve, step conversions, reach and engagement from Fivetran"
```

---

### Task 3: Diagnosis model — sums, attention, retention

**Files:**
- Create: `src/cs/insights/diagnosis.py`
- Test: `tests/insights/test_diagnosis.py`

**Interfaces:**
- Produces: `Sums` (frozen dataclass, `Sums.from_record(mapping)`), `Metric`, `CurvePoint`, `Curve`, `Tier`, constants `LIFT_THRESHOLD=0.15`, `ATTENTION_FLOOR=1000`, `RETENTION_FLOOR=100`, functions `lift(observed, baseline) -> float | None`, `status_from_lift(lift) -> str`, `attention_tier(fmt, window, baseline) -> Tier`, `retention_tier(fmt, window, baseline, duration_ms) -> Tier`.

- [ ] **Step 1: Write the failing tests** (`tests/insights/test_diagnosis.py`)

```python
from decimal import Decimal

import pytest

from cs.insights.diagnosis import (
    Sums, attention_tier, lift, retention_tier, status_from_lift,
)


def sums(**kw) -> Sums:
    return Sums(**kw)


def test_lift_and_status():
    assert lift(0.38, 0.31) == pytest.approx(0.225806, abs=1e-6)
    assert lift(None, 0.3) is None and lift(0.3, None) is None and lift(0.3, 0.0) is None
    assert status_from_lift(0.2) == "strong"
    assert status_from_lift(0.15) == "strong"
    assert status_from_lift(0.1) == "on_par"
    assert status_from_lift(-0.15) == "weak"
    assert status_from_lift(None) == "not_enough_data"


def test_sums_from_record_coerces_nulls_and_decimals():
    s = Sums.from_record({"impressions": None, "spend": Decimal("1.5"), "video_watch_seconds": None})
    assert s.impressions == 0 and s.spend == Decimal("1.5") and s.video_watch_seconds == Decimal(0)


def test_attention_video_uses_hook_rate_and_reports_ctr_too():
    w = sums(impressions=10_000, video_3s_views=3_800, link_clicks=140)
    b = sums(impressions=100_000, video_3s_views=31_000, link_clicks=1_200)
    t = attention_tier("video", w, b)
    assert t.tier == 1 and t.key == "attention" and t.status == "strong"
    assert [m.key for m in t.metrics] == ["hook_rate", "link_ctr"]
    hook = t.metrics[0]
    assert hook.value == pytest.approx(0.38) and hook.baseline == pytest.approx(0.31)
    assert hook.lift == pytest.approx(0.2258, abs=1e-3)
    assert t.decision is None


def test_attention_image_uses_link_ctr_only_and_weak_gets_decision():
    w = sums(impressions=5_000, link_clicks=40)      # 0.8%
    b = sums(impressions=50_000, link_clicks=600)    # 1.2%
    t = attention_tier("image", w, b)
    assert [m.key for m in t.metrics] == ["link_ctr"]
    assert t.status == "weak"
    assert "stopping the scroll" in t.decision


def test_attention_floor_applies_to_creative_and_baseline():
    t = attention_tier("video", sums(impressions=900, video_3s_views=400), sums(impressions=50_000, video_3s_views=10_000))
    assert t.status == "not_enough_data" and "1,000" in t.reason
    t = attention_tier("video", sums(impressions=5_000, video_3s_views=400), sums(impressions=500, video_3s_views=100))
    assert t.status == "not_enough_data"


def test_retention_not_applicable_for_images_and_carousels():
    for fmt in ("image", "carousel"):
        t = retention_tier(fmt, sums(), sums(), None)
        assert t.tier == 2 and t.status == "not_applicable" and t.curve is None
        assert t.reason


def test_retention_curve_hold_rate_and_drop_segment():
    w = sums(video_3s_views=1_000, video_thruplay=210, video_plays=1_000, video_p25=710,
             video_p50=290, video_p75=180, video_p95=140, video_p100=130,
             video_watch_seconds=Decimal("7100"))
    b = sums(video_3s_views=10_000, video_thruplay=3_400, video_plays=10_000, video_p25=7_500,
             video_p50=5_400, video_p75=4_200, video_p95=3_500, video_p100=3_200,
             video_watch_seconds=Decimal("110000"))
    t = retention_tier("video", w, b, 24_000)
    assert t.status == "weak"
    hold = t.metrics[0]
    assert hold.key == "hold_rate" and hold.value == pytest.approx(0.21) and hold.baseline == pytest.approx(0.34)
    assert [p.p for p in t.curve.points] == [0, 25, 50, 75, 95, 100]
    assert t.curve.points[0].share == 1.0 and t.curve.points[0].baseline_share == 1.0
    assert t.curve.points[2].share == pytest.approx(0.29)
    # Biggest excess drop vs baseline is p25 -> p50 (0.42 drop vs 0.21 baseline drop).
    assert t.curve.drop_segment == (25, 50)
    assert t.curve.avg_watch_s == pytest.approx(7.1) and t.curve.duration_s == 24.0
    assert "between 25% and 50%" in t.decision and "6s" in t.decision and "12s" in t.decision


def test_retention_strong_has_no_drop_segment_and_floor_applies():
    w = sums(video_3s_views=1_000, video_thruplay=500, video_plays=1_000, video_p25=900,
             video_p50=700, video_p75=600, video_p95=550, video_p100=500)
    b = sums(video_3s_views=10_000, video_thruplay=3_400, video_plays=10_000, video_p25=7_500,
             video_p50=5_400, video_p75=4_200, video_p95=3_500, video_p100=3_200)
    t = retention_tier("video", w, b, None)
    assert t.status == "strong" and t.curve.drop_segment is None and t.decision is None
    assert t.curve.duration_s is None
    t = retention_tier("video", sums(video_plays=50, video_3s_views=60, video_thruplay=10), b, None)
    assert t.status == "not_enough_data" and "100" in t.reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/insights/test_diagnosis.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'cs.insights.diagnosis'`

- [ ] **Step 3: Write the module** (`src/cs/insights/diagnosis.py`)

```python
"""Per-creative diagnostic funnel: attention -> retention -> conversion -> fatigue -> context.

Pure functions over additive window sums. No I/O, no LLM. Spec:
docs/superpowers/specs/2026-09-12-creative-diagnosis-panel-design.md
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal
from typing import Any, Literal

LIFT_THRESHOLD = 0.15
FREQUENCY_RISE_THRESHOLD = 0.30
ATTENTION_FLOOR = 1_000
RETENTION_FLOOR = 100
STEP_FLOOR = 100
FATIGUE_FLOOR = 1_000
FATIGUE_MIN_DAYS = 21
CURVE_POINTS = (0, 25, 50, 75, 95, 100)

Status = Literal[
    "strong", "on_par", "weak", "not_applicable", "not_enough_data", "too_young", "not_measured"
]


@dataclass(frozen=True)
class Sums:
    """Additive totals over a window of metric_daily rows."""

    impressions: int = 0
    reach: int = 0
    link_clicks: int = 0
    spend: Decimal = Decimal(0)
    video_3s_views: int = 0
    video_plays: int = 0
    video_p25: int = 0
    video_p50: int = 0
    video_p75: int = 0
    video_p95: int = 0
    video_p100: int = 0
    video_thruplay: int = 0
    video_watch_seconds: Decimal = Decimal(0)
    landing_page_views: int = 0
    content_views: int = 0
    add_to_carts: int = 0
    checkouts: int = 0
    purchases: int = 0
    post_reactions: int = 0
    comments: int = 0
    saves: int = 0
    conversions: Decimal = Decimal(0)
    conversion_value: Decimal = Decimal(0)

    @classmethod
    def from_record(cls, r: Mapping[str, Any]) -> Sums:
        kw: dict[str, Any] = {}
        for f in fields(cls):
            v = r.get(f.name)
            if v is None:
                continue
            kw[f.name] = Decimal(str(v)) if f.type == "Decimal" else int(v)
        return cls(**kw)


@dataclass
class Metric:
    key: str
    label: str
    value: float | None
    baseline: float | None
    lift: float | None
    note: str


@dataclass
class CurvePoint:
    p: int
    share: float | None
    baseline_share: float | None


@dataclass
class Curve:
    points: list[CurvePoint]
    drop_segment: tuple[int, int] | None
    avg_watch_s: float | None
    duration_s: float | None


@dataclass
class Step:
    key: str
    label: str
    rate: float | None
    baseline_rate: float | None
    lift: float | None
    numerator: int
    denominator: int
    readable: bool


@dataclass
class Tier:
    tier: int
    key: str
    name: str
    question: str
    status: Status
    decision: str | None = None
    reason: str | None = None
    metrics: list[Metric] = field(default_factory=list)
    curve: Curve | None = None
    steps: list[Step] | None = None


def ratio(numerator: float | int | Decimal, denominator: float | int | Decimal) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def lift(observed: float | None, baseline: float | None) -> float | None:
    """Relative change vs baseline; higher is better for every metric passed here."""
    if observed is None or baseline is None or baseline == 0:
        return None
    return round((observed - baseline) / baseline, 6)


def status_from_lift(value: float | None) -> Status:
    if value is None:
        return "not_enough_data"
    if value >= LIFT_THRESHOLD:
        return "strong"
    if value <= -LIFT_THRESHOLD:
        return "weak"
    return "on_par"


def _metric(key: str, label: str, value: float | None, baseline: float | None, note: str, *, enough: bool) -> Metric:
    return Metric(key, label, value, baseline, lift(value, baseline) if enough else None, note)


# ---------------------------------------------------------------- tier 1: attention

def attention_tier(fmt: str, w: Sums, b: Sums) -> Tier:
    enough = w.impressions >= ATTENTION_FLOOR and b.impressions >= ATTENTION_FLOOR
    ctr = _metric(
        "link_ctr", "Link CTR", ratio(w.link_clicks, w.impressions), ratio(b.link_clicks, b.impressions),
        "link clicks ÷ impressions", enough=enough,
    )
    if fmt == "video":
        hook = _metric(
            "hook_rate", "Hook rate", ratio(w.video_3s_views, w.impressions),
            ratio(b.video_3s_views, b.impressions),
            f"3s views ÷ impressions · {w.impressions:,} impressions", enough=enough,
        )
        metrics, primary = [hook, ctr], hook
    else:
        metrics, primary = [ctr], ctr
    tier = Tier(1, "attention", "Attention", "Is the opening 1–3s stopping the scroll?", status_from_lift(primary.lift), metrics=metrics)
    if not enough:
        tier.reason = (
            f"Only {w.impressions:,} impressions in the window; attention needs {ATTENTION_FLOOR:,}."
            if w.impressions < ATTENTION_FLOOR
            else f"The account baseline has only {b.impressions:,} impressions; it needs {ATTENTION_FLOOR:,}."
        )
    elif tier.status == "weak":
        tier.decision = (
            "The first three seconds are not stopping the scroll. Recut the opening or swap the thumbnail; keep the rest."
            if fmt == "video"
            else "The image is not stopping the scroll. Try a different visual or headline; keep the offer."
        )
    else:
        tier.reason = "Nothing to change here. Keep this opening if you recut."
    return tier


# ---------------------------------------------------------------- tier 2: retention

_CURVE_ATTRS = {25: "video_p25", 50: "video_p50", 75: "video_p75", 95: "video_p95", 100: "video_p100"}


def _curve_points(w: Sums, b: Sums) -> list[CurvePoint]:
    pts = [CurvePoint(0, 1.0 if w.video_plays else None, 1.0 if b.video_plays else None)]
    for p, attr in _CURVE_ATTRS.items():
        pts.append(CurvePoint(p, ratio(getattr(w, attr), w.video_plays), ratio(getattr(b, attr), b.video_plays)))
    return pts


def _drop_segment(points: list[CurvePoint]) -> tuple[int, int] | None:
    best: tuple[float, tuple[int, int]] | None = None
    for a, c in zip(points, points[1:], strict=False):
        if None in (a.share, c.share, a.baseline_share, c.baseline_share):
            continue
        excess = (a.share - c.share) - (a.baseline_share - c.baseline_share)
        if best is None or excess > best[0]:
            best = (excess, (a.p, c.p))
    return best[1] if best else None


def retention_tier(fmt: str, w: Sums, b: Sums, duration_ms: int | None) -> Tier:
    tier = Tier(2, "retention", "Retention", "Where in the video does attention drop?", "not_applicable")
    if fmt != "video":
        noun = "Carousel" if fmt == "carousel" else "Image"
        tier.reason = f"{noun} creative. There is no watch-through curve to read; link CTR carries the scroll-stopping question."
        return tier
    enough = w.video_plays >= RETENTION_FLOOR and b.video_plays >= RETENTION_FLOOR
    hold = _metric(
        "hold_rate", "Hold rate", ratio(w.video_thruplay, w.video_3s_views), ratio(b.video_thruplay, b.video_3s_views),
        f"ThruPlay ÷ 3s views · {w.video_plays:,} plays", enough=enough,
    )
    points = _curve_points(w, b)
    duration_s = duration_ms / 1000 if duration_ms else None
    tier.metrics = [hold]
    tier.curve = Curve(points, None, ratio(w.video_watch_seconds, w.video_plays), duration_s)
    tier.status = status_from_lift(hold.lift)
    if not enough:
        tier.reason = f"Only {w.video_plays:,} plays in the window; retention needs {RETENTION_FLOOR:,}."
        return tier
    if tier.status == "weak":
        seg = _drop_segment(points)
        tier.curve.drop_segment = seg
        if seg:
            where = f"between {seg[0]}% and {seg[1]}%"
            if duration_s:
                where += f" ({seg[0] / 100 * duration_s:.0f}s–{seg[1] / 100 * duration_s:.0f}s)"
            tier.decision = f"Attention drops {where}. Fix pacing or the cut there, or move the CTA earlier."
        else:
            tier.decision = "Viewers leave earlier than the account average. Tighten the middle section or move the CTA earlier."
    else:
        tier.reason = "Viewers stay with this video at least as long as the account average."
    return tier
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/insights/test_diagnosis.py`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add src/cs/insights/diagnosis.py tests/insights/test_diagnosis.py
git commit -m "feat(insights): diagnosis model with attention and retention tiers"
```

---

### Task 4: Diagnosis model — conversion, fatigue, context, verdict, serialization

**Files:**
- Modify: `src/cs/insights/diagnosis.py`
- Test: `tests/insights/test_diagnosis.py`

**Interfaces:**
- Consumes: everything from Task 3.
- Produces: `conversion_tier(w, b) -> Tier`, `fatigue_tier(fmt, first14, trailing7, days_live) -> Tier`, `context_tier() -> Tier`, `Verdict`, `Diagnosis` with `to_dict()`, and `diagnose(*, fmt, window, baseline, baseline_creatives, first14, trailing7, days_live, duration_ms, window_start, window_end) -> Diagnosis`.

- [ ] **Step 1: Write the failing tests** (append to `tests/insights/test_diagnosis.py`)

```python
from datetime import date

from cs.insights.diagnosis import (
    context_tier, conversion_tier, diagnose, fatigue_tier,
)


def test_conversion_steps_readable_chain_and_on_par():
    w = sums(link_clicks=5_800, landing_page_views=3_700, content_views=1_780, add_to_carts=196,
             checkouts=80, purchases=30, spend=Decimal("1230"), conversions=Decimal(30))
    b = sums(link_clicks=58_000, landing_page_views=35_400, content_views=15_900, add_to_carts=1_900,
             checkouts=740, purchases=270, spend=Decimal("11880"), conversions=Decimal(270))
    t = conversion_tier(w, b)
    assert t.tier == 3 and t.status == "on_par"
    assert [s.key for s in t.steps] == ["landing_page", "content_view", "add_to_cart", "checkout", "purchase"]
    lp = t.steps[0]
    assert lp.rate == pytest.approx(3_700 / 5_800) and lp.baseline_rate == pytest.approx(35_400 / 58_000)
    assert lp.numerator == 3_700 and lp.denominator == 5_800 and lp.readable
    # checkouts=80 < 100 -> purchase step unreadable, everything before it readable.
    assert [s.readable for s in t.steps] == [True, True, True, True, False]
    assert t.steps[4].rate is None and t.steps[4].denominator == 80
    cpa = t.metrics[0]
    assert cpa.key == "cpa" and cpa.value == pytest.approx(41.0) and cpa.baseline == pytest.approx(44.0)
    assert cpa.lift == pytest.approx(0.068, abs=1e-3)  # lower CPA is better: sign flipped


def test_conversion_weak_names_worst_step():
    w = sums(link_clicks=5_000, landing_page_views=3_000, content_views=600, add_to_carts=300, checkouts=150, purchases=60)
    b = sums(link_clicks=50_000, landing_page_views=30_000, content_views=14_000, add_to_carts=3_000, checkouts=1_500, purchases=600)
    t = conversion_tier(w, b)
    assert t.status == "weak" and "Content view" in t.decision


def test_conversion_strong_requires_end_to_end_lift_and_not_enough_data():
    w = sums(link_clicks=5_000, landing_page_views=3_500, content_views=2_000, add_to_carts=600, checkouts=400, purchases=200)
    b = sums(link_clicks=50_000, landing_page_views=30_000, content_views=15_000, add_to_carts=3_000, checkouts=1_500, purchases=600)
    assert conversion_tier(w, b).status == "strong"
    t = conversion_tier(sums(link_clicks=40), b)
    assert t.status == "not_enough_data" and all(not s.readable for s in t.steps)


def test_fatigue_too_young_and_floors():
    t = fatigue_tier("video", None, None, 9)
    assert t.tier == 4 and t.status == "too_young" and "day 21" in t.reason
    assert fatigue_tier("video", None, None, None).status == "too_young"
    t = fatigue_tier("video", sums(impressions=500), sums(impressions=5_000), 30)
    assert t.status == "not_enough_data"


def test_fatigue_early_decay_and_stable():
    first = sums(impressions=20_000, reach=14_300, video_3s_views=8_200, link_clicks=300,
                 post_reactions=300, comments=60, saves=60)
    trailing = sums(impressions=10_000, reach=4_350, video_3s_views=3_300, link_clicks=120,
                    post_reactions=120, comments=30, saves=40)
    t = fatigue_tier("video", first, trailing, 40)
    assert t.status == "weak"
    freq, hook, eng = t.metrics
    assert freq.key == "frequency" and freq.value == pytest.approx(10_000 / 4_350) and freq.baseline == pytest.approx(20_000 / 14_300)
    assert freq.lift > 0.3
    assert hook.key == "hook_rate" and hook.lift < -0.15
    assert eng.key == "engagement_rate" and eng.value == pytest.approx(190 / 10_000)
    assert "Frequency is climbing" in t.decision
    # Frequency up but hook and engagement holding -> stable.
    steady = sums(impressions=10_000, reach=4_350, video_3s_views=4_100, link_clicks=150, post_reactions=150, comments=30, saves=30)
    assert fatigue_tier("video", first, steady, 40).status == "on_par"
    # Image creatives use link CTR as the attention trend.
    assert fatigue_tier("image", first, trailing, 40).metrics[1].key == "link_ctr"


def test_context_is_always_locked():
    t = context_tier()
    assert t.tier == 5 and t.status == "not_measured" and "not connected" in t.reason and t.metrics == []


def _video_window_weak_retention():
    w = sums(impressions=412_000, link_clicks=5_800, video_3s_views=156_000, video_thruplay=32_800,
             video_plays=156_000, video_p25=110_800, video_p50=45_200, video_p75=28_100,
             video_p95=21_800, video_p100=20_300, landing_page_views=3_700, content_views=1_780,
             add_to_carts=196, checkouts=80, purchases=30, spend=Decimal("1230"), conversions=Decimal(30))
    b = sums(impressions=4_000_000, link_clicks=48_000, video_3s_views=1_240_000, video_thruplay=421_600,
             video_plays=1_240_000, video_p25=930_000, video_p50=670_000, video_p75=520_000,
             video_p95=434_000, video_p100=397_000, landing_page_views=29_300, content_views=13_200,
             add_to_carts=1_580, checkouts=616, purchases=222, spend=Decimal("9800"), conversions=Decimal(222))
    return w, b


def test_diagnose_broken_layer_is_first_weak_tier():
    w, b = _video_window_weak_retention()
    d = diagnose(fmt="video", window=w, baseline=b, baseline_creatives=41, first14=None, trailing7=None,
                 days_live=12, duration_ms=24_000, window_start=date(2026, 8, 14), window_end=date(2026, 9, 12))
    assert [t.key for t in d.tiers] == ["attention", "retention", "conversion", "fatigue", "context"]
    assert d.tiers[0].status == "strong" and d.tiers[1].status == "weak" and d.tiers[3].status == "too_young"
    assert d.broken_tier == 2
    assert d.verdict.kind == "broken"
    assert d.verdict.headline.startswith("Broken layer: Retention.")
    assert "Attention is working" in d.verdict.detail
    out = d.to_dict()
    assert out["window_start"] == "2026-08-14" and out["format"] == "video" and out["baseline_creatives"] == 41
    assert out["tiers"][1]["curve"]["drop_segment"] == [25, 50]
    assert out["verdict"]["kind"] == "broken"
    import json
    json.dumps(out)  # every value must be JSON-native


def test_diagnose_healthy_and_too_early():
    w, b = _video_window_weak_retention()
    strong = Sums(**{**w.__dict__, "video_thruplay": 60_000, "video_p50": 90_000, "video_p75": 70_000})
    d = diagnose(fmt="video", window=strong, baseline=b, baseline_creatives=41, first14=None, trailing7=None,
                 days_live=12, duration_ms=None, window_start=date(2026, 8, 14), window_end=date(2026, 9, 12))
    assert d.broken_tier is None and d.verdict.kind == "healthy"
    d = diagnose(fmt="image", window=sums(impressions=640), baseline=b, baseline_creatives=41, first14=None,
                 trailing7=None, days_live=3, duration_ms=None, window_start=date(2026, 8, 14), window_end=date(2026, 9, 12))
    assert d.verdict.kind == "too_early" and "640" in d.verdict.detail and d.broken_tier is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/insights/test_diagnosis.py`
Expected: FAIL — `ImportError: cannot import name 'context_tier'`

- [ ] **Step 3: Append the remaining tiers, verdict and `diagnose`** to `src/cs/insights/diagnosis.py`

```python
# ---------------------------------------------------------------- tier 3: conversion

_STEPS = (
    ("landing_page", "Click → Landing page", "link_clicks", "landing_page_views"),
    ("content_view", "→ Content view", "landing_page_views", "content_views"),
    ("add_to_cart", "→ Add to cart", "content_views", "add_to_carts"),
    ("checkout", "→ Checkout", "add_to_carts", "checkouts"),
    ("purchase", "→ Purchase", "checkouts", "purchases"),
)


def conversion_tier(w: Sums, b: Sums) -> Tier:
    steps: list[Step] = []
    readable = True
    for key, label, den_attr, num_attr in _STEPS:
        den_w, den_b = getattr(w, den_attr), getattr(b, den_attr)
        readable = readable and den_w >= STEP_FLOOR and den_b >= STEP_FLOOR
        rate = ratio(getattr(w, num_attr), den_w) if readable else None
        base = ratio(getattr(b, num_attr), den_b) if readable else None
        steps.append(Step(key, label, rate, base, lift(rate, base) if readable else None, getattr(w, num_attr), den_w, readable))
    cpa_w, cpa_b = ratio(w.spend, w.conversions), ratio(b.spend, b.conversions)
    cpa_lift = lift(cpa_w, cpa_b)
    cpa = Metric("cpa", "CPA", cpa_w, cpa_b, -cpa_lift if cpa_lift is not None else None, "spend ÷ conversions")
    tier = Tier(3, "conversion", "Conversion", "Which funnel step under-delivers?", "on_par", metrics=[cpa], steps=steps)
    scored = [s for s in steps if s.readable and s.lift is not None]
    if not scored:
        tier.status = "not_enough_data"
        tier.reason = f"Steps show only once the previous step has {STEP_FLOOR} events. Nothing past the click is readable yet."
        return tier
    worst = min(scored, key=lambda s: s.lift)
    if worst.lift <= -LIFT_THRESHOLD:
        tier.status = "weak"
        tier.decision = f"People drop at {worst.label.lstrip('→ ')}. Check that the landing page delivers what the creative promises."
        return tier
    end_to_end = lift(ratio(w.purchases, w.link_clicks), ratio(b.purchases, b.link_clicks)) if steps[-1].readable else None
    tier.status = "strong" if end_to_end is not None and end_to_end >= LIFT_THRESHOLD else "on_par"
    tier.reason = (
        "More of the people who click end up buying than for the account's other creatives."
        if tier.status == "strong"
        else "The people who click convert like everyone else's. The landing page is not the problem."
    )
    return tier


# ---------------------------------------------------------------- tier 4: fatigue

def fatigue_tier(fmt: str, first14: Sums | None, trailing7: Sums | None, days_live: int | None) -> Tier:
    tier = Tier(4, "fatigue", "Fatigue", "Is this creative wearing out?", "too_young")
    if days_live is None:
        tier.reason = "No impressions yet."
        return tier
    if days_live < FATIGUE_MIN_DAYS or first14 is None or trailing7 is None:
        tier.reason = f"Live for {days_live} days. Fatigue compares the last 7 days against the first 14, so this reads from day {FATIGUE_MIN_DAYS}."
        return tier
    f, t = first14, trailing7
    enough = f.impressions >= FATIGUE_FLOOR and t.impressions >= FATIGUE_FLOOR
    freq = _metric("frequency", "Daily frequency", ratio(t.impressions, t.reach), ratio(f.impressions, f.reach), "last 7 days vs first 14", enough=enough)
    if fmt == "video":
        attn = _metric("hook_rate", "Hook rate trend", ratio(t.video_3s_views, t.impressions), ratio(f.video_3s_views, f.impressions), "same windows", enough=enough)
    else:
        attn = _metric("link_ctr", "Link CTR trend", ratio(t.link_clicks, t.impressions), ratio(f.link_clicks, f.impressions), "same windows", enough=enough)
    eng = _metric(
        "engagement_rate", "Engagement rate",
        ratio(t.post_reactions + t.comments + t.saves, t.impressions),
        ratio(f.post_reactions + f.comments + f.saves, f.impressions),
        "reactions, comments, saves ÷ impressions", enough=enough,
    )
    tier.metrics = [freq, attn, eng]
    if not enough:
        tier.status = "not_enough_data"
        tier.reason = f"Both windows need {FATIGUE_FLOOR:,} impressions to compare."
        return tier
    rising = freq.lift is not None and freq.lift >= FREQUENCY_RISE_THRESHOLD
    falling = (attn.lift is not None and attn.lift <= -LIFT_THRESHOLD) or (eng.lift is not None and eng.lift <= -LIFT_THRESHOLD)
    if rising and falling:
        tier.status = "weak"
        tier.decision = "Frequency is climbing faster than the hook is holding. Refresh the opening or rotate a variant in before efficiency drops."
    else:
        tier.status = "on_par"
        tier.reason = "No decay signal yet. Frequency and response are moving together."
    return tier


# ---------------------------------------------------------------- tier 5: context

def context_tier() -> Tier:
    return Tier(
        5, "context", "Context", "Does it need a native recut per placement?", "not_measured",
        reason="Placement breakdown (Reels vs Feed vs Stories) is not connected for this account yet. When it is, this row shows hook rate and CTR per placement.",
    )


# ---------------------------------------------------------------- verdict + assembly

@dataclass
class Verdict:
    kind: Literal["broken", "healthy", "too_early"]
    headline: str
    detail: str


@dataclass
class Diagnosis:
    window_start: date
    window_end: date
    format: str
    baseline_creatives: int
    broken_tier: int | None
    verdict: Verdict
    tiers: list[Tier]

    def to_dict(self) -> dict[str, Any]:
        return _plain(self)


_UNMEASURED = {"not_applicable", "not_enough_data", "too_young", "not_measured"}


def _verdict(tiers: list[Tier], window: Sums) -> tuple[int | None, Verdict]:
    weak = [t for t in tiers if t.status == "weak"]
    if weak:
        broken = weak[0]
        passed = [t.name for t in tiers if t.tier < broken.tier and t.status in ("strong", "on_par")]
        detail = ""
        if passed:
            detail = f"{' and '.join(passed)} {'are' if len(passed) > 1 else 'is'} working. "
        detail += "Fix this layer before touching anything else."
        return broken.tier, Verdict("broken", f"Broken layer: {broken.name}. {broken.decision}", detail)
    if all(t.status in _UNMEASURED for t in tiers):
        return None, Verdict(
            "too_early", "Too early to diagnose.",
            f"Only {window.impressions:,} impressions in the last 30 days. Attention needs {ATTENTION_FLOOR:,} to be worth reading.",
        )
    return None, Verdict(
        "healthy", "No broken layer. This creative is on par or better at every measured step.",
        "Scale it, and use it as the reference when you brief the next batch.",
    )


def diagnose(
    *,
    fmt: str,
    window: Sums,
    baseline: Sums,
    baseline_creatives: int,
    first14: Sums | None,
    trailing7: Sums | None,
    days_live: int | None,
    duration_ms: int | None,
    window_start: date,
    window_end: date,
) -> Diagnosis:
    tiers = [
        attention_tier(fmt, window, baseline),
        retention_tier(fmt, window, baseline, duration_ms),
        conversion_tier(window, baseline),
        fatigue_tier(fmt, first14, trailing7, days_live),
        context_tier(),
    ]
    broken, verdict = _verdict(tiers, window)
    return Diagnosis(window_start, window_end, fmt, baseline_creatives, broken, verdict, tiers)


def _plain(obj: Any) -> Any:
    """dataclasses -> dicts, dates -> ISO strings, tuples -> lists, Decimals -> floats."""
    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: _plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_plain(x) for x in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    return obj
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest -q tests/insights/test_diagnosis.py`
Expected: PASS. If `test_conversion_weak_names_worst_step` fails on the label, check `worst.label.lstrip('→ ')` yields `Content view`.

- [ ] **Step 5: Lint and commit**

```bash
make lint
git add src/cs/insights/diagnosis.py tests/insights/test_diagnosis.py
git commit -m "feat(insights): conversion, fatigue, context tiers and the diagnosis verdict"
```

---

### Task 5: Repo queries and `GET /creatives/{id}/diagnosis`

**Files:**
- Modify: `src/cs/core/repo/creatives.py` (add three queries; add `duration_ms` to `get_creative`)
- Modify: `src/cs/api/routers/creatives.py`
- Test: `tests/api/test_creative_diagnosis.py`

**Interfaces:**
- Consumes: `Sums.from_record`, `diagnose`, `FATIGUE_MIN_DAYS` from `cs.insights.diagnosis`.
- Produces: `repo.creative_window_sums(db, org_id, creative_id, since, until) -> asyncpg.Record`; `repo.baseline_window_sums(db, org_id, ad_account_id, fmt, exclude_creative_id, since, until) -> asyncpg.Record` (sums + `creatives` count); `repo.creative_first_metric_day(db, org_id, creative_id) -> date | None`; `get_creative` row gains `primary_asset_duration_ms`.

- [ ] **Step 1: Write the failing test** (`tests/api/test_creative_diagnosis.py`)

```python
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import repo
from tests.conftest import dev_headers

H = dev_headers(org="org_diag")
H_OTHER = dev_headers(org="org_diag_other")


def _row(ad: str, day: date, *, imp: int, views3: int, plays: int, thru: int, p25: int, p50: int, reach: int) -> MetricRow:
    return MetricRow(
        ad, day, "all", Decimal("10"), imp, imp // 100, imp // 100, Decimal(1), Decimal(40),
        views3, thru, p50, p50 // 2, {}, reach=reach, video_plays=plays, video_p25=p25,
        video_p75=p50 // 2, video_p95=p50 // 3, landing_page_views=imp // 150,
    )


async def _seed(db, client, *, today: date):
    me = (await client.get("/me", headers=H)).json()
    org_id = UUID(me["org_id"])
    acct = await repo.upsert_ad_account(db, org_id, "1", "Main", "USD")

    async def creative(pid: str, fmt: str) -> UUID:
        cid, _ = await repo.upsert_creative(
            db, org_id, acct, PlatformCreative(pid, fmt, "H " + pid, "P", None, None, "u", None, None, {}), "raw"
        )
        await repo.upsert_ad(db, org_id, acct, PlatformAd("ad_" + pid, pid, "ACTIVE", None, None, pid), cid)
        return cid

    target = await creative("t", "video")
    aid = await repo.insert_asset(db, org_id, target, "original_video", "o/t.mp4", "sha", "video/mp4", duration_ms=24_000)
    await repo.set_primary_asset(db, org_id, target, aid)
    peer_video = await creative("pv", "video")
    peer_image = await creative("pi", "image")

    rows: list[MetricRow] = []
    for i in range(30):
        d = today - timedelta(days=i)
        # target: weak retention (p50 share 0.29 vs peers 0.54), strong hook.
        rows.append(_row("ad_t", d, imp=20_000, views3=7_600, plays=7_600, thru=1_600, p25=5_400, p50=2_200, reach=8_000))
        rows.append(_row("ad_pv", d, imp=20_000, views3=6_200, plays=6_200, thru=2_100, p25=4_650, p50=3_350, reach=9_000))
        rows.append(_row("ad_pi", d, imp=20_000, views3=0, plays=0, thru=0, p25=0, p50=0, reach=9_000))
    # a row outside the window must not count
    rows.append(_row("ad_t", today - timedelta(days=40), imp=1_000_000, views3=0, plays=0, thru=0, p25=0, p50=0, reach=1))
    await repo.upsert_metrics(db, org_id, acct, rows)
    return org_id, target, peer_video, peer_image


async def test_diagnosis_endpoint_uses_same_format_baseline_and_excludes_self(client, db):
    today = date.today()
    _, target, _, _ = await _seed(db, client, today=today)
    r = await client.get(f"/creatives/{target}/diagnosis", headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["format"] == "video" and d["baseline_creatives"] == 1  # only the video peer
    assert d["window_end"] == today.isoformat() and d["window_start"] == (today - timedelta(days=29)).isoformat()
    tiers = {t["key"]: t for t in d["tiers"]}
    assert tiers["attention"]["status"] == "strong"
    assert tiers["retention"]["status"] == "weak"
    assert tiers["retention"]["curve"]["duration_s"] == 24.0
    assert tiers["retention"]["curve"]["drop_segment"] == [25, 50]
    assert tiers["context"]["status"] == "not_measured"
    assert d["broken_tier"] == 2 and d["verdict"]["kind"] == "broken"
    # 30 live days >= 21 -> fatigue is computed, not too_young
    assert tiers["fatigue"]["status"] in ("on_par", "weak")
    hook = next(m for m in tiers["attention"]["metrics"] if m["key"] == "hook_rate")
    assert abs(hook["value"] - 0.38) < 1e-6 and abs(hook["baseline"] - 0.31) < 1e-6


async def test_diagnosis_404s_for_unknown_and_cross_org(client, db):
    _, target, _, _ = await _seed(db, client, today=date.today())
    assert (await client.get(f"/creatives/{target}/diagnosis", headers=H_OTHER)).status_code == 404
    assert (await client.get("/creatives/00000000-0000-0000-0000-000000000000/diagnosis", headers=H)).status_code == 404


async def test_diagnosis_with_no_metrics_is_too_early(client, db):
    me = (await client.get("/me", headers=H)).json()
    org_id = UUID(me["org_id"])
    acct = await repo.upsert_ad_account(db, org_id, "1", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db, org_id, acct, PlatformCreative("x", "image", "H", "P", None, None, "u", None, None, {}), "raw"
    )
    d = (await client.get(f"/creatives/{cid}/diagnosis", headers=H)).json()
    assert d["verdict"]["kind"] == "too_early" and d["baseline_creatives"] == 0
    assert {t["status"] for t in d["tiers"]} == {"not_enough_data", "not_applicable", "too_young", "not_measured"}
```

Check `repo.insert_asset`'s signature first (`grep -n "def insert_asset" -A 12 src/cs/core/repo/assets.py`); if `duration_ms` is not a keyword parameter, pass it however that function accepts it (the `asset` table has the column). Adjust the call, not the test intent.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/api/test_creative_diagnosis.py`
Expected: FAIL — 404 on the diagnosis route (unknown path) or `AttributeError` on the repo functions.

- [ ] **Step 3: Add the repo queries** (`src/cs/core/repo/creatives.py`). Change `get_creative`'s SELECT to include duration:

```python
        SELECT c.*, a.path AS primary_asset_path, a.mime AS primary_asset_mime,
               a.duration_ms AS primary_asset_duration_ms
```

Append:

```python
_SUMS_SQL = """
    sum(impressions) AS impressions, sum(reach) AS reach, sum(link_clicks) AS link_clicks,
    sum(spend) AS spend, sum(video_3s_views) AS video_3s_views, sum(video_plays) AS video_plays,
    sum(video_p25) AS video_p25, sum(video_p50) AS video_p50, sum(video_p75) AS video_p75,
    sum(video_p95) AS video_p95, sum(video_p100) AS video_p100,
    sum(video_thruplay) AS video_thruplay, sum(video_watch_seconds) AS video_watch_seconds,
    sum(landing_page_views) AS landing_page_views, sum(content_views) AS content_views,
    sum(add_to_carts) AS add_to_carts, sum(checkouts) AS checkouts, sum(purchases) AS purchases,
    sum(post_reactions) AS post_reactions, sum(comments) AS comments, sum(saves) AS saves,
    sum(conversions) AS conversions, sum(conversion_value) AS conversion_value
"""


async def creative_window_sums(
    db: Database, org_id: UUID, creative_id: UUID, since: date, until: date
) -> asyncpg.Record:
    """Always returns one row; every sum is NULL when no rows fall in the window."""
    return await db.fetchrow(
        f"SELECT {_SUMS_SQL} FROM metric_daily "
        "WHERE org_id=$1 AND creative_id=$2 AND day BETWEEN $3 AND $4",
        org_id, creative_id, since, until,
    )


async def baseline_window_sums(
    db: Database, org_id: UUID, ad_account_id: UUID, fmt: str, exclude_creative_id: UUID,
    since: date, until: date,
) -> asyncpg.Record:
    """Pooled sums over every *other* creative of the same format in the account."""
    return await db.fetchrow(
        f"SELECT {_SUMS_SQL}, count(DISTINCT m.creative_id) AS creatives "
        "FROM metric_daily m JOIN creative c ON c.id = m.creative_id "
        "WHERE m.org_id=$1 AND m.ad_account_id=$2 AND c.format=$3 AND m.creative_id <> $4 "
        "AND m.day BETWEEN $5 AND $6",
        org_id, ad_account_id, fmt, exclude_creative_id, since, until,
    )


async def creative_first_metric_day(db: Database, org_id: UUID, creative_id: UUID) -> date | None:
    return await db.fetchval(
        "SELECT min(day) FROM metric_daily WHERE org_id=$1 AND creative_id=$2 AND impressions > 0",
        org_id, creative_id,
    )
```

`_SUMS_SQL` column names are unqualified; that is safe in `baseline_window_sums` because `creative` has none of those columns. `src/cs/core/repo/__init__.py` re-exports by name: add `baseline_window_sums`, `creative_first_metric_day`, `creative_window_sums` to the `from cs.core.repo.creatives import (...)` block and to `__all__` (alphabetical).

- [ ] **Step 4: Add the endpoint** (`src/cs/api/routers/creatives.py`). Add imports:

```python
from datetime import UTC, date, datetime, timedelta

from cs.insights.diagnosis import FATIGUE_MIN_DAYS, Sums, diagnose
```

Append:

```python
WINDOW_DAYS = 30


@router.get("/{creative_id}/diagnosis")
async def creative_diagnosis(
    creative_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    c = await repo.get_creative(db, auth.org_id, creative_id)
    if not c:
        raise HTTPException(404)
    until = datetime.now(UTC).date()
    since = until - timedelta(days=WINDOW_DAYS - 1)
    window = Sums.from_record(await repo.creative_window_sums(db, auth.org_id, creative_id, since, until))
    base = await repo.baseline_window_sums(
        db, auth.org_id, c["ad_account_id"], c["format"], creative_id, since, until
    )
    first_day = await repo.creative_first_metric_day(db, auth.org_id, creative_id)
    days_live = (until - first_day).days + 1 if first_day else None
    first14 = trailing7 = None
    if days_live is not None and days_live >= FATIGUE_MIN_DAYS:
        first14 = Sums.from_record(
            await repo.creative_window_sums(db, auth.org_id, creative_id, first_day, first_day + timedelta(days=13))
        )
        trailing7 = Sums.from_record(
            await repo.creative_window_sums(db, auth.org_id, creative_id, until - timedelta(days=6), until)
        )
    return diagnose(
        fmt=c["format"],
        window=window,
        baseline=Sums.from_record(base),
        baseline_creatives=int(base["creatives"] or 0),
        first14=first14,
        trailing7=trailing7,
        days_live=days_live,
        duration_ms=c["primary_asset_duration_ms"],
        window_start=since,
        window_end=until,
    ).to_dict()
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q tests/api/test_creative_diagnosis.py tests/api/test_accounts_and_creatives.py`
Expected: PASS. (`get_creative` now returns one more key; the existing creative test should still pass — if it asserts exact key sets, add `primary_asset_duration_ms` there.)

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q && make lint
git add src/cs/core/repo/creatives.py src/cs/api/routers/creatives.py tests/api/test_creative_diagnosis.py
git commit -m "feat(api): serve a per-creative diagnosis against the account baseline"
```

---

### Task 6: Web types, client, and the `DiagnosisPanel` component

**Files:**
- Modify: `web/src/lib/types.ts`, `web/src/lib/api.ts`
- Create: `web/src/components/diagnosis-panel.tsx`
- Test: `web/tests/components/diagnosis-panel.test.tsx`

**Interfaces:**
- Consumes: the JSON shape from Task 5.
- Produces: `DiagnosisPanel({ diagnosis })` (server-compatible, no hooks). `api.creativeDiagnosis(id)`.

- [ ] **Step 1: Add types** (`web/src/lib/types.ts`, append)

```ts
export type DiagnosisStatus = 'strong' | 'on_par' | 'weak' | 'not_applicable' | 'not_enough_data' | 'too_young' | 'not_measured';
export type DiagnosisMetric = { key: string; label: string; value: number | null; baseline: number | null; lift: number | null; note: string };
export type DiagnosisCurvePoint = { p: number; share: number | null; baseline_share: number | null };
export type DiagnosisCurve = { points: DiagnosisCurvePoint[]; drop_segment: [number, number] | null; avg_watch_s: number | null; duration_s: number | null };
export type DiagnosisStep = { key: string; label: string; rate: number | null; baseline_rate: number | null; lift: number | null; numerator: number; denominator: number; readable: boolean };
export type DiagnosisTier = {
  tier: number;
  key: 'attention' | 'retention' | 'conversion' | 'fatigue' | 'context';
  name: string;
  question: string;
  status: DiagnosisStatus;
  decision: string | null;
  reason: string | null;
  metrics: DiagnosisMetric[];
  curve: DiagnosisCurve | null;
  steps: DiagnosisStep[] | null;
};
export type Diagnosis = {
  window_start: string;
  window_end: string;
  format: 'image' | 'video' | 'carousel';
  baseline_creatives: number;
  broken_tier: number | null;
  verdict: { kind: 'broken' | 'healthy' | 'too_early'; headline: string; detail: string };
  tiers: DiagnosisTier[];
};
```

- [ ] **Step 2: Add the client call** (`web/src/lib/api.ts`, after `creativeMetrics`)

```ts
  creativeDiagnosis: (id: string) => apiFetch<import('./types').Diagnosis>(`/creatives/${id}/diagnosis`),
```

- [ ] **Step 3: Write the failing test** (`web/tests/components/diagnosis-panel.test.tsx`)

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DiagnosisPanel } from '@/components/diagnosis-panel';
import type { Diagnosis, DiagnosisTier } from '@/lib/types';

const tier = (over: Partial<DiagnosisTier> & Pick<DiagnosisTier, 'tier' | 'key' | 'name' | 'status'>): DiagnosisTier => ({
  question: 'Q?',
  decision: null,
  reason: null,
  metrics: [],
  curve: null,
  steps: null,
  ...over,
});

const broken: Diagnosis = {
  window_start: '2026-08-14',
  window_end: '2026-09-12',
  format: 'video',
  baseline_creatives: 41,
  broken_tier: 2,
  verdict: { kind: 'broken', headline: 'Broken layer: Retention. Attention drops between 25% and 50%.', detail: 'Attention is working.' },
  tiers: [
    tier({ tier: 1, key: 'attention', name: 'Attention', status: 'strong', reason: 'Nothing to change here.', metrics: [
      { key: 'hook_rate', label: 'Hook rate', value: 0.38, baseline: 0.31, lift: 0.2258, note: '3s views ÷ impressions' },
      { key: 'link_ctr', label: 'Link CTR', value: 0.014, baseline: 0.012, lift: 0.1667, note: 'link clicks ÷ impressions' },
    ] }),
    tier({ tier: 2, key: 'retention', name: 'Retention', status: 'weak', decision: 'Attention drops between 25% and 50%.', metrics: [
      { key: 'hold_rate', label: 'Hold rate', value: 0.21, baseline: 0.34, lift: -0.38, note: 'ThruPlay ÷ 3s views' },
    ], curve: { points: [
      { p: 0, share: 1, baseline_share: 1 }, { p: 25, share: 0.71, baseline_share: 0.75 }, { p: 50, share: 0.29, baseline_share: 0.54 },
      { p: 75, share: 0.18, baseline_share: 0.42 }, { p: 95, share: 0.14, baseline_share: 0.35 }, { p: 100, share: 0.13, baseline_share: 0.32 },
    ], drop_segment: [25, 50], avg_watch_s: 7.1, duration_s: 24 } }),
    tier({ tier: 3, key: 'conversion', name: 'Conversion', status: 'on_par', reason: 'Converts like everyone else.', metrics: [
      { key: 'cpa', label: 'CPA', value: 41, baseline: 44, lift: 0.068, note: 'spend ÷ conversions' },
    ], steps: [
      { key: 'landing_page', label: 'Click → Landing page', rate: 0.64, baseline_rate: 0.61, lift: 0.05, numerator: 3700, denominator: 5800, readable: true },
      { key: 'content_view', label: '→ Content view', rate: 0.48, baseline_rate: 0.45, lift: 0.07, numerator: 1780, denominator: 3700, readable: true },
      { key: 'add_to_cart', label: '→ Add to cart', rate: null, baseline_rate: null, lift: null, numerator: 42, denominator: 90, readable: false },
    ] }),
    tier({ tier: 4, key: 'fatigue', name: 'Fatigue', status: 'weak', decision: 'Frequency is climbing faster than the hook is holding.', metrics: [
      { key: 'frequency', label: 'Daily frequency', value: 2.3, baseline: 1.4, lift: 0.64, note: 'last 7 days vs first 14' },
      { key: 'hook_rate', label: 'Hook rate trend', value: 0.33, baseline: 0.41, lift: -0.2, note: 'same windows' },
      { key: 'engagement_rate', label: 'Engagement rate', value: 0.019, baseline: 0.021, lift: -0.1, note: 'reactions ÷ impressions' },
    ] }),
    tier({ tier: 5, key: 'context', name: 'Context', status: 'not_measured', reason: 'Placement breakdown is not connected.' }),
  ],
};

describe('DiagnosisPanel', () => {
  it('renders the verdict, baseline note, and every tier with its status label', () => {
    render(<DiagnosisPanel diagnosis={broken} />);
    expect(screen.getByText(/Broken layer: Retention/)).toBeInTheDocument();
    expect(screen.getByText('vs 41 other video creatives in this account')).toBeInTheDocument();
    expect(screen.getByText('Strong')).toBeInTheDocument();
    expect(screen.getAllByText('Weak')).toHaveLength(1); // fatigue's weak is labelled differently
    expect(screen.getByText('Early decay')).toBeInTheDocument();
    expect(screen.getByText('On par')).toBeInTheDocument();
    expect(screen.getByText('Not measured')).toBeInTheDocument();
  });

  it('formats ratios as percentages with baseline and lift, and frequency as a plain number', () => {
    render(<DiagnosisPanel diagnosis={broken} />);
    expect(screen.getByText('38%')).toBeInTheDocument();
    expect(screen.getByText('vs 31%')).toBeInTheDocument();
    expect(screen.getByText('+23%')).toBeInTheDocument();
    expect(screen.getByText('−38%')).toBeInTheDocument();
    expect(screen.getByText('2.3')).toBeInTheDocument();
    expect(screen.getByText('was 1.4')).toBeInTheDocument();
    expect(screen.getByText('$41')).toBeInTheDocument();
  });

  it('shows the decision only on weak tiers and the reason otherwise', () => {
    render(<DiagnosisPanel diagnosis={broken} />);
    expect(screen.getByText('Attention drops between 25% and 50%.')).toBeInTheDocument();
    expect(screen.getByText('Nothing to change here.')).toBeInTheDocument();
  });

  it('draws the retention curve with point labels and marks unreadable steps', () => {
    render(<DiagnosisPanel diagnosis={broken} />);
    expect(screen.getByText('p50 · 29%')).toBeInTheDocument();
    expect(screen.getByText(/Avg watch time 7.1s of 24s/)).toBeInTheDocument();
    expect(screen.getByText('42 · below floor')).toBeInTheDocument();
  });

  it('renders healthy and too-early verdicts and not-applicable retention', () => {
    const healthy: Diagnosis = { ...broken, broken_tier: null, verdict: { kind: 'healthy', headline: 'No broken layer.', detail: 'Scale it.' },
      tiers: broken.tiers.map((t) => (t.key === 'retention' ? { ...t, status: 'not_applicable', reason: 'Image creative.', curve: null, metrics: [] } : t)) };
    const { unmount } = render(<DiagnosisPanel diagnosis={healthy} />);
    expect(screen.getByText('No broken layer.')).toBeInTheDocument();
    expect(screen.getByText('Not applicable')).toBeInTheDocument();
    expect(screen.getByText('Image creative.')).toBeInTheDocument();
    unmount();
    render(<DiagnosisPanel diagnosis={{ ...broken, verdict: { kind: 'too_early', headline: 'Too early to diagnose.', detail: 'Only 640 impressions.' } }} />);
    expect(screen.getByText('Too early to diagnose.')).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd web && npm test -- tests/components/diagnosis-panel.test.tsx`
Expected: FAIL — cannot resolve `@/components/diagnosis-panel`

- [ ] **Step 5: Write the component** (`web/src/components/diagnosis-panel.tsx`). This mirrors the approved mockup (canvas "Creative Diagnosis Panel"); keep the class vocabulary of `insight-card.tsx` / `creative-card.tsx`.

```tsx
import { AlertCircle, CheckCircle2, Clock, Lock } from 'lucide-react';
import { cn } from 'cn';
import type { Diagnosis, DiagnosisCurve, DiagnosisMetric, DiagnosisStatus, DiagnosisStep, DiagnosisTier } from '@/lib/types';

const pct = (x: number) => `${Math.round(x * 100)}%`;
const pct1 = (x: number) => `${(x * 100).toFixed(1)}%`;
const signed = (x: number) => `${x >= 0 ? '+' : '−'}${Math.round(Math.abs(x) * 100)}%`;

function fmtValue(m: DiagnosisMetric, v: number | null): string {
  if (v == null) return '—';
  if (m.key === 'cpa') return `$${Math.round(v)}`;
  if (m.key === 'frequency') return v.toFixed(1);
  if (m.key === 'link_ctr' || m.key === 'engagement_rate') return pct1(v);
  return pct(v);
}

function statusLabel(tier: DiagnosisTier): string {
  if (tier.key === 'fatigue' && tier.status === 'weak') return 'Early decay';
  if (tier.key === 'fatigue' && tier.status === 'on_par') return 'Stable';
  return { strong: 'Strong', on_par: 'On par', weak: 'Weak', not_applicable: 'Not applicable', not_enough_data: 'Not enough data', too_young: 'Too young', not_measured: 'Not measured' }[tier.status];
}

const pillClass: Record<DiagnosisStatus, string> = {
  strong: 'bg-success/10 text-success',
  on_par: 'bg-muted text-muted-foreground',
  weak: 'bg-destructive/10 text-destructive',
  not_applicable: 'border border-border text-muted-foreground',
  not_enough_data: 'border border-border text-muted-foreground',
  too_young: 'border border-border text-muted-foreground',
  not_measured: 'border border-dashed border-muted-foreground/50 text-muted-foreground',
};

const railClass: Record<DiagnosisStatus, string> = {
  strong: 'bg-success/10 text-success',
  on_par: 'bg-muted text-muted-foreground',
  weak: 'bg-destructive text-primary-foreground',
  not_applicable: 'bg-muted text-muted-foreground',
  not_enough_data: 'bg-muted text-muted-foreground',
  too_young: 'bg-muted text-muted-foreground',
  not_measured: 'border border-dashed border-muted-foreground/50 text-muted-foreground',
};

function liftClass(tier: DiagnosisTier, m: DiagnosisMetric): string {
  if (m.lift == null) return 'text-muted-foreground';
  if (tier.key === 'fatigue') return Math.abs(m.lift) >= 0.15 ? 'text-chart-2' : 'text-muted-foreground';
  return m.lift >= 0.15 ? 'text-success' : m.lift <= -0.15 ? 'text-destructive' : 'text-muted-foreground';
}

function Pill({ tier }: { tier: DiagnosisTier }) {
  return (
    <span className={cn('inline-flex h-5 w-fit items-center gap-1 rounded-4xl px-2 text-xs font-medium', pillClass[tier.status])}>
      {tier.status === 'not_measured' && <Lock className="size-3" aria-hidden="true" />}
      {statusLabel(tier)}
    </span>
  );
}

function MetricBlock({ tier, m }: { tier: DiagnosisTier; m: DiagnosisMetric }) {
  const compare = tier.key === 'fatigue' ? 'was' : 'vs';
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{m.label}</span>
      <div className="flex items-baseline gap-2">
        <span className="text-[22px] leading-none font-semibold tabular-nums">{fmtValue(m, m.value)}</span>
        {m.baseline != null && <span className="text-xs text-muted-foreground tabular-nums">{`${compare} ${fmtValue(m, m.baseline)}`}</span>}
        {m.lift != null && <span className={cn('text-xs font-semibold tabular-nums', liftClass(tier, m))}>{signed(m.lift)}</span>}
      </div>
      <span className="text-[11px] text-muted-foreground">{m.note}</span>
    </div>
  );
}

function RetentionCurve({ curve }: { curve: DiagnosisCurve }) {
  const W = 320, H = 72, top = 4, bottom = 66;
  const x = (i: number) => (i / (curve.points.length - 1)) * W;
  const y = (s: number | null) => (s == null ? bottom : bottom - s * (bottom - top));
  const line = (key: 'share' | 'baseline_share') => curve.points.map((p, i) => `${x(i)},${y(p[key])}`).join(' ');
  const seg = curve.drop_segment;
  const segIdx = seg ? [curve.points.findIndex((p) => p.p === seg[0]), curve.points.findIndex((p) => p.p === seg[1])] : null;
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <span className="text-xs text-muted-foreground">Watch-through curve · share of plays reaching each point</span>
      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block" role="img" aria-label="Watch-through curve">
        <line x1="0" y1={bottom} x2={W} y2={bottom} className="stroke-border" strokeWidth="1" />
        {segIdx && segIdx[0] >= 0 && segIdx[1] >= 0 && (
          <rect x={x(segIdx[0])} y={top - 2} width={x(segIdx[1]) - x(segIdx[0])} height={bottom - top + 2} className="fill-destructive/10" />
        )}
        <polyline fill="none" className="stroke-muted-foreground" strokeWidth="1.5" strokeDasharray="4 3" points={line('baseline_share')} />
        <polyline fill="none" className="stroke-destructive" strokeWidth="2.5" strokeLinejoin="round" points={line('share')} />
        {curve.points.map((p, i) => <circle key={p.p} cx={x(i)} cy={y(p.share)} r="3" className="fill-destructive" />)}
      </svg>
      <div className="grid text-[11px] text-muted-foreground tabular-nums" style={{ gridTemplateColumns: `repeat(${curve.points.length}, minmax(0, 1fr))` }}>
        {curve.points.map((p, i) => {
          const inDrop = seg && (p.p === seg[0] || p.p === seg[1]);
          return (
            <span key={p.p} className={cn(i === 0 ? 'text-left' : i === curve.points.length - 1 ? 'text-right' : 'text-center', inDrop && 'font-semibold text-destructive')}>
              {`p${p.p} · ${p.share == null ? '—' : pct(p.share)}`}
            </span>
          );
        })}
      </div>
      <span className="text-[11px] text-muted-foreground">
        Dashed line is the account baseline.
        {curve.avg_watch_s != null && ` Avg watch time ${curve.avg_watch_s.toFixed(1)}s${curve.duration_s != null ? ` of ${Math.round(curve.duration_s)}s` : ''}.`}
      </span>
    </div>
  );
}

function StepChain({ steps }: { steps: DiagnosisStep[] }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-stretch gap-1.5">
        {steps.map((s) => (
          <div key={s.key} className={cn('flex flex-1 flex-col gap-0.5 rounded-lg border px-2.5 py-2', s.readable ? 'border-border' : 'border-dashed border-muted-foreground/40 text-muted-foreground')}>
            <span className="text-[11px] text-muted-foreground">{s.label}</span>
            {s.readable && s.rate != null ? (
              <div className="flex items-baseline gap-1.5">
                <span className="text-base font-semibold tabular-nums">{pct(s.rate)}</span>
                {s.baseline_rate != null && <span className="text-[11px] text-muted-foreground tabular-nums">{`vs ${pct(s.baseline_rate)}`}</span>}
              </div>
            ) : (
              <span className="text-xs tabular-nums">{s.numerator > 0 ? `${s.numerator} · below floor` : '—'}</span>
            )}
          </div>
        ))}
      </div>
      <span className="text-[11px] text-muted-foreground">Each step as a share of the previous one.</span>
    </div>
  );
}

function TierRow({ tier, broken, last }: { tier: DiagnosisTier; broken: boolean; last: boolean }) {
  const evidence = tier.status === 'weak' ? tier.decision : tier.reason;
  return (
    <div className={cn('grid gap-5 px-5 py-4 lg:grid-cols-[200px_minmax(0,1fr)_260px]', !last && 'border-b border-border', broken && 'bg-destructive/[0.03] shadow-[inset_3px_0_0_var(--destructive)]')}>
      <div className="flex items-start gap-3">
        <div className="flex shrink-0 flex-col items-center gap-1">
          <div className={cn('flex size-6 items-center justify-center rounded-full text-xs font-semibold tabular-nums', railClass[tier.status])}>{tier.tier}</div>
          {!last && <div className="h-10 w-px bg-border" />}
        </div>
        <div className="flex flex-col gap-1.5">
          <span className={cn('text-sm font-semibold', tier.status === 'not_measured' && 'text-muted-foreground')}>{tier.name}</span>
          <Pill tier={tier} />
          <span className="text-xs text-muted-foreground">{tier.question}</span>
        </div>
      </div>
      <div className="flex min-w-0 flex-wrap gap-7">
        {tier.metrics.map((m) => <MetricBlock key={m.key} tier={tier} m={m} />)}
        {tier.curve && <RetentionCurve curve={tier.curve} />}
        {tier.steps && <StepChain steps={tier.steps} />}
        {tier.metrics.length === 0 && !tier.curve && !tier.steps && evidence && (
          <p className="text-[13px] leading-snug text-muted-foreground">{evidence}</p>
        )}
      </div>
      <div className={cn('text-[13px] leading-snug', tier.status === 'weak' ? 'font-semibold' : 'text-muted-foreground')}>
        {(tier.metrics.length > 0 || tier.curve || tier.steps) && evidence}
      </div>
    </div>
  );
}

export function DiagnosisPanel({ diagnosis: d }: { diagnosis: Diagnosis }) {
  const v = d.verdict;
  const Icon = v.kind === 'broken' ? AlertCircle : v.kind === 'healthy' ? CheckCircle2 : Clock;
  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-sm font-semibold">Diagnosis (last 30 days)</h2>
        <span className="text-xs text-muted-foreground">{`vs ${d.baseline_creatives} other ${d.format} creatives in this account`}</span>
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div className={cn('flex items-center gap-3 border-b border-border px-5 py-3.5', v.kind === 'broken' && 'bg-destructive/[0.06]', v.kind === 'healthy' && 'bg-success/[0.06]', v.kind === 'too_early' && 'bg-muted')}>
          <Icon className={cn('size-[18px] shrink-0', v.kind === 'broken' ? 'text-destructive' : v.kind === 'healthy' ? 'text-success' : 'text-muted-foreground')} aria-hidden="true" />
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-semibold">{v.headline}</span>
            <span className="text-[13px] text-muted-foreground">{v.detail}</span>
          </div>
        </div>
        {d.tiers.map((t, i) => <TierRow key={t.key} tier={t} broken={t.tier === d.broken_tier} last={i === d.tiers.length - 1} />)}
      </div>
    </section>
  );
}
```

`text-chart-2` relies on `--color-chart-2` from `globals.css` `@theme inline` (amber). `rounded-4xl` is the badge radius token already defined there.

- [ ] **Step 6: Run the tests**

Run: `cd web && npm test -- tests/components/diagnosis-panel.test.tsx`
Expected: PASS. If `'42 · below floor'` fails, confirm the unreadable add-to-cart step in the fixture has `numerator: 42`.

- [ ] **Step 7: Type-check, lint, commit**

```bash
cd web && npx tsc --noEmit && npm run lint
cd .. && git add web/src/lib/types.ts web/src/lib/api.ts web/src/components/diagnosis-panel.tsx web/tests/components/diagnosis-panel.test.tsx
git commit -m "feat(web): diagnosis panel component"
```

---

### Task 7: Mount the panel on the creative detail page and verify end to end

**Files:**
- Modify: `web/src/app/(app)/creatives/[id]/page.tsx`
- Modify: `docs/superpowers/plans/README.md` (execution status line)

**Interfaces:**
- Consumes: `api.creativeDiagnosis`, `DiagnosisPanel`.

- [ ] **Step 1: Mount the panel** (`web/src/app/(app)/creatives/[id]/page.tsx`)

```tsx
import { DiagnosisPanel } from '@/components/diagnosis-panel';
// ...
  const [creative, tags, metrics, dims, diagnosis] = await Promise.all([
    api.creative(id),
    api.creativeTags(id),
    api.creativeMetrics(id, iso(since), iso(until)),
    api.dimensions(),
    api.creativeDiagnosis(id),
  ]);
// ... inside the right column, before the Performance section:
        <DiagnosisPanel diagnosis={diagnosis} />
```

- [ ] **Step 2: Type-check, lint, build**

Run: `cd web && npx tsc --noEmit && npm run lint && npm run build`
Expected: clean

- [ ] **Step 3: Run everything**

Run: `uv run pytest -q && make lint && cd web && npm test`
Expected: all green. Report exact counts.

- [ ] **Step 4: Prove it against real data.** With `.env`'s `DATABASE_URL` (Supabase), apply the migration to the primary database and re-run the fold for the org whose schema has the retention report, then hit the endpoint:

```bash
~/.local/bin/dbmate --env DATABASE_URL --no-dump-schema --migrations-dir db/migrations up
uv run python - <<'EOF'
import asyncio
from uuid import UUID
from cs.core.config import Settings
from cs.core.db import Database
from cs.core import repo
from cs.ingest.fivetran import insights
from cs.jobs.registry import JobContext

async def main():
    s = Settings()
    db = await Database.connect(s.database_url)
    for org in await db.fetch("SELECT org_id FROM meta_connection"):
        conn = await repo.get_meta_connection(db, org["org_id"])
        # JobContext is a plain dataclass and sync_insights never touches blobs or job.
        ctx = JobContext(db=db, blobs=None, settings=s, job=None)
        print(org["org_id"], await insights.sync_insights(ctx, org["org_id"], conn))
asyncio.run(main())
EOF
```

Expect `retention_tables: True` for the org whose schema has `video_retention_ad`, and `written > 0` only if that org has `ad` rows (as of 2026-09-12 org 2's first sync was still landing and had no `ad_account` row yet, so `written` may be 0 with a large `skipped`; that is the existing activation gate, not a bug in this work — say so in the report).

Then start the API (`uv run python -m cs.api` or the Makefile target) and the web app (`cd web && npm run dev`), open a video creative's detail page in the browser, and confirm the panel renders. Screenshot it for the report.

- [ ] **Step 5: Update the plans README** — add one line under `## Execution status`:

```
- 2026-09-12 creative-diagnosis-panel: executed; per-creative diagnosis endpoint + panel live; Context tier locked pending placement breakdown.
```

- [ ] **Step 6: Commit**

```bash
git add "web/src/app/(app)/creatives/[id]/page.tsx" docs/superpowers/plans/README.md
git commit -m "feat(web): show the diagnosis panel on the creative detail page"
```
