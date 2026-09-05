# Phase 2: Creative Tagging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose every image and video creative into structured, versioned tags with Claude, store them with confidence and evidence, embed each creative for similarity search, and support bulk backfills through the Message Batches API.

**Architecture:** A YAML taxonomy compiles to a JSON schema used for structured output. `tagging/image.py` and `tagging/video.py` prepare inputs (resized image; keyframes + transcript for video). `tagging/runner.py` builds the prompt, calls a `Tagger` (Claude behind a Protocol, with a fake for tests), validates, and persists `tag_run` + `tag` rows. Embeddings go through an `Embedder` Protocol to the AI Gateway's OpenAI-compatible endpoint. Batches use a `BatchTagger` Protocol.

**Tech Stack:** `anthropic` Python SDK (>=1.0), Pillow, PyYAML, ffmpeg (subprocess), httpx, pgvector.

**Spec:** `docs/superpowers/specs/2026-09-04-creative-strategist-design.md` (section 6)

## Global Constraints

- Default tagging model `claude-opus-5` (from `settings.claude_model_tagging`); adaptive thinking (default); `output_config.format` JSON schema; refusal fallback via `client.beta.messages.create(..., betas=["server-side-fallback-2026-07-01"], fallbacks="default")`; always check `stop_reason == "refusal"` before reading content.
- Batches API requests must **not** include `fallbacks`/`betas` (rejected on batches).
- Images sent to Claude are resized so the longest side ≤ 1568px and encoded JPEG quality 85.
- Video: keyframes at 0s, 1s, 3s plus scene changes (threshold 0.3), capped at 24 frames total, ordered by timestamp; audio transcribed through the AI Gateway REST endpoint `POST {ai_gateway_base_url}/v4/ai/transcription-model` with header `ai-model-id`.
- `tag_run` unique on `(creative_id, taxonomy_version_id, model)`.
- Embedding dimension 1536 (`openai/text-embedding-3-small`).
- All Phase 0 and Phase 1 constraints apply.

---

## File structure

```
src/cs/tagging/reconcile.py          # reenqueue_untagged(db) -> int (Task 2: Phase 1 tagging debt)
scripts/reenqueue_tagging.py         # CLI wrapper for reconcile.reenqueue_untagged
db/migrations/20260904000004_tagging.sql
src/cs/tagging/__init__.py
src/cs/tagging/taxonomy.yaml         # the taxonomy (versioned by content hash)
src/cs/tagging/taxonomy.py           # load_taxonomy() -> Taxonomy; to_json_schema(); validate_tags()
src/cs/tagging/prompts.py            # system + user prompt builders
src/cs/tagging/images.py             # prepare_image(bytes) -> (jpeg bytes, w, h)
src/cs/tagging/video.py              # extract_keyframes(), extract_audio(), Transcriber + GatewayTranscriber
src/cs/tagging/tagger.py             # Tagger Protocol, TagResult, ClaudeTagger
src/cs/tagging/batch.py              # BatchTagger Protocol, ClaudeBatchTagger
src/cs/tagging/embed.py              # Embedder Protocol, GatewayEmbedder, tag_text()
src/cs/tagging/fakes.py              # FakeTagger, FakeTranscriber, FakeEmbedder, FakeBatchTagger
src/cs/tagging/repo.py               # taxonomy_version, tag_run, tag, creative_embedding persistence + queries
src/cs/tagging/runner.py             # tag_creative(ctx, creative_id, tagger, transcriber) orchestration
src/cs/tagging/jobs.py               # tagging.tag_creative, tagging.embed_creative, tagging.batch_submit, tagging.batch_poll
src/cs/api/routers/tags.py           # /creatives/{id}/tags, /tags/dimensions, /creatives/{id}/retag, /creatives/{id}/similar
scripts/eval_tagging.py              # agreement vs tests/evals/labeled.jsonl (real API, opt-in)
tests/evals/labeled.jsonl            # hand-labeled creatives from the real account (Task 8)
tests/tagging/...
```

---

### Task 1: Migration, taxonomy, and schema compilation

**Files:**
- Create: `db/migrations/20260904000004_tagging.sql`, `src/cs/tagging/__init__.py`, `src/cs/tagging/taxonomy.yaml`, `src/cs/tagging/taxonomy.py`
- Test: `tests/tagging/test_taxonomy.py`

**Interfaces:**
- Tables: `taxonomy_version(id, version TEXT, yaml_sha256, created_at)`, `tag_run(id, org_id, creative_id, taxonomy_version_id, model, status, prompt_sha256, input_tokens, output_tokens, error, created_at, finished_at)`, `tag(id, org_id, tag_run_id, creative_id, dimension, value, confidence, evidence)`, `creative_embedding(creative_id PK, org_id, tag_run_id, embedding vector(1536), created_at)`.
- `Dimension(name: str, group: str, applies_to: list[str], values: list[str] | None, description: str)`; `Taxonomy(version: str, sha256: str, dimensions: list[Dimension])`.
- `load_taxonomy(path=None) -> Taxonomy` (cached); `taxonomy.for_format(fmt) -> list[Dimension]`; `taxonomy.json_schema(fmt) -> dict`; `taxonomy.validate(tags: list[dict], fmt) -> list[dict]` (drops unknown dimensions, coerces unknown enum values to `"other"`, clamps confidence to [0,1]).

- [ ] **Step 1: Migration**

`db/migrations/20260904000004_tagging.sql`:

```sql
-- migrate:up
CREATE TABLE taxonomy_version (
  id UUID PRIMARY KEY,
  version TEXT NOT NULL,
  yaml_sha256 TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tag_run (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  creative_id UUID NOT NULL REFERENCES creative(id),
  taxonomy_version_id UUID NOT NULL REFERENCES taxonomy_version(id),
  model TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','running','done','failed')),
  prompt_sha256 TEXT,
  input_tokens INT,
  output_tokens INT,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE (creative_id, taxonomy_version_id, model)
);

CREATE TABLE tag (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  tag_run_id UUID NOT NULL REFERENCES tag_run(id),
  creative_id UUID NOT NULL REFERENCES creative(id),
  dimension TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL NOT NULL,
  evidence TEXT
);
CREATE INDEX tag_org_dim_val_idx ON tag (org_id, dimension, value);
CREATE INDEX tag_creative_idx ON tag (creative_id, tag_run_id);

CREATE TABLE creative_embedding (
  creative_id UUID PRIMARY KEY REFERENCES creative(id),
  org_id UUID NOT NULL REFERENCES org(id),
  tag_run_id UUID NOT NULL REFERENCES tag_run(id),
  embedding vector(1536) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX creative_embedding_hnsw ON creative_embedding USING hnsw (embedding vector_cosine_ops);

-- migrate:down
DROP TABLE creative_embedding;
DROP TABLE tag;
DROP TABLE tag_run;
DROP TABLE taxonomy_version;
```

- [ ] **Step 2: Write the taxonomy**

`src/cs/tagging/taxonomy.yaml`:

```yaml
version: "1.0"
dimensions:
  # ---- hook ----
  - name: hook_type
    group: hook
    applies_to: [image, video, carousel]
    values: [problem_statement, question, bold_claim, statistic, social_proof, before_after, curiosity_gap, offer_led, demo_first, ugc_reaction, other]
    description: The attention device used in the first moment (first frame / first 3 seconds / headline).
  - name: first_3s_content
    group: hook
    applies_to: [video]
    values: [product_closeup, person_talking, text_overlay, problem_scene, result_scene, brand_logo, other]
    description: What dominates the first three seconds of the video.
  - name: hook_text_present
    group: hook
    applies_to: [image, video, carousel]
    values: ["yes", "no"]
    description: Whether on-asset text functions as a hook.
  # ---- message ----
  - name: message_angle
    group: message
    applies_to: [image, video, carousel]
    values: [pain_relief, aspiration, convenience, savings, quality, status, fear_of_missing_out, community, health, novelty, other]
    description: The primary persuasive angle.
  - name: offer_type
    group: message
    applies_to: [image, video, carousel]
    values: [none, discount_percent, discount_amount, free_trial, free_shipping, bundle, limited_time, gift, other]
    description: The explicit offer, if any.
  - name: urgency
    group: message
    applies_to: [image, video, carousel]
    values: [none, mild, strong]
    description: Degree of urgency or scarcity language.
  - name: proof_type
    group: message
    applies_to: [image, video, carousel]
    values: [none, testimonial, review_stars, expert, press, numbers, comparison, guarantee, other]
    description: Kind of proof shown.
  - name: claim_specificity
    group: message
    applies_to: [image, video, carousel]
    values: [vague, moderate, specific]
    description: How concrete the main claim is.
  # ---- cta ----
  - name: cta_style
    group: cta
    applies_to: [image, video, carousel]
    values: [none, button_only, spoken, on_asset_text, spoken_and_text, other]
    description: How the call to action is delivered.
  - name: cta_verb
    group: cta
    applies_to: [image, video, carousel]
    values: [shop, learn, try, get, start, sign_up, download, book, other, none]
    description: The imperative verb of the CTA.
  # ---- visual ----
  - name: visual_style
    group: visual
    applies_to: [image, video, carousel]
    values: [ugc, studio_product, lifestyle, animation, screenshot_ui, text_only, meme, collage, illustration, other]
    description: The overall production style.
  - name: color_palette
    group: visual
    applies_to: [image, video, carousel]
    values: [bright, muted, dark, monochrome, brand_colors, pastel, high_contrast, other]
    description: Dominant palette.
  - name: text_density
    group: visual
    applies_to: [image, video, carousel]
    values: [none, low, medium, high]
    description: Amount of on-asset text.
  - name: layout
    group: visual
    applies_to: [image, carousel]
    values: [single_focus, split, grid, text_top_image_bottom, image_top_text_bottom, overlay_center, other]
    description: Spatial arrangement.
  - name: brand_presence
    group: visual
    applies_to: [image, video, carousel]
    values: [none, subtle, prominent]
    description: Logo/brand visibility.
  - name: product_visible
    group: visual
    applies_to: [image, video, carousel]
    values: ["yes", "no"]
    description: Whether the product itself is shown.
  - name: product_timing
    group: visual
    applies_to: [video]
    values: [first_3s, 3_to_10s, after_10s, never]
    description: When the product first appears.
  # ---- people ----
  - name: people_present
    group: people
    applies_to: [image, video, carousel]
    values: [none, one, two_plus]
    description: Number of people visible.
  - name: face_closeup
    group: people
    applies_to: [image, video, carousel]
    values: ["yes", "no"]
    description: A face fills a large share of the frame at some point.
  - name: presenter_type
    group: people
    applies_to: [video]
    values: [none, creator_ugc, founder, actor, customer, expert, other]
    description: Who presents, if anyone.
  - name: demographic_signal
    group: people
    applies_to: [image, video, carousel]
    values: [none, young_adult, adult, senior, family, other]
    description: Apparent target demographic shown.
  # ---- tone ----
  - name: tone
    group: tone
    applies_to: [image, video, carousel]
    values: [playful, serious, urgent, warm, authoritative, humorous, inspirational, neutral, other]
    description: Emotional register.
  - name: humor_present
    group: tone
    applies_to: [image, video, carousel]
    values: ["yes", "no"]
    description: Any humor.
  # ---- format ----
  - name: aspect_ratio
    group: format
    applies_to: [image, video, carousel]
    values: ["1:1", "4:5", "9:16", "16:9", other]
    description: Aspect ratio of the asset.
  - name: format_subtype
    group: format
    applies_to: [image, video, carousel]
    values: [static, gif_like, talking_head, montage, tutorial, unboxing, testimonial, comparison, listicle, other]
    description: Recognizable ad sub-format.
  # ---- video only ----
  - name: pacing
    group: video
    applies_to: [video]
    values: [slow, medium, fast]
    description: Cut frequency and motion.
  - name: music_present
    group: video
    applies_to: [video]
    values: ["yes", "no", unknown]
    description: Whether music is audible in the transcript/audio.
  - name: voiceover
    group: video
    applies_to: [video]
    values: [none, on_camera, off_camera, both]
    description: Spoken narration type.
  - name: caption_style
    group: video
    applies_to: [video]
    values: [none, subtitles, kinetic_text, headline_cards, mixed]
    description: On-screen text treatment.
  - name: storyline
    group: video
    applies_to: [video]
    values: [problem_solution, day_in_life, testimonial, demo, before_after, listicle, narrative, other]
    description: Overall story structure.
  - name: ending
    group: video
    applies_to: [video]
    values: [cta_card, product_shot, logo, loop, abrupt, other]
    description: How the video ends.
  # ---- free text summaries ----
  - name: hook_summary
    group: summary
    applies_to: [image, video, carousel]
    values: null
    description: One sentence describing the hook verbatim or paraphrased.
  - name: message_summary
    group: summary
    applies_to: [image, video, carousel]
    values: null
    description: One sentence describing the main message.
```

- [ ] **Step 3: Write failing tests**

`tests/tagging/test_taxonomy.py`:

```python
from cs.tagging.taxonomy import load_taxonomy


def test_load_and_filter():
    t = load_taxonomy()
    assert t.version == "1.0" and len(t.sha256) == 64
    names = {d.name for d in t.for_format("image")}
    assert "hook_type" in names and "pacing" not in names
    assert "pacing" in {d.name for d in t.for_format("video")}


def test_json_schema_shape():
    schema = load_taxonomy().json_schema("video")
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    item = schema["properties"]["tags"]["items"]
    assert set(item["required"]) == {"dimension", "value", "confidence", "evidence"}
    assert "pacing" in item["properties"]["dimension"]["enum"]


def test_validate_coerces_and_drops():
    t = load_taxonomy()
    out = t.validate([
        {"dimension": "hook_type", "value": "question", "confidence": 1.4, "evidence": "e"},
        {"dimension": "hook_type", "value": "made_up", "confidence": 0.5, "evidence": "e"},
        {"dimension": "pacing", "value": "fast", "confidence": 0.9, "evidence": "e"},
        {"dimension": "nope", "value": "x", "confidence": 0.9, "evidence": "e"},
        {"dimension": "hook_summary", "value": "Opens with a question", "confidence": 0.9, "evidence": "e"},
    ], "image")
    assert [(o["dimension"], o["value"], o["confidence"]) for o in out] == [
        ("hook_type", "question", 1.0), ("hook_type", "other", 0.5), ("hook_summary", "Opens with a question", 0.9)]
```

- [ ] **Step 4: Run to verify they fail**

Run: `make migrate-test && uv run pytest tests/tagging/test_taxonomy.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 5: Implement**

Add `pyyaml>=6` and `pillow>=10` to `pyproject.toml` dependencies, then `uv sync --extra dev`.

`src/cs/tagging/__init__.py`: empty.

`src/cs/tagging/taxonomy.py`:

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).with_name("taxonomy.yaml")


@dataclass(frozen=True)
class Dimension:
    name: str
    group: str
    applies_to: tuple[str, ...]
    values: tuple[str, ...] | None
    description: str


@dataclass(frozen=True)
class Taxonomy:
    version: str
    sha256: str
    dimensions: tuple[Dimension, ...]

    def for_format(self, fmt: str) -> list[Dimension]:
        return [d for d in self.dimensions if fmt in d.applies_to]

    def json_schema(self, fmt: str) -> dict:
        dims = self.for_format(fmt)
        return {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "dimension": {"type": "string", "enum": [d.name for d in dims]},
                            "value": {"type": "string"},
                            "confidence": {"type": "number"},
                            "evidence": {"type": "string"},
                        },
                        "required": ["dimension", "value", "confidence", "evidence"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["tags"],
            "additionalProperties": False,
        }

    def validate(self, tags: list[dict], fmt: str) -> list[dict]:
        by_name = {d.name: d for d in self.for_format(fmt)}
        out: list[dict] = []
        for t in tags:
            d = by_name.get(t.get("dimension"))
            if d is None:
                continue
            value = str(t.get("value", "")).strip()
            if d.values is not None and value not in d.values:
                value = "other" if "other" in d.values else d.values[0]
            conf = float(t.get("confidence", 0.0))
            out.append({
                "dimension": d.name,
                "value": value,
                "confidence": max(0.0, min(1.0, conf)),
                "evidence": str(t.get("evidence", ""))[:500],
            })
        return out


@lru_cache
def load_taxonomy(path: Path | None = None) -> Taxonomy:
    p = path or DEFAULT_PATH
    raw = p.read_bytes()
    data = yaml.safe_load(raw)
    dims = tuple(
        Dimension(
            name=d["name"], group=d["group"], applies_to=tuple(d["applies_to"]),
            values=tuple(str(v) for v in d["values"]) if d.get("values") else None,
            description=d["description"],
        )
        for d in data["dimensions"]
    )
    return Taxonomy(version=str(data["version"]), sha256=hashlib.sha256(raw).hexdigest(), dimensions=dims)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/tagging/test_taxonomy.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add db/migrations/20260904000004_tagging.sql src/cs/tagging pyproject.toml uv.lock tests/tagging/test_taxonomy.py
git commit -m "feat(tagging): taxonomy v1.0, schema compilation, and tagging tables"
```

---

### Task 2: Reconcile Phase 1 tagging debt — carousel card downloads and dead-job re-enqueue

**Context:** Per `docs/superpowers/plans/README.md`'s Phase 1 follow-ups: every `tagging.tag_creative`
job Phase 1 enqueued has no handler yet and dead-letters after 5 attempts, and carousel creatives get
the `carousel` format label but no asset download, so they are never tagged. This task closes both
gaps before any tagging logic is built, so the moment Task 5 registers the `tagging.tag_creative`
handler, every eligible creative — including carousels — already has a path to a tag run.

**Files:**
- Modify: `src/cs/connectors/base.py`, `src/cs/connectors/meta/parse.py`, `src/cs/connectors/fakes.py`, `src/cs/ingest/service.py`, `src/cs/ingest/jobs.py`
- Create: `tests/fixtures/meta/creative_carousel.json`, `src/cs/tagging/reconcile.py`, `scripts/reenqueue_tagging.py`
- Test: extend `tests/connectors/test_meta_parse.py`, extend `tests/ingest/test_jobs.py`, new `tests/tagging/test_reconcile.py`

**Interfaces:**
- `PlatformCreative.child_asset_urls: list[str] | None = None` (new field, default `None` so every
  existing positional construction across Phase 1 tests keeps working).
- `parse.creative_from_graph` sets `child_asset_urls` from `object_story_spec.link_data.child_attachments[].picture`
  when `format == "carousel"`; `asset_url` becomes `None` for both `video` and `carousel` (carousels
  never carry one usable top-level image).
- `service.download_carousel(ctx, connector, creative_id, asset_urls) -> None`: downloads each card,
  stores it as an `original_image` asset with `seq=i`, sets the primary asset to card 0, and enqueues
  `tagging.tag_creative` — mirroring `download_creative`. Because the primary asset then points at a
  normal `original_image` row, the Task 5 runner tags carousels exactly like single images with zero
  changes to its code.
- Job `meta.download_carousel` payload `{"creative_id", "asset_urls"}`, dedup key `str(creative_id)`.
- `cs.tagging.reconcile.reenqueue_untagged(db) -> int`: finds every creative with a `primary_asset_id`
  and no `done` `tag_run`, re-enqueues `tagging.tag_creative` (dedup key `f"tag:{creative_id}"`, so
  it's a no-op against a job that's already pending/running and a fresh row against one that has gone
  `dead`), returns the count enqueued.

- [ ] **Step 1: Write failing tests**

`tests/fixtures/meta/creative_carousel.json`:

```json
{"id": "5003", "name": "Carousel C",
 "object_story_spec": {"link_data": {"message": "Swipe to see more", "link": "https://example.com/c",
   "call_to_action": {"type": "SHOP_NOW"},
   "child_attachments": [
     {"link": "https://example.com/c1", "name": "Card 1", "picture": "https://scontent.example/card1.jpg"},
     {"link": "https://example.com/c2", "name": "Card 2", "picture": "https://scontent.example/card2.jpg"}
   ]}}}
```

Append to `tests/connectors/test_meta_parse.py`:

```python
def test_creative_carousel_extracts_card_urls():
    c = parse.creative_from_graph(json.loads((FX / "creative_carousel.json").read_text()))
    assert c.format == "carousel" and c.asset_url is None
    assert c.child_asset_urls == [
        "https://scontent.example/card1.jpg", "https://scontent.example/card2.jpg"]
```

Append to `tests/ingest/test_jobs.py`:

```python
async def test_backfill_downloads_carousel_cards_and_sets_primary(db, blobs, settings, org_id, account_id, fake):
    fake.add_creative(
        PlatformCreative("c3", "carousel", "Swipe", "P3", None, "SHOP_NOW", "https://z", None, None, {},
                         child_asset_urls=["https://img/card0", "https://img/card1"]),
        child_assets=[b"CARD0DATA", b"CARD1DATA"],
    )
    fake.add_ad(PlatformAd("ad3", "C", "ACTIVE", "as1", "cp1", "c3"))
    await queue.enqueue(db, org_id, "meta.backfill", {"ad_account_id": str(account_id)})
    await _drain(db, blobs, settings)
    cid = await db.fetchval("SELECT id FROM creative WHERE platform_creative_id='c3'")
    assets = await db.fetch("SELECT kind, seq FROM asset WHERE creative_id=$1 ORDER BY seq", cid)
    assert [(a["kind"], a["seq"]) for a in assets] == [("original_image", 0), ("original_image", 1)]
    primary_path = await db.fetchval(
        "SELECT a.path FROM creative c JOIN asset a ON a.id = c.primary_asset_id WHERE c.id=$1", cid)
    assert primary_path is not None and "card_00" in primary_path
```

`tests/tagging/test_reconcile.py`:

```python
import io

import pytest
from PIL import Image

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.core.ids import new_id
from cs.ingest import repo as ingest_repo
from cs.tagging.reconcile import reenqueue_untagged


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (16, 16)).save(b, format="PNG")
    return b.getvalue()


@pytest.fixture
async def untagged_and_tagged(db, org_id, settings, blobs):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    ids = []
    for n in ("c1", "c2"):
        cid, _ = await ingest_repo.upsert_creative(
            db, org_id, acct, PlatformCreative(n, "image", "H", "P", None, None, "u", None, None, {}), "raw")
        key = f"{org_id}/creatives/{cid}/original.png"
        await blobs.put(key, _png(), "image/png")
        aid = await ingest_repo.insert_asset(db, org_id, cid, "original_image", key, "sha", "image/png")
        await ingest_repo.set_primary_asset(db, org_id, cid, aid)
        ids.append(cid)
    tv_id = new_id()
    await db.execute("INSERT INTO taxonomy_version (id, version, yaml_sha256) VALUES ($1, '1.0', 'sha-x')", tv_id)
    await db.execute(
        "INSERT INTO tag_run (id, org_id, creative_id, taxonomy_version_id, model, status) "
        "VALUES ($1,$2,$3,$4,'fake','done')", new_id(), org_id, ids[1], tv_id)
    return ids  # (untagged, tagged)


async def test_reenqueues_only_creatives_without_a_done_run(db, untagged_and_tagged):
    untagged, _tagged = untagged_and_tagged
    n = await reenqueue_untagged(db)
    assert n == 1
    job = await db.fetchrow("SELECT job_type, payload FROM job")
    assert job["job_type"] == "tagging.tag_creative" and job["payload"]["creative_id"] == str(untagged)


async def test_reenqueue_is_a_noop_against_a_pending_job(db, untagged_and_tagged):
    await reenqueue_untagged(db)
    n = await reenqueue_untagged(db)
    assert n == 0
    assert await db.fetchval("SELECT count(*) FROM job") == 1


async def test_reenqueue_revives_a_dead_lettered_job(db, org_id, untagged_and_tagged):
    untagged, _tagged = untagged_and_tagged
    await queue.enqueue(db, org_id, "tagging.tag_creative", {"creative_id": str(untagged)},
                        dedup_key=f"tag:{untagged}")
    await db.execute("UPDATE job SET status='dead' WHERE job_type='tagging.tag_creative'")
    n = await reenqueue_untagged(db)
    assert n == 1
    assert await db.fetchval("SELECT count(*) FROM job WHERE status='pending'") == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/connectors/test_meta_parse.py tests/ingest/test_jobs.py tests/tagging/test_reconcile.py -v`
Expected: FAIL (`child_asset_urls` unexpected kwarg / `ModuleNotFoundError: cs.tagging.reconcile`)

- [ ] **Step 3: Implement carousel parsing**

Modify `src/cs/connectors/base.py` — add to `PlatformCreative`, after the `raw` field:

```python
    child_asset_urls: list[str] | None = None
```

Modify `src/cs/connectors/meta/parse.py` — add a helper and wire it into `creative_from_graph`:

```python
def _carousel_card_urls(link: dict) -> list[str] | None:
    cards = link.get("child_attachments") or []
    urls = [c["picture"] for c in cards if c.get("picture")]
    return urls or None
```

Change the `asset_url` line to `asset_url = None if fmt in ("video", "carousel") else obj.get("image_url")`,
and add `child_asset_urls=_carousel_card_urls(link) if fmt == "carousel" else None` to the returned
`PlatformCreative(...)`.

- [ ] **Step 4: Implement carousel download**

Modify `src/cs/connectors/fakes.py` — extend `add_creative`:

```python
    def add_creative(self, c: PlatformCreative, asset_bytes: bytes | None = None,
                      child_assets: list[bytes] | None = None) -> None:
        self.creatives[c.platform_creative_id] = c
        if asset_bytes is not None and c.asset_url:
            self.assets[c.asset_url] = (asset_bytes, c.asset_mime or "application/octet-stream")
        if child_assets and c.child_asset_urls:
            for url, data in zip(c.child_asset_urls, child_assets):
                self.assets[url] = (data, "image/jpeg")
```

Modify `src/cs/ingest/service.py` — in `sync_account`, replace the single asset-download block with:

```python
            kind = "original_video" if c.format == "video" else "original_image"
            if c.format == "carousel" and c.child_asset_urls:
                if not await repo.creative_has_asset(ctx.db, org_id, creative_id, "original_image"):
                    await queue.enqueue(
                        ctx.db, org_id, "meta.download_carousel",
                        {"creative_id": str(creative_id), "asset_urls": c.child_asset_urls},
                        dedup_key=str(creative_id),
                    )
            elif c.asset_url and not await repo.creative_has_asset(ctx.db, org_id, creative_id, kind):
                await queue.enqueue(
                    ctx.db, org_id, "meta.download_creative",
                    {"creative_id": str(creative_id), "asset_url": c.asset_url, "kind": kind},
                    dedup_key=str(creative_id),
                )
```

and add, alongside `download_creative`:

```python
async def download_carousel(
    ctx: JobContext, connector: AdPlatformConnector, creative_id: UUID, asset_urls: list[str]
) -> None:
    org_id = ctx.job.org_id
    primary_id = None
    for i, url in enumerate(asset_urls):
        data, mime = await connector.download_asset(url)
        ext = {"image/png": "png", "image/jpeg": "jpg"}.get(mime, "jpg")
        key = f"{org_id}/creatives/{creative_id}/card_{i:02d}.{ext}"
        await ctx.blobs.put(key, data, mime)
        asset_id = await repo.insert_asset(
            ctx.db, org_id, creative_id, "original_image", key, sha256_hex(data), mime, seq=i
        )
        if i == 0:
            primary_id = asset_id
    if primary_id is not None:
        await repo.set_primary_asset(ctx.db, org_id, creative_id, primary_id)
        await queue.enqueue(
            ctx.db, org_id, "tagging.tag_creative", {"creative_id": str(creative_id)},
            dedup_key=f"tag:{creative_id}",
        )
```

Modify `src/cs/ingest/jobs.py` — add:

```python
@handler("meta.download_carousel")
async def download_carousel(ctx: JobContext) -> None:
    p = ctx.job.payload
    creative_id = UUID(p["creative_id"])
    acct = await ctx.db.fetchrow(
        "SELECT a.* FROM ad_account a JOIN creative c ON c.ad_account_id = a.id "
        "WHERE c.id = $1 AND c.org_id = $2",
        creative_id,
        ctx.job.org_id,
    )
    if acct is None:
        raise RuntimeError("ad_account not found for creative")
    async with httpx.AsyncClient(timeout=120) as http:
        connector = service.CONNECTOR_FACTORY(ctx.settings, acct, http, ctx.blobs)
        await service.download_carousel(ctx, connector, creative_id, p["asset_urls"])
    log.info(
        "carousel downloaded",
        extra={"creative_id": str(creative_id), "cards": len(p["asset_urls"])},
    )
```

- [ ] **Step 5: Implement the re-enqueue reconciliation**

`src/cs/tagging/reconcile.py`:

```python
from __future__ import annotations

from cs.core import queue
from cs.core.db import Database


async def reenqueue_untagged(db: Database) -> int:
    """Re-enqueue tagging for every creative with a primary asset and no
    completed tag_run. Idempotent: enqueue() no-ops against a job that is
    already pending/running under the same dedup key, and inserts a fresh
    pending row for one that has gone 'dead'."""
    rows = await db.fetch(
        """
        SELECT c.id, c.org_id FROM creative c
        WHERE c.primary_asset_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM tag_run r WHERE r.creative_id = c.id AND r.status = 'done')
        """
    )
    n = 0
    for r in rows:
        jid = await queue.enqueue(
            db, r["org_id"], "tagging.tag_creative", {"creative_id": str(r["id"])},
            dedup_key=f"tag:{r['id']}",
        )
        n += int(jid is not None)
    return n
```

`scripts/reenqueue_tagging.py`:

```python
"""Re-enqueue tagging for creatives Phase 1 left behind: their
tagging.tag_creative job dead-lettered before this phase's handler existed,
or (for carousels) they had no asset to tag at all until Task 2 added
carousel card downloads.

Usage: uv run python scripts/reenqueue_tagging.py
"""
import asyncio

from cs.core.config import get_settings
from cs.core.db import Database
from cs.tagging.reconcile import reenqueue_untagged


async def main() -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        n = await reenqueue_untagged(db)
        print(f"re-enqueued {n} tagging job(s)")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/connectors tests/ingest tests/tagging -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/cs/connectors src/cs/ingest src/cs/tagging/reconcile.py scripts/reenqueue_tagging.py \
        tests/connectors/test_meta_parse.py tests/fixtures/meta/creative_carousel.json \
        tests/ingest/test_jobs.py tests/tagging/test_reconcile.py
git commit -m "fix(tagging): carousel card downloads and re-enqueue Phase 1's orphaned tagging jobs"
```

---

### Task 3: Image preparation, prompts, and the Tagger protocol with Claude implementation

**Files:**
- Create: `src/cs/tagging/images.py`, `src/cs/tagging/prompts.py`, `src/cs/tagging/tagger.py`, `src/cs/tagging/fakes.py`
- Test: `tests/tagging/test_images.py`, `tests/tagging/test_tagger.py`

**Interfaces:**
- `prepare_image(data: bytes, max_side: int = 1568) -> tuple[bytes, int, int]` → JPEG bytes, width, height.
- `prompts.SYSTEM: str`; `prompts.user_text(taxonomy, fmt, copy: dict, transcript: str | None, frame_timestamps: list[float] | None) -> str`; `prompts.prompt_sha(system, user_text, schema) -> str`.
- `@dataclass TagResult(tags: list[dict], input_tokens: int, output_tokens: int, model: str, refused: bool = False)`
- `Tagger` Protocol: `async tag(images: list[tuple[bytes, str]], user_text: str, schema: dict) -> TagResult` (images are `(jpeg_bytes, "image/jpeg")`).
- `ClaudeTagger(settings, client: anthropic.AsyncAnthropic | None = None, model: str | None = None)`.
- `FakeTagger(tags_by_call: list[list[dict]] | None = None)` returns canned tags and records calls in `.calls`.

- [ ] **Step 1: Write failing tests**

`tests/tagging/test_images.py`:

```python
import io

from PIL import Image

from cs.tagging.images import prepare_image


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_prepare_resizes_and_encodes_jpeg():
    data, w, h = prepare_image(_png(4000, 2000))
    assert (w, h) == (1568, 784)
    assert data[:3] == b"\xff\xd8\xff"


def test_prepare_keeps_small_images():
    data, w, h = prepare_image(_png(300, 300))
    assert (w, h) == (300, 300)
```

`tests/tagging/test_tagger.py`:

```python
import json
from types import SimpleNamespace

from cs.tagging.prompts import SYSTEM, prompt_sha, user_text
from cs.tagging.tagger import ClaudeTagger
from cs.tagging.taxonomy import load_taxonomy


class _FakeBetaMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _client(response):
    return SimpleNamespace(beta=SimpleNamespace(messages=_FakeBetaMessages(response)))


def _response(text, stop="end_turn"):
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model="claude-opus-5",
    )


async def test_claude_tagger_sends_images_and_parses_json(settings):
    payload = {"tags": [{"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "e"}]}
    client = _client(_response(json.dumps(payload)))
    tagger = ClaudeTagger(settings, client=client)
    schema = load_taxonomy().json_schema("image")
    res = await tagger.tag([(b"\xff\xd8\xff", "image/jpeg")], "describe", schema)
    assert res.tags == payload["tags"] and res.input_tokens == 10 and not res.refused
    call = client.beta.messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    content = call["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert content[-1]["type"] == "text"


async def test_claude_tagger_handles_refusal(settings):
    client = _client(_response("", stop="refusal"))
    res = await ClaudeTagger(settings, client=client).tag([], "x", {"type": "object"})
    assert res.refused and res.tags == []


def test_prompt_builders():
    t = load_taxonomy()
    text = user_text(t, "video", {"headline": "H", "primary_text": "P"}, "hello world", [0.0, 1.0])
    assert "pacing" in text and "hello world" in text and "t=1.0s" in text
    assert len(prompt_sha(SYSTEM, text, {"a": 1})) == 64
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/tagging/test_images.py tests/tagging/test_tagger.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement images and prompts**

`src/cs/tagging/images.py`:

```python
import io

from PIL import Image


def prepare_image(data: bytes, max_side: int = 1568) -> tuple[bytes, int, int]:
    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB")
    w, h = im.size
    scale = max(w, h) / max_side
    if scale > 1:
        im = im.resize((round(w / scale), round(h / scale)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue(), im.size[0], im.size[1]
```

`src/cs/tagging/prompts.py`:

```python
from __future__ import annotations

import hashlib
import json

from cs.tagging.taxonomy import Taxonomy

SYSTEM = (
    "You are a senior performance-creative analyst. You decompose paid social ad creatives into "
    "structured elements so a team can learn which elements drive performance. Look at every image "
    "carefully; for videos the images are keyframes in time order with timestamps and a transcript. "
    "For each dimension listed, pick exactly one value from its allowed values (or write a short free-text "
    "value where no values are listed), give a confidence between 0 and 1, and cite concrete visual or "
    "textual evidence. Emit one entry per dimension; do not skip dimensions."
)


def user_text(taxonomy: Taxonomy, fmt: str, copy: dict, transcript: str | None,
              frame_timestamps: list[float] | None) -> str:
    lines = [f"Creative format: {fmt}", "", "Ad copy fields:"]
    for k in ("headline", "primary_text", "description", "cta_type", "link_url"):
        if copy.get(k):
            lines.append(f"- {k}: {copy[k]}")
    if frame_timestamps:
        lines += ["", "Keyframe timestamps (in the order the images appear): "
                  + ", ".join(f"t={t:.1f}s" for t in frame_timestamps)]
    if transcript is not None:
        lines += ["", "Transcript:", transcript.strip() or "(no speech detected)"]
    lines += ["", "Dimensions to tag:"]
    for d in taxonomy.for_format(fmt):
        vals = ", ".join(d.values) if d.values else "free text, one sentence"
        lines.append(f"- {d.name} ({d.group}): {d.description} Allowed values: {vals}")
    lines += ["", 'Return JSON: {"tags": [{"dimension", "value", "confidence", "evidence"}, ...]}']
    return "\n".join(lines)


def prompt_sha(system: str, user: str, schema: dict) -> str:
    h = hashlib.sha256()
    h.update(system.encode()); h.update(b"\x00"); h.update(user.encode()); h.update(b"\x00")
    h.update(json.dumps(schema, sort_keys=True).encode())
    return h.hexdigest()
```

- [ ] **Step 4: Implement tagger and fakes**

Add `anthropic>=1.0` to `pyproject.toml` dependencies; `uv sync --extra dev`.

`src/cs/tagging/tagger.py`:

```python
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Protocol

import anthropic

from cs.core.config import Settings


@dataclass
class TagResult:
    tags: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    refused: bool = False


class Tagger(Protocol):
    async def tag(self, images: list[tuple[bytes, str]], user_text: str, schema: dict) -> TagResult: ...


def build_content(images: list[tuple[bytes, str]], user_text: str) -> list[dict]:
    content: list[dict] = []
    for i, (data, mime) in enumerate(images):
        content.append({"type": "text", "text": f"Image {i + 1}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                                                     "data": base64.standard_b64encode(data).decode()}})
    content.append({"type": "text", "text": user_text})
    return content


class ClaudeTagger:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None, model: str | None = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.model = model or settings.claude_model_tagging

    async def tag(self, images: list[tuple[bytes, str]], user_text: str, schema: dict) -> TagResult:
        from cs.tagging.prompts import SYSTEM

        resp = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            messages=[{"role": "user", "content": build_content(images, user_text)}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        usage = getattr(resp, "usage", None)
        result = TagResult(input_tokens=getattr(usage, "input_tokens", 0) or 0,
                           output_tokens=getattr(usage, "output_tokens", 0) or 0, model=resp.model)
        if resp.stop_reason == "refusal":
            result.refused = True
            return result
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        result.tags = json.loads(text).get("tags", []) if text else []
        return result
```

`src/cs/tagging/fakes.py`:

```python
from __future__ import annotations

from cs.tagging.tagger import TagResult

DEFAULT_TAGS = [
    {"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "opens with a question"},
    {"dimension": "message_angle", "value": "pain_relief", "confidence": 0.8, "evidence": "mentions tiredness"},
    {"dimension": "visual_style", "value": "ugc", "confidence": 0.7, "evidence": "handheld look"},
    {"dimension": "hook_summary", "value": "Asks if you are tired of waking up tired", "confidence": 0.9, "evidence": "headline"},
]


class FakeTagger:
    def __init__(self, tags_by_call: list[list[dict]] | None = None, refuse: bool = False):
        self.tags_by_call = list(tags_by_call or [])
        self.refuse = refuse
        self.calls: list[dict] = []

    async def tag(self, images, user_text, schema) -> TagResult:
        self.calls.append({"n_images": len(images), "user_text": user_text, "schema": schema})
        if self.refuse:
            return TagResult(model="fake", refused=True)
        tags = self.tags_by_call.pop(0) if self.tags_by_call else DEFAULT_TAGS
        return TagResult(tags=list(tags), input_tokens=100, output_tokens=50, model="fake")


class FakeTranscriber:
    def __init__(self, text: str = "hello world"):
        self.text = text
        self.calls = 0

    async def transcribe(self, audio: bytes, media_type: str) -> str:
        self.calls += 1
        return self.text


class FakeEmbedder:
    dim = 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for i, ch in enumerate(t.encode()[: self.dim]):
                v[i] = (ch % 97) / 97.0
            out.append(v)
        return out


class FakeBatchTagger:
    def __init__(self):
        self.submitted: dict[str, list[dict]] = {}
        self.ended: set[str] = set()

    async def submit(self, requests: list[dict]) -> str:
        bid = f"batch_{len(self.submitted) + 1}"
        self.submitted[bid] = requests
        return bid

    async def poll(self, batch_id: str) -> tuple[bool, list[dict]]:
        if batch_id not in self.ended:
            return False, []
        return True, [{"custom_id": r["custom_id"], "ok": True, "tags": DEFAULT_TAGS, "input_tokens": 1, "output_tokens": 1}
                      for r in self.submitted[batch_id]]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/tagging/test_images.py tests/tagging/test_tagger.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cs/tagging pyproject.toml uv.lock tests/tagging
git commit -m "feat(tagging): image prep, prompts, Claude tagger with structured output and refusal handling"
```

---

### Task 4: Video keyframes, audio, and transcription

**Files:**
- Create: `src/cs/tagging/video.py`
- Test: `tests/tagging/test_video.py`

**Interfaces:**
- `@dataclass Keyframe(timestamp: float, jpeg: bytes, width: int, height: int)`
- `extract_keyframes(video: bytes, max_frames: int = 24, scene_threshold: float = 0.3) -> list[Keyframe]` (ffmpeg via subprocess; fixed 0s/1s/3s plus scene changes; sorted; deduped by timestamp).
- `extract_audio(video: bytes) -> bytes | None` (mp3 mono 16kHz; `None` if no audio stream).
- `probe_duration_ms(video: bytes) -> int`.
- `Transcriber` Protocol: `async transcribe(audio: bytes, media_type: str) -> str`.
- `GatewayTranscriber(settings, http: httpx.AsyncClient)` → POST `{base}/v4/ai/transcription-model`, headers `Authorization: Bearer`, `ai-model-id`, JSON `{"audio": b64, "mediaType": media_type}` → `.text`.

- [ ] **Step 1: Write failing tests**

`tests/tagging/test_video.py`:

```python
import shutil
import subprocess

import httpx
import pytest
import respx

from cs.tagging.video import GatewayTranscriber, extract_audio, extract_keyframes, probe_duration_ms

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory) -> bytes:
    p = tmp_path_factory.mktemp("v") / "s.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(p),
    ], check=True)
    return p.read_bytes()


def test_keyframes_include_fixed_timestamps(sample_video):
    frames = extract_keyframes(sample_video)
    ts = [round(f.timestamp, 1) for f in frames]
    assert ts[:3] == [0.0, 1.0, 3.0] and len(frames) <= 24
    assert frames[0].jpeg[:3] == b"\xff\xd8\xff" and frames[0].width == 320


def test_audio_and_duration(sample_video):
    assert probe_duration_ms(sample_video) in range(4900, 5200)
    audio = extract_audio(sample_video)
    assert audio and audio[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3")


@respx.mock
async def test_gateway_transcriber(settings):
    settings.ai_gateway_api_key = "k"
    route = respx.post("https://ai-gateway.vercel.sh/v4/ai/transcription-model").mock(
        return_value=httpx.Response(200, json={"text": "hi there", "segments": [], "language": "en"}))
    async with httpx.AsyncClient() as http:
        text = await GatewayTranscriber(settings, http).transcribe(b"abc", "audio/mpeg")
    assert text == "hi there"
    req = route.calls[0].request
    assert req.headers["ai-model-id"] == "openai/whisper-1" and req.headers["authorization"] == "Bearer k"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/tagging/test_video.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/cs/tagging/video.py`:

```python
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from cs.core.config import Settings
from cs.tagging.images import prepare_image

FIXED_TIMESTAMPS = (0.0, 1.0, 3.0)


@dataclass
class Keyframe:
    timestamp: float
    jpeg: bytes
    width: int
    height: int


def _run(cmd: list[str], input_bytes: bytes | None = None) -> bytes:
    proc = subprocess.run(cmd, input=input_bytes, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr.decode(errors='ignore')[-500:]}")
    return proc.stdout


def probe_duration_ms(video: bytes) -> int:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(video); f.flush()
        out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", f.name])
    return int(float(json.loads(out)["format"]["duration"]) * 1000)


def _scene_timestamps(path: str, threshold: float) -> list[float]:
    proc = subprocess.run(
        ["ffmpeg", "-i", path, "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    out: list[float] = []
    for line in proc.stderr.splitlines():
        if "showinfo" in line and "pts_time:" in line:
            out.append(float(line.split("pts_time:")[1].split()[0]))
    return out


def _frame_at(path: str, ts: float) -> bytes:
    return _run(["ffmpeg", "-loglevel", "error", "-ss", f"{ts:.3f}", "-i", path, "-frames:v", "1",
                 "-f", "image2", "-vcodec", "png", "-"])


def extract_keyframes(video: bytes, max_frames: int = 24, scene_threshold: float = 0.3) -> list[Keyframe]:
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "in.mp4")
        Path(path).write_bytes(video)
        duration = probe_duration_ms(video) / 1000
        stamps = [t for t in FIXED_TIMESTAMPS if t < duration]
        for t in _scene_timestamps(path, scene_threshold):
            if all(abs(t - s) > 0.4 for s in stamps):
                stamps.append(t)
        stamps = sorted(stamps)
        if len(stamps) > max_frames:
            fixed = [t for t in stamps if t in FIXED_TIMESTAMPS]
            rest = [t for t in stamps if t not in FIXED_TIMESTAMPS]
            step = max(1, len(rest) // (max_frames - len(fixed)))
            stamps = sorted(fixed + rest[::step][: max_frames - len(fixed)])
        frames: list[Keyframe] = []
        for t in stamps:
            png = _frame_at(path, t)
            jpeg, w, h = prepare_image(png)
            frames.append(Keyframe(t, jpeg, w, h))
        return frames


def extract_audio(video: bytes) -> bytes | None:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(video); f.flush()
        probe = _run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                      "-of", "json", f.name])
        if not json.loads(probe).get("streams"):
            return None
        return _run(["ffmpeg", "-loglevel", "error", "-i", f.name, "-vn", "-ac", "1", "-ar", "16000",
                     "-b:a", "48k", "-f", "mp3", "-"])


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, media_type: str) -> str: ...


class GatewayTranscriber:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http

    async def transcribe(self, audio: bytes, media_type: str) -> str:
        r = await self.http.post(
            f"{self.settings.ai_gateway_base_url}/v4/ai/transcription-model",
            headers={"Authorization": f"Bearer {self.settings.ai_gateway_api_key}",
                     "ai-model-id": self.settings.transcription_model, "Content-Type": "application/json"},
            json={"audio": base64.b64encode(audio).decode(), "mediaType": media_type},
            timeout=300,
        )
        r.raise_for_status()
        return r.json().get("text", "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tagging/test_video.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cs/tagging/video.py tests/tagging/test_video.py
git commit -m "feat(tagging): ffmpeg keyframes and audio extraction, gateway transcriber"
```

---

### Task 5: Tagging repository, runner, and job

**Files:**
- Create: `src/cs/tagging/repo.py`, `src/cs/tagging/runner.py`, `src/cs/tagging/jobs.py`
- Modify: `src/cs/jobs/all_handlers.py` (add `import cs.tagging.jobs`)
- Test: `tests/tagging/test_runner.py`

**Interfaces:**
- repo: `ensure_taxonomy_version(db, taxonomy) -> UUID`; `start_tag_run(db, org_id, creative_id, taxonomy_version_id, model) -> UUID | None` (None if a `done` run already exists); `finish_tag_run(db, org_id, run_id, tags, prompt_sha, input_tokens, output_tokens)`; `fail_tag_run(db, org_id, run_id, error)`; `latest_tags(db, org_id, creative_id) -> list[Record]`; `latest_run_id(db, org_id, creative_id) -> UUID | None`.
- runner: `async tag_creative(ctx: JobContext, creative_id: UUID, tagger: Tagger, transcriber: Transcriber) -> UUID | None` (run id). Reads the primary asset from blobs; images → `prepare_image`; videos → keyframes (also stored as `asset` rows kind `keyframe` + `transcript`), audio → transcript; builds prompt; calls tagger; validates; persists; enqueues `tagging.embed_creative`.
- Testing seams: `cs.tagging.jobs.TAGGER_FACTORY(settings) -> Tagger`, `TRANSCRIBER_FACTORY(settings, http) -> Transcriber`.
- Job `tagging.tag_creative` payload `{"creative_id"}`; `tagging.retag_creative` same payload but forces a new run with the current taxonomy/model by deleting nothing — it is a no-op if a done run exists for the same `(taxonomy, model)`, otherwise runs.

- [ ] **Step 1: Write failing tests**

`tests/tagging/test_runner.py`:

```python
import io
import shutil
import subprocess
from uuid import UUID

import pytest
from PIL import Image

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.ingest import repo as ingest_repo
from cs.tagging import jobs as tagging_jobs
from cs.tagging import repo, runner
from cs.tagging.fakes import FakeTagger, FakeTranscriber
from cs.jobs.registry import JobContext
from cs.core.queue import Job


def _png():
    b = io.BytesIO(); Image.new("RGB", (64, 64), (0, 0, 255)).save(b, format="PNG"); return b.getvalue()


@pytest.fixture
async def image_creative(db, org_id, settings, blobs):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    cid, _ = await ingest_repo.upsert_creative(db, org_id, acct, PlatformCreative("c1", "image", "H", "P", None, "SHOP_NOW", "u", None, None, {}), "raw")
    key = f"{org_id}/creatives/{cid}/original.png"
    await blobs.put(key, _png(), "image/png")
    aid = await ingest_repo.insert_asset(db, org_id, cid, "original_image", key, "sha", "image/png")
    await ingest_repo.set_primary_asset(db, org_id, cid, aid)
    return cid


def _ctx(db, blobs, settings, org_id, payload):
    return JobContext(db=db, blobs=blobs, settings=settings, job=Job(UUID(int=1), org_id, "tagging.tag_creative", payload, 1, 5))


async def test_tag_image_creative_persists_tags(db, blobs, settings, org_id, image_creative):
    tagger, tr = FakeTagger(), FakeTranscriber()
    run_id = await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), image_creative, tagger, tr)
    assert run_id is not None and tagger.calls[0]["n_images"] == 1 and tr.calls == 0
    tags = await repo.latest_tags(db, org_id, image_creative)
    assert {t["dimension"] for t in tags} >= {"hook_type", "message_angle", "hook_summary"}
    run = await db.fetchrow("SELECT status, input_tokens, model FROM tag_run WHERE id=$1", run_id)
    assert run["status"] == "done" and run["input_tokens"] == 100 and run["model"] == settings.claude_model_tagging
    assert await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.embed_creative'") == 1


async def test_tag_is_idempotent_per_taxonomy_and_model(db, blobs, settings, org_id, image_creative):
    ctx = _ctx(db, blobs, settings, org_id, {})
    first = await runner.tag_creative(ctx, image_creative, FakeTagger(), FakeTranscriber())
    second = await runner.tag_creative(ctx, image_creative, FakeTagger(), FakeTranscriber())
    assert first is not None and second is None


async def test_refusal_marks_run_failed(db, blobs, settings, org_id, image_creative):
    with pytest.raises(RuntimeError):
        await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), image_creative, FakeTagger(refuse=True), FakeTranscriber())
    assert await db.fetchval("SELECT status FROM tag_run") == "failed"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_tag_video_creative_uses_keyframes_and_transcript(db, blobs, settings, org_id, tmp_path):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    cid, _ = await ingest_repo.upsert_creative(db, org_id, acct, PlatformCreative("v1", "video", "H", "P", None, None, "u", None, None, {}), "raw")
    p = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(p)], check=True)
    key = f"{org_id}/creatives/{cid}/original.mp4"
    await blobs.put(key, p.read_bytes(), "video/mp4")
    aid = await ingest_repo.insert_asset(db, org_id, cid, "original_video", key, "sha", "video/mp4")
    await ingest_repo.set_primary_asset(db, org_id, cid, aid)

    tagger, tr = FakeTagger(), FakeTranscriber("buy now")
    await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), cid, tagger, tr)
    assert tagger.calls[0]["n_images"] >= 3 and "buy now" in tagger.calls[0]["user_text"] and tr.calls == 1
    kinds = await db.fetch("SELECT kind, count(*) AS n FROM asset WHERE creative_id=$1 GROUP BY kind", cid)
    by = {r["kind"]: r["n"] for r in kinds}
    assert by["keyframe"] >= 3 and by["transcript"] == 1


async def test_job_handler_uses_factories(db, blobs, settings, org_id, image_creative, monkeypatch):
    monkeypatch.setattr(tagging_jobs, "TAGGER_FACTORY", lambda s: FakeTagger())
    monkeypatch.setattr(tagging_jobs, "TRANSCRIBER_FACTORY", lambda s, http: FakeTranscriber())
    from cs.jobs.worker import run_once

    await queue.enqueue(db, org_id, "tagging.tag_creative", {"creative_id": str(image_creative)})
    await run_once(db, blobs, settings, "w")
    assert await db.fetchval("SELECT status FROM tag_run") == "done"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/tagging/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement repo**

`src/cs/tagging/repo.py`:

```python
from __future__ import annotations

from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id
from cs.tagging.taxonomy import Taxonomy


async def ensure_taxonomy_version(db: Database, taxonomy: Taxonomy) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO taxonomy_version (id, version, yaml_sha256) VALUES ($1, $2, $3)
        ON CONFLICT (yaml_sha256) DO UPDATE SET version = EXCLUDED.version RETURNING id
        """, new_id(), taxonomy.version, taxonomy.sha256)
    return row["id"]


async def start_tag_run(db: Database, org_id: UUID, creative_id: UUID, taxonomy_version_id: UUID, model: str) -> UUID | None:
    existing = await db.fetchrow(
        "SELECT id, status FROM tag_run WHERE creative_id=$1 AND taxonomy_version_id=$2 AND model=$3",
        creative_id, taxonomy_version_id, model)
    if existing and existing["status"] == "done":
        return None
    if existing:
        await db.execute("UPDATE tag_run SET status='running', error=NULL WHERE id=$1", existing["id"])
        await db.execute("DELETE FROM tag WHERE tag_run_id=$1", existing["id"])
        return existing["id"]
    rid = new_id()
    await db.execute(
        "INSERT INTO tag_run (id, org_id, creative_id, taxonomy_version_id, model, status) VALUES ($1,$2,$3,$4,$5,'running')",
        rid, org_id, creative_id, taxonomy_version_id, model)
    return rid


async def finish_tag_run(db: Database, org_id: UUID, run_id: UUID, tags: list[dict], prompt_sha: str,
                         input_tokens: int, output_tokens: int) -> None:
    creative_id = await db.fetchval("SELECT creative_id FROM tag_run WHERE id=$1", run_id)
    async with db.transaction() as tx:
        for t in tags:
            await tx.execute(
                "INSERT INTO tag (id, org_id, tag_run_id, creative_id, dimension, value, confidence, evidence) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                new_id(), org_id, run_id, creative_id, t["dimension"], t["value"], t["confidence"], t["evidence"])
        await tx.execute(
            "UPDATE tag_run SET status='done', prompt_sha256=$2, input_tokens=$3, output_tokens=$4, finished_at=now() WHERE id=$1",
            run_id, prompt_sha, input_tokens, output_tokens)


async def fail_tag_run(db: Database, org_id: UUID, run_id: UUID, error: str) -> None:
    await db.execute("UPDATE tag_run SET status='failed', error=$2, finished_at=now() WHERE id=$1", run_id, error[:2000])


async def latest_run_id(db: Database, org_id: UUID, creative_id: UUID) -> UUID | None:
    return await db.fetchval(
        "SELECT id FROM tag_run WHERE org_id=$1 AND creative_id=$2 AND status='done' ORDER BY finished_at DESC LIMIT 1",
        org_id, creative_id)


async def latest_tags(db: Database, org_id: UUID, creative_id: UUID) -> list[asyncpg.Record]:
    rid = await latest_run_id(db, org_id, creative_id)
    if rid is None:
        return []
    return await db.fetch("SELECT dimension, value, confidence, evidence FROM tag WHERE tag_run_id=$1 ORDER BY dimension", rid)
```

- [ ] **Step 4: Implement runner**

`src/cs/tagging/runner.py`:

```python
from __future__ import annotations

from uuid import UUID

from cs.core import queue
from cs.core.blobs import sha256_hex
from cs.ingest import repo as ingest_repo
from cs.jobs.registry import JobContext
from cs.tagging import repo
from cs.tagging.images import prepare_image
from cs.tagging.prompts import SYSTEM, prompt_sha, user_text
from cs.tagging.tagger import Tagger
from cs.tagging.taxonomy import load_taxonomy
from cs.tagging.video import Transcriber, extract_audio, extract_keyframes, probe_duration_ms


async def _prepare_inputs(ctx: JobContext, creative, transcriber: Transcriber):
    org_id = ctx.job.org_id
    data = await ctx.blobs.get(creative["primary_asset_path"])
    if creative["format"] == "video":
        frames = extract_keyframes(data)
        images = [(f.jpeg, "image/jpeg") for f in frames]
        stamps = [f.timestamp for f in frames]
        for i, f in enumerate(frames):
            key = f"{org_id}/creatives/{creative['id']}/keyframes/{i:02d}.jpg"
            await ctx.blobs.put(key, f.jpeg, "image/jpeg")
            await ingest_repo.insert_asset(ctx.db, org_id, creative["id"], "keyframe", key, sha256_hex(f.jpeg),
                                           "image/jpeg", f.width, f.height, int(f.timestamp * 1000), seq=i)
        audio = extract_audio(data)
        transcript = await transcriber.transcribe(audio, "audio/mpeg") if audio else ""
        tkey = f"{org_id}/creatives/{creative['id']}/transcript.txt"
        await ctx.blobs.put(tkey, transcript.encode(), "text/plain")
        await ingest_repo.insert_asset(ctx.db, org_id, creative["id"], "transcript", tkey, sha256_hex(transcript.encode()),
                                       "text/plain", duration_ms=probe_duration_ms(data))
        return images, transcript, stamps
    jpeg, _, _ = prepare_image(data)
    return [(jpeg, "image/jpeg")], None, None


async def tag_creative(ctx: JobContext, creative_id: UUID, tagger: Tagger, transcriber: Transcriber) -> UUID | None:
    org_id = ctx.job.org_id
    creative = await ingest_repo.get_creative(ctx.db, org_id, creative_id)
    if creative is None or not creative["primary_asset_path"]:
        raise RuntimeError(f"creative {creative_id} has no primary asset yet")
    taxonomy = load_taxonomy()
    tv_id = await repo.ensure_taxonomy_version(ctx.db, taxonomy)
    model = ctx.settings.claude_model_tagging
    run_id = await repo.start_tag_run(ctx.db, org_id, creative_id, tv_id, model)
    if run_id is None:
        return None
    try:
        images, transcript, stamps = await _prepare_inputs(ctx, creative, transcriber)
        fmt = creative["format"]
        copy = {k: creative[k] for k in ("headline", "primary_text", "description", "cta_type", "link_url")}
        text = user_text(taxonomy, fmt, copy, transcript, stamps)
        schema = taxonomy.json_schema(fmt)
        result = await tagger.tag(images, text, schema)
        if result.refused:
            raise RuntimeError("model refused to tag creative")
        tags = taxonomy.validate(result.tags, fmt)
        await repo.finish_tag_run(ctx.db, org_id, run_id, tags, prompt_sha(SYSTEM, text, schema),
                                  result.input_tokens, result.output_tokens)
    except Exception as exc:
        await repo.fail_tag_run(ctx.db, org_id, run_id, str(exc))
        raise
    await queue.enqueue(ctx.db, org_id, "tagging.embed_creative", {"creative_id": str(creative_id)},
                        dedup_key=f"embed:{creative_id}")
    return run_id
```

- [ ] **Step 5: Implement jobs**

`src/cs/tagging/jobs.py`:

```python
from __future__ import annotations

from uuid import UUID

import httpx

from cs.core.config import Settings
from cs.jobs.registry import JobContext, handler
from cs.tagging import runner
from cs.tagging.tagger import ClaudeTagger, Tagger
from cs.tagging.video import GatewayTranscriber, Transcriber


def default_tagger_factory(settings: Settings) -> Tagger:
    return ClaudeTagger(settings)


def default_transcriber_factory(settings: Settings, http: httpx.AsyncClient) -> Transcriber:
    return GatewayTranscriber(settings, http)


TAGGER_FACTORY = default_tagger_factory
TRANSCRIBER_FACTORY = default_transcriber_factory


@handler("tagging.tag_creative")
async def tag_creative(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    async with httpx.AsyncClient(timeout=300) as http:
        await runner.tag_creative(ctx, creative_id, TAGGER_FACTORY(ctx.settings), TRANSCRIBER_FACTORY(ctx.settings, http))
```

Add `import cs.tagging.jobs  # noqa: F401` to `src/cs/jobs/all_handlers.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/tagging/test_runner.py tests/ingest -v`
Expected: PASS (the Phase 1 backfill test now sees `tagging.tag_creative` handled; its tagger is the real `ClaudeTagger` unless patched — add `monkeypatch.setattr(tagging_jobs, "TAGGER_FACTORY", lambda s: FakeTagger())` and the transcriber equivalent to the Phase 1 `fake` fixture in `tests/ingest/test_jobs.py`.)

- [ ] **Step 7: Commit**

```bash
git add src/cs/tagging src/cs/jobs/all_handlers.py tests/tagging tests/ingest/test_jobs.py
git commit -m "feat(tagging): tag runner with idempotent runs, video inputs, and job handler"
```

---

### Task 6: Embeddings and similarity

**Files:**
- Create: `src/cs/tagging/embed.py`
- Modify: `src/cs/tagging/repo.py` (append `upsert_embedding`, `similar_creatives`), `src/cs/tagging/jobs.py` (add handler + `EMBEDDER_FACTORY`)
- Test: `tests/tagging/test_embed.py`

**Interfaces:**
- `tag_text(copy: dict, tags: list[Record | dict]) -> str` — deterministic text: `"dimension=value; ..."` plus headline and primary text.
- `Embedder` Protocol: `async embed(texts: list[str]) -> list[list[float]]`.
- `GatewayEmbedder(settings, http)` → POST `{base}/v1/embeddings` JSON `{"model", "input": [...]}` with Bearer auth → `data[i].embedding`.
- repo: `upsert_embedding(db, org_id, creative_id, tag_run_id, vector: list[float])`; `similar_creatives(db, org_id, creative_id, k=10) -> list[Record]` (`id`, `distance`, headline, format).
- Job `tagging.embed_creative` payload `{"creative_id"}`; seam `EMBEDDER_FACTORY(settings, http) -> Embedder`.

- [ ] **Step 1: Write failing tests**

`tests/tagging/test_embed.py`:

```python
import httpx
import pytest
import respx

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.ingest import repo as ingest_repo
from cs.tagging import jobs as tagging_jobs
from cs.tagging import repo
from cs.tagging.embed import GatewayEmbedder, tag_text
from cs.tagging.fakes import FakeEmbedder
from cs.tagging.taxonomy import load_taxonomy


def test_tag_text_is_deterministic():
    tags = [{"dimension": "hook_type", "value": "question"}, {"dimension": "tone", "value": "warm"}]
    a = tag_text({"headline": "H", "primary_text": "P"}, tags)
    assert a == tag_text({"headline": "H", "primary_text": "P"}, list(reversed(tags)))
    assert "hook_type=question" in a and "H" in a


@respx.mock
async def test_gateway_embedder(settings):
    settings.ai_gateway_api_key = "k"
    respx.post("https://ai-gateway.vercel.sh/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 1536}]}))
    async with httpx.AsyncClient() as http:
        vecs = await GatewayEmbedder(settings, http).embed(["x"])
    assert len(vecs[0]) == 1536


@pytest.fixture
async def two_tagged(db, org_id, settings):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    ids = []
    for n, hook in (("c1", "question"), ("c2", "question"), ("c3", "statistic")):
        cid, _ = await ingest_repo.upsert_creative(db, org_id, acct, PlatformCreative(n, "image", hook, "P", None, None, "u", None, None, {}), "raw")
        rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
        await repo.finish_tag_run(db, org_id, rid, [{"dimension": "hook_type", "value": hook, "confidence": 1, "evidence": ""}], "sha", 1, 1)
        ids.append(cid)
    return ids


async def test_embed_job_and_similarity(db, blobs, settings, org_id, two_tagged, monkeypatch):
    monkeypatch.setattr(tagging_jobs, "EMBEDDER_FACTORY", lambda s, http: FakeEmbedder())
    from cs.jobs.worker import run_once

    for cid in two_tagged:
        await queue.enqueue(db, org_id, "tagging.embed_creative", {"creative_id": str(cid)})
    while await run_once(db, blobs, settings, "w"):
        pass
    assert await db.fetchval("SELECT count(*) FROM creative_embedding") == 3
    sim = await repo.similar_creatives(db, org_id, two_tagged[0], k=2)
    assert sim[0]["id"] == two_tagged[1]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/tagging/test_embed.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

`src/cs/tagging/embed.py`:

```python
from __future__ import annotations

from typing import Protocol

import httpx

from cs.core.config import Settings


def tag_text(copy: dict, tags) -> str:
    pairs = sorted(f"{t['dimension']}={t['value']}" for t in tags)
    parts = ["; ".join(pairs)]
    if copy.get("headline"):
        parts.append(f"headline: {copy['headline']}")
    if copy.get("primary_text"):
        parts.append(f"text: {str(copy['primary_text'])[:500]}")
    return "\n".join(parts)


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class GatewayEmbedder:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http

    async def embed(self, texts: list[str]) -> list[list[float]]:
        r = await self.http.post(
            f"{self.settings.ai_gateway_base_url}/v1/embeddings",
            headers={"Authorization": f"Bearer {self.settings.ai_gateway_api_key}"},
            json={"model": self.settings.embedding_model, "input": texts}, timeout=60)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]
```

Append to `src/cs/tagging/repo.py`:

```python
async def upsert_embedding(db: Database, org_id: UUID, creative_id: UUID, tag_run_id: UUID, vector: list[float]) -> None:
    literal = "[" + ",".join(f"{x:.7f}" for x in vector) + "]"
    await db.execute(
        """
        INSERT INTO creative_embedding (creative_id, org_id, tag_run_id, embedding) VALUES ($1, $2, $3, $4::vector)
        ON CONFLICT (creative_id) DO UPDATE SET tag_run_id = EXCLUDED.tag_run_id, embedding = EXCLUDED.embedding, created_at = now()
        """, creative_id, org_id, tag_run_id, literal)


async def similar_creatives(db: Database, org_id: UUID, creative_id: UUID, k: int = 10) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT c.id, c.headline, c.format, (e.embedding <=> ref.embedding) AS distance
        FROM creative_embedding ref
        JOIN creative_embedding e ON e.org_id = ref.org_id AND e.creative_id <> ref.creative_id
        JOIN creative c ON c.id = e.creative_id
        WHERE ref.creative_id = $2 AND ref.org_id = $1
        ORDER BY e.embedding <=> ref.embedding LIMIT $3
        """, org_id, creative_id, k)
```

Append to `src/cs/tagging/jobs.py`:

```python
from cs.ingest import repo as ingest_repo  # noqa: E402
from cs.tagging import repo as tag_repo  # noqa: E402
from cs.tagging.embed import Embedder, GatewayEmbedder, tag_text  # noqa: E402


def default_embedder_factory(settings: Settings, http: httpx.AsyncClient) -> Embedder:
    return GatewayEmbedder(settings, http)


EMBEDDER_FACTORY = default_embedder_factory


@handler("tagging.embed_creative")
async def embed_creative(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    org_id = ctx.job.org_id
    run_id = await tag_repo.latest_run_id(ctx.db, org_id, creative_id)
    if run_id is None:
        raise RuntimeError("no completed tag run to embed")
    creative = await ingest_repo.get_creative(ctx.db, org_id, creative_id)
    tags = await tag_repo.latest_tags(ctx.db, org_id, creative_id)
    text = tag_text({"headline": creative["headline"], "primary_text": creative["primary_text"]}, tags)
    async with httpx.AsyncClient() as http:
        [vec] = await EMBEDDER_FACTORY(ctx.settings, http).embed([text])
    await tag_repo.upsert_embedding(ctx.db, org_id, creative_id, run_id, vec)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tagging -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cs/tagging tests/tagging/test_embed.py
git commit -m "feat(tagging): gateway embeddings and pgvector similarity"
```

---

### Task 7: Batch backfill through the Message Batches API

**Files:**
- Create: `src/cs/tagging/batch.py`
- Modify: `src/cs/tagging/repo.py` (append `untagged_creative_ids`), `src/cs/tagging/jobs.py` (add `tagging.batch_submit`, `tagging.batch_poll`, `BATCH_FACTORY`), `db/migrations/20260904000005_tag_batch.sql`
- Test: `tests/tagging/test_batch.py`

**Interfaces:**
- Table `tag_batch(id UUID PK, org_id, provider_batch_id TEXT, taxonomy_version_id, model, status CHECK IN ('submitted','ended'), request_count INT, created_at, ended_at)` and `tag_batch_item(tag_batch_id, custom_id TEXT, tag_run_id UUID, PRIMARY KEY (tag_batch_id, custom_id))`.
- `BatchTagger` Protocol: `async submit(requests: list[dict]) -> str` where each request is `{"custom_id": str, "images": [(bytes, mime)], "user_text": str, "schema": dict}`; `async poll(batch_id) -> tuple[bool, list[dict]]` with results `{"custom_id", "ok": bool, "tags": list, "input_tokens", "output_tokens", "error": str | None}`.
- `ClaudeBatchTagger(settings, client=None)` using `client.messages.batches.create/retrieve/results` (no `fallbacks`, no `betas`).
- repo: `untagged_creative_ids(db, org_id, taxonomy_version_id, model, limit) -> list[UUID]` (creatives with a primary asset and no `done` run).
- Jobs: `tagging.batch_submit` payload `{"ad_account_id"?: str, "limit": int}` — prepares inputs for up to `limit` untagged creatives (image only in batches; videos still go through `tagging.tag_creative` because of the per-creative ffmpeg work), creates `running` tag runs, submits, records `tag_batch`, enqueues `tagging.batch_poll` with `run_after = now + 60s`. `tagging.batch_poll` payload `{"tag_batch_id"}` — polls; if not ended, re-enqueues itself in 120s; if ended, finishes/fails each run and enqueues embeddings.
- API trigger (Task 8): `POST /tagging/backfill`.

- [ ] **Step 1: Migration**

`db/migrations/20260904000005_tag_batch.sql`:

```sql
-- migrate:up
CREATE TABLE tag_batch (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  provider_batch_id TEXT NOT NULL,
  taxonomy_version_id UUID NOT NULL REFERENCES taxonomy_version(id),
  model TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('submitted', 'ended')),
  request_count INT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at TIMESTAMPTZ
);
CREATE TABLE tag_batch_item (
  tag_batch_id UUID NOT NULL REFERENCES tag_batch(id),
  custom_id TEXT NOT NULL,
  tag_run_id UUID NOT NULL REFERENCES tag_run(id),
  PRIMARY KEY (tag_batch_id, custom_id)
);
-- migrate:down
DROP TABLE tag_batch_item;
DROP TABLE tag_batch;
```

- [ ] **Step 2: Write failing tests**

`tests/tagging/test_batch.py`:

```python
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.ingest import repo as ingest_repo
from cs.jobs.worker import run_once
from cs.tagging import jobs as tagging_jobs
from cs.tagging.batch import ClaudeBatchTagger
from cs.tagging.fakes import FakeBatchTagger, FakeEmbedder


def _png():
    b = io.BytesIO(); Image.new("RGB", (32, 32)).save(b, format="PNG"); return b.getvalue()


@pytest.fixture
async def three_images(db, org_id, settings, blobs):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    ids = []
    for n in ("a", "b", "c"):
        cid, _ = await ingest_repo.upsert_creative(db, org_id, acct, PlatformCreative(n, "image", n, "P", None, None, "u", None, None, {}), "raw")
        key = f"{org_id}/creatives/{cid}/original.png"
        await blobs.put(key, _png(), "image/png")
        aid = await ingest_repo.insert_asset(db, org_id, cid, "original_image", key, "s", "image/png")
        await ingest_repo.set_primary_asset(db, org_id, cid, aid)
        ids.append(cid)
    return ids


async def test_batch_submit_and_poll_completes_runs(db, blobs, settings, org_id, three_images, monkeypatch):
    fake = FakeBatchTagger()
    monkeypatch.setattr(tagging_jobs, "BATCH_FACTORY", lambda s: fake)
    monkeypatch.setattr(tagging_jobs, "EMBEDDER_FACTORY", lambda s, http: FakeEmbedder())
    await queue.enqueue(db, org_id, "tagging.batch_submit", {"limit": 100})
    await run_once(db, blobs, settings, "w")
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='running'") == 3
    assert await db.fetchval("SELECT status FROM tag_batch") == "submitted"
    await db.execute("UPDATE job SET run_after = now() WHERE job_type='tagging.batch_poll'")
    await run_once(db, blobs, settings, "w")  # not ended -> re-enqueued
    assert await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.batch_poll' AND status='pending'") == 1
    fake.ended.add("batch_1")
    await db.execute("UPDATE job SET run_after = now() WHERE job_type='tagging.batch_poll'")
    await run_once(db, blobs, settings, "w")
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='done'") == 3
    assert await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.embed_creative'") == 3


async def test_claude_batch_tagger_shapes_requests(settings):
    created = {}

    class Batches:
        async def create(self, requests):
            created["requests"] = requests
            return SimpleNamespace(id="msgbatch_1")

        async def retrieve(self, bid):
            return SimpleNamespace(processing_status="ended")

        async def results(self, bid):
            async def gen():
                yield SimpleNamespace(custom_id="c1", result=SimpleNamespace(
                    type="succeeded", message=SimpleNamespace(stop_reason="end_turn",
                        content=[SimpleNamespace(type="text", text='{"tags": []}')],
                        usage=SimpleNamespace(input_tokens=1, output_tokens=1))))
            return gen()

    client = SimpleNamespace(messages=SimpleNamespace(batches=Batches()))
    bt = ClaudeBatchTagger(settings, client=client)
    bid = await bt.submit([{"custom_id": "c1", "images": [(b"\xff\xd8\xff", "image/jpeg")], "user_text": "t", "schema": {"type": "object"}}])
    assert bid == "msgbatch_1"
    params = created["requests"][0]["params"]
    assert "fallbacks" not in params and "betas" not in params and params["model"] == settings.claude_model_tagging
    ended, results = await bt.poll(bid)
    assert ended and results[0]["ok"] and results[0]["custom_id"] == "c1"
```

- [ ] **Step 3: Run to verify they fail**

Run: `make migrate-test && uv run pytest tests/tagging/test_batch.py -v`
Expected: FAIL

- [ ] **Step 4: Implement batch tagger**

`src/cs/tagging/batch.py`:

```python
from __future__ import annotations

import json
from typing import Protocol

import anthropic

from cs.core.config import Settings
from cs.tagging.prompts import SYSTEM
from cs.tagging.tagger import build_content


class BatchTagger(Protocol):
    async def submit(self, requests: list[dict]) -> str: ...
    async def poll(self, batch_id: str) -> tuple[bool, list[dict]]: ...


class ClaudeBatchTagger:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.model = settings.claude_model_tagging

    async def submit(self, requests: list[dict]) -> str:
        payload = [{
            "custom_id": r["custom_id"],
            "params": {
                "model": self.model,
                "max_tokens": 16000,
                "system": SYSTEM,
                "messages": [{"role": "user", "content": build_content(r["images"], r["user_text"])}],
                "output_config": {"format": {"type": "json_schema", "schema": r["schema"]}},
            },
        } for r in requests]
        batch = await self.client.messages.batches.create(requests=payload)
        return batch.id

    async def poll(self, batch_id: str) -> tuple[bool, list[dict]]:
        batch = await self.client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            return False, []
        out: list[dict] = []
        async for item in await self.client.messages.batches.results(batch_id):
            res = item.result
            if res.type == "succeeded" and res.message.stop_reason != "refusal":
                text = next((b.text for b in res.message.content if getattr(b, "type", "") == "text"), "{}")
                out.append({"custom_id": item.custom_id, "ok": True, "tags": json.loads(text).get("tags", []),
                            "input_tokens": res.message.usage.input_tokens, "output_tokens": res.message.usage.output_tokens,
                            "error": None})
            else:
                err = "refusal" if res.type == "succeeded" else f"{res.type}: {getattr(res, 'error', '')}"
                out.append({"custom_id": item.custom_id, "ok": False, "tags": [], "input_tokens": 0, "output_tokens": 0, "error": err})
        return True, out
```

Append to `src/cs/tagging/repo.py`:

```python
async def untagged_creative_ids(db: Database, org_id: UUID, taxonomy_version_id: UUID, model: str, limit: int,
                                formats: tuple[str, ...] = ("image",)) -> list[UUID]:
    rows = await db.fetch(
        """
        SELECT c.id FROM creative c
        WHERE c.org_id = $1 AND c.primary_asset_id IS NOT NULL AND c.format = ANY($4)
          AND NOT EXISTS (SELECT 1 FROM tag_run r WHERE r.creative_id = c.id AND r.taxonomy_version_id = $2
                          AND r.model = $3 AND r.status IN ('done', 'running'))
        ORDER BY c.first_seen_at LIMIT $5
        """, org_id, taxonomy_version_id, model, list(formats), limit)
    return [r["id"] for r in rows]
```

- [ ] **Step 5: Add batch jobs**

Append to `src/cs/tagging/jobs.py`:

```python
from datetime import UTC, datetime, timedelta  # noqa: E402

from cs.core import queue  # noqa: E402
from cs.core.ids import new_id  # noqa: E402
from cs.tagging.batch import BatchTagger, ClaudeBatchTagger  # noqa: E402
from cs.tagging.images import prepare_image  # noqa: E402
from cs.tagging.prompts import SYSTEM, prompt_sha, user_text  # noqa: E402
from cs.tagging.taxonomy import load_taxonomy  # noqa: E402


def default_batch_factory(settings: Settings) -> BatchTagger:
    return ClaudeBatchTagger(settings)


BATCH_FACTORY = default_batch_factory


@handler("tagging.batch_submit")
async def batch_submit(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    taxonomy = load_taxonomy()
    tv = await tag_repo.ensure_taxonomy_version(ctx.db, taxonomy)
    model = ctx.settings.claude_model_tagging
    ids = await tag_repo.untagged_creative_ids(ctx.db, org_id, tv, model, int(ctx.job.payload.get("limit", 500)))
    if not ids:
        return
    requests, items = [], []
    for cid in ids:
        creative = await ingest_repo.get_creative(ctx.db, org_id, cid)
        run_id = await tag_repo.start_tag_run(ctx.db, org_id, cid, tv, model)
        if run_id is None:
            continue
        jpeg, _, _ = prepare_image(await ctx.blobs.get(creative["primary_asset_path"]))
        copy = {k: creative[k] for k in ("headline", "primary_text", "description", "cta_type", "link_url")}
        text = user_text(taxonomy, "image", copy, None, None)
        schema = taxonomy.json_schema("image")
        requests.append({"custom_id": str(run_id), "images": [(jpeg, "image/jpeg")], "user_text": text, "schema": schema})
        items.append((run_id, prompt_sha(SYSTEM, text, schema)))
    provider_id = await BATCH_FACTORY(ctx.settings).submit(requests)
    batch_id = new_id()
    await ctx.db.execute(
        "INSERT INTO tag_batch (id, org_id, provider_batch_id, taxonomy_version_id, model, status, request_count) "
        "VALUES ($1,$2,$3,$4,$5,'submitted',$6)", batch_id, org_id, provider_id, tv, model, len(requests))
    for run_id, sha in items:
        await ctx.db.execute("INSERT INTO tag_batch_item (tag_batch_id, custom_id, tag_run_id) VALUES ($1,$2,$3)",
                             batch_id, str(run_id), run_id)
        await ctx.db.execute("UPDATE tag_run SET prompt_sha256=$2 WHERE id=$1", run_id, sha)
    await queue.enqueue(ctx.db, org_id, "tagging.batch_poll", {"tag_batch_id": str(batch_id)},
                        dedup_key=f"poll:{batch_id}", run_after=datetime.now(UTC) + timedelta(seconds=60))


@handler("tagging.batch_poll")
async def batch_poll(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    batch_id = UUID(ctx.job.payload["tag_batch_id"])
    batch = await ctx.db.fetchrow("SELECT * FROM tag_batch WHERE id=$1 AND org_id=$2", batch_id, org_id)
    ended, results = await BATCH_FACTORY(ctx.settings).poll(batch["provider_batch_id"])
    if not ended:
        await queue.enqueue(ctx.db, org_id, "tagging.batch_poll", {"tag_batch_id": str(batch_id)},
                            dedup_key=f"poll:{batch_id}", run_after=datetime.now(UTC) + timedelta(seconds=120))
        return
    taxonomy = load_taxonomy()
    for res in results:
        run_id = UUID(res["custom_id"])
        if res["ok"]:
            creative_id = await ctx.db.fetchval("SELECT creative_id FROM tag_run WHERE id=$1", run_id)
            sha = await ctx.db.fetchval("SELECT prompt_sha256 FROM tag_run WHERE id=$1", run_id)
            await tag_repo.finish_tag_run(ctx.db, org_id, run_id, taxonomy.validate(res["tags"], "image"), sha or "",
                                          res["input_tokens"], res["output_tokens"])
            await queue.enqueue(ctx.db, org_id, "tagging.embed_creative", {"creative_id": str(creative_id)},
                                dedup_key=f"embed:{creative_id}")
        else:
            await tag_repo.fail_tag_run(ctx.db, org_id, run_id, res["error"] or "batch item failed")
    await ctx.db.execute("UPDATE tag_batch SET status='ended', ended_at=now() WHERE id=$1", batch_id)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/tagging -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add db/migrations/20260904000005_tag_batch.sql src/cs/tagging tests/tagging/test_batch.py
git commit -m "feat(tagging): message batches backfill with submit/poll jobs"
```

---

### Task 8: Tags API, backfill trigger, and eval harness

**Files:**
- Create: `src/cs/api/routers/tags.py`, `scripts/eval_tagging.py`, `tests/evals/README.md`, `tests/evals/labeled.jsonl` (seed with 0 rows; filled by hand from the real account)
- Modify: `src/cs/api/app.py`, `Makefile` (add `evals` target)
- Test: `tests/api/test_tags.py`

**Interfaces:**
- Routes: `GET /creatives/{id}/tags` → `[{dimension, value, confidence, evidence}]`; `GET /creatives/{id}/similar?k` → `[{id, headline, format, distance}]`; `POST /creatives/{id}/retag` (admin) → enqueues `tagging.tag_creative` with `dedup_key=f"tag:{id}"`; `GET /tags/dimensions` → taxonomy dimensions for the UI; `POST /tagging/backfill` (admin, body `{"limit": 500}`) → enqueues `tagging.batch_submit`.
- `scripts/eval_tagging.py`: reads `tests/evals/labeled.jsonl` rows `{"creative_id": str, "labels": {dimension: value}}`, tags each creative with the real `ClaudeTagger` (requires `ANTHROPIC_API_KEY`), prints per-dimension agreement and overall, exits non-zero if overall < `--min-agreement` (default 0.7).

- [ ] **Step 1: Write failing API test**

`tests/api/test_tags.py`:

```python
from cs.connectors.base import PlatformCreative
from cs.core.crypto import encrypt_str
from cs.ingest import repo as ingest_repo
from cs.tagging import repo
from cs.tagging.taxonomy import load_taxonomy
from tests.conftest import dev_headers

H = dev_headers(org="org_tags")


async def _seed(client, db, settings):
    from uuid import UUID
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    cid, _ = await ingest_repo.upsert_creative(db, org_id, acct, PlatformCreative("c", "image", "H", "P", None, None, "u", None, None, {}), "raw")
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    await repo.finish_tag_run(db, org_id, rid, [{"dimension": "tone", "value": "warm", "confidence": 0.8, "evidence": "e"}], "s", 1, 1)
    return cid


async def test_tags_endpoints(client, db, settings):
    cid = await _seed(client, db, settings)
    r = await client.get(f"/creatives/{cid}/tags", headers=H)
    assert r.json() == [{"dimension": "tone", "value": "warm", "confidence": 0.8, "evidence": "e"}]
    r = await client.get("/tags/dimensions", headers=H)
    assert any(d["name"] == "hook_type" for d in r.json())
    r = await client.post(f"/creatives/{cid}/retag", headers=H)
    assert r.status_code == 200
    assert await db.fetchval("SELECT job_type FROM job") == "tagging.tag_creative"
    r = await client.post("/tagging/backfill", headers=H, json={"limit": 10})
    assert r.status_code == 200 and await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.batch_submit'") == 1
    r = await client.get(f"/creatives/{cid}/similar", headers=H)
    assert r.status_code == 200 and r.json() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/api/test_tags.py -v`
Expected: FAIL (404)

- [ ] **Step 3: Implement router**

`src/cs/api/routers/tags.py`:

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_db
from cs.core import queue
from cs.core.db import Database
from cs.ingest import repo as ingest_repo
from cs.tagging import repo
from cs.tagging.taxonomy import load_taxonomy

router = APIRouter()


class BackfillBody(BaseModel):
    limit: int = 500


@router.get("/creatives/{creative_id}/tags")
async def creative_tags(creative_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    if not await ingest_repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    return [dict(r) for r in await repo.latest_tags(db, auth.org_id, creative_id)]


@router.get("/creatives/{creative_id}/similar")
async def similar(creative_id: UUID, k: int = 10, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    rows = await repo.similar_creatives(db, auth.org_id, creative_id, min(k, 50))
    return [{**dict(r), "id": str(r["id"]), "distance": float(r["distance"])} for r in rows]


@router.post("/creatives/{creative_id}/retag")
async def retag(creative_id: UUID, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)) -> dict:
    if not await ingest_repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    jid = await queue.enqueue(db, auth.org_id, "tagging.tag_creative", {"creative_id": str(creative_id)}, dedup_key=f"tag:{creative_id}")
    return {"job_id": str(jid) if jid else None}


@router.get("/tags/dimensions")
async def dimensions(auth: AuthContext = Depends(current_auth)) -> list[dict]:
    t = load_taxonomy()
    return [{"name": d.name, "group": d.group, "applies_to": list(d.applies_to), "values": list(d.values) if d.values else None,
             "description": d.description} for d in t.dimensions]


@router.post("/tagging/backfill")
async def backfill(body: BackfillBody, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)) -> dict:
    jid = await queue.enqueue(db, auth.org_id, "tagging.batch_submit", {"limit": body.limit}, dedup_key="batch_submit")
    return {"job_id": str(jid) if jid else None}
```

Include `tags.router` in `src/cs/api/app.py`.

- [ ] **Step 4: Write the eval harness**

`scripts/eval_tagging.py`:

```python
"""Tag hand-labeled creatives with the real model and report agreement per dimension.

Usage: ANTHROPIC_API_KEY=... uv run python scripts/eval_tagging.py --min-agreement 0.7
Requires DATABASE_URL to point at a database containing the labeled creatives and their blobs.
"""
import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path
from uuid import UUID

import httpx

from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.queue import Job
from cs.jobs.registry import JobContext
from cs.tagging import repo, runner
from cs.tagging.tagger import ClaudeTagger
from cs.tagging.video import GatewayTranscriber

LABELS = Path("tests/evals/labeled.jsonl")


async def main(min_agreement: float) -> int:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    blobs = blob_store_from_settings(settings)
    rows = [json.loads(line) for line in LABELS.read_text().splitlines() if line.strip()]
    if not rows:
        print("no labeled rows"); return 0
    hits, totals = defaultdict(int), defaultdict(int)
    async with httpx.AsyncClient() as http:
        tagger, tr = ClaudeTagger(settings), GatewayTranscriber(settings, http)
        for r in rows:
            cid = UUID(r["creative_id"])
            org_id = await db.fetchval("SELECT org_id FROM creative WHERE id=$1", cid)
            await db.execute("DELETE FROM tag WHERE creative_id=$1", cid)
            await db.execute("DELETE FROM tag_run WHERE creative_id=$1", cid)
            ctx = JobContext(db, blobs, settings, Job(UUID(int=0), org_id, "eval", {}, 1, 1))
            await runner.tag_creative(ctx, cid, tagger, tr)
            got = {t["dimension"]: t["value"] for t in await repo.latest_tags(db, org_id, cid)}
            for dim, want in r["labels"].items():
                totals[dim] += 1
                hits[dim] += int(got.get(dim) == want)
    overall = sum(hits.values()) / max(1, sum(totals.values()))
    for dim in sorted(totals):
        print(f"{dim:28s} {hits[dim]}/{totals[dim]}  {hits[dim] / totals[dim]:.2f}")
    print(f"\noverall agreement: {overall:.3f} (min {min_agreement})")
    await db.close()
    return 0 if overall >= min_agreement else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--min-agreement", type=float, default=0.7)
    sys.exit(asyncio.run(main(ap.parse_args().min_agreement)))
```

`tests/evals/README.md`:

```markdown
# Tagging evals

`labeled.jsonl` holds one JSON object per line: `{"creative_id": "<uuid>", "labels": {"hook_type": "question", ...}}`.
Label at least 50 creatives from the connected account (mix of image and video) by hand after the first backfill.
Run `make evals` (needs ANTHROPIC_API_KEY and AI_GATEWAY_API_KEY). CI does not run this; run it before changing
`taxonomy.yaml` or `prompts.py` and again after, and record both numbers in the PR.
```

Create `tests/evals/labeled.jsonl` as an empty file. Add to `Makefile`:

```makefile
evals:
	uv run python scripts/eval_tagging.py --min-agreement 0.7
```

- [ ] **Step 5: Run the suite**

Run: `make lint && make test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cs/api tests/api/test_tags.py scripts/eval_tagging.py tests/evals Makefile
git commit -m "feat(tagging): tags API, batch backfill trigger, and eval harness"
```

---

## Self-review

- **Spec coverage:** Phase 1's dead-lettered `tagging.tag_creative` jobs and carousels' missing assets (README Phase 1 follow-ups) → Task 2. taxonomy ~40 dimensions with groups incl. video-only → Task 1 (33 dims, within the "around 40" spec language). Image single-call structured output → Task 3. Video keyframes 0/1/3s + scene change cap 24 + transcript → Task 4. Model default, adaptive thinking, refusal fallback, structured outputs → Task 3. Batches for backfills >50 → Task 7 (the threshold is the API caller's `limit`; the UI in Phase 5 triggers batch backfill only when the untagged count exceeds 50). Idempotency on `(creative, taxonomy, model)` → Task 5. Embeddings + similarity → Task 6. Evals on ~50 labeled creatives → Task 8.
- **Placeholder scan:** none. `labeled.jsonl` is intentionally empty until the real account is labeled; the harness handles zero rows.
- **Type consistency:** `TagResult` fields, `Tagger.tag(images, user_text, schema)`, `FakeTagger.calls[i]["n_images"|"user_text"]`, seams `TAGGER_FACTORY(settings)`, `TRANSCRIBER_FACTORY(settings, http)`, `EMBEDDER_FACTORY(settings, http)`, `BATCH_FACTORY(settings)` are used with those exact signatures in tests and jobs. Job payload keys: `creative_id`, `limit`, `tag_batch_id`.
