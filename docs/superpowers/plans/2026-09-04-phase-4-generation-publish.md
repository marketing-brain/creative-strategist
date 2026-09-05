# Phase 4: Generation and Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a brand kit plus an insight or concept, generate on-brand copy, static images in three aspect ratios, and short image-to-video clips; queue them for human review; publish approved assets to Meta as paused ads; and close the loop when the next sync sees the published ad.

**Architecture:** `generation/` holds a `BrandKit` repo, a `CopyWriter` (Claude), an `ImageGenerator` (AI Gateway OpenAI-compatible images API), a `VideoGenerator` (a Node CLI in `tools/video-gen/` wrapping the AI SDK's video functions, driven by subprocess), and Pillow/ffmpeg compositing. One job, `generation.run`, orchestrates them and writes `generated_asset` drafts. `publish/` implements the Meta upload/create path and a `publish.meta` job. Ingest learns to link a synced creative back to its `generated_asset`.

**Tech Stack:** `openai` Python SDK (pointed at `https://ai-gateway.vercel.sh/v1`), Pillow, ffmpeg, Node 22 + `ai` + `@ai-sdk/gateway`, Meta Marketing API.

**Spec:** `docs/superpowers/specs/2026-09-04-creative-strategist-design.md` (sections 8, 9)

## Global Constraints

- Nothing publishes without an `approved` review state. `review_state` moves one way from `draft`.
- Ads are created with `status=PAUSED` unless the publish form explicitly asks for `ACTIVE`.
- Aspects produced for every static: `1:1` (1080×1080), `4:5` (1080×1350), `9:16` (1080×1920).
- Image model from `settings.image_model` (default `bfl/flux-2-pro`) via `POST {ai_gateway_base_url}/v1/images/generations` with `response_format=b64_json`; reference-guided variants via `/v1/images/edits`.
- Video model from `settings.video_model` (default `klingai/kling-v3.0-i2v`), 5 seconds, via the Node CLI with `poll` enabled; the start still must be a hosted URL (blob `url_for`), never inline base64.
- Copy model `settings.claude_model_copy`, refusal fallback on, structured output.
- All earlier constraints apply.
- Follow-ups this phase must close, all in Task 0, ahead of the generation/brand work: the Phase 1 final review's "introduce the derived-metrics SQL view spec §4 describes so insights and the API share one definition" (never done in Phase 3, now three divergent ctr/cpa/roas implementations exist); the Phase 2 final review's carousel-first-card-only tagging limitation (escalated by the Phase 3 review since pattern/fatigue insights now wrap card-0-only tags in confident LLM prose); and the Phase 2 final review's `extract_keyframes`/`extract_audio` ffmpeg subprocess calls running on the event loop thread instead of `asyncio.to_thread`.

---

## File structure

```
src/cs/core/metrics.py             # derived_metrics(): shared ctr/cpa/roas formula (Task 0)
db/migrations/20260904000007_generation.sql
src/cs/generation/__init__.py
src/cs/generation/brand.py         # BrandKit dataclass, repo (create version, get active), logo storage
src/cs/generation/copy.py          # CopyWriter Protocol, ClaudeCopyWriter (write + brand check)
src/cs/generation/images.py        # ImageGenerator Protocol, GatewayImageGenerator
src/cs/generation/compose.py       # fit_to_aspect(), overlay_logo(), video_end_card_and_captions()
src/cs/generation/video.py         # VideoGenerator Protocol, NodeVideoGenerator (subprocess -> tools/video-gen)
src/cs/generation/fakes.py         # FakeCopyWriter, FakeImageGenerator, FakeVideoGenerator
src/cs/generation/repo.py          # generation + generated_asset persistence and review transitions
src/cs/generation/service.py       # build_brief(), run_generation()
src/cs/generation/jobs.py          # generation.run + factories
src/cs/publish/__init__.py
src/cs/publish/meta.py             # publish_generated_asset()
src/cs/publish/jobs.py             # publish.meta
src/cs/api/routers/brand.py        # /brand-kit
src/cs/api/routers/generation.py   # /generations, /generated-assets/{id}/(approve|reject|publish)
src/cs/api/routers/meta_targets.py # /accounts/{id}/campaigns, /accounts/{id}/adsets
tools/video-gen/package.json
tools/video-gen/index.mjs
tests/generation/..., tests/publish/..., tests/e2e/test_loop.py
```

Task 0 also modifies (no new migration): `src/cs/insights/patterns.py`, `src/cs/api/routers/creatives.py` (both re-point at `cs.core.metrics.derived_metrics`); `src/cs/tagging/runner.py`, `src/cs/tagging/prompts.py`, `src/cs/ingest/repo.py` (carousel multi-card tagging); `src/cs/tagging/runner.py` again (ffmpeg calls via `asyncio.to_thread`).

---

### Task 0: Follow-ups from the Phase 1-3 final reviews

**Why this task exists:** three items were carried forward and none is safe to defer further now that generation/publish will read from the same metrics and tags. (1) The Phase 1 final review assigned "introduce the derived-metrics SQL view spec §4 describes" to Phase 3; Phase 3 didn't do it, so `src/cs/api/routers/creatives.py::_derived` and `src/cs/insights/patterns.py::metrics_from_sums` (also used by `fatigue.py` and `copilot.py`) independently compute ctr/cpa/roas with different rounding (4/2/2 vs 6/4/4) and different null semantics (0.0 vs `None` when there are no impressions) — a Copilot answer can already disagree with what `/creatives` shows for the same data, and generation briefs pull from insights, so the drift would propagate into ad copy. (2) The Phase 2 final review flagged carousel creatives as tagged via card 0's image only; the Phase 3 review escalated this because pattern/fatigue insights now aggregate those card-0-only tags into confident LLM prose — and Phase 4's `build_brief()` (Task 5) quotes that same prose into generation briefs, so a wrong tag now drives ad creative, not just an insight. (3) The Phase 2 final review noted `extract_keyframes`'s and `extract_audio`'s ffmpeg subprocess calls run synchronously on the event loop thread in `tagging.tag_creative`, risking the job's heartbeat starving past the reclaim window on a long video.

**Ruling on "SQL view":** the spec's "computed in views, not stored" describes intent (derived metrics aren't persisted columns), not a mandate for a literal `CREATE VIEW`. The three call sites first aggregate `metric_daily` differently — one groups by day for a single creative, one sums per segment vs. a baseline across many creatives, one sums two date-windows per creative already fetched into Python — so a single relational view over raw rows can't serve all three without each site re-deriving its own aggregation on top of it anyway. A shared Python function given the aggregated sums is the one piece those three call sites don't already have in common, and is what actually produces "one definition." Cost if wrong: a future contributor re-litigates this and moves the formula into SQL; the call sites already share one function, so that move only touches `src/cs/core/metrics.py`, not its callers.

**Ruling on rounding/null semantics:** canonicalize on `metrics_from_sums`'s existing precision (ctr round 6, cpa/roas round 4) and its `None`-when-undefined semantics (not `_derived`'s 0.0-when-no-impressions), since insights' lift comparisons need the precision and no test locks in the 0.0 behavior (confirmed by search — the only creatives-metrics test, `tests/api/test_accounts_and_creatives.py::test_creatives_list_detail_metrics`, seeds impressions=1000 and never hits the zero case). Cost if wrong: Phase 5's frontend sees `null` instead of `0%` for a creative with no impressions yet and must handle it — a one-line display-side change, not a data bug.

**Files:**
- Create: `src/cs/core/metrics.py`; Test: `tests/core/test_metrics.py`
- Modify: `src/cs/insights/patterns.py`, `src/cs/api/routers/creatives.py`
- Modify: `src/cs/ingest/repo.py`, `src/cs/tagging/runner.py`, `src/cs/tagging/prompts.py`; Test: `tests/ingest/test_repo.py`, `tests/tagging/test_runner.py`
- Modify: `src/cs/tagging/runner.py` (ffmpeg calls); Test: `tests/tagging/test_runner.py`

**Interfaces:**
- `cs.core.metrics.derived_metrics(spend: Decimal, impressions: int, clicks: int, conversions: Decimal, conversion_value: Decimal) -> dict{ctr, cpa, roas}` — exact body of today's `patterns.metrics_from_sums`, moved verbatim.
- `cs.insights.patterns.metrics_from_sums` becomes `from cs.core.metrics import derived_metrics as metrics_from_sums` (re-export; `fatigue.py` and `copilot.py` import sites are unchanged).
- `cs.api.routers.creatives._derived` calls `derived_metrics(...)` instead of computing ctr/cpa/roas inline.
- `cs.ingest.repo.list_assets_by_kind(db, org_id, creative_id, kind) -> list[Record]`, ordered by `seq`.
- `cs.tagging.runner._prepare_inputs`: for `format == "carousel"`, fetch every `original_image` asset via `list_assets_by_kind` (not just `primary_asset_path`) and pass one image per card to the tagger, in `seq` order.
- `cs.tagging.runner._prepare_inputs`: `extract_keyframes`, `extract_audio`, and the transcript-asset's `probe_duration_ms` call all wrapped in `asyncio.to_thread`.

- [ ] **Step 1: Write failing tests — shared metrics**

`tests/core/test_metrics.py`:

```python
from decimal import Decimal

from cs.core.metrics import derived_metrics
from cs.insights.patterns import metrics_from_sums


def test_derived_metrics_formula_and_nulls():
    assert derived_metrics(Decimal("100"), 10000, 200, Decimal("10"), Decimal("500")) == {
        "ctr": 0.02,
        "cpa": 10.0,
        "roas": 5.0,
    }
    assert derived_metrics(Decimal("0"), 0, 0, Decimal("0"), Decimal("0")) == {
        "ctr": None,
        "cpa": None,
        "roas": None,
    }


def test_insights_reexports_the_same_function():
    assert metrics_from_sums is derived_metrics
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/core/test_metrics.py -v`
Expected: FAIL (`cs.core.metrics` does not exist yet)

- [ ] **Step 3: Implement**

`src/cs/core/metrics.py`:

```python
from __future__ import annotations

from decimal import Decimal


def derived_metrics(
    spend: Decimal, impressions: int, clicks: int, conversions: Decimal, conversion_value: Decimal
) -> dict:
    spend_f, conv_f, val_f = float(spend), float(conversions), float(conversion_value)
    return {
        "ctr": round(clicks / impressions, 6) if impressions else None,
        "cpa": round(spend_f / conv_f, 4) if conv_f else None,
        "roas": round(val_f / spend_f, 4) if spend_f else None,
    }
```

In `src/cs/insights/patterns.py`, replace the `metrics_from_sums` function body with:

```python
from cs.core.metrics import derived_metrics as metrics_from_sums
```

(remove the old `def metrics_from_sums(...)` block entirely; `signed_lift` and everything else in the file is unchanged. `fatigue.py` and `copilot.py` both `from cs.insights.patterns import metrics_from_sums` already — leave those imports as-is.)

In `src/cs/api/routers/creatives.py`, replace `_derived`'s inline ctr/cpa/roas lines:

```python
from cs.core.metrics import derived_metrics


def _derived(r) -> dict:
    out = {
        **{
            k: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v)
            for k, v in dict(r).items()
        },
        **{k: int(r[k] or 0) for k in COUNT_FIELDS if k in r},
    }
    out["day"] = r["day"].isoformat()
    out.update(
        derived_metrics(r["spend"], int(r["impressions"]), int(r["clicks"]), r["conversions"], r["conversion_value"])
    )
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/core/test_metrics.py tests/insights tests/api/test_accounts_and_creatives.py -v`
Expected: PASS (`test_creatives_list_detail_metrics`'s `ctr == 0.02, cpa == 5.0, roas == 10.0` still holds at the new precision — 20/1000, 10/2, 100/10 are exact ratios)

- [ ] **Step 5: Write failing tests — carousel multi-card tagging**

Add to `tests/ingest/test_repo.py`:

```python
async def test_list_assets_by_kind_orders_by_seq(db, org_id, settings):
    from cs.connectors.base import PlatformCreative
    from cs.core.crypto import encrypt_str
    from cs.ingest import repo

    acct = await repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    cid, _ = await repo.upsert_creative(
        db, org_id, acct, PlatformCreative("cx", "carousel", "H", "P", None, None, "u", None, None, {}), "raw"
    )
    for i in (2, 0, 1):  # insert out of order
        await repo.insert_asset(db, org_id, cid, "original_image", f"o/{i}.png", "sha", "image/png", seq=i)
    rows = await repo.list_assets_by_kind(db, org_id, cid, "original_image")
    assert [r["seq"] for r in rows] == [0, 1, 2]
```

Add to `tests/tagging/test_runner.py`:

```python
@pytest.fixture
async def carousel_creative(db, org_id, settings, blobs):
    acct = await ingest_repo.upsert_ad_account(
        db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None
    )
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("cx", "carousel", "H", "P", None, "SHOP_NOW", "u", None, None, {}),
        "raw",
    )
    asset_ids = []
    for i in range(3):
        key = f"{org_id}/creatives/{cid}/card_{i:02d}.png"
        await blobs.put(key, _png(), "image/png")
        aid = await ingest_repo.insert_asset(
            db, org_id, cid, "original_image", key, "sha", "image/png", seq=i
        )
        asset_ids.append(aid)
    await ingest_repo.set_primary_asset(db, org_id, cid, asset_ids[0])
    return cid


async def test_tag_carousel_creative_uses_all_cards(db, blobs, settings, org_id, carousel_creative):
    tagger, tr = FakeTagger(), FakeTranscriber()
    run_id = await runner.tag_creative(
        _ctx(db, blobs, settings, org_id, {}), carousel_creative, tagger, tr
    )
    assert run_id is not None and tagger.calls[0]["n_images"] == 3 and tr.calls == 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_ffmpeg_calls_run_off_the_event_loop_thread(db, blobs, settings, org_id, tmp_path, monkeypatch):
    import threading

    from cs.tagging import runner as runner_mod
    from cs.tagging.video import Keyframe

    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)
    seen_threads = []

    def fake_extract_keyframes(data):
        seen_threads.append(threading.current_thread())
        return [Keyframe(0.0, _png(), 64, 64)]

    def fake_extract_audio(data):
        seen_threads.append(threading.current_thread())
        return None

    monkeypatch.setattr(runner_mod, "extract_keyframes", fake_extract_keyframes)
    monkeypatch.setattr(runner_mod, "extract_audio", fake_extract_audio)
    await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), cid, FakeTagger(), FakeTranscriber())
    assert len(seen_threads) == 2
    assert all(t is not threading.current_thread() for t in seen_threads)
```

- [ ] **Step 6: Run to verify they fail**

Run: `uv run pytest tests/ingest/test_repo.py tests/tagging/test_runner.py -v`
Expected: FAIL (`list_assets_by_kind` doesn't exist; carousel test gets `n_images == 1`; the thread-offload test's monkeypatched functions run on the main thread since `asyncio.to_thread` isn't used yet)

- [ ] **Step 7: Implement**

In `src/cs/ingest/repo.py`, add after `get_asset_by_seq`:

```python
async def list_assets_by_kind(
    db: Database, org_id: UUID, creative_id: UUID, kind: str
) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT * FROM asset WHERE org_id=$1 AND creative_id=$2 AND kind=$3 ORDER BY seq",
        org_id,
        creative_id,
        kind,
    )
```

In `src/cs/tagging/runner.py`, restructure `_prepare_inputs` to branch on format before fetching the primary asset (carousels don't tag from it) and add the carousel case:

```python
async def _prepare_inputs(ctx: JobContext, creative, transcriber: Transcriber):
    org_id = ctx.job.org_id
    if creative["format"] == "carousel":
        cards = await ingest_repo.list_assets_by_kind(
            ctx.db, org_id, creative["id"], "original_image"
        )
        images = []
        for card in cards:
            raw = await ctx.blobs.get(card["path"])
            jpeg, _, _ = prepare_image(raw)
            images.append((jpeg, "image/jpeg"))
        return images, None, None
    data = await ctx.blobs.get(creative["primary_asset_path"])
    if creative["format"] == "video":
        await ingest_repo.delete_assets_by_kind(
            ctx.db, org_id, creative["id"], ("keyframe", "transcript")
        )
        frames = await asyncio.to_thread(extract_keyframes, data)
        images = [(f.jpeg, "image/jpeg") for f in frames]
        stamps = [f.timestamp for f in frames]
        for i, f in enumerate(frames):
            key = f"{org_id}/creatives/{creative['id']}/keyframes/{i:02d}.jpg"
            await ctx.blobs.put(key, f.jpeg, "image/jpeg")
            await ingest_repo.insert_asset(
                ctx.db,
                org_id,
                creative["id"],
                "keyframe",
                key,
                sha256_hex(f.jpeg),
                "image/jpeg",
                f.width,
                f.height,
                int(f.timestamp * 1000),
                seq=i,
            )
        audio = await asyncio.to_thread(extract_audio, data)
        transcript = await transcriber.transcribe(audio, "audio/mpeg") if audio else ""
        tkey = f"{org_id}/creatives/{creative['id']}/transcript.txt"
        await ctx.blobs.put(tkey, transcript.encode(), "text/plain")
        await ingest_repo.insert_asset(
            ctx.db,
            org_id,
            creative["id"],
            "transcript",
            tkey,
            sha256_hex(transcript.encode()),
            "text/plain",
            duration_ms=await asyncio.to_thread(probe_duration_ms, data),
        )
        return images, transcript, stamps
    jpeg, _, _ = prepare_image(data)
    return [(jpeg, "image/jpeg")], None, None
```

Add `import asyncio` to `src/cs/tagging/runner.py`'s imports.

In `src/cs/tagging/prompts.py`, update `SYSTEM` so the model knows what multiple images mean for a carousel (currently only explains this for video keyframes):

```python
SYSTEM = (
    "You are a senior performance-creative analyst. You decompose paid social ad creatives into "
    "structured elements so a team can learn which elements drive performance. Look at every image "
    "carefully; for videos the images are keyframes in time order with timestamps and a "
    "transcript; for carousels the images are the cards in swipe order, one image per card. For "
    "each dimension listed, pick exactly one value from its allowed values (or "
    "write a short free-text value where no values are listed), give a confidence between 0 and "
    "1, and cite concrete visual or textual evidence. Emit one entry per dimension; do not skip "
    "dimensions."
)
```

- [ ] **Step 8: Run to verify they pass, then commit**

Run: `uv run pytest tests/core/test_metrics.py tests/insights tests/api/test_accounts_and_creatives.py tests/ingest tests/tagging -v`
Expected: PASS

```bash
git add src/cs/core/metrics.py src/cs/insights/patterns.py src/cs/api/routers/creatives.py \
        src/cs/ingest/repo.py src/cs/tagging/runner.py src/cs/tagging/prompts.py \
        tests/core/test_metrics.py tests/ingest/test_repo.py tests/tagging/test_runner.py
git commit -m "fix(insights,tagging): shared derived-metrics formula, carousel multi-card tagging, ffmpeg off the event loop"
```

---

### Task 1: Migration and brand kit

**Files:**
- Create: `db/migrations/20260904000007_generation.sql`, `src/cs/generation/__init__.py`, `src/cs/generation/brand.py`, `src/cs/api/routers/brand.py`
- Modify: `src/cs/api/app.py`
- Test: `tests/generation/test_brand.py`, `tests/api/test_brand.py`

**Interfaces:**
- Tables `brand_kit`, `generation`, `generated_asset`, `publish_job` (columns per spec section 4, plus `generation.error TEXT`, `generated_asset.error TEXT`, `publish_job.form JSONB`).
- `@dataclass BrandKit(id, org_id, version, colors: list[str], fonts: list[str], logo_path: str | None, voice_rules: str, prohibited_claims: list[str])`
- `create_brand_kit(db, org_id, colors, fonts, voice_rules, prohibited_claims, logo_path=None, logo_sha256=None) -> BrandKit` (new version = max+1, sets `org.active_brand_kit_id`)
- `get_active_brand_kit(db, org_id) -> BrandKit | None`
- Routes: `GET /brand-kit`; `POST /brand-kit` JSON `{colors, fonts, voice_rules, prohibited_claims}`; `POST /brand-kit/logo` multipart `file` (PNG/SVG) → creates a new version copying fields with the new logo.

- [ ] **Step 1: Migration**

`db/migrations/20260904000007_generation.sql`:

```sql
-- migrate:up
CREATE TABLE brand_kit (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  version INT NOT NULL,
  colors JSONB NOT NULL DEFAULT '[]'::jsonb,
  fonts JSONB NOT NULL DEFAULT '[]'::jsonb,
  logo_path TEXT,
  logo_sha256 TEXT,
  voice_rules TEXT NOT NULL DEFAULT '',
  prohibited_claims TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, version)
);
ALTER TABLE org ADD CONSTRAINT org_active_brand_kit_fk FOREIGN KEY (active_brand_kit_id) REFERENCES brand_kit(id);

CREATE TABLE generation (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  brand_kit_id UUID NOT NULL REFERENCES brand_kit(id),
  source_insight_id UUID REFERENCES insight(id),
  source_creative_ids UUID[] NOT NULL DEFAULT '{}',
  concept TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('static', 'video')),
  variants INT NOT NULL DEFAULT 4,
  model TEXT,
  prompt TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed')),
  error TEXT,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE TABLE generated_asset (
  id UUID PRIMARY KEY,
  generation_id UUID NOT NULL REFERENCES generation(id),
  org_id UUID NOT NULL REFERENCES org(id),
  kind TEXT NOT NULL CHECK (kind IN ('image', 'video')),
  aspect TEXT NOT NULL CHECK (aspect IN ('1:1', '4:5', '9:16')),
  variant INT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  headline TEXT,
  primary_text TEXT,
  cta_type TEXT,
  review_state TEXT NOT NULL DEFAULT 'draft' CHECK (review_state IN ('draft', 'approved', 'rejected')),
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX generated_asset_review_idx ON generated_asset (org_id, review_state, created_at DESC);

CREATE TABLE publish_job (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  generated_asset_id UUID NOT NULL REFERENCES generated_asset(id),
  ad_account_id UUID NOT NULL REFERENCES ad_account(id),
  campaign_id TEXT NOT NULL,
  adset_id TEXT NOT NULL,
  ad_status_requested TEXT NOT NULL CHECK (ad_status_requested IN ('PAUSED', 'ACTIVE')),
  form JSONB NOT NULL,
  platform_creative_id TEXT,
  platform_ad_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed')),
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE INDEX publish_job_platform_creative_idx ON publish_job (org_id, platform_creative_id);

-- migrate:down
DROP TABLE publish_job;
DROP TABLE generated_asset;
DROP TABLE generation;
ALTER TABLE org DROP CONSTRAINT org_active_brand_kit_fk;
DROP TABLE brand_kit;
```

- [ ] **Step 2: Write failing tests**

`tests/generation/test_brand.py`:

```python
from cs.generation.brand import create_brand_kit, get_active_brand_kit


async def test_versions_and_active(db, org_id):
    assert await get_active_brand_kit(db, org_id) is None
    k1 = await create_brand_kit(db, org_id, ["#112233"], ["Inter"], "Warm, direct.", ["cures"])
    k2 = await create_brand_kit(db, org_id, ["#112233", "#FFFFFF"], ["Inter"], "Warm, direct.", ["cures"], "o/logo.png", "sha")
    assert (k1.version, k2.version) == (1, 2)
    active = await get_active_brand_kit(db, org_id)
    assert active.id == k2.id and active.logo_path == "o/logo.png" and active.prohibited_claims == ["cures"]
```

`tests/api/test_brand.py`:

```python
from tests.conftest import dev_headers

H = dev_headers(org="org_brand")


async def test_brand_kit_routes(client, blobs):
    assert (await client.get("/brand-kit", headers=H)).status_code == 404
    r = await client.post("/brand-kit", headers=H, json={"colors": ["#000"], "fonts": ["Inter"], "voice_rules": "v", "prohibited_claims": ["x"]})
    assert r.status_code == 200 and r.json()["version"] == 1
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    r = await client.post("/brand-kit/logo", headers=H, files={"file": ("logo.png", png, "image/png")})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["logo_url"].endswith("/logo.png")
    assert (await client.get("/brand-kit", headers=H)).json()["colors"] == ["#000"]
```

- [ ] **Step 3: Run to verify they fail**

Run: `make migrate-test && uv run pytest tests/generation/test_brand.py tests/api/test_brand.py -v`
Expected: FAIL

- [ ] **Step 4: Implement**

`src/cs/generation/__init__.py`: empty.

`src/cs/generation/brand.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id


@dataclass
class BrandKit:
    id: UUID
    org_id: UUID
    version: int
    colors: list[str]
    fonts: list[str]
    logo_path: str | None
    voice_rules: str
    prohibited_claims: list[str]


def _from_row(r) -> BrandKit:
    return BrandKit(r["id"], r["org_id"], r["version"], list(r["colors"]), list(r["fonts"]), r["logo_path"],
                    r["voice_rules"], list(r["prohibited_claims"]))


async def create_brand_kit(db: Database, org_id: UUID, colors: list[str], fonts: list[str], voice_rules: str,
                           prohibited_claims: list[str], logo_path: str | None = None, logo_sha256: str | None = None) -> BrandKit:
    async with db.transaction() as tx:
        version = (await tx.fetchval("SELECT coalesce(max(version), 0) FROM brand_kit WHERE org_id=$1", org_id)) + 1
        kid = new_id()
        row = await tx.fetchrow(
            "INSERT INTO brand_kit (id, org_id, version, colors, fonts, logo_path, logo_sha256, voice_rules, prohibited_claims) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *",
            kid, org_id, version, colors, fonts, logo_path, logo_sha256, voice_rules, prohibited_claims)
        await tx.execute("UPDATE org SET active_brand_kit_id=$2 WHERE id=$1", org_id, kid)
    return _from_row(row)


async def get_active_brand_kit(db: Database, org_id: UUID) -> BrandKit | None:
    row = await db.fetchrow("SELECT k.* FROM org o JOIN brand_kit k ON k.id = o.active_brand_kit_id WHERE o.id=$1", org_id)
    return _from_row(row) if row else None


async def get_brand_kit(db: Database, org_id: UUID, kit_id: UUID) -> BrandKit | None:
    row = await db.fetchrow("SELECT * FROM brand_kit WHERE org_id=$1 AND id=$2", org_id, kit_id)
    return _from_row(row) if row else None
```

`src/cs/api/routers/brand.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_blobs, get_db
from cs.core.blobs import BlobStore, sha256_hex
from cs.core.db import Database
from cs.generation import brand

router = APIRouter(prefix="/brand-kit")


class KitBody(BaseModel):
    colors: list[str] = []
    fonts: list[str] = []
    voice_rules: str = ""
    prohibited_claims: list[str] = []


async def _ser(k: brand.BrandKit, blobs: BlobStore) -> dict:
    return {"id": str(k.id), "version": k.version, "colors": k.colors, "fonts": k.fonts, "voice_rules": k.voice_rules,
            "prohibited_claims": k.prohibited_claims,
            "logo_url": await blobs.url_for(k.logo_path) if k.logo_path else None}


@router.get("")
async def get_kit(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db), blobs: BlobStore = Depends(get_blobs)) -> dict:
    k = await brand.get_active_brand_kit(db, auth.org_id)
    if not k:
        raise HTTPException(404, "no brand kit yet")
    return await _ser(k, blobs)


@router.post("")
async def create_kit(body: KitBody, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db),
                     blobs: BlobStore = Depends(get_blobs)) -> dict:
    prev = await brand.get_active_brand_kit(db, auth.org_id)
    k = await brand.create_brand_kit(db, auth.org_id, body.colors, body.fonts, body.voice_rules, body.prohibited_claims,
                                     prev.logo_path if prev else None, None)
    return await _ser(k, blobs)


@router.post("/logo")
async def upload_logo(file: UploadFile, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db),
                      blobs: BlobStore = Depends(get_blobs)) -> dict:
    if file.content_type not in ("image/png", "image/svg+xml"):
        raise HTTPException(400, "logo must be PNG or SVG")
    data = await file.read()
    ext = "png" if file.content_type == "image/png" else "svg"
    key = f"{auth.org_id}/brand/logo-{sha256_hex(data)[:12]}.{ext}"
    await blobs.put(key, data, file.content_type)
    prev = await brand.get_active_brand_kit(db, auth.org_id)
    k = await brand.create_brand_kit(db, auth.org_id, prev.colors if prev else [], prev.fonts if prev else [],
                                     prev.voice_rules if prev else "", prev.prohibited_claims if prev else [], key, sha256_hex(data))
    return await _ser(k, blobs)
```

Include the router in `src/cs/api/app.py`.

- [ ] **Step 5: Run to verify they pass, then commit**

Run: `uv run pytest tests/generation/test_brand.py tests/api/test_brand.py -v`
Expected: PASS

```bash
git add db/migrations/20260904000007_generation.sql src/cs/generation src/cs/api tests/generation/test_brand.py tests/api/test_brand.py
git commit -m "feat(generation): schema and versioned brand kit with logo upload"
```

---

### Task 2: Copywriter with brand check

**Files:**
- Create: `src/cs/generation/copy.py`, `src/cs/generation/fakes.py`
- Test: `tests/generation/test_copy.py`

**Interfaces:**
- `@dataclass CopySet(headline: str, primary_text: str, cta_type: str)`
- `CopyWriter` Protocol: `async write(brief: str, kit: BrandKit, n: int) -> list[CopySet]`
- `ClaudeCopyWriter(settings, client=None)`: call 1 writes `n` sets (structured output); call 2 checks each against `kit.voice_rules` and `kit.prohibited_claims` (structured output `{"approved": [bool]}`), returns only approved sets; local pre-filter drops any set containing a prohibited claim substring (case-insensitive) before the model check.
- `FakeCopyWriter(sets=None)` returns canned sets; `FakeImageGenerator` returns a solid PNG per call and records prompts; `FakeVideoGenerator` returns a tiny valid mp4 generated by ffmpeg (or a fixed bytes blob if ffmpeg missing) and records calls.

- [ ] **Step 1: Write failing test**

`tests/generation/test_copy.py`:

```python
import json
from types import SimpleNamespace
from uuid import UUID

from cs.generation.brand import BrandKit
from cs.generation.copy import ClaudeCopyWriter, contains_prohibited


def _kit():
    return BrandKit(UUID(int=1), UUID(int=2), 1, ["#000"], ["Inter"], None, "Warm and direct.", ["cures", "guaranteed"])


def test_prohibited_filter():
    assert contains_prohibited("This Cures everything", ["cures"])
    assert not contains_prohibited("Feel better", ["cures"])


async def test_write_then_check(settings):
    responses = [
        json.dumps({"sets": [
            {"headline": "Sleep deeper", "primary_text": "Tired of waking up tired?", "cta_type": "SHOP_NOW"},
            {"headline": "Guaranteed sleep", "primary_text": "x", "cta_type": "SHOP_NOW"},
            {"headline": "Rest easy", "primary_text": "Wind down in minutes.", "cta_type": "LEARN_MORE"},
        ]}),
        json.dumps({"approved": [True, False]}),
    ]
    calls = []

    class M:
        async def create(self, **kw):
            calls.append(kw)
            return SimpleNamespace(stop_reason="end_turn", model="m", content=[SimpleNamespace(type="text", text=responses.pop(0))])

    client = SimpleNamespace(beta=SimpleNamespace(messages=M()))
    sets = await ClaudeCopyWriter(settings, client=client).write("brief", _kit(), 3)
    assert [s.headline for s in sets] == ["Sleep deeper"]
    assert "guaranteed" in calls[0]["messages"][0]["content"].lower()  # prohibited list is in the prompt
    assert len(calls) == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/generation/test_copy.py -v`
Expected: FAIL

- [ ] **Step 3: Implement copy and fakes**

`src/cs/generation/copy.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import anthropic

from cs.core.config import Settings
from cs.generation.brand import BrandKit

CTA_TYPES = ["SHOP_NOW", "LEARN_MORE", "SIGN_UP", "GET_OFFER", "DOWNLOAD", "BOOK_NOW", "SUBSCRIBE"]

WRITE_SCHEMA = {"type": "object", "properties": {"sets": {"type": "array", "items": {
    "type": "object", "properties": {"headline": {"type": "string"}, "primary_text": {"type": "string"},
                                     "cta_type": {"type": "string", "enum": CTA_TYPES}},
    "required": ["headline", "primary_text", "cta_type"], "additionalProperties": False}}},
    "required": ["sets"], "additionalProperties": False}
CHECK_SCHEMA = {"type": "object", "properties": {"approved": {"type": "array", "items": {"type": "boolean"}}},
                "required": ["approved"], "additionalProperties": False}

WRITE_SYSTEM = ("You write paid social ad copy for Meta. Headlines under 40 characters, primary text under 125 characters, "
                "one clear idea each, no emojis unless the voice rules ask for them, no claims you cannot support.")
CHECK_SYSTEM = ("You are a brand compliance reviewer. Approve a copy set only if it follows the voice rules and contains "
                "none of the prohibited claims, in spirit as well as wording.")


@dataclass
class CopySet:
    headline: str
    primary_text: str
    cta_type: str


def contains_prohibited(text: str, prohibited: list[str]) -> bool:
    low = text.lower()
    return any(p.lower() in low for p in prohibited if p.strip())


class CopyWriter(Protocol):
    async def write(self, brief: str, kit: BrandKit, n: int) -> list[CopySet]: ...


class ClaudeCopyWriter:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.model = settings.claude_model_copy

    async def _call(self, system: str, user: str, schema: dict) -> dict:
        resp = await self.client.beta.messages.create(
            model=self.model, max_tokens=4000, system=system,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}})
        if resp.stop_reason == "refusal":
            return {}
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        return json.loads(text) if text else {}

    async def write(self, brief: str, kit: BrandKit, n: int) -> list[CopySet]:
        user = (f"Brief:\n{brief}\n\nVoice rules:\n{kit.voice_rules}\n\nProhibited claims (never use or imply): "
                f"{', '.join(kit.prohibited_claims) or 'none'}\n\nWrite {n} distinct copy sets.")
        data = await self._call(WRITE_SYSTEM, user, WRITE_SCHEMA)
        sets = [CopySet(s["headline"], s["primary_text"], s["cta_type"]) for s in data.get("sets", [])]
        sets = [s for s in sets if not contains_prohibited(f"{s.headline} {s.primary_text}", kit.prohibited_claims)]
        if not sets:
            return []
        check_user = json.dumps({"voice_rules": kit.voice_rules, "prohibited_claims": kit.prohibited_claims,
                                 "sets": [s.__dict__ for s in sets]})
        verdict = await self._call(CHECK_SYSTEM, check_user, CHECK_SCHEMA)
        approved = verdict.get("approved", [True] * len(sets))
        return [s for s, ok in zip(sets, approved, strict=False) if ok]
```

`src/cs/generation/fakes.py`:

```python
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile

from PIL import Image

from cs.generation.copy import CopySet


def solid_png(w: int = 1024, h: int = 1024, color=(40, 90, 200)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, format="PNG")
    return b.getvalue()


def tiny_mp4(seconds: int = 2) -> bytes:
    if shutil.which("ffmpeg") is None:
        return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=blue:s=320x568:d={seconds}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", f.name], check=True)
        return open(f.name, "rb").read()


class FakeCopyWriter:
    def __init__(self, sets: list[CopySet] | None = None):
        self.sets = sets or [CopySet("Sleep deeper", "Tired of waking up tired?", "SHOP_NOW"),
                             CopySet("Rest easy", "Wind down in minutes.", "LEARN_MORE")]
        self.briefs: list[str] = []

    async def write(self, brief, kit, n) -> list[CopySet]:
        self.briefs.append(brief)
        return self.sets[:n]


class FakeImageGenerator:
    def __init__(self):
        self.calls: list[dict] = []

    async def generate(self, prompt: str, reference_images: list[bytes], n: int = 1) -> list[bytes]:
        self.calls.append({"prompt": prompt, "refs": len(reference_images), "n": n})
        return [solid_png() for _ in range(n)]


class FakeVideoGenerator:
    def __init__(self):
        self.calls: list[dict] = []

    async def generate(self, still_url: str, prompt: str, duration_s: int, aspect: str) -> bytes:
        self.calls.append({"still_url": still_url, "prompt": prompt, "duration_s": duration_s, "aspect": aspect})
        return tiny_mp4()
```

- [ ] **Step 4: Run to verify it passes, then commit**

Run: `uv run pytest tests/generation/test_copy.py -v`
Expected: PASS

```bash
git add src/cs/generation/copy.py src/cs/generation/fakes.py tests/generation/test_copy.py
git commit -m "feat(generation): Claude copywriter with prohibited-claim filter and brand check"
```

---

### Task 3: Image generation and compositing

**Files:**
- Create: `src/cs/generation/images.py`, `src/cs/generation/compose.py`
- Test: `tests/generation/test_images.py`, `tests/generation/test_compose.py`

**Interfaces:**
- `ImageGenerator` Protocol: `async generate(prompt: str, reference_images: list[bytes], n: int = 1) -> list[bytes]` (PNG/JPEG bytes).
- `GatewayImageGenerator(settings, client: openai.AsyncOpenAI | None = None)` — no references → `client.images.generate(model, prompt, n, response_format="b64_json")`; with references → `client.images.edit(model, image=[files...], prompt, n)`; returns decoded `b64_json`.
- `ASPECTS = {"1:1": (1080, 1080), "4:5": (1080, 1350), "9:16": (1080, 1920)}`
- `fit_to_aspect(image: bytes, aspect: str) -> bytes` (center-crop then resize, JPEG q90).
- `overlay_logo(image: bytes, logo_png: bytes, position: str = "bottom_right", scale: float = 0.18, margin: float = 0.04) -> bytes`.
- `video_with_end_card(video: bytes, caption: str, logo_png: bytes | None, end_card_bg: str, aspect: str) -> bytes` (ffmpeg: drawtext caption at bottom, 1.5s end card with logo).

- [ ] **Step 1: Write failing tests**

`tests/generation/test_images.py`:

```python
import base64
from types import SimpleNamespace

from cs.generation.fakes import solid_png
from cs.generation.images import GatewayImageGenerator


async def test_generate_without_references_uses_generations(settings):
    calls = {}

    class Images:
        async def generate(self, **kw):
            calls["generate"] = kw
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(solid_png(64, 64)).decode())])

        async def edit(self, **kw):
            calls["edit"] = kw
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(solid_png(64, 64)).decode())])

    gen = GatewayImageGenerator(settings, client=SimpleNamespace(images=Images()))
    out = await gen.generate("a bottle", [], n=1)
    assert out[0][:8] == b"\x89PNG\r\n\x1a\n" and calls["generate"]["model"] == settings.image_model
    out = await gen.generate("a bottle", [solid_png(32, 32)], n=1)
    assert "edit" in calls and calls["edit"]["prompt"] == "a bottle"
```

`tests/generation/test_compose.py`:

```python
import io
import shutil

import pytest
from PIL import Image

from cs.generation.compose import fit_to_aspect, overlay_logo, video_with_end_card
from cs.generation.fakes import solid_png, tiny_mp4


def _size(b: bytes):
    return Image.open(io.BytesIO(b)).size


def test_fit_to_aspect_sizes():
    src = solid_png(2000, 1000)
    assert _size(fit_to_aspect(src, "1:1")) == (1080, 1080)
    assert _size(fit_to_aspect(src, "4:5")) == (1080, 1350)
    assert _size(fit_to_aspect(src, "9:16")) == (1080, 1920)


def test_overlay_logo_changes_corner():
    base = fit_to_aspect(solid_png(1080, 1080, (0, 0, 0)), "1:1")
    logo = solid_png(200, 200, (255, 255, 255))
    out = Image.open(io.BytesIO(overlay_logo(base, logo)))
    assert out.getpixel((1060, 1060))[0] > 200 and out.getpixel((20, 20))[0] < 30


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_with_end_card_extends_duration():
    from cs.tagging.video import probe_duration_ms

    out = video_with_end_card(tiny_mp4(2), "Sleep deeper", solid_png(200, 200), "#112233", "9:16")
    assert probe_duration_ms(out) >= 3300
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/generation/test_images.py tests/generation/test_compose.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Add `openai>=1.50` to dependencies; `uv sync --extra dev`.

`src/cs/generation/images.py`:

```python
from __future__ import annotations

import base64
import io
from typing import Protocol

import openai

from cs.core.config import Settings


class ImageGenerator(Protocol):
    async def generate(self, prompt: str, reference_images: list[bytes], n: int = 1) -> list[bytes]: ...


class GatewayImageGenerator:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(api_key=settings.ai_gateway_api_key,
                                                   base_url=f"{settings.ai_gateway_base_url}/v1")
        self.model = settings.image_model

    async def generate(self, prompt: str, reference_images: list[bytes], n: int = 1) -> list[bytes]:
        if reference_images:
            files = []
            for i, data in enumerate(reference_images[:4]):
                f = io.BytesIO(data); f.name = f"ref{i}.png"; files.append(f)
            result = await self.client.images.edit(model=self.model, image=files, prompt=prompt, n=n)
        else:
            result = await self.client.images.generate(model=self.model, prompt=prompt, n=n, response_format="b64_json")
        return [base64.b64decode(d.b64_json) for d in result.data if getattr(d, "b64_json", None)]
```

`src/cs/generation/compose.py`:

```python
from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ASPECTS = {"1:1": (1080, 1080), "4:5": (1080, 1350), "9:16": (1080, 1920)}


def fit_to_aspect(image: bytes, aspect: str) -> bytes:
    tw, th = ASPECTS[aspect]
    im = Image.open(io.BytesIO(image)).convert("RGB")
    w, h = im.size
    target = tw / th
    if w / h > target:
        nw = int(h * target); left = (w - nw) // 2; im = im.crop((left, 0, left + nw, h))
    else:
        nh = int(w / target); top = (h - nh) // 2; im = im.crop((0, top, w, top + nh))
    im = im.resize((tw, th), Image.LANCZOS)
    out = io.BytesIO(); im.save(out, format="JPEG", quality=90)
    return out.getvalue()


def overlay_logo(image: bytes, logo_png: bytes, position: str = "bottom_right", scale: float = 0.18, margin: float = 0.04) -> bytes:
    base = Image.open(io.BytesIO(image)).convert("RGBA")
    logo = Image.open(io.BytesIO(logo_png)).convert("RGBA")
    lw = int(base.width * scale)
    logo = logo.resize((lw, int(logo.height * lw / logo.width)), Image.LANCZOS)
    m = int(base.width * margin)
    x = base.width - logo.width - m if "right" in position else m
    y = base.height - logo.height - m if "bottom" in position else m
    base.alpha_composite(logo, (x, y))
    out = io.BytesIO(); base.convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()


def _hex_to_ffmpeg(color: str) -> str:
    return "0x" + color.lstrip("#")


def video_with_end_card(video: bytes, caption: str, logo_png: bytes | None, end_card_bg: str, aspect: str,
                        end_seconds: float = 1.5) -> bytes:
    tw, th = ASPECTS[aspect]
    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        (dd / "in.mp4").write_bytes(video)
        safe = caption.replace("'", "").replace(":", "\\:")
        vf = (f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},"
              f"drawtext=text='{safe}':fontcolor=white:fontsize={th // 22}:box=1:boxcolor=black@0.5:boxborderw=16:"
              f"x=(w-text_w)/2:y=h-text_h-{th // 12}")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(dd / "in.mp4"), "-vf", vf, "-an",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", str(dd / "main.mp4")], check=True)
        card_inputs = ["-f", "lavfi", "-i", f"color=c={_hex_to_ffmpeg(end_card_bg)}:s={tw}x{th}:d={end_seconds}:r=30"]
        if logo_png:
            (dd / "logo.png").write_bytes(logo_png)
            lw = tw // 3
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *card_inputs, "-i", str(dd / "logo.png"),
                            "-filter_complex", f"[1:v]scale={lw}:-1[l];[0:v][l]overlay=(W-w)/2:(H-h)/2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(end_seconds), str(dd / "card.mp4")], check=True)
        else:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *card_inputs, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-t", str(end_seconds), str(dd / "card.mp4")], check=True)
        (dd / "list.txt").write_text("file 'main.mp4'\nfile 'card.mp4'\n")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(dd / "list.txt"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dd / "out.mp4")], check=True)
        return (dd / "out.mp4").read_bytes()
```

- [ ] **Step 4: Run to verify they pass, then commit**

Run: `uv run pytest tests/generation/test_images.py tests/generation/test_compose.py -v`
Expected: PASS

```bash
git add src/cs/generation/images.py src/cs/generation/compose.py pyproject.toml uv.lock tests/generation
git commit -m "feat(generation): gateway image generation and Pillow/ffmpeg compositing"
```

---

### Task 4: Video generation via Node CLI

**Files:**
- Create: `tools/video-gen/package.json`, `tools/video-gen/index.mjs`, `tools/video-gen/README.md`, `src/cs/generation/video.py`
- Test: `tests/generation/test_video_gen.py`

**Interfaces:**
- CLI: `node tools/video-gen/index.mjs --model <slug> --image-url <https url> --prompt <text> --duration 5 --aspect 9:16 --out <path.mp4>`; reads `AI_GATEWAY_API_KEY`; exits 0 and writes the mp4, or exits 1 with a JSON error on stderr. `--dry-run` prints the resolved options as JSON and exits 0 without calling the gateway.
- `VideoGenerator` Protocol: `async generate(still_url: str, prompt: str, duration_s: int, aspect: str) -> bytes`.
- `NodeVideoGenerator(settings, script: Path = <repo>/tools/video-gen/index.mjs, timeout_s: int = 900)`.

- [ ] **Step 1: Write failing test**

`tests/generation/test_video_gen.py`:

```python
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cs.generation.video import NodeVideoGenerator

SCRIPT = Path(__file__).resolve().parents[2] / "tools/video-gen/index.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_cli_dry_run_parses_args():
    out = subprocess.run(["node", str(SCRIPT), "--dry-run", "--model", "m/x", "--image-url", "https://h/a.jpg",
                          "--prompt", "p", "--duration", "5", "--aspect", "9:16", "--out", "/tmp/x.mp4"],
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == {"model": "m/x", "imageUrl": "https://h/a.jpg", "prompt": "p", "duration": 5, "aspectRatio": "9:16", "out": "/tmp/x.mp4"}


async def test_node_generator_runs_script(settings, tmp_path):
    fake = tmp_path / "fake.mjs"
    fake.write_text("import fs from 'node:fs'; const i = process.argv.indexOf('--out'); fs.writeFileSync(process.argv[i+1], Buffer.from('MP4!'));")
    gen = NodeVideoGenerator(settings, script=fake)
    assert await gen.generate("https://h/a.jpg", "p", 5, "9:16") == b"MP4!"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/generation/test_video_gen.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the CLI**

`tools/video-gen/package.json`:

```json
{
  "name": "video-gen",
  "private": true,
  "type": "module",
  "dependencies": {
    "@ai-sdk/gateway": "latest",
    "ai": "latest"
  }
}
```

`tools/video-gen/index.mjs`:

```js
import fs from 'node:fs';
import { experimental_generateVideo as generateVideo } from 'ai';

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}

const opts = {
  model: arg('model'),
  imageUrl: arg('image-url'),
  prompt: arg('prompt'),
  duration: Number(arg('duration', '5')),
  aspectRatio: arg('aspect', '9:16'),
  out: arg('out'),
};

if (process.argv.includes('--dry-run')) {
  process.stdout.write(JSON.stringify(opts));
  process.exit(0);
}

for (const k of ['model', 'imageUrl', 'prompt', 'out']) {
  if (!opts[k]) { process.stderr.write(JSON.stringify({ error: `missing --${k}` })); process.exit(1); }
}

try {
  // A plain 'creator/model' string routes through AI Gateway. `poll` runs the job asynchronously so no single
  // request stays open for the whole generation; `prompt.image` must be a hosted URL for the async flow.
  const result = await generateVideo({
    model: opts.model,
    prompt: { image: opts.imageUrl, text: opts.prompt },
    duration: opts.duration,
    aspectRatio: opts.aspectRatio,
    poll: { intervalMs: 5000, timeoutMs: 15 * 60 * 1000 },
  });
  fs.writeFileSync(opts.out, result.videos[0].uint8Array);
  process.stdout.write(JSON.stringify({ ok: true, bytes: result.videos[0].uint8Array.length }));
} catch (err) {
  process.stderr.write(JSON.stringify({ error: String(err?.message ?? err) }));
  process.exit(1);
}
```

`tools/video-gen/README.md`:

```markdown
Video generation sidecar. Install once with `npm install` in this directory. The Python worker calls
`node index.mjs` with `AI_GATEWAY_API_KEY` in the environment. Check the current model list at
https://vercel.com/ai-gateway/models?capabilities=video-generation and set VIDEO_MODEL to an `i2v`-capable slug.
```

- [ ] **Step 4: Implement the Python wrapper**

`src/cs/generation/video.py`:

```python
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Protocol

from cs.core.config import Settings

DEFAULT_SCRIPT = Path(__file__).resolve().parents[3] / "tools" / "video-gen" / "index.mjs"


class VideoGenerator(Protocol):
    async def generate(self, still_url: str, prompt: str, duration_s: int, aspect: str) -> bytes: ...


class NodeVideoGenerator:
    def __init__(self, settings: Settings, script: Path = DEFAULT_SCRIPT, timeout_s: int = 900):
        self.settings = settings
        self.script = script
        self.timeout_s = timeout_s

    async def generate(self, still_url: str, prompt: str, duration_s: int, aspect: str) -> bytes:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.mp4"
            env = {**os.environ, "AI_GATEWAY_API_KEY": self.settings.ai_gateway_api_key}
            proc = await asyncio.create_subprocess_exec(
                "node", str(self.script), "--model", self.settings.video_model, "--image-url", still_url,
                "--prompt", prompt, "--duration", str(duration_s), "--aspect", aspect, "--out", str(out),
                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                _, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
            except TimeoutError:
                proc.kill()
                raise RuntimeError("video generation timed out") from None
            if proc.returncode != 0:
                raise RuntimeError(f"video-gen failed: {err.decode(errors='ignore')[-500:]}")
            return out.read_bytes()
```

- [ ] **Step 5: Install and run tests, then commit**

Run: `(cd tools/video-gen && npm install) && uv run pytest tests/generation/test_video_gen.py -v`
Expected: PASS

```bash
git add tools/video-gen/package.json tools/video-gen/package-lock.json tools/video-gen/index.mjs tools/video-gen/README.md src/cs/generation/video.py tests/generation/test_video_gen.py
git commit -m "feat(generation): image-to-video through AI Gateway via Node sidecar"
```

---

### Task 5: Generation repository, service, job, and API

**Files:**
- Create: `src/cs/generation/repo.py`, `src/cs/generation/service.py`, `src/cs/generation/jobs.py`, `src/cs/api/routers/generation.py`
- Modify: `src/cs/jobs/all_handlers.py`, `src/cs/api/app.py`
- Test: `tests/generation/test_service.py`, `tests/api/test_generation.py`

**Interfaces:**
- repo: `create_generation(db, org_id, brand_kit_id, kind, variants, created_by, source_insight_id=None, source_creative_ids=(), concept=None) -> UUID`; `set_generation_status(db, org_id, gid, status, error=None, model=None, prompt=None)`; `insert_generated_asset(db, org_id, gid, kind, aspect, variant, path, sha256, headline, primary_text, cta_type) -> UUID`; `list_generations(db, org_id, limit=50)`; `get_generation(db, org_id, gid)`; `list_generated_assets(db, org_id, generation_id=None, review_state=None, limit=100)`; `get_generated_asset(db, org_id, aid)`; `set_review_state(db, org_id, aid, state, user_id) -> bool` (only from `draft`).
- service: `async build_brief(db, org_id, generation_row) -> str` (from insight fields + explanation + suggested test, or concept; plus source creatives' headlines and top tags); `async run_generation(ctx, generation_id, copywriter, image_gen, video_gen) -> None`.
- Job `generation.run` payload `{"generation_id"}`; seams `COPYWRITER_FACTORY(settings)`, `IMAGE_FACTORY(settings)`, `VIDEO_FACTORY(settings)`.
- Routes: `POST /generations` `{kind, variants?, source_insight_id?, source_creative_ids?, concept?}` (requires active brand kit; enqueues job) → `{id}`; `GET /generations`; `GET /generations/{id}` → generation + assets with `url`.

- [ ] **Step 1: Write failing tests**

`tests/generation/test_service.py`:

```python
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from cs.core.queue import Job
from cs.generation import repo, service
from cs.generation.brand import create_brand_kit
from cs.generation.fakes import FakeCopyWriter, FakeImageGenerator, FakeVideoGenerator, solid_png
from cs.insights import repo as insight_repo
from cs.insights.types import InsightDraft
from cs.jobs.registry import JobContext


@pytest.fixture
async def kit(db, org_id, blobs):
    await blobs.put(f"{org_id}/brand/logo.png", solid_png(200, 200, (255, 255, 255)), "image/png")
    return await create_brand_kit(db, org_id, ["#112233"], ["Inter"], "Warm.", ["cures"], f"{org_id}/brand/logo.png", "s")


def _ctx(db, blobs, settings, org_id):
    return JobContext(db=db, blobs=blobs, settings=settings, job=Job(UUID(int=1), org_id, "generation.run", {}, 1, 5))


async def test_build_brief_from_insight(db, org_id, kit):
    d = InsightDraft("pattern", "hook_type", "question", "ctr", date(2026, 8, 1), date(2026, 8, 31), 6, Decimal("1500"), 0.02, 0.03, 0.5)
    iid = await insight_repo.insert_insight(db, org_id, d, "Questions win", "Test 2 more")
    gid = await repo.create_generation(db, org_id, kit.id, "static", 2, "u1", source_insight_id=iid)
    brief = await service.build_brief(db, org_id, await repo.get_generation(db, org_id, gid))
    assert "hook_type = question" in brief and "Questions win" in brief and "Test 2 more" in brief


async def test_run_static_generation_produces_assets(db, blobs, settings, org_id, kit):
    gid = await repo.create_generation(db, org_id, kit.id, "static", 2, "u1", concept="Morning routine")
    img = FakeImageGenerator()
    await service.run_generation(_ctx(db, blobs, settings, org_id), gid, FakeCopyWriter(), img, FakeVideoGenerator())
    assets = await repo.list_generated_assets(db, org_id, generation_id=gid)
    assert len(assets) == 6 and {a["aspect"] for a in assets} == {"1:1", "4:5", "9:16"}
    assert all(a["review_state"] == "draft" and a["headline"] for a in assets)
    assert (await repo.get_generation(db, org_id, gid))["status"] == "done"
    assert img.calls[0]["n"] == 2 and "Morning routine" in img.calls[0]["prompt"]
    assert await blobs.exists(assets[0]["path"])


async def test_run_video_generation_uses_first_still(db, blobs, settings, org_id, kit):
    gid = await repo.create_generation(db, org_id, kit.id, "video", 1, "u1", concept="c")
    vid = FakeVideoGenerator()
    await service.run_generation(_ctx(db, blobs, settings, org_id), gid, FakeCopyWriter(), FakeImageGenerator(), vid)
    assets = await repo.list_generated_assets(db, org_id, generation_id=gid)
    kinds = sorted(a["kind"] for a in assets)
    assert kinds.count("video") == 1 and kinds.count("image") == 3
    assert vid.calls[0]["aspect"] == "9:16" and vid.calls[0]["still_url"].endswith(".jpg")


async def test_copy_failure_marks_generation_failed(db, blobs, settings, org_id, kit):
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u1", concept="c")
    with pytest.raises(RuntimeError):
        await service.run_generation(_ctx(db, blobs, settings, org_id), gid, FakeCopyWriter(sets=[]), FakeImageGenerator(), FakeVideoGenerator())
    g = await repo.get_generation(db, org_id, gid)
    assert g["status"] == "failed" and "no copy" in g["error"]
```

`tests/api/test_generation.py`:

```python
from tests.conftest import dev_headers

H = dev_headers(org="org_gen")


async def test_generation_routes(client, db):
    r = await client.post("/generations", headers=H, json={"kind": "static", "concept": "x"})
    assert r.status_code == 400  # no brand kit
    await client.post("/brand-kit", headers=H, json={"colors": ["#000"], "fonts": [], "voice_rules": "", "prohibited_claims": []})
    r = await client.post("/generations", headers=H, json={"kind": "static", "concept": "x", "variants": 3})
    assert r.status_code == 200
    gid = r.json()["id"]
    assert await db.fetchval("SELECT job_type FROM job") == "generation.run"
    assert (await client.get("/generations", headers=H)).json()[0]["id"] == gid
    detail = (await client.get(f"/generations/{gid}", headers=H)).json()
    assert detail["status"] == "pending" and detail["assets"] == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/generation/test_service.py tests/api/test_generation.py -v`
Expected: FAIL

- [ ] **Step 3: Implement repo**

`src/cs/generation/repo.py`:

```python
from __future__ import annotations

from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id


async def create_generation(db: Database, org_id: UUID, brand_kit_id: UUID, kind: str, variants: int, created_by: str,
                            source_insight_id: UUID | None = None, source_creative_ids: tuple[UUID, ...] = (),
                            concept: str | None = None) -> UUID:
    gid = new_id()
    await db.execute(
        "INSERT INTO generation (id, org_id, brand_kit_id, source_insight_id, source_creative_ids, concept, kind, variants, status, created_by) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'pending',$9)",
        gid, org_id, brand_kit_id, source_insight_id, list(source_creative_ids), concept, kind, variants, created_by)
    return gid


async def set_generation_status(db: Database, org_id: UUID, gid: UUID, status: str, error: str | None = None,
                                model: str | None = None, prompt: str | None = None) -> None:
    await db.execute(
        "UPDATE generation SET status=$3, error=$4, model=coalesce($5, model), prompt=coalesce($6, prompt), "
        "finished_at = CASE WHEN $3 IN ('done','failed') THEN now() ELSE finished_at END WHERE org_id=$1 AND id=$2",
        org_id, gid, status, error, model, prompt)


async def get_generation(db: Database, org_id: UUID, gid: UUID) -> asyncpg.Record | None:
    return await db.fetchrow("SELECT * FROM generation WHERE org_id=$1 AND id=$2", org_id, gid)


async def list_generations(db: Database, org_id: UUID, limit: int = 50) -> list[asyncpg.Record]:
    return await db.fetch("SELECT * FROM generation WHERE org_id=$1 ORDER BY created_at DESC LIMIT $2", org_id, limit)


async def insert_generated_asset(db: Database, org_id: UUID, gid: UUID, kind: str, aspect: str, variant: int, path: str,
                                 sha256: str, headline: str, primary_text: str, cta_type: str) -> UUID:
    aid = new_id()
    await db.execute(
        "INSERT INTO generated_asset (id, generation_id, org_id, kind, aspect, variant, path, sha256, headline, primary_text, cta_type) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)", aid, gid, org_id, kind, aspect, variant, path, sha256, headline, primary_text, cta_type)
    return aid


async def list_generated_assets(db: Database, org_id: UUID, generation_id: UUID | None = None, review_state: str | None = None,
                                limit: int = 100) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT * FROM generated_asset WHERE org_id=$1 AND ($2::uuid IS NULL OR generation_id=$2) "
        "AND ($3::text IS NULL OR review_state=$3) ORDER BY created_at, variant, aspect LIMIT $4",
        org_id, generation_id, review_state, limit)


async def get_generated_asset(db: Database, org_id: UUID, aid: UUID) -> asyncpg.Record | None:
    return await db.fetchrow("SELECT * FROM generated_asset WHERE org_id=$1 AND id=$2", org_id, aid)


async def set_review_state(db: Database, org_id: UUID, aid: UUID, state: str, user_id: str) -> bool:
    res = await db.execute(
        "UPDATE generated_asset SET review_state=$3, reviewed_by=$4, reviewed_at=now() "
        "WHERE org_id=$1 AND id=$2 AND review_state='draft'", org_id, aid, state, user_id)
    return res.endswith("1")
```

- [ ] **Step 4: Implement service and jobs**

`src/cs/generation/service.py`:

```python
from __future__ import annotations

from uuid import UUID

from cs.core.blobs import sha256_hex
from cs.core.db import Database
from cs.generation import repo
from cs.generation.brand import get_brand_kit
from cs.generation.compose import ASPECTS, fit_to_aspect, overlay_logo, video_with_end_card
from cs.generation.copy import CopyWriter
from cs.generation.images import ImageGenerator
from cs.generation.video import VideoGenerator
from cs.jobs.registry import JobContext
from cs.tagging import repo as tag_repo

VIDEO_SECONDS = 5


async def build_brief(db: Database, org_id: UUID, g) -> str:
    lines: list[str] = []
    if g["source_insight_id"]:
        i = await db.fetchrow("SELECT * FROM insight WHERE org_id=$1 AND id=$2", org_id, g["source_insight_id"])
        if i:
            lines += [f"Winning pattern: {i['dimension']} = {i['value']} ({i['metric'].upper()} lift {i['lift'] * 100:+.0f}%).",
                      f"Why: {i['explanation']}", f"Direction: {i['suggested_test']}"]
    if g["concept"]:
        lines.append(f"Concept: {g['concept']}")
    for cid in g["source_creative_ids"] or []:
        c = await db.fetchrow("SELECT headline, primary_text FROM creative WHERE org_id=$1 AND id=$2", org_id, cid)
        if c:
            tags = await tag_repo.latest_tags(db, org_id, cid)
            top = ", ".join(f"{t['dimension']}={t['value']}" for t in tags[:8])
            lines.append(f"Reference creative: headline '{c['headline']}', text '{c['primary_text']}', tags: {top}")
    return "\n".join(lines) or "Fresh concept for the brand."


def _image_prompt(brief: str, kit, copy) -> str:
    return (f"Paid social ad image. {brief}\nHeadline to visually support (do not render text): {copy.headline}. "
            f"Brand colors: {', '.join(kit.colors) or 'any'}. Clean composition, room for a logo in the bottom right, "
            f"photo-real, no watermark, no text.")


async def run_generation(ctx: JobContext, generation_id: UUID, copywriter: CopyWriter, image_gen: ImageGenerator,
                         video_gen: VideoGenerator) -> None:
    org_id = ctx.job.org_id
    g = await repo.get_generation(ctx.db, org_id, generation_id)
    kit = await get_brand_kit(ctx.db, org_id, g["brand_kit_id"])
    await repo.set_generation_status(ctx.db, org_id, generation_id, "running")
    try:
        brief = await build_brief(ctx.db, org_id, g)
        sets = await copywriter.write(brief, kit, g["variants"])
        if not sets:
            raise RuntimeError("no copy passed the brand check")
        refs = []
        for cid in (g["source_creative_ids"] or [])[:2]:
            c = await ctx.db.fetchrow("SELECT a.path FROM creative c JOIN asset a ON a.id=c.primary_asset_id WHERE c.id=$1 AND c.format='image'", cid)
            if c:
                refs.append(await ctx.blobs.get(c["path"]))
        logo = await ctx.blobs.get(kit.logo_path) if kit.logo_path else None
        prompt = _image_prompt(brief, kit, sets[0])
        raws = await image_gen.generate(prompt, refs, n=len(sets))
        await repo.set_generation_status(ctx.db, org_id, generation_id, "running", model=ctx.settings.image_model, prompt=prompt)
        produced = 0
        first_still_key: str | None = None
        for variant, (raw, copy) in enumerate(zip(raws, sets, strict=False), start=1):
            for aspect in ASPECTS:
                img = fit_to_aspect(raw, aspect)
                if logo:
                    img = overlay_logo(img, logo)
                key = f"{org_id}/generated/{generation_id}/v{variant}-{aspect.replace(':', 'x')}.jpg"
                await ctx.blobs.put(key, img, "image/jpeg")
                await repo.insert_generated_asset(ctx.db, org_id, generation_id, "image", aspect, variant, key, sha256_hex(img),
                                                  copy.headline, copy.primary_text, copy.cta_type)
                produced += 1
                if aspect == "9:16" and first_still_key is None:
                    first_still_key = key
        if g["kind"] == "video" and first_still_key:
            still_url = await ctx.blobs.url_for(first_still_key, expires_seconds=3600)
            clip = await video_gen.generate(still_url, f"Subtle cinematic motion. {sets[0].headline}", VIDEO_SECONDS, "9:16")
            final = video_with_end_card(clip, sets[0].headline, logo, kit.colors[0] if kit.colors else "#000000", "9:16")
            key = f"{org_id}/generated/{generation_id}/v1-9x16.mp4"
            await ctx.blobs.put(key, final, "video/mp4")
            await repo.insert_generated_asset(ctx.db, org_id, generation_id, "video", "9:16", 1, key, sha256_hex(final),
                                              sets[0].headline, sets[0].primary_text, sets[0].cta_type)
            produced += 1
        await repo.set_generation_status(ctx.db, org_id, generation_id, "done")
    except Exception as exc:
        await repo.set_generation_status(ctx.db, org_id, generation_id, "failed", error=str(exc)[:2000])
        raise
```

`src/cs/generation/jobs.py`:

```python
from __future__ import annotations

from uuid import UUID

from cs.core.config import Settings
from cs.generation import service
from cs.generation.copy import ClaudeCopyWriter, CopyWriter
from cs.generation.images import GatewayImageGenerator, ImageGenerator
from cs.generation.video import NodeVideoGenerator, VideoGenerator
from cs.jobs.registry import JobContext, handler


def default_copywriter(settings: Settings) -> CopyWriter:
    return ClaudeCopyWriter(settings)


def default_image_gen(settings: Settings) -> ImageGenerator:
    return GatewayImageGenerator(settings)


def default_video_gen(settings: Settings) -> VideoGenerator:
    return NodeVideoGenerator(settings)


COPYWRITER_FACTORY = default_copywriter
IMAGE_FACTORY = default_image_gen
VIDEO_FACTORY = default_video_gen


@handler("generation.run")
async def run(ctx: JobContext) -> None:
    await service.run_generation(ctx, UUID(ctx.job.payload["generation_id"]), COPYWRITER_FACTORY(ctx.settings),
                                 IMAGE_FACTORY(ctx.settings), VIDEO_FACTORY(ctx.settings))
```

Add `import cs.generation.jobs  # noqa: F401` to `src/cs/jobs/all_handlers.py`.

- [ ] **Step 5: Implement router**

`src/cs/api/routers/generation.py`:

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.core import queue
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.generation import repo
from cs.generation.brand import get_active_brand_kit

router = APIRouter()


class NewGeneration(BaseModel):
    kind: str
    variants: int = 4
    source_insight_id: UUID | None = None
    source_creative_ids: list[UUID] = []
    concept: str | None = None


def _ser(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, list):
            out[k] = [str(x) if isinstance(x, UUID) else x for x in v]
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


@router.post("/generations")
async def create(body: NewGeneration, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    if body.kind not in ("static", "video"):
        raise HTTPException(400, "kind must be static or video")
    kit = await get_active_brand_kit(db, auth.org_id)
    if not kit:
        raise HTTPException(400, "create a brand kit first")
    if not body.concept and not body.source_insight_id:
        raise HTTPException(400, "provide a concept or a source insight")
    gid = await repo.create_generation(db, auth.org_id, kit.id, body.kind, max(1, min(body.variants, 8)), auth.user_id,
                                       body.source_insight_id, tuple(body.source_creative_ids), body.concept)
    await queue.enqueue(db, auth.org_id, "generation.run", {"generation_id": str(gid)}, dedup_key=f"gen:{gid}")
    return {"id": str(gid)}


@router.get("/generations")
async def list_gens(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    return [_ser(r) for r in await repo.list_generations(db, auth.org_id)]


@router.get("/generations/{gid}")
async def get_gen(gid: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db),
                  blobs: BlobStore = Depends(get_blobs)) -> dict:
    g = await repo.get_generation(db, auth.org_id, gid)
    if not g:
        raise HTTPException(404)
    assets = []
    for a in await repo.list_generated_assets(db, auth.org_id, generation_id=gid):
        assets.append({**_ser(a), "url": await blobs.url_for(a["path"])})
    return {**_ser(g), "assets": assets}
```

Include the router in `src/cs/api/app.py`.

- [ ] **Step 6: Run to verify they pass, then commit**

Run: `uv run pytest tests/generation tests/api/test_generation.py -v`
Expected: PASS

```bash
git add src/cs/generation src/cs/api src/cs/jobs/all_handlers.py tests/generation tests/api/test_generation.py
git commit -m "feat(generation): generation job producing copy, statics, and video drafts; generations API"
```

---

### Task 6: Review, Meta publish, targets, and loop closure

**Files:**
- Create: `src/cs/publish/__init__.py`, `src/cs/publish/meta.py`, `src/cs/publish/jobs.py`, `src/cs/api/routers/meta_targets.py`
- Modify: `src/cs/connectors/meta/connector.py` (implement `upload_image`, `upload_video`, `create_ad`, add `list_campaigns`, `list_adsets`), `src/cs/connectors/fakes.py` (add `list_campaigns`, `list_adsets`), `src/cs/connectors/base.py`, `src/cs/api/routers/generation.py` (review + publish routes), `src/cs/ingest/service.py` (link `generated_asset_id`), `src/cs/jobs/all_handlers.py`
- Test: `tests/connectors/test_meta_publish.py`, `tests/publish/test_publish.py`, `tests/api/test_review_publish.py`

**Interfaces:**
- `AdPlatformConnector` gains `async list_campaigns(account_id) -> list[dict]` (`{id, name, status}`) and `async list_adsets(account_id, campaign_id) -> list[dict]`.
- `MetaConnector.upload_image` → `POST act_{id}/adimages` multipart `filename` → returns `images[<name>].hash`; `upload_video` → `POST act_{id}/advideos` multipart `source` → `id`; `create_ad(account_id, adset_id, name, creative_spec, status)` → `POST act_{id}/adcreatives` with `object_story_spec` → creative id, then `POST act_{id}/ads` → ad id. `GraphClient.post(path, data=None, files=None) -> dict` is added.
- `publish.meta.build_creative_spec(asset_row, form: dict, page_id: str, image_hash: str | None, video_id: str | None) -> dict`.
- `publish.meta.publish_generated_asset(ctx, connector, publish_job_row) -> tuple[str, str]`.
- Job `publish.meta` payload `{"publish_job_id"}`.
- Routes: `POST /generated-assets/{id}/approve`, `/reject` → `{ok}`; `POST /generated-assets/{id}/publish` body `{ad_account_id, campaign_id, adset_id, page_id, headline?, primary_text?, cta_type?, link_url, status: "PAUSED"|"ACTIVE"}` → creates `publish_job` (only if `approved`) and enqueues; `GET /publish-jobs`; `GET /review-queue?state=draft` → assets with urls; `GET /accounts/{id}/campaigns`, `GET /accounts/{id}/adsets?campaign_id`.
- Loop closure: in `ingest.service.sync_account`, after `upsert_creative`, if `created` and a `publish_job` in this org has `platform_creative_id == c.platform_creative_id`, set `creative.generated_asset_id`.

- [ ] **Step 1: Write failing tests**

`tests/connectors/test_meta_publish.py`:

```python
import httpx
import respx

from cs.connectors.meta.client import GraphClient
from cs.connectors.meta.connector import MetaConnector

G = "https://graph.facebook.com/v21.0"


@respx.mock
async def test_upload_and_create_ad(settings):
    respx.post(f"{G}/act_1/adimages").mock(return_value=httpx.Response(200, json={"images": {"a.jpg": {"hash": "h1"}}}))
    respx.post(f"{G}/act_1/adcreatives").mock(return_value=httpx.Response(200, json={"id": "cr9"}))
    respx.post(f"{G}/act_1/ads").mock(return_value=httpx.Response(200, json={"id": "ad9"}))
    respx.get(f"{G}/act_1/campaigns").mock(return_value=httpx.Response(200, json={"data": [{"id": "c1", "name": "C", "status": "ACTIVE"}]}))
    async with httpx.AsyncClient() as http:
        mc = MetaConnector(GraphClient(settings, "tok", http))
        assert await mc.upload_image("1", b"jpg", "a.jpg") == "h1"
        assert await mc.create_ad("1", "as1", "My ad", {"page_id": "p"}, "PAUSED") == ("cr9", "ad9")
        assert (await mc.list_campaigns("1"))[0]["id"] == "c1"
```

`tests/publish/test_publish.py`:

```python
from uuid import UUID

import pytest

from cs.connectors.fakes import FakeConnector
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.generation import repo
from cs.generation.brand import create_brand_kit
from cs.generation.fakes import solid_png
from cs.ingest import repo as ingest_repo
from cs.ingest import service as ingest_service
from cs.jobs.worker import run_once
from cs.publish.meta import build_creative_spec


@pytest.fixture
async def approved_asset(db, org_id, blobs):
    kit = await create_brand_kit(db, org_id, [], [], "", [])
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u", concept="c")
    key = f"{org_id}/generated/{gid}/v1-1x1.jpg"
    await blobs.put(key, solid_png(64, 64), "image/jpeg")
    aid = await repo.insert_generated_asset(db, org_id, gid, "image", "1:1", 1, key, "s", "H", "P", "SHOP_NOW")
    assert await repo.set_review_state(db, org_id, aid, "approved", "u")
    return aid


def test_build_creative_spec_image():
    spec = build_creative_spec({"kind": "image", "headline": "H", "primary_text": "P", "cta_type": "SHOP_NOW"},
                               {"link_url": "https://x", "page_id": "p1"}, image_hash="h", video_id=None)
    assert spec["object_story_spec"]["page_id"] == "p1"
    ld = spec["object_story_spec"]["link_data"]
    assert ld["image_hash"] == "h" and ld["name"] == "H" and ld["call_to_action"]["type"] == "SHOP_NOW"


async def test_publish_job_and_loop_closure(db, blobs, settings, org_id, approved_asset, monkeypatch):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    fake = FakeConnector()
    monkeypatch.setattr(ingest_service, "CONNECTOR_FACTORY", lambda s, a, http, b: fake)
    pj = await db.fetchval(
        "INSERT INTO publish_job (id, org_id, generated_asset_id, ad_account_id, campaign_id, adset_id, ad_status_requested, form, status) "
        "VALUES ($1,$2,$3,$4,'c1','as1','PAUSED',$5,'pending') RETURNING id",
        UUID(int=7), org_id, approved_asset, acct, {"link_url": "https://x", "page_id": "p1"})
    await queue.enqueue(db, org_id, "publish.meta", {"publish_job_id": str(pj)})
    await run_once(db, blobs, settings, "w")
    row = await db.fetchrow("SELECT status, platform_creative_id, platform_ad_id FROM publish_job WHERE id=$1", pj)
    assert row["status"] == "done" and row["platform_creative_id"] == "cr_new_1" and row["platform_ad_id"] == "ad_new_1"
    assert fake.created_ads[0]["status"] == "PAUSED" and fake.uploads[0][0] == "image"

    # next sync sees the new ad and links the creative back to the generated asset
    from cs.connectors.base import PlatformAd, PlatformCreative
    fake.add_creative(PlatformCreative("cr_new_1", "image", "H", "P", None, "SHOP_NOW", "https://x", "https://img/n", "image/jpeg", {}), b"x")
    fake.add_ad(PlatformAd("ad_new_1", "My ad", "PAUSED", "as1", "c1", "cr_new_1"))
    await ingest_repo.set_ad_account_status(db, org_id, acct, "active")
    await queue.enqueue(db, org_id, "meta.sync_incremental", {"ad_account_id": str(acct)})
    while await run_once(db, blobs, settings, "w"):
        pass
    linked = await db.fetchval("SELECT generated_asset_id FROM creative WHERE platform_creative_id='cr_new_1'")
    assert linked == approved_asset
```

`tests/api/test_review_publish.py`:

```python
from cs.generation import repo
from cs.generation.brand import create_brand_kit
from tests.conftest import dev_headers

H = dev_headers(org="org_rev")


async def _asset(client, db):
    from uuid import UUID
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    kit = await create_brand_kit(db, org_id, [], [], "", [])
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u", concept="c")
    return org_id, await repo.insert_generated_asset(db, org_id, gid, "image", "1:1", 1, "p.jpg", "s", "H", "P", "SHOP_NOW")


async def test_review_transitions_and_publish_gate(client, db, settings):
    from cs.core.crypto import encrypt_str
    from cs.ingest import repo as ingest_repo
    org_id, aid = await _asset(client, db)
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    form = {"ad_account_id": str(acct), "campaign_id": "c", "adset_id": "a", "page_id": "p", "link_url": "https://x", "status": "PAUSED"}
    assert (await client.post(f"/generated-assets/{aid}/publish", headers=H, json=form)).status_code == 409
    assert len((await client.get("/review-queue?state=draft", headers=H)).json()) == 1
    assert (await client.post(f"/generated-assets/{aid}/approve", headers=H)).json() == {"ok": True}
    assert (await client.post(f"/generated-assets/{aid}/reject", headers=H)).json() == {"ok": False}
    r = await client.post(f"/generated-assets/{aid}/publish", headers=H, json=form)
    assert r.status_code == 200 and await db.fetchval("SELECT job_type FROM job") == "publish.meta"
    assert (await client.get("/publish-jobs", headers=H)).json()[0]["status"] == "pending"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/connectors/test_meta_publish.py tests/publish tests/api/test_review_publish.py -v`
Expected: FAIL

- [ ] **Step 3: Extend the connector base, fake, and Meta implementation**

Append to `AdPlatformConnector` in `src/cs/connectors/base.py`:

```python
    async def list_campaigns(self, account_id: str) -> list[dict]: ...
    async def list_adsets(self, account_id: str, campaign_id: str) -> list[dict]: ...
```

Append to `FakeConnector`:

```python
    async def list_campaigns(self, account_id: str) -> list[dict]:
        return [{"id": "c1", "name": "Campaign 1", "status": "ACTIVE"}]

    async def list_adsets(self, account_id: str, campaign_id: str) -> list[dict]:
        return [{"id": "as1", "name": "Ad set 1", "status": "ACTIVE", "campaign_id": campaign_id}]
```

Add to `GraphClient` in `src/cs/connectors/meta/client.py`:

```python
    async def post(self, path: str, data: dict | None = None, files: dict | None = None) -> dict:
        payload = {**(data or {}), "access_token": self.token}
        for attempt in range(6):
            resp = await self.http.post(f"{self.base}/{path}", data=payload, files=files, timeout=300)
            if resp.status_code == 200:
                return resp.json()
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            code = (body.get("error") or {}).get("code")
            if resp.status_code == 429 or code in RETRYABLE_CODES:
                await asyncio.sleep(self._backoff_seconds(resp, attempt))
                continue
            raise GraphError(f"{resp.status_code} POST {path}: {body.get('error') or resp.text[:200]}")
        raise GraphError(f"rate limit retries exhausted for POST {path}")
```

Replace the three `NotImplementedError` methods in `MetaConnector` and add the two list methods:

```python
    async def upload_image(self, account_id: str, data: bytes, name: str) -> str:
        res = await self.client.post(f"act_{account_id}/adimages", files={"filename": (name, data, "image/jpeg")})
        return next(iter(res["images"].values()))["hash"]

    async def upload_video(self, account_id: str, data: bytes, name: str) -> str:
        res = await self.client.post(f"act_{account_id}/advideos", files={"source": (name, data, "video/mp4")})
        return res["id"]

    async def create_ad(self, account_id: str, adset_id: str, name: str, creative_spec: dict, status: str) -> tuple[str, str]:
        cr = await self.client.post(f"act_{account_id}/adcreatives", data={"name": name, **{k: json.dumps(v) if isinstance(v, dict) else v for k, v in creative_spec.items()}})
        ad = await self.client.post(f"act_{account_id}/ads", data={"name": name, "adset_id": adset_id, "status": status,
                                                                  "creative": json.dumps({"creative_id": cr["id"]})})
        return cr["id"], ad["id"]

    async def list_campaigns(self, account_id: str) -> list[dict]:
        rows = await self.client.get_all(f"act_{account_id}/campaigns", {"fields": "id,name,status", "limit": 200})
        return [{"id": r["id"], "name": r.get("name", ""), "status": r.get("status", "")} for r in rows]

    async def list_adsets(self, account_id: str, campaign_id: str) -> list[dict]:
        rows = await self.client.get_all(f"{campaign_id}/adsets", {"fields": "id,name,status,campaign_id", "limit": 200})
        return [{"id": r["id"], "name": r.get("name", ""), "status": r.get("status", ""), "campaign_id": r.get("campaign_id")} for r in rows]
```

- [ ] **Step 4: Implement publish module**

`src/cs/publish/__init__.py`: empty.

`src/cs/publish/meta.py`:

```python
from __future__ import annotations

from cs.connectors.base import AdPlatformConnector
from cs.jobs.registry import JobContext


def build_creative_spec(asset: dict, form: dict, image_hash: str | None, video_id: str | None) -> dict:
    headline = form.get("headline") or asset["headline"]
    text = form.get("primary_text") or asset["primary_text"]
    cta = {"type": form.get("cta_type") or asset["cta_type"] or "LEARN_MORE", "value": {"link": form["link_url"]}}
    if asset["kind"] == "video":
        story = {"video_data": {"video_id": video_id, "title": headline, "message": text, "call_to_action": cta,
                                "image_hash": image_hash} if image_hash else
                 {"video_id": video_id, "title": headline, "message": text, "call_to_action": cta}}
    else:
        story = {"link_data": {"image_hash": image_hash, "link": form["link_url"], "name": headline, "message": text,
                               "call_to_action": cta}}
    return {"object_story_spec": {"page_id": form["page_id"], **story}}


async def publish_generated_asset(ctx: JobContext, connector: AdPlatformConnector, pj) -> tuple[str, str]:
    org_id = ctx.job.org_id
    asset = await ctx.db.fetchrow("SELECT * FROM generated_asset WHERE org_id=$1 AND id=$2", org_id, pj["generated_asset_id"])
    if asset["review_state"] != "approved":
        raise RuntimeError("asset is not approved")
    acct = await ctx.db.fetchrow("SELECT * FROM ad_account WHERE org_id=$1 AND id=$2", org_id, pj["ad_account_id"])
    data = await ctx.blobs.get(asset["path"])
    account_id = acct["platform_account_id"]
    image_hash = video_id = None
    if asset["kind"] == "video":
        video_id = await connector.upload_video(account_id, data, f"{asset['id']}.mp4")
    else:
        image_hash = await connector.upload_image(account_id, data, f"{asset['id']}.jpg")
    spec = build_creative_spec(dict(asset), pj["form"], image_hash, video_id)
    name = f"CS {asset['headline'][:40]} {str(asset['id'])[-6:]}"
    return await connector.create_ad(account_id, pj["adset_id"], name, spec, pj["ad_status_requested"])
```

`src/cs/publish/jobs.py`:

```python
from __future__ import annotations

from uuid import UUID

import httpx

from cs.ingest import service as ingest_service
from cs.jobs.registry import JobContext, handler
from cs.publish.meta import publish_generated_asset


@handler("publish.meta")
async def publish_meta(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    pj = await ctx.db.fetchrow("SELECT * FROM publish_job WHERE org_id=$1 AND id=$2", org_id, UUID(ctx.job.payload["publish_job_id"]))
    await ctx.db.execute("UPDATE publish_job SET status='running' WHERE id=$1", pj["id"])
    acct = await ctx.db.fetchrow("SELECT * FROM ad_account WHERE id=$1", pj["ad_account_id"])
    try:
        async with httpx.AsyncClient(timeout=300) as http:
            connector = ingest_service.CONNECTOR_FACTORY(ctx.settings, acct, http, ctx.blobs)
            creative_id, ad_id = await publish_generated_asset(ctx, connector, pj)
        await ctx.db.execute("UPDATE publish_job SET status='done', platform_creative_id=$2, platform_ad_id=$3, finished_at=now() WHERE id=$1",
                             pj["id"], creative_id, ad_id)
    except Exception as exc:
        await ctx.db.execute("UPDATE publish_job SET status='failed', error=$2, finished_at=now() WHERE id=$1", pj["id"], str(exc)[:2000])
        raise
```

Add `import cs.publish.jobs  # noqa: F401` to `src/cs/jobs/all_handlers.py`.

- [ ] **Step 5: Loop closure in ingest**

In `src/cs/ingest/service.py::sync_account`, right after `creative_id, created = await repo.upsert_creative(...)`, add:

```python
        if created:
            ga = await ctx.db.fetchval(
                "SELECT generated_asset_id FROM publish_job WHERE org_id=$1 AND platform_creative_id=$2 AND status='done' LIMIT 1",
                org_id, c.platform_creative_id)
            if ga:
                await ctx.db.execute("UPDATE creative SET generated_asset_id=$2 WHERE id=$1", creative_id, ga)
```

- [ ] **Step 6: Review, publish, and targets routes**

Append to `src/cs/api/routers/generation.py`:

```python
class PublishBody(BaseModel):
    ad_account_id: UUID
    campaign_id: str
    adset_id: str
    page_id: str
    link_url: str
    headline: str | None = None
    primary_text: str | None = None
    cta_type: str | None = None
    status: str = "PAUSED"


@router.get("/review-queue")
async def review_queue(state: str = "draft", auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db),
                       blobs: BlobStore = Depends(get_blobs)) -> list[dict]:
    out = []
    for a in await repo.list_generated_assets(db, auth.org_id, review_state=state):
        out.append({**_ser(a), "url": await blobs.url_for(a["path"])})
    return out


@router.post("/generated-assets/{aid}/approve")
async def approve(aid: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    return {"ok": await repo.set_review_state(db, auth.org_id, aid, "approved", auth.user_id)}


@router.post("/generated-assets/{aid}/reject")
async def reject(aid: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    return {"ok": await repo.set_review_state(db, auth.org_id, aid, "rejected", auth.user_id)}


@router.post("/generated-assets/{aid}/publish")
async def publish(aid: UUID, body: PublishBody, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    from cs.core.ids import new_id

    a = await repo.get_generated_asset(db, auth.org_id, aid)
    if not a:
        raise HTTPException(404)
    if a["review_state"] != "approved":
        raise HTTPException(409, "asset must be approved before publishing")
    if body.status not in ("PAUSED", "ACTIVE"):
        raise HTTPException(400, "status must be PAUSED or ACTIVE")
    pj = new_id()
    form = body.model_dump(mode="json")
    await db.execute(
        "INSERT INTO publish_job (id, org_id, generated_asset_id, ad_account_id, campaign_id, adset_id, ad_status_requested, form, status) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'pending')", pj, auth.org_id, aid, body.ad_account_id, body.campaign_id, body.adset_id, body.status, form)
    await queue.enqueue(db, auth.org_id, "publish.meta", {"publish_job_id": str(pj)}, dedup_key=f"publish:{pj}")
    return {"publish_job_id": str(pj)}


@router.get("/publish-jobs")
async def publish_jobs(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    rows = await db.fetch("SELECT * FROM publish_job WHERE org_id=$1 ORDER BY created_at DESC LIMIT 100", auth.org_id)
    return [_ser(r) for r in rows]
```

`src/cs/api/routers/meta_targets.py`:

```python
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db, get_settings_dep
from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database
from cs.ingest import repo, service

router = APIRouter(prefix="/accounts")


async def _connector_and_account(db, settings, blobs, auth, account_id, http):
    acct = await repo.get_ad_account(db, auth.org_id, account_id)
    if not acct:
        raise HTTPException(404)
    return service.CONNECTOR_FACTORY(settings, acct, http, blobs), acct


@router.get("/{account_id}/campaigns")
async def campaigns(account_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db),
                    settings: Settings = Depends(get_settings_dep), blobs: BlobStore = Depends(get_blobs)) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as http:
        c, acct = await _connector_and_account(db, settings, blobs, auth, account_id, http)
        return await c.list_campaigns(acct["platform_account_id"])


@router.get("/{account_id}/adsets")
async def adsets(account_id: UUID, campaign_id: str, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db),
                 settings: Settings = Depends(get_settings_dep), blobs: BlobStore = Depends(get_blobs)) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as http:
        c, acct = await _connector_and_account(db, settings, blobs, auth, account_id, http)
        return await c.list_adsets(acct["platform_account_id"], campaign_id)
```

Include `meta_targets.router` in `src/cs/api/app.py`.

- [ ] **Step 7: Run to verify they pass, then commit**

Run: `uv run pytest tests/connectors tests/publish tests/api -v`
Expected: PASS

```bash
git add src/cs tests
git commit -m "feat(publish): review transitions, Meta upload/create ad, publish job, loop closure"
```

---

### Task 7: End-to-end loop test

**Files:**
- Create: `tests/e2e/test_loop.py`

**Interfaces:**
- Uses only fakes: `FakeConnector`, `FakeTagger`, `FakeTranscriber`, `FakeEmbedder`, `FakeExplainer`, `FakeCopyWriter`, `FakeImageGenerator`, `FakeVideoGenerator`.

- [ ] **Step 1: Write the test**

`tests/e2e/test_loop.py`:

```python
"""Backfill -> tag -> insight -> generate -> approve -> publish -> sync closes the loop, all on fakes."""
from datetime import date, timedelta
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.connectors.fakes import FakeConnector
from cs.core import queue
from cs.generation import jobs as gen_jobs
from cs.generation.fakes import FakeCopyWriter, FakeImageGenerator, FakeVideoGenerator, solid_png
from cs.ingest import service as ingest_service
from cs.insights import jobs as insight_jobs
from cs.insights.fakes import FakeExplainer
from cs.jobs.worker import run_once
from cs.tagging import jobs as tag_jobs
from cs.tagging.fakes import FakeEmbedder, FakeTagger, FakeTranscriber
from tests.conftest import dev_headers

H = dev_headers(org="org_e2e")


async def _drain(db, blobs, settings):
    for _ in range(200):
        if not await run_once(db, blobs, settings, "w"):
            break


async def test_full_loop(client, db, blobs, settings, monkeypatch):
    fake = FakeConnector()
    monkeypatch.setattr(ingest_service, "CONNECTOR_FACTORY", lambda s, a, http, b: fake)
    q_tags = [{"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "e"}]
    s_tags = [{"dimension": "hook_type", "value": "statistic", "confidence": 0.9, "evidence": "e"}]
    monkeypatch.setattr(tag_jobs, "TAGGER_FACTORY", lambda s: FakeTagger(tags_by_call=[q_tags] * 6 + [s_tags] * 6))
    monkeypatch.setattr(tag_jobs, "TRANSCRIBER_FACTORY", lambda s, http: FakeTranscriber())
    monkeypatch.setattr(tag_jobs, "EMBEDDER_FACTORY", lambda s, http: FakeEmbedder())
    monkeypatch.setattr(insight_jobs, "EXPLAINER_FACTORY", lambda s: FakeExplainer())
    monkeypatch.setattr(gen_jobs, "COPYWRITER_FACTORY", lambda s: FakeCopyWriter())
    monkeypatch.setattr(gen_jobs, "IMAGE_FACTORY", lambda s: FakeImageGenerator())
    monkeypatch.setattr(gen_jobs, "VIDEO_FACTORY", lambda s: FakeVideoGenerator())

    days = [date.today() - timedelta(days=i + 1) for i in range(10)]
    for i in range(12):
        name = f"q{i}" if i < 6 else f"s{i}"
        ctr_clicks = 30 if i < 6 else 10
        fake.add_creative(PlatformCreative(name, "image", name, "P", None, "SHOP_NOW", "https://x", f"https://img/{name}", "image/png", {}), solid_png(64, 64))
        fake.add_ad(PlatformAd(f"ad_{name}", name, "ACTIVE", "as1", "c1", name))
        for d in days:
            fake.add_metric(MetricRow(f"ad_{name}", d, "facebook:feed", Decimal("30"), 1000, ctr_clicks, ctr_clicks, Decimal("1"), Decimal("50"), 0, 0, 0, 0, {}))

    # 1. connect + activate (backfill)
    from uuid import UUID
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    from cs.core.crypto import encrypt_str
    from cs.ingest import repo as ingest_repo
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "Main", "USD", encrypt_str(settings, "t"), None)
    await client.post(f"/accounts/{acct}/activate", headers=H)
    await _drain(db, blobs, settings)
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='done'") == 12

    # 2. insights
    await queue.enqueue(db, org_id, "insights.compute_patterns", {"ad_account_id": str(acct)})
    await _drain(db, blobs, settings)
    ins = (await client.get("/insights?kind=pattern", headers=H)).json()
    winner = next(i for i in ins if i["value"] == "question" and i["metric"] == "ctr")
    assert winner["lift"] > 0

    # 3. brand kit + generate from the insight
    await client.post("/brand-kit", headers=H, json={"colors": ["#112233"], "fonts": [], "voice_rules": "", "prohibited_claims": []})
    gid = (await client.post("/generations", headers=H, json={"kind": "static", "variants": 1, "source_insight_id": winner["id"]})).json()["id"]
    await _drain(db, blobs, settings)
    detail = (await client.get(f"/generations/{gid}", headers=H)).json()
    assert detail["status"] == "done" and len(detail["assets"]) == 3

    # 4. approve + publish
    aid = detail["assets"][0]["id"]
    await client.post(f"/generated-assets/{aid}/approve", headers=H)
    r = await client.post(f"/generated-assets/{aid}/publish", headers=H, json={
        "ad_account_id": str(acct), "campaign_id": "c1", "adset_id": "as1", "page_id": "p", "link_url": "https://x", "status": "PAUSED"})
    assert r.status_code == 200
    await _drain(db, blobs, settings)
    pj = (await client.get("/publish-jobs", headers=H)).json()[0]
    assert pj["status"] == "done" and pj["platform_ad_id"] == "ad_new_1"

    # 5. next sync closes the loop
    fake.add_creative(PlatformCreative("cr_new_1", "image", "Sleep deeper", "P", None, "SHOP_NOW", "https://x", "https://img/new", "image/png", {}), solid_png(64, 64))
    fake.add_ad(PlatformAd("ad_new_1", "CS ad", "PAUSED", "as1", "c1", "cr_new_1"))
    await client.post(f"/accounts/{acct}/sync", headers=H)
    await _drain(db, blobs, settings)
    linked = await db.fetchval("SELECT generated_asset_id FROM creative WHERE platform_creative_id='cr_new_1'")
    assert str(linked) == aid
```

- [ ] **Step 2: Run the whole suite**

Run: `make lint && make test`
Expected: PASS including `tests/e2e/test_loop.py`

- [ ] **Step 3: Commit**

```bash
git add tests/e2e
git commit -m "test: end-to-end loop on fakes from backfill to loop closure"
```

---

## Self-review

- **Spec coverage:** brand kit versioned with logo, colors, fonts, voice, prohibited claims → Task 1. Copy with second-pass brand check → Task 2. Statics via gateway with references, resized to the three aspects, logo composited → Tasks 3, 5. Video via gateway image-to-video seeded from a still, captions + end card → Tasks 4, 5. Drafts, one-way review, publish form with campaign/ad set fetched live, paused by default → Task 6. Loop closure on next sync → Task 6 + e2e Task 7. Partial success: `run_generation` records each produced asset as it goes, so an image failure mid-way leaves earlier assets in place and the generation `failed` with the error.
- **Placeholder scan:** none.
- **Type consistency:** `FakeConnector.created_ads[i]["status"]`, `uploads[i][0]`, `create_ad` return `("cr_new_n", "ad_new_n")` match the Phase 1 fake. `repo.set_review_state` returns bool used by routes. Job payload keys: `generation_id`, `publish_job_id`. Seams: `COPYWRITER_FACTORY`, `IMAGE_FACTORY`, `VIDEO_FACTORY`, `ingest_service.CONNECTOR_FACTORY`.
