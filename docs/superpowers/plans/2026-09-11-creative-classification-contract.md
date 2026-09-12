# Creative Classification Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make it structurally impossible for the Fivetran ingest path to classify a video as an image, by replacing the `else: image` default with a verdict-plus-evidence contract that returns `unresolved` when nothing proves a format.

**Architecture:** A pure `classify()` function evaluates ordered rules against a creative row plus three preloaded lookup sets, returning a format and the evidence source that produced it. The database gains an `unresolved` format and a `format_source` column tied together by a CHECK constraint, so a guessed verdict cannot be stored. An independent contradiction detector re-derives video-ness from Fivetran metrics and quarantines any `image` verdict it disproves.

**Tech Stack:** Python 3.12, asyncpg, pytest (asyncio_mode=auto), Postgres, Next.js/TypeScript frontend.

**Spec:** `docs/superpowers/specs/2026-09-11-creative-classification-contract-design.md`

## Global Constraints

- No Meta Graph API or OAuth integration. Resolution uses Fivetran-replicated tables and Apify only.
- Soundness over completeness: never assert an unproven format. A missing table or a failed Apify call degrades toward `unresolved`, never toward `image`.
- `format_source` is mandatory on every write. `legacy_guess` is never written by new code.
- Org 2 (`org_01a089c9_231e_7000_bdaa_39277b4cc94b__meta_ads`) is the reference dataset. Do not break org 1's sync: a missing optional table degrades completeness, it does not raise.
- Run tests with `make test` (applies test migrations, then `uv run pytest -q`). Lint with `uv run ruff check .` — line length 100.
- Commit after every task.

---

### Task 1: Schema — make a guessed verdict unstorable

**Files:**
- Create: `db/migrations/20260911000001_classification_contract.sql`
- Test: `tests/ingest/test_classification_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `creative.format` accepts `'unresolved'`; `creative.format_source TEXT NOT NULL`; `creative.format_verified_at TIMESTAMPTZ NULL`. Constraint `creative_unresolved_consistency` enforces `(format = 'unresolved') = (format_source = 'unresolved')`.

- [ ] **Step 1: Write the migration**

```sql
-- db/migrations/20260911000001_classification_contract.sql
ALTER TABLE creative DROP CONSTRAINT creative_format_check;
ALTER TABLE creative ADD CONSTRAINT creative_format_check
  CHECK (format IN ('image', 'video', 'carousel', 'unresolved'));

ALTER TABLE creative ADD COLUMN format_source TEXT NOT NULL DEFAULT 'legacy_guess';
ALTER TABLE creative ADD COLUMN format_verified_at TIMESTAMPTZ;

ALTER TABLE creative ADD CONSTRAINT creative_format_source_check
  CHECK (format_source IN (
    'meta_object_type', 'asset_feed_spec_video', 'video_view_metric',
    'meta_video_id', 'carousel_attachments', 'apify_media_type',
    'manual_review', 'unresolved', 'legacy_guess'
  ));

ALTER TABLE creative ADD CONSTRAINT creative_unresolved_consistency
  CHECK ((format = 'unresolved') = (format_source = 'unresolved'));

CREATE INDEX creative_format_source_idx ON creative (org_id, format_source);
```

- [ ] **Step 2: Write the failing test**

```python
# tests/ingest/test_classification_schema.py
import asyncpg
import pytest

from cs.core.ids import new_id


async def _insert(db, org_id, account_id, *, fmt, source):
    await db.execute(
        "INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id,"
        " format, format_source, raw_path) VALUES ($1, $2, $3, $4, $5, $6, '')",
        new_id(), org_id, account_id, new_id().hex, fmt, source,
    )


async def test_unresolved_requires_unresolved_source(db, org_id, ad_account_id):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(db, org_id, ad_account_id, fmt="unresolved", source="meta_object_type")


async def test_resolved_format_cannot_claim_unresolved_source(db, org_id, ad_account_id):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(db, org_id, ad_account_id, fmt="image", source="unresolved")


async def test_unknown_source_is_rejected(db, org_id, ad_account_id):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(db, org_id, ad_account_id, fmt="image", source="vibes")


async def test_valid_combinations_are_accepted(db, org_id, ad_account_id):
    await _insert(db, org_id, ad_account_id, fmt="unresolved", source="unresolved")
    await _insert(db, org_id, ad_account_id, fmt="video", source="video_view_metric")
```

If `tests/conftest.py` has no `ad_account_id` fixture, add one that inserts an `ad_account` row and yields its id, following the existing `org_id` fixture's pattern.

- [ ] **Step 3: Run the test to verify it fails**

Run: `make migrate-test && uv run pytest tests/ingest/test_classification_schema.py -v`
Expected: FAIL — `column "format_source" does not exist` before the migration is applied.

- [ ] **Step 4: Apply migration and re-run**

Run: `make migrate-test && uv run pytest tests/ingest/test_classification_schema.py -v`
Expected: PASS, all four tests.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/20260911000001_classification_contract.sql tests/ingest/test_classification_schema.py
git commit -m "feat(ingest): add format_source so a guessed creative format cannot be stored"
```

---

### Task 2: The classifier — a pure function with no default branch

**Files:**
- Create: `src/cs/ingest/fivetran/classify.py`
- Create: `tests/ingest/test_classify.py`
- Modify: `src/cs/connectors/base.py:8` (extend the `Format` literal)

**Interfaces:**
- Consumes: Task 1's `format_source` vocabulary.
- Produces: `Classification` frozen dataclass with fields `format: str`, `source: str`, `asset_url: str | None`, `child_urls: list[str] | None`; and `classify(row, *, videos_by_id, afs_video_creative_ids, video_view_creative_ids) -> Classification`. Also `carousel_urls(raw: str | None) -> list[str] | None`, moved here from `transform.py` so `classify.py` has no import back into `transform.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ingest/test_classify.py
import json

from cs.ingest.fivetran.classify import classify

EMPTY = {"videos_by_id": {}, "afs_video_creative_ids": set(), "video_view_creative_ids": set()}


def _row(**over):
    row = {
        "id": "cr1", "object_type": None, "video_id": None, "video_video_id": None,
        "image_url": None, "thumbnail_url": None,
        "object_story_link_data_child_attachments": None,
    }
    row.update(over)
    return row


def test_object_type_video_is_video():
    r = classify(_row(object_type="VIDEO", video_video_id="v1"),
                 videos_by_id={"v1": "https://cdn/v.mp4"},
                 afs_video_creative_ids=set(), video_view_creative_ids=set())
    assert (r.format, r.source, r.asset_url) == ("video", "meta_object_type", "https://cdn/v.mp4")


def test_object_type_video_without_resolvable_source_is_still_video():
    r = classify(_row(object_type="VIDEO", video_video_id="missing"), **EMPTY)
    assert (r.format, r.source, r.asset_url) == ("video", "meta_object_type", None)


def test_object_type_photo_is_image():
    r = classify(_row(object_type="PHOTO", image_url="https://cdn/i.jpg"), **EMPTY)
    assert (r.format, r.source, r.asset_url) == ("image", "meta_object_type", "https://cdn/i.jpg")


def test_child_attachments_are_carousel():
    raw = json.dumps([{"picture": "https://cdn/a.jpg"}, {"picture": "https://cdn/b.jpg"}])
    r = classify(_row(object_type="SHARE", object_story_link_data_child_attachments=raw), **EMPTY)
    assert (r.format, r.source) == ("carousel", "carousel_attachments")
    assert r.child_urls == ["https://cdn/a.jpg", "https://cdn/b.jpg"]


def test_carousel_outranks_video_view_metric():
    # A carousel holding one video card earns video_views. It is still a carousel.
    raw = json.dumps([{"picture": "https://cdn/a.jpg"}])
    r = classify(_row(object_type="SHARE", object_story_link_data_child_attachments=raw),
                 videos_by_id={}, afs_video_creative_ids=set(),
                 video_view_creative_ids={"cr1"})
    assert r.format == "carousel"


def test_resolvable_video_id_is_video():
    r = classify(_row(object_type="SHARE", video_video_id="v9"),
                 videos_by_id={"v9": "https://cdn/v9.mp4"},
                 afs_video_creative_ids=set(), video_view_creative_ids=set())
    assert (r.format, r.source, r.asset_url) == ("video", "meta_video_id", "https://cdn/v9.mp4")


def test_asset_feed_spec_membership_is_video():
    r = classify(_row(object_type="SHARE"), videos_by_id={},
                 afs_video_creative_ids={"cr1"}, video_view_creative_ids=set())
    assert (r.format, r.source) == ("video", "asset_feed_spec_video")


def test_video_view_metric_is_video():
    r = classify(_row(object_type="SHARE"), videos_by_id={},
                 afs_video_creative_ids=set(), video_view_creative_ids={"cr1"})
    assert (r.format, r.source) == ("video", "video_view_metric")


def test_share_with_no_evidence_is_unresolved_not_image():
    # The regression test that matters: this is the exact shape that used to
    # download a video preview frame and label it an image.
    r = classify(_row(object_type="SHARE", thumbnail_url="https://cdn/frame.jpg"), **EMPTY)
    assert (r.format, r.source, r.asset_url) == ("unresolved", "unresolved", None)


def test_unknown_object_type_is_unresolved():
    r = classify(_row(object_type="PRIVACY_CHECK_FAIL", thumbnail_url="https://cdn/f.jpg"), **EMPTY)
    assert r.format == "unresolved"


def test_missing_object_type_is_unresolved():
    r = classify(_row(image_url="https://cdn/i.jpg"), **EMPTY)
    assert r.format == "unresolved"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cs.ingest.fivetran.classify'`.

- [ ] **Step 3: Write the implementation**

```python
# src/cs/ingest/fivetran/classify.py
from __future__ import annotations

import json
from collections.abc import Container, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Classification:
    format: str
    source: str
    asset_url: str | None = None
    child_urls: list[str] | None = None


def _pid(value: object) -> str | None:
    return None if value is None else str(value)


def carousel_urls(raw: str | None) -> list[str] | None:
    """object_story_link_data_child_attachments -> picture URLs."""
    if not raw:
        return None
    try:
        cards = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(cards, list):
        return None
    urls = [c["picture"] for c in cards if isinstance(c, dict) and c.get("picture")]
    return urls or None


def classify(
    row: Mapping,
    *,
    videos_by_id: Mapping[str, str],
    afs_video_creative_ids: Container[str],
    video_view_creative_ids: Container[str],
) -> Classification:
    """Ordered evidence rules. There is deliberately no default branch: anything
    unproven returns 'unresolved' rather than falling through to 'image'."""
    creative_id = _pid(row["id"])
    object_type = row["object_type"]
    video_ref = _pid(row["video_video_id"] or row["video_id"])

    if object_type == "VIDEO":
        return Classification("video", "meta_object_type", videos_by_id.get(video_ref or ""))
    if object_type == "PHOTO":
        return Classification(
            "image", "meta_object_type", row["image_url"] or row["thumbnail_url"]
        )

    # Structural evidence of a carousel outranks every video rule below: a carousel
    # holding one video card earns video_views without ceasing to be a carousel.
    children = carousel_urls(row["object_story_link_data_child_attachments"])
    if children:
        return Classification("carousel", "carousel_attachments", None, children)

    if video_ref and video_ref in videos_by_id:
        return Classification("video", "meta_video_id", videos_by_id[video_ref])
    if creative_id in afs_video_creative_ids:
        return Classification("video", "asset_feed_spec_video")
    if creative_id in video_view_creative_ids:
        return Classification("video", "video_view_metric")

    return Classification("unresolved", "unresolved")
```

Then extend the format literal:

```python
# src/cs/connectors/base.py:8
Format = Literal["image", "video", "carousel", "unresolved"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_classify.py -v && uv run ruff check .`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add src/cs/ingest/fivetran/classify.py tests/ingest/test_classify.py src/cs/connectors/base.py
git commit -m "feat(ingest): add evidence-based creative classifier with no default branch"
```

---

### Task 3: Wire the classifier into the Fivetran transform

**Files:**
- Modify: `src/cs/ingest/fivetran/transform.py:42-79` (delete `_carousel_urls` and `_format_and_urls`), `:97-200` (`_sync_ads_and_creatives`)
- Modify: `src/cs/ingest/fivetran/schema.py` (add the optional table name)
- Modify: `src/cs/ingest/fivetran/fixtures.py:40-45, 94-110` (add `object_type`, add the asset-feed-spec table)
- Modify: `src/cs/core/repo/creatives.py:13-43` (persist `format_source`)
- Test: `tests/ingest/test_fivetran_transform.py`

**Interfaces:**
- Consumes: `classify`, `Classification`, `carousel_urls` from Task 2.
- Produces: `upsert_creative(db, org_id, ad_account_id, c, raw_path, *, format_source)`; loaders `_afs_video_creative_ids(db, schema) -> set[str]` and `_video_view_creative_ids(db, schema) -> set[str]`, both returning an empty set when their table is absent.

- [ ] **Step 1: Add the optional table name and fixture support**

```python
# src/cs/ingest/fivetran/schema.py — add below AD_VIDEO_HISTORY
AD_ASSET_FEED_SPEC_VIDEO = "ad_asset_feed_spec_video"
```

In `fixtures.py`, add `object_type TEXT` to the `creative_history` DDL (append it to the column list, before `_fivetran_synced`), create the new table, and extend the insert helper:

```python
    await db.execute(f"""
        CREATE TABLE "{schema}".{AD_ASSET_FEED_SPEC_VIDEO} (
          creative_id TEXT, video_id TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
```

```python
async def insert_asset_feed_spec_video(
    db: Database, schema: str, *, creative_id: str, video_id: str
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".{AD_ASSET_FEED_SPEC_VIDEO} VALUES ($1, $2, now())',
        creative_id,
        video_id,
    )
```

Add an `object_type: str | None = None` keyword to `insert_campaign_ad_creative` and append it to that function's `creative_history` INSERT (extend the positional `VALUES` list by one placeholder and pass `object_type` last, before `now()`).

- [ ] **Step 2: Write the failing tests**

```python
# tests/ingest/test_fivetran_transform.py — append

async def test_share_creative_without_evidence_is_unresolved_and_downloads_nothing(
    db, blobs, settings, org_id
):
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db, SCHEMA, campaign_id="c1", account_id="999", adset_id="as1", ad_id="ad1",
        creative_id="cr1", name="n", headline="H", image_url=None,
        object_type="SHARE",
    )
    await db.execute(
        f'UPDATE "{SCHEMA}".creative_history SET thumbnail_url=$1', "https://cdn/frame.jpg"
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    accounts = await repo.list_ad_accounts(db, org_id)
    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    row = await db.fetchrow("SELECT format, format_source FROM creative WHERE org_id=$1", org_id)
    assert row["format"] == "unresolved"
    assert row["format_source"] == "unresolved"
    # No asset download job may be enqueued for a creative we cannot type.
    assert await db.fetchval("SELECT count(*) FROM job WHERE kind='meta.download_creative'") == 0


async def test_asset_feed_spec_video_membership_classifies_as_video(db, blobs, settings, org_id):
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db, SCHEMA, campaign_id="c1", account_id="999", adset_id="as1", ad_id="ad1",
        creative_id="cr1", name="n", headline="H", image_url=None, object_type="SHARE",
    )
    await insert_asset_feed_spec_video(db, SCHEMA, creative_id="cr1", video_id="v1")
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    accounts = await repo.list_ad_accounts(db, org_id)
    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    row = await db.fetchrow("SELECT format, format_source FROM creative WHERE org_id=$1", org_id)
    assert (row["format"], row["format_source"]) == ("video", "asset_feed_spec_video")
```

Import `insert_asset_feed_spec_video` at the top of the test file.

- [ ] **Step 3: Run tests to verify they fail**

Run: `make migrate-test && uv run pytest tests/ingest/test_fivetran_transform.py -v`
Expected: FAIL — `format` is `image`, and a `meta.download_creative` job exists.

- [ ] **Step 4: Implement**

In `transform.py`, delete `_carousel_urls` and `_format_and_urls`. Add the loaders:

```python
async def _optional_creative_ids(db, sql: str, *, table: str) -> set[str]:
    """A table Fivetran has not synced for this connection degrades completeness,
    never soundness -- the classifier falls back to 'unresolved', not 'image'. The
    warning is the precondition alert: coverage is silently reduced otherwise."""
    try:
        rows = await db.fetch(sql)
    except asyncpg.UndefinedTableError:
        log.warning(
            "classification signal unavailable -- coverage degraded, more creatives "
            "will be unresolved",
            extra={"table": table},
        )
        return set()
    return {str(r["creative_id"]) for r in rows if r["creative_id"] is not None}


async def _afs_video_creative_ids(db, schema: str) -> set[str]:
    return await _optional_creative_ids(
        db,
        f"SELECT DISTINCT creative_id FROM {_q(schema, AD_ASSET_FEED_SPEC_VIDEO)}"
        " WHERE video_id IS NOT NULL",
        table=AD_ASSET_FEED_SPEC_VIDEO,
    )


async def _video_view_creative_ids(db, schema: str) -> set[str]:
    return await _optional_creative_ids(
        db,
        f"""
        SELECT DISTINCT a.creative_id FROM (
          SELECT id, creative_id, row_number() OVER (PARTITION BY id
            ORDER BY _fivetran_synced DESC) rn FROM {_q(schema, AD_HISTORY)}
        ) a JOIN {_q(schema, BASIC_AD_ACTIONS)} act ON act.ad_id = a.id::text
        WHERE a.rn = 1 AND act.action_type = 'video_view' AND act.value > 0
        """,
        table=BASIC_AD_ACTIONS,
    )
```

Extend the `cs.ingest.fivetran.schema` import block at `transform.py:12` with `AD_ASSET_FEED_SPEC_VIDEO` and `BASIC_AD_ACTIONS` (the latter is currently imported only by `insights.py`).

**Move the existing carousel test.** `tests/ingest/test_fivetran_transform.py:516-525` calls `fivetran_transform._carousel_urls` directly and will break. Move `test_carousel_urls_returns_none_for_malformed_shapes` into `tests/ingest/test_classify.py`, importing `carousel_urls` from `cs.ingest.fivetran.classify` and dropping the leading underscore at each call site. Do not delete the assertions — they cover malformed-JSON shapes the new module must still handle.

In `_sync_ads_and_creatives`, load both sets once before the loop, then replace the `_format_and_urls` call and everything derived from it:

```python
    afs_ids = await _afs_video_creative_ids(db, schema)
    vv_ids = await _video_view_creative_ids(db, schema)
```

```python
            verdict = classify(
                cr,
                videos_by_id=videos_by_id,
                afs_video_creative_ids=afs_ids,
                video_view_creative_ids=vv_ids,
            )
            platform_creative = PlatformCreative(
                platform_creative_id=_pid(cr["id"]),
                format=verdict.format,
                headline=cr["title"],
                primary_text=cr["body"],
                description=None,
                cta_type=cr["call_to_action_type"],
                link_url=cr["object_story_link_data_link"],
                asset_url=verdict.asset_url,
                asset_mime=None,
                child_asset_urls=verdict.child_urls,
                raw=dict(cr),
            )
            creative_id, created = await repo.upsert_creative(
                db, org_id, acct_id, platform_creative, raw_path="",
                format_source=verdict.source,
            )
```

Then gate the download branches on a resolved verdict. Replace the `kind = ...` line and the `if/elif` chain's entry condition:

```python
            if verdict.format == "unresolved":
                continue  # nothing proves a format; download and tagging stay blocked
            kind = "original_video" if verdict.format == "video" else "original_image"
```

The remaining `if fmt == "carousel"` / `elif asset_url` / `elif fmt == "video"` branches keep their existing bodies, reading `verdict.format`, `verdict.asset_url`, and `verdict.child_urls` instead of the old locals.

Note `asset_mime` is now `None` rather than the hardcoded `"image/jpeg"`, which was wrong for every video.

In `repo.upsert_creative`, add the keyword-only parameter and persist it:

```python
async def upsert_creative(
    db: Database, org_id: UUID, ad_account_id: UUID, c: PlatformCreative, raw_path: str,
    *, format_source: str,
) -> tuple[UUID, bool]:
```

Add `format_source` and `format_verified_at` to the INSERT column list and `VALUES` (passing `format_source` and `now()`), and to the `ON CONFLICT DO UPDATE SET` clause alongside `format = EXCLUDED.format`.

- [ ] **Step 5: Run the full suite**

Run: `make test`
Expected: PASS. Other call sites of `upsert_creative` will fail to compile if any exist — search with `grep -rn "upsert_creative" src tests` and pass an explicit `format_source` at each.

- [ ] **Step 6: Commit**

```bash
git add src/cs/ingest/fivetran/ src/cs/core/repo/creatives.py tests/ingest/
git commit -m "feat(ingest): classify creatives from evidence instead of defaulting to image"
```

---

### Task 4: Keep unresolved creatives out of every image surface

**Files:**
- Modify: `src/cs/tagging/repo.py:162` (confirm default excludes unresolved)
- Modify: `web/src/lib/types.ts:4`, `web/src/components/creative-card.tsx:7`
- Test: `tests/ingest/test_repo.py`

**Interfaces:**
- Consumes: Task 3's persisted `format='unresolved'` rows.
- Produces: no new signatures. Guarantees that filtering by `image` never returns an `unresolved` creative.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ingest/test_repo.py — append

async def test_image_filter_excludes_unresolved(db, org_id, ad_account_id):
    from cs.core import repo
    from cs.core.ids import new_id
    for fmt, source in (("image", "meta_object_type"), ("unresolved", "unresolved")):
        await db.execute(
            "INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id,"
            " format, format_source, raw_path) VALUES ($1,$2,$3,$4,$5,$6,'')",
            new_id(), org_id, ad_account_id, new_id().hex, fmt, source,
        )
    rows = await repo.list_creatives(db, org_id, ad_account_id, "image", 50, 0, None, None)
    assert [r["format"] for r in rows] == ["image"]
    assert await repo.count_creatives(db, org_id, ad_account_id, "image", None, None) == 1
```

Match the real positional signature of `list_creatives` / `count_creatives` in `src/cs/core/repo/creatives.py:60-120`; adjust the call if it differs.

- [ ] **Step 2: Run to verify**

Run: `uv run pytest tests/ingest/test_repo.py -v`
Expected: PASS already — `format = $3` is an exact match, so this test *pins* existing behavior rather than changing it. If it fails, the filter is looser than assumed; fix the query to compare exactly.

- [ ] **Step 3: Update the frontend types**

```typescript
// web/src/lib/types.ts:4 — widen the union
format: 'image' | 'video' | 'carousel' | 'unresolved';
```

```typescript
// web/src/components/creative-card.tsx:7
const formatIcon = { video: PlayCircle, carousel: Layers, image: null, unresolved: HelpCircle } as const;
```

Import `HelpCircle` from `lucide-react` alongside the existing icons.

- [ ] **Step 4: Run frontend checks**

Run: `cd web && npx tsc --noEmit && npm test`
Expected: PASS. Fix any exhaustive-switch errors the widened union surfaces.

- [ ] **Step 5: Commit**

```bash
git add tests/ingest/test_repo.py web/src/lib/types.ts web/src/components/creative-card.tsx
git commit -m "feat(creatives): keep unresolved creatives out of the image bucket"
```

---

### Task 5: Harden Apify into a positive assertion

**Files:**
- Modify: `src/cs/ingest/instagram_scrape.py:28-48`
- Test: `tests/ingest/test_instagram_scrape.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `resolve_media(http, settings, permalink_url) -> tuple[str, str | None]` returning `(format, video_url)` where format is `"video"`, `"image"`, `"carousel"`, or `"unresolved"`. `resolve_video_url` is replaced by this.

- [ ] **Step 1: Probe the actor's real response shape**

Before writing code, confirm the field name and value set. With an `APIFY_API_KEY` in `.env`, run one known-video and one known-image permalink through the actor and record the keys returned. Write the observed mapping into the module docstring. **If no key is available, stop and report** — do not guess the field name. The contract permits dropping this task: those creatives stay `unresolved` and the rest of the plan is unaffected.

- [ ] **Step 2: Write the failing tests**

```python
# tests/ingest/test_instagram_scrape.py — append

import pytest
from cs.ingest import instagram_scrape


class _FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


class _FakeHttp:
    def __init__(self, payload): self._p = payload
    async def post(self, *a, **k): return _FakeResp(self._p)


async def test_explicit_video_type_resolves_to_video(settings):
    settings.apify_api_key = "k"
    http = _FakeHttp([{"type": "Video", "videoUrl": "https://cdn/v.mp4"}])
    assert await instagram_scrape.resolve_media(http, settings, "https://insta/p/x/") == (
        "video", "https://cdn/v.mp4")


async def test_explicit_image_type_resolves_to_image(settings):
    settings.apify_api_key = "k"
    http = _FakeHttp([{"type": "Image"}])
    assert await instagram_scrape.resolve_media(http, settings, "https://insta/p/x/") == (
        "image", None)


async def test_missing_type_is_unresolved_never_image(settings):
    # A scraper that breaks must not be able to manufacture an 'image' verdict.
    settings.apify_api_key = "k"
    http = _FakeHttp([{"caption": "no type field"}])
    assert await instagram_scrape.resolve_media(http, settings, "https://insta/p/x/") == (
        "unresolved", None)


async def test_unknown_type_is_unresolved(settings):
    settings.apify_api_key = "k"
    http = _FakeHttp([{"type": "SomethingNew"}])
    assert await instagram_scrape.resolve_media(http, settings, "https://insta/p/x/") == (
        "unresolved", None)
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/ingest/test_instagram_scrape.py -v`
Expected: FAIL — `resolve_media` does not exist.

- [ ] **Step 4: Implement**

```python
_TYPE_MAP = {"Video": "video", "Image": "image", "Sidecar": "carousel"}


async def resolve_media(
    http: httpx.AsyncClient, settings: Settings, permalink_url: str
) -> tuple[str, str | None]:
    """Returns (format, video_url). Requires a positive type assertion from the actor:
    a missing or unrecognized type yields 'unresolved', never a format. A scraper that
    silently stops extracting must not be able to manufacture an 'image' verdict."""
    if not settings.apify_api_key:
        raise InstagramScrapeError("APIFY_API_KEY is not configured", retryable=False)
    try:
        resp = await http.post(
            _APIFY_RUN_URL,
            headers={"Authorization": f"Bearer {settings.apify_api_key}"},
            json={"directUrls": [permalink_url], "resultsLimit": 1},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise InstagramScrapeError(f"Apify request failed: {exc}", retryable=True) from exc
    if not isinstance(data, list) or not data:
        raise InstagramScrapeError("Apify returned no items for this URL", retryable=False)
    item = data[0]
    fmt = _TYPE_MAP.get(item.get("type"))
    if fmt is None:
        return "unresolved", None
    return fmt, item.get("videoUrl") if fmt == "video" else None
```

Update the `meta.resolve_instagram_video` handler in `src/cs/ingest/jobs.py:107-125` to call `resolve_media`, and to persist the returned format with `format_source='apify_media_type'` when it is not `unresolved`, downloading only when a `video_url` came back.

- [ ] **Step 5: Run tests**

Run: `make test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cs/ingest/instagram_scrape.py src/cs/ingest/jobs.py tests/ingest/test_instagram_scrape.py
git commit -m "fix(ingest): require a positive media type from Apify instead of inferring image"
```

---

### Task 6: Contradiction detector

**Files:**
- Create: `src/cs/ingest/fivetran/audit.py`
- Modify: `src/cs/ingest/jobs.py` (register `meta.audit_classification`)
- Test: `tests/ingest/test_classification_audit.py`

**Interfaces:**
- Consumes: Task 3's loaders and `format_source` column.
- Produces: `audit_classifications(ctx, org_id, connection) -> dict[str, int]` with keys `checked`, `violations`, `quarantined`.

- [ ] **Step 1: Write the failing test**

```python
# tests/ingest/test_classification_audit.py
from datetime import date

from cs.core import repo
from cs.core.ids import new_id
from cs.ingest.fivetran import audit
from cs.ingest.fivetran.fixtures import create_fixture_schema, insert_campaign_ad_creative
from cs.jobs.registry import JobContext

SCHEMA = "audittest__meta_ads"


async def _setup(db, org_id, ad_account_id, *, fmt, source):
    """One creative in the app DB, plus a Fivetran ad with video_view actions
    against it -- the shape the detector must recognize."""
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db, SCHEMA, campaign_id="c1", account_id="999", adset_id="as1", ad_id="ad1",
        creative_id="cr1", name="n", headline="H", image_url=None, object_type="SHARE",
    )
    await db.execute(
        f'INSERT INTO "{SCHEMA}".basic_ad_actions VALUES ($1, $2, $3, $4, $5)',
        "ad1", date(2026, 9, 1), "video_view", 0, 1200,
    )
    cid = new_id()
    await db.execute(
        "INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id,"
        " format, format_source, raw_path) VALUES ($1,$2,$3,'cr1',$4,$5,'')",
        cid, org_id, ad_account_id, fmt, source,
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "c1", SCHEMA)
    return cid, await repo.get_meta_connection_by_id(db, conn_id)


async def test_image_with_video_views_is_quarantined(db, blobs, settings, org_id, ad_account_id):
    cid, conn = await _setup(
        db, org_id, ad_account_id, fmt="image", source="meta_object_type")
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    result = await audit.audit_classifications(ctx, org_id, conn)

    assert result["violations"] == 1
    row = await db.fetchrow("SELECT format, format_source FROM creative WHERE id=$1", cid)
    assert (row["format"], row["format_source"]) == ("unresolved", "unresolved")


async def test_carousel_with_video_views_is_not_a_violation(
    db, blobs, settings, org_id, ad_account_id
):
    # A carousel may legitimately contain a video card, so video views do not
    # contradict a carousel verdict the way they contradict an image verdict.
    cid, conn = await _setup(
        db, org_id, ad_account_id, fmt="carousel", source="carousel_attachments")
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    result = await audit.audit_classifications(ctx, org_id, conn)

    assert result["violations"] == 0
    row = await db.fetchrow("SELECT format FROM creative WHERE id=$1", cid)
    assert row["format"] == "carousel"
```

Check the `basic_ad_actions` fixture column order in `src/cs/ingest/fivetran/fixtures.py` (`ad_id, date, action_type, index, value`) and match the positional INSERT to it.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ingest/test_classification_audit.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

```python
# src/cs/ingest/fivetran/audit.py
"""Re-derives video-ness from Fivetran metrics independently of whatever produced a
creative's verdict, so a wrong classification fails loudly instead of sitting in the
library. A carousel with video views is legitimate; an image with video views is not."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from cs.ingest.fivetran.transform import _afs_video_creative_ids, _video_view_creative_ids
from cs.jobs.registry import JobContext

log = logging.getLogger(__name__)


async def audit_classifications(
    ctx: JobContext, org_id: UUID, connection: asyncpg.Record
) -> dict[str, Any]:
    schema = connection["schema_name"]
    proven_video = await _afs_video_creative_ids(ctx.db, schema)
    proven_video |= await _video_view_creative_ids(ctx.db, schema)

    rows = await ctx.db.fetch(
        "SELECT id, platform_creative_id FROM creative"
        " WHERE org_id=$1 AND format='image'",
        org_id,
    )
    violations = [r for r in rows if r["platform_creative_id"] in proven_video]
    for r in violations:
        await ctx.db.execute(
            "UPDATE creative SET format='unresolved', format_source='unresolved',"
            " format_verified_at=now() WHERE id=$1",
            r["id"],
        )
        log.error(
            "classification contract violation: image creative has video evidence",
            extra={"creative_id": str(r["id"]), "org_id": str(org_id)},
        )
    return {"checked": len(rows), "violations": len(violations), "quarantined": len(violations)}
```

Register a `@handler("meta.audit_classification")` job in `src/cs/ingest/jobs.py` following the shape of the existing handlers, and enqueue it at the end of the Fivetran sync handler.

- [ ] **Step 4: Run tests**

Run: `make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cs/ingest/fivetran/audit.py src/cs/ingest/jobs.py tests/ingest/test_classification_audit.py
git commit -m "feat(ingest): quarantine image verdicts contradicted by video metrics"
```

---

### Task 7: Require the asset-feed-spec table on new connectors

**Files:**
- Modify: `src/cs/connectors/fivetran/client.py:11-18`
- Test: `tests/connectors/test_fivetran_client.py`

**Interfaces:**
- Consumes: `AD_ASSET_FEED_SPEC_VIDEO` from Task 3.
- Produces: `REQUIRED_TABLES` includes `ad_asset_feed_spec_video`.

- [ ] **Step 1: Write the failing test**

```python
# tests/connectors/test_fivetran_client.py — append
from cs.connectors.fivetran import client


def test_asset_feed_spec_video_is_required():
    # Rule 4 of the classification contract reads this table. lock_down_and_activate
    # sets ALLOW_COLUMNS, which blocks tables discovered later -- so a table missing
    # from REQUIRED_TABLES can never appear for a new connection.
    assert "ad_asset_feed_spec_video" in client.REQUIRED_TABLES
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/connectors/test_fivetran_client.py -v`
Expected: FAIL — assertion error.

- [ ] **Step 3: Add the table**

```python
REQUIRED_TABLES = (
    "campaign_history",
    "ad_history",
    "creative_history",
    "ad_video_history",
    "ad_asset_feed_spec_video",
    "basic_ad",
    "basic_ad_actions",
    "basic_ad_action_values",
)
```

Keep whatever trailing entries already exist; only add the new one.

- [ ] **Step 4: Run tests**

Run: `make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cs/connectors/fivetran/client.py tests/connectors/test_fivetran_client.py
git commit -m "fix(fivetran): sync ad_asset_feed_spec_video, required by the classification contract"
```

---

### Task 8: Remediate existing mislabeled creatives

**Files:**
- Create: `scripts/reclassify_creatives.py`
- Test: manual, against org 2

**Interfaces:**
- Consumes: every prior task.
- Produces: a dry-run-by-default script. No new importable API.

**Gate:** Do not run this against production until Tasks 1-7 are merged and the contradiction detector has reported cleanly for one sync cycle. Re-tagging reclassified videos runs through `video_tagging_model` and costs real money (`video_tagging_enabled` defaults to `True`, `src/cs/core/config.py:45`).

- [ ] **Step 1: Write the script with dry-run as the default**

```python
# scripts/reclassify_creatives.py
"""Re-runs the classification contract over creatives ingested under the old
`else: image` default. Dry-run unless --apply is passed."""
import argparse
import asyncio
from collections import Counter
from uuid import UUID

from cs.core import queue, repo
from cs.core.db import connect
from cs.ingest.fivetran.classify import classify
from cs.ingest.fivetran.transform import (
    _afs_video_creative_ids,
    _latest_rows,
    _video_view_creative_ids,
)
from cs.ingest.fivetran.schema import AD_VIDEO_HISTORY, CREATIVE_HISTORY


async def main(org_id: UUID, apply: bool) -> None:
    db = await connect()
    conn = await repo.get_meta_connection(db, org_id)
    schema = conn["schema_name"]

    videos = {str(r["id"]): r["source"]
              for r in await _latest_rows(db, schema, AD_VIDEO_HISTORY) if r["source"]}
    afs_ids = await _afs_video_creative_ids(db, schema)
    vv_ids = await _video_view_creative_ids(db, schema)

    tally: Counter[str] = Counter()
    for cr in await _latest_rows(db, schema, CREATIVE_HISTORY):
        verdict = classify(
            cr, videos_by_id=videos,
            afs_video_creative_ids=afs_ids, video_view_creative_ids=vv_ids,
        )
        existing = await db.fetchrow(
            "SELECT id, format FROM creative WHERE org_id=$1 AND platform_creative_id=$2",
            org_id, str(cr["id"]),
        )
        if existing is None:
            continue
        tally[f"{existing['format']} -> {verdict.format} ({verdict.source})"] += 1
        if not apply or existing["format"] == verdict.format:
            continue

        await db.execute(
            "UPDATE creative SET format=$2, format_source=$3, format_verified_at=now()"
            " WHERE id=$1",
            existing["id"], verdict.format, verdict.source,
        )
        if existing["format"] == "image" and verdict.format == "video":
            # The stored asset is a video preview frame, and the tags on it were
            # produced by the image model against the image taxonomy. Both go.
            await db.execute("UPDATE creative SET primary_asset_id=NULL WHERE id=$1",
                             existing["id"])
            await db.execute(
                "DELETE FROM asset WHERE creative_id=$1 AND kind='original_image'",
                existing["id"])
            await db.execute("DELETE FROM tag WHERE tag_run_id IN"
                             " (SELECT id FROM tag_run WHERE creative_id=$1)", existing["id"])
            await db.execute("DELETE FROM tag_run WHERE creative_id=$1", existing["id"])
            if verdict.asset_url:
                await queue.enqueue(
                    db, org_id, "meta.download_creative",
                    {"creative_id": str(existing["id"]), "asset_url": verdict.asset_url,
                     "kind": "original_video"},
                    dedup_key=f"reclassify:{existing['id']}",
                )

    for line, n in tally.most_common():
        print(f"{n:6d}  {line}")
    print("\nDRY RUN -- pass --apply to write" if not apply else "\napplied")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    asyncio.run(main(UUID(a.org), a.apply))
```

Verify `connect()`, `repo.get_meta_connection`, and the `tag` / `tag_run` table and column names against the codebase before running — adjust to match rather than assuming these signatures. Confirm the blob objects behind deleted `asset` rows are either cleaned up or intentionally orphaned; decide explicitly.

- [ ] **Step 2: Dry-run against org 2 and compare to the expected distribution**

Run: `uv run python scripts/reclassify_creatives.py --org <org2-uuid>`
Expected tally, from the design validation — treat a material deviation as a bug in the classifier, not in the expectation:

| verdict | source | creatives with ads |
|---|---|---|
| video | meta_object_type | 417 |
| video | asset_feed_spec_video | 494 |
| video | video_view_metric | 22 |
| unresolved | unresolved | 170 |

- [ ] **Step 3: Apply, then verify no legacy rows remain**

Run: `uv run python scripts/reclassify_creatives.py --org <org2-uuid> --apply`
Then: `SELECT format_source, count(*) FROM creative WHERE org_id=... GROUP BY 1` — zero rows may remain with `legacy_guess`.

- [ ] **Step 4: Commit**

```bash
git add scripts/reclassify_creatives.py
git commit -m "feat(ingest): add reclassification backfill for mislabeled creatives"
```

---

## Notes for the executor

- Task 5 is droppable. If no `APIFY_API_KEY` exists, stop at its Step 1 and report; the contract holds without it and those creatives stay `unresolved`.
- The single most important test in this plan is `test_share_with_no_evidence_is_unresolved_not_image` (Task 2). It is the regression guard for the entire bug class. If a future change makes it fail, the default branch has come back.
- Tasks 1-3 are the core and must land together to be coherent. Tasks 4-7 are independently reviewable. Task 8 is gated on all of them.
