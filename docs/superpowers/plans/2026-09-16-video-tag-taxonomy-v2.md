# Video Tag Taxonomy v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the video tag set with a five-layer profile (computed numerics, closed single-value, closed multi-value with timestamps, open canonicalized vocabulary, free text) stored in the existing `tag` table, so videos can later be clustered into creative-concept families.

**Architecture:** `taxonomy.yaml` becomes version 2.0 with a `kind` per dimension and a `nested_formats: [video]` switch. Video gets a nested strict JSON schema and a flattening validator; image and carousel keep the legacy flat schema byte-for-byte. The runner's video path probes the file with ffprobe/ffmpeg, builds a prompt with the org's existing open-tag vocabulary, makes one model call, flattens, canonicalizes open slugs through a new `tag_vocab` table, and writes rows with four new nullable columns.

**Tech Stack:** Python 3.12, asyncpg, FastAPI, dbmate migrations, ffmpeg/ffprobe, Gemini via Vercel AI Gateway (OpenAI-compatible client), pytest (asyncio auto), Next.js + vitest/jsdom.

**Spec:** `docs/superpowers/specs/2026-09-16-video-tag-taxonomy-v2-design.md`

## Global Constraints

- Image and carousel tagging must be unchanged: `json_schema('image')` and `json_schema('carousel')` produce the same output as before this work, and the legacy `validate` path is untouched.
- `VIDEO_TAGGING_ENABLED` stays `false` in production; nothing in this plan flips it.
- `tag.confidence` is NOT NULL: multi, ordered, open, text and computed rows store `1.0`.
- `beats` must be non-empty and non-decreasing in `t_start_s`; a violation fails the run.
- `concept_tags` is capped at 6 items (truncate, do not fail). Other open dimensions are unbounded.
- Open slugs are normalized: lowercase, non-alphanumerics to `_`, repeats collapsed, ends stripped, max 48 chars.
- Aspect ratio labels: nearest of `9:16`, `4:5`, `1:1`, `16:9` within 3% of the target ratio, else `other`.
- Scene detection: ffmpeg `select='gt(scene,0.3)'`; `cuts_per_10s = cuts / duration_s * 10` rounded to 1 decimal; a cut-probe failure omits the row, an ffprobe failure fails the run.
- Tests run against the shared Supabase test database `cs_test_diagnosis`, never `cs_test` (another worktree uses it). Every pytest command in this plan is prefixed with:

  ```bash
  cd "/Users/anantchandra/Desktop/Custom LP/creative-strategist" && export DATABASE_URL_TEST="$(grep '^DATABASE_URL_TEST=' .env | cut -d= -f2- | sed 's#/cs_test?#/cs_test_diagnosis?#')"
  ```

  The conftest runs `dbmate up` on that URL before the session, so new migrations apply automatically.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Lint before every commit: `uv run ruff check . && uv run ruff format .`

## File map

| File | Responsibility |
|---|---|
| `db/migrations/20260916000003_tag_v2.sql` | Four nullable columns on `tag`; new `tag_vocab` table |
| `src/cs/tagging/repo.py` | Write and read the new columns; `latest_tag_value` |
| `src/cs/tagging/taxonomy.yaml` | Version 2.0: kinds, `nested_formats`, video layers |
| `src/cs/tagging/taxonomy.py` | `Dimension` gains kind fields; nested schema; nested validator |
| `src/cs/tagging/vocab.py` | Slug normalization and `tag_vocab` access |
| `src/cs/tagging/video.py` | `probe_video`, `aspect_label` |
| `src/cs/tagging/prompts.py` | `SYSTEM_V2`, `user_text_nested` |
| `src/cs/tagging/tagger.py` | `system` argument; `TagResult.payload` |
| `src/cs/tagging/fakes.py` | Nested default payload for tests |
| `src/cs/tagging/runner.py` | Video v2 path: `profile_video` (no writes) and persistence |
| `src/cs/tagging/consistency.py` | Agreement scoring between two profiles |
| `scripts/tag_consistency.py` | Tags a sample twice in memory and prints agreement |
| `src/cs/api/routers/tags.py` | `kind` on dimensions; transcript endpoint; open values endpoint |
| `web/src/lib/types.ts`, `web/src/lib/api.ts` | New fields and endpoint |
| `web/src/components/tag-list.tsx` | Render per kind |
| `web/src/components/creative-filters.tsx`, `web/src/app/(app)/creatives/page.tsx` | Open-dimension values in the filter |

---

### Task 1: Migration and repo columns

**Files:**
- Create: `db/migrations/20260916000003_tag_v2.sql`
- Modify: `src/cs/tagging/repo.py` (`finish_tag_run`, `latest_tags`; add `latest_tag_value`)
- Test: `tests/tagging/test_repo.py`

**Interfaces:**
- Produces: `finish_tag_run(db, org_id, run_id, tags: list[dict], prompt_sha, input_tokens, output_tokens)` where each tag dict may carry optional keys `kind` (default `"single"`), `raw_value`, `t_start_s`, `ordinal`.
- Produces: `latest_tags(...)` rows now include `kind, raw_value, t_start_s, ordinal`, ordered by `dimension, ordinal, t_start_s, value`.
- Produces: `latest_tag_value(db, org_id, creative_id, dimension) -> str | None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/tagging/test_repo.py`:

```python
async def test_finish_tag_run_round_trips_v2_columns(db, org_id, settings):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD")
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("v", "video", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    rows = [
        {"dimension": "messenger", "kind": "single", "value": "expert_on_camera",
         "confidence": 0.9, "evidence": "coat"},
        {"dimension": "beats", "kind": "ordered", "value": "problem", "confidence": 1.0,
         "evidence": "", "t_start_s": 5.0, "ordinal": 1},
        {"dimension": "beats", "kind": "ordered", "value": "hook", "confidence": 1.0,
         "evidence": "", "t_start_s": 0.0, "ordinal": 0},
        {"dimension": "concept_tags", "kind": "open", "value": "pharmacist_warning",
         "raw_value": "Pharmacist Warning", "confidence": 1.0, "evidence": "e"},
        {"dimension": "transcript", "kind": "text", "value": "hello there",
         "confidence": 1.0, "evidence": ""},
    ]
    await repo.finish_tag_run(db, org_id, rid, rows, "sha", 1, 1)
    got = [dict(r) for r in await repo.latest_tags(db, org_id, cid)]
    assert [(r["dimension"], r["value"], r["ordinal"]) for r in got] == [
        ("beats", "hook", 0),
        ("beats", "problem", 1),
        ("concept_tags", "pharmacist_warning", None),
        ("messenger", "expert_on_camera", None),
        ("transcript", "hello there", None),
    ]
    assert got[1]["t_start_s"] == 5.0 and got[1]["kind"] == "ordered"
    assert got[2]["raw_value"] == "Pharmacist Warning" and got[2]["kind"] == "open"
    assert got[3]["kind"] == "single" and got[3]["raw_value"] is None
    assert await repo.latest_tag_value(db, org_id, cid, "transcript") == "hello there"
    assert await repo.latest_tag_value(db, org_id, cid, "nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tagging/test_repo.py::test_finish_tag_run_round_trips_v2_columns -q`
Expected: FAIL. Either `column "kind" of relation "tag" does not exist` or `AttributeError: module 'cs.tagging.repo' has no attribute 'latest_tag_value'`.

- [ ] **Step 3: Write the migration**

Create `db/migrations/20260916000003_tag_v2.sql`:

```sql
-- migrate:up
-- Tag taxonomy v2: one tag row can now be one item of a set (multi), one step of an
-- ordered sequence (ordinal), an open-vocabulary slug (raw_value keeps what the model
-- said before canonicalization), a computed numeric, or a text field. Legacy rows are
-- 'single' with the other columns NULL.
ALTER TABLE tag
  ADD COLUMN kind TEXT NOT NULL DEFAULT 'single',
  ADD COLUMN t_start_s REAL,
  ADD COLUMN ordinal INT,
  ADD COLUMN raw_value TEXT;

-- Per-org canonical vocabulary for open dimensions. A slug's canonical_slug is itself
-- until someone merges it; count is bumped for the canonical row on each finished run.
CREATE TABLE tag_vocab (
  org_id UUID NOT NULL REFERENCES org(id),
  dimension TEXT NOT NULL,
  slug TEXT NOT NULL,
  canonical_slug TEXT NOT NULL,
  count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, dimension, slug)
);
CREATE INDEX tag_vocab_count_idx ON tag_vocab (org_id, dimension, count DESC);

-- migrate:down
DROP INDEX tag_vocab_count_idx;
DROP TABLE tag_vocab;
ALTER TABLE tag
  DROP COLUMN raw_value,
  DROP COLUMN ordinal,
  DROP COLUMN t_start_s,
  DROP COLUMN kind;
```

- [ ] **Step 4: Update the repo**

In `src/cs/tagging/repo.py`, replace the insert loop inside `finish_tag_run` with:

```python
        for t in tags:
            await tx.execute(
                "INSERT INTO tag (id, org_id, tag_run_id, creative_id, dimension, value, "
                "confidence, evidence, kind, raw_value, t_start_s, ordinal) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
                new_id(),
                org_id,
                run_id,
                creative_id,
                t["dimension"],
                t["value"],
                t["confidence"],
                t["evidence"],
                t.get("kind", "single"),
                t.get("raw_value"),
                t.get("t_start_s"),
                t.get("ordinal"),
            )
```

Replace `latest_tags` and add `latest_tag_value`:

```python
async def latest_tags(db: Database, org_id: UUID, creative_id: UUID) -> list[asyncpg.Record]:
    rid = await latest_run_id(db, org_id, creative_id)
    if rid is None:
        return []
    return await db.fetch(
        "SELECT dimension, value, confidence, evidence, kind, raw_value, t_start_s, ordinal "
        "FROM tag WHERE tag_run_id=$1 ORDER BY dimension, ordinal, t_start_s, value",
        rid,
    )


async def latest_tag_value(
    db: Database, org_id: UUID, creative_id: UUID, dimension: str
) -> str | None:
    rid = await latest_run_id(db, org_id, creative_id)
    if rid is None:
        return None
    return await db.fetchval(
        "SELECT value FROM tag WHERE tag_run_id=$1 AND dimension=$2 LIMIT 1", rid, dimension
    )
```

- [ ] **Step 5: Run the tagging and API tests**

Run: `uv run pytest tests/tagging/test_repo.py tests/api/test_tags.py -q`
Expected: the new test passes; `test_tags_endpoints` FAILS on the equality of the tags list because the response now has extra keys. Fix it in `tests/api/test_tags.py` by replacing the first assertion with:

```python
    assert r.json() == [
        {
            "dimension": "tone",
            "value": "warm",
            "confidence": 0.8,
            "evidence": "e",
            "kind": "single",
            "raw_value": None,
            "t_start_s": None,
            "ordinal": None,
        }
    ]
```

Run again: `uv run pytest tests/tagging/test_repo.py tests/api/test_tags.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add db/migrations/20260916000003_tag_v2.sql src/cs/tagging/repo.py tests/tagging/test_repo.py tests/api/test_tags.py
git commit -m "feat(tagging): tag rows carry kind, timestamp, ordinal and raw value; tag_vocab table

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Taxonomy v2 file, loader, nested schema, nested validator

**Files:**
- Modify: `src/cs/tagging/taxonomy.yaml`
- Modify: `src/cs/tagging/taxonomy.py`
- Create: `src/cs/tagging/vocab.py` (only `normalize` in this task; DB functions come in Task 3)
- Test: `tests/tagging/test_taxonomy.py`, `tests/tagging/test_vocab.py`

**Interfaces:**
- Produces: `Dimension(name, group, applies_to, values, description, kind, timestamped, max_values, vocab_hint, computed)`.
- Produces: `Taxonomy.nested_formats: tuple[str, ...]`, `Taxonomy.is_nested(fmt) -> bool`, `Taxonomy.open_dimensions(fmt) -> list[Dimension]`.
- Produces: `Taxonomy.json_schema(fmt)` returns the nested schema for nested formats, the legacy flat schema otherwise.
- Produces: `Taxonomy.validate(payload_or_tags, fmt)` dispatches: nested formats take the model's nested payload dict and return row dicts `{dimension, kind, value, raw_value, confidence, evidence, t_start_s, ordinal}`; legacy formats take the flat tag list as before. Raises `ValueError` on a nested payload that breaks a rule.
- Produces: `vocab.normalize(slug: str) -> str`.

- [ ] **Step 1: Write the failing taxonomy tests**

Replace the whole of `tests/tagging/test_taxonomy.py` with:

```python
import pytest

from cs.tagging.taxonomy import load_taxonomy

LEGACY_IMAGE_DIMENSIONS = [
    "hook_type", "hook_text_present", "message_angle", "offer_type", "urgency", "proof_type",
    "claim_specificity", "cta_style", "cta_verb", "visual_style", "color_palette",
    "text_density", "layout", "brand_presence", "product_visible", "people_present",
    "face_closeup", "demographic_signal", "tone", "humor_present", "aspect_ratio",
    "format_subtype", "hook_summary", "message_summary",
]

VIDEO_DIMENSIONS = {
    "numeric": ["duration_s", "cuts_per_10s"],
    # file order: brand_presence and aspect_ratio live in the shared legacy block
    "single": ["brand_presence", "aspect_ratio", "messenger", "narration", "captions", "opening_shot",
               "production", "setting", "product_reveal", "ending", "music"],
    "multi": ["hook_devices", "proof_devices", "angles", "offer_elements", "cta_modes", "registers"],
    "ordered": ["beats"],
    "open": ["concept_tags", "topics", "comparison_targets", "audience_callouts", "claims",
             "visual_motifs"],
    "text": ["hook_verbatim", "concept_summary", "transcript"],
}


def test_version_and_nested_formats():
    t = load_taxonomy()
    assert t.version == "2.0" and len(t.sha256) == 64
    assert t.nested_formats == ("video",)
    assert t.is_nested("video") and not t.is_nested("image") and not t.is_nested("carousel")


def test_image_and_carousel_keep_the_legacy_dimensions():
    t = load_taxonomy()
    assert [d.name for d in t.for_format("image")] == LEGACY_IMAGE_DIMENSIONS
    carousel = [d.name for d in t.for_format("carousel")]
    assert carousel == LEGACY_IMAGE_DIMENSIONS
    assert all(d.kind in ("single", "text") for d in t.for_format("image"))


def test_video_dimensions_by_kind():
    t = load_taxonomy()
    by_kind: dict[str, list[str]] = {}
    for d in t.for_format("video"):
        by_kind.setdefault(d.kind, []).append(d.name)
    assert by_kind == VIDEO_DIMENSIONS
    names = {d.name: d for d in t.for_format("video")}
    assert names["duration_s"].computed and names["aspect_ratio"].computed
    assert not names["messenger"].computed
    assert names["hook_devices"].timestamped and not names["angles"].timestamped
    assert names["concept_tags"].max_values == 6 and names["topics"].max_values is None
    assert names["concept_tags"].vocab_hint == 40
    assert [d.name for d in t.open_dimensions("video")] == VIDEO_DIMENSIONS["open"]


def test_legacy_flat_schema_is_unchanged_for_images():
    schema = load_taxonomy().json_schema("image")
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    item = schema["properties"]["tags"]["items"]
    assert set(item["required"]) == {"dimension", "value", "confidence", "evidence"}
    assert item["properties"]["dimension"]["enum"] == LEGACY_IMAGE_DIMENSIONS
    assert item["properties"]["value"] == {"type": "string"}


def test_nested_schema_enforces_every_enum_and_skips_computed():
    schema = load_taxonomy().json_schema("video")
    assert list(schema["properties"]) == ["single", "multi", "ordered", "open", "text"]
    assert schema["required"] == ["single", "multi", "ordered", "open", "text"]
    single = schema["properties"]["single"]
    assert "aspect_ratio" not in single["properties"]
    messenger = single["properties"]["messenger"]
    assert messenger["properties"]["value"]["enum"][0] == "none"
    assert messenger["required"] == ["value", "confidence", "evidence"]
    hook = schema["properties"]["multi"]["properties"]["hook_devices"]["items"]
    assert hook["required"] == ["value", "evidence", "t_start_s"]
    assert "audience_callout" in hook["properties"]["value"]["enum"]
    angles = schema["properties"]["multi"]["properties"]["angles"]["items"]
    assert "t_start_s" not in angles["properties"]
    beats = schema["properties"]["ordered"]["properties"]["beats"]["items"]
    assert beats["required"] == ["value", "t_start_s"]
    concept = schema["properties"]["open"]["properties"]["concept_tags"]["items"]
    assert concept["required"] == ["slug", "evidence"]
    assert schema["properties"]["text"]["properties"]["transcript"] == {"type": "string"}
    # strict mode: every object lists all of its properties as required
    for section in schema["properties"].values():
        assert section["required"] == list(section["properties"])
        assert section["additionalProperties"] is False


def _payload() -> dict:
    single = {
        "messenger": ("expert_on_camera", 0.9, "coat"),
        "narration": ("on_camera_speech", 1.0, "speaks"),
        "captions": ("text_cards", 0.9, "overlays"),
        "opening_shot": ("face_to_camera", 1.0, "face"),
        "production": ("phone_ugc", 0.8, "handheld"),
        "setting": ("home", 0.9, "kitchen"),
        "product_reveal": ("after_10s", 1.0, "0:55"),
        "brand_presence": ("prominent", 1.0, "logo"),
        "ending": ("cta_card", 1.0, "card"),
        "music": ("none", 1.0, "silent"),
    }
    return {
        "single": {k: {"value": v, "confidence": c, "evidence": e} for k, (v, c, e) in single.items()},
        "multi": {
            "hook_devices": [
                {"value": "warning_or_risk", "t_start_s": 0.0, "evidence": "a"},
                {"value": "audience_callout", "t_start_s": 1.5, "evidence": "b"},
                {"value": "warning_or_risk", "t_start_s": 2.0, "evidence": "dup"},
            ],
            "proof_devices": [{"value": "expert_credential", "t_start_s": 3.0, "evidence": "c"}],
            "angles": [{"value": "risk_avoidance", "evidence": "d"}],
            "offer_elements": [],
            "cta_modes": [{"value": "end_card", "t_start_s": 88.0, "evidence": "e"}],
            "registers": [{"value": "fear", "evidence": "f"}, {"value": "authority", "evidence": "g"}],
        },
        "ordered": {
            "beats": [
                {"value": "hook", "t_start_s": 0.0},
                {"value": "problem", "t_start_s": 5.0},
                {"value": "proof", "t_start_s": 5.0},
                {"value": "cta", "t_start_s": 85.0},
            ]
        },
        "open": {
            "concept_tags": [
                {"slug": "Pharmacist Warning", "evidence": "h"},
                {"slug": "pharmacist-warning", "evidence": "same after normalization"},
                {"slug": "otc sleep aid risks", "evidence": "i"},
            ],
            "topics": [{"slug": "sleep", "evidence": "j"}],
            "comparison_targets": [],
            "audience_callouts": [],
            "claims": [],
            "visual_motifs": [{"slug": "  Pharmacy--Coat  ", "evidence": "k"}],
        },
        "text": {
            "hook_verbatim": "Worst OTC Sleep Aids!! / As a pharmacist",
            "concept_summary": "A pharmacist warns. She recommends gummies.",
            "transcript": "As a pharmacist with ten years",
        },
    }


def test_validate_nested_flattens_every_kind():
    rows = load_taxonomy().validate(_payload(), "video")
    by_dim: dict[str, list[dict]] = {}
    for r in rows:
        by_dim.setdefault(r["dimension"], []).append(r)
    assert set(rows[0]) == {
        "dimension", "kind", "value", "raw_value", "confidence", "evidence", "t_start_s", "ordinal"
    }
    m = by_dim["messenger"][0]
    assert (m["kind"], m["value"], m["confidence"], m["t_start_s"], m["ordinal"]) == (
        "single", "expert_on_camera", 0.9, None, None
    )
    assert [(r["value"], r["t_start_s"]) for r in by_dim["hook_devices"]] == [
        ("warning_or_risk", 0.0), ("audience_callout", 1.5)
    ]
    assert all(r["confidence"] == 1.0 for r in by_dim["hook_devices"])
    assert by_dim["angles"][0]["t_start_s"] is None
    assert "offer_elements" not in by_dim
    assert [(r["value"], r["ordinal"], r["t_start_s"]) for r in by_dim["beats"]] == [
        ("hook", 0, 0.0), ("problem", 1, 5.0), ("proof", 2, 5.0), ("cta", 3, 85.0)
    ]
    assert [(r["value"], r["raw_value"]) for r in by_dim["concept_tags"]] == [
        ("pharmacist_warning", "Pharmacist Warning"), ("otc_sleep_aid_risks", "otc sleep aid risks")
    ]
    assert by_dim["visual_motifs"][0]["value"] == "pharmacy_coat"
    assert "comparison_targets" not in by_dim
    assert by_dim["transcript"][0] == {
        "dimension": "transcript", "kind": "text", "value": "As a pharmacist with ten years",
        "raw_value": None, "confidence": 1.0, "evidence": "", "t_start_s": None, "ordinal": None,
    }
    # computed dimensions are never produced by the validator
    assert not {"duration_s", "cuts_per_10s", "aspect_ratio"} & set(by_dim)


def test_validate_nested_caps_concept_tags_at_six():
    p = _payload()
    p["open"]["concept_tags"] = [{"slug": f"idea_{i}", "evidence": ""} for i in range(9)]
    rows = load_taxonomy().validate(p, "video")
    assert [r["value"] for r in rows if r["dimension"] == "concept_tags"] == [
        f"idea_{i}" for i in range(6)
    ]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda p: p["single"]["messenger"].update(value="wizard"), "messenger"),
        (lambda p: p["single"].pop("music"), "music"),
        (lambda p: p["multi"]["angles"].append({"value": "nope", "evidence": ""}), "angles"),
        (lambda p: p["ordered"].update(beats=[]), "beats"),
        (
            lambda p: p["ordered"].update(
                beats=[{"value": "hook", "t_start_s": 3.0}, {"value": "cta", "t_start_s": 1.0}]
            ),
            "beats",
        ),
    ],
)
def test_validate_nested_rejects_rule_breaks(mutate, message):
    p = _payload()
    mutate(p)
    with pytest.raises(ValueError, match=message):
        load_taxonomy().validate(p, "video")


def test_validate_nested_clamps_confidence_and_truncates_evidence():
    p = _payload()
    p["single"]["messenger"].update(confidence=7, evidence="x" * 600)
    rows = load_taxonomy().validate(p, "video")
    m = next(r for r in rows if r["dimension"] == "messenger")
    assert m["confidence"] == 1.0 and len(m["evidence"]) == 500


def test_legacy_validate_still_coerces_and_dedupes_for_images():
    t = load_taxonomy()
    out = t.validate(
        [
            {"dimension": "hook_type", "value": "question", "confidence": 1.4, "evidence": "e"},
            {"dimension": "hook_type", "value": "made_up", "confidence": 0.5, "evidence": "e"},
            {"dimension": "pacing", "value": "fast", "confidence": 0.9, "evidence": "e"},
            {"dimension": "hook_summary", "value": "Opens with a question", "confidence": 0.9,
             "evidence": "e"},
        ],
        "image",
    )
    assert [(o["dimension"], o["value"], o["confidence"]) for o in out] == [
        ("hook_type", "question", 1.0),
        ("hook_summary", "Opens with a question", 0.9),
    ]
```

And create `tests/tagging/test_vocab.py` with the pure test only (DB tests are added in Task 3):

```python
import pytest

from cs.tagging.vocab import normalize


@pytest.mark.parametrize(
    "raw, slug",
    [
        ("Pharmacist Warning", "pharmacist_warning"),
        ("pharmacist-warning", "pharmacist_warning"),
        ("  Pharmacy--Coat  ", "pharmacy_coat"),
        ("Benadryl users!", "benadryl_users"),
        ("___", ""),
        ("x" * 60, "x" * 48),
        ("a" * 47 + "_b", "a" * 47),
    ],
)
def test_normalize(raw, slug):
    assert normalize(raw) == slug
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tagging/test_taxonomy.py tests/tagging/test_vocab.py -q`
Expected: FAIL. The first assertion sees version `"1.0"`; `test_vocab` fails with `ModuleNotFoundError: cs.tagging.vocab`.

- [ ] **Step 3: Write `vocab.normalize`**

Create `src/cs/tagging/vocab.py`:

```python
"""Open-vocabulary tags: slug normalization and the per-org canonical vocabulary."""

from __future__ import annotations

import re

MAX_SLUG = 48


def normalize(slug: str) -> str:
    """lowercase snake_case: non-alphanumerics become '_', runs collapse, ends are stripped,
    and the result is cut at 48 characters (then stripped again so it never ends in '_')."""
    s = re.sub(r"[^a-z0-9]+", "_", slug.lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:MAX_SLUG].rstrip("_")
```

- [ ] **Step 4: Rewrite `taxonomy.yaml` as version 2.0**

Replace the whole file with the following. The image/carousel block is the current file with `video` removed from every `applies_to` except `aspect_ratio` and `brand_presence`, and the nine video-only dimensions (`first_3s_content`, `product_timing`, `presenter_type`, `pacing`, `music_present`, `voiceover`, `caption_style`, `storyline`, `ending`) deleted. Legacy dimensions carry no `kind`; the loader defaults it.

```yaml
version: "2.0"
# Formats whose model call returns the nested v2 profile (spec §2). Every other format
# keeps the legacy flat {"tags": [...]} schema.
nested_formats: [video]
dimensions:
  # =====================================================================================
  # image / carousel -- legacy dimensions, unchanged for those formats
  # =====================================================================================
  - name: hook_type
    group: hook
    applies_to: [image, carousel]
    values: [problem_statement, question, bold_claim, statistic, social_proof, before_after, curiosity_gap, offer_led, demo_first, ugc_reaction, other]
    description: The attention device used in the first moment (first frame / first 3 seconds / headline).
  - name: hook_text_present
    group: hook
    applies_to: [image, carousel]
    values: ["yes", "no"]
    description: Whether on-asset text functions as a hook.
  - name: message_angle
    group: message
    applies_to: [image, carousel]
    values: [pain_relief, aspiration, convenience, savings, quality, status, fear_of_missing_out, community, health, novelty, other]
    description: The primary persuasive angle.
  - name: offer_type
    group: message
    applies_to: [image, carousel]
    values: [none, discount_percent, discount_amount, free_trial, free_shipping, bundle, limited_time, gift, other]
    description: The explicit offer, if any.
  - name: urgency
    group: message
    applies_to: [image, carousel]
    values: [none, mild, strong]
    description: Degree of urgency or scarcity language.
  - name: proof_type
    group: message
    applies_to: [image, carousel]
    values: [none, testimonial, review_stars, expert, press, numbers, comparison, guarantee, other]
    description: Kind of proof shown.
  - name: claim_specificity
    group: message
    applies_to: [image, carousel]
    values: [vague, moderate, specific]
    description: How concrete the main claim is.
  - name: cta_style
    group: cta
    applies_to: [image, carousel]
    values: [none, button_only, spoken, on_asset_text, spoken_and_text, other]
    description: How the call to action is delivered.
  - name: cta_verb
    group: cta
    applies_to: [image, carousel]
    values: [shop, learn, try, get, start, sign_up, download, book, other, none]
    description: The imperative verb of the CTA.
  - name: visual_style
    group: visual
    applies_to: [image, carousel]
    values: [ugc, studio_product, lifestyle, animation, screenshot_ui, text_only, meme, collage, illustration, other]
    description: The overall production style.
  - name: color_palette
    group: visual
    applies_to: [image, carousel]
    values: [bright, muted, dark, monochrome, brand_colors, pastel, high_contrast, other]
    description: Dominant palette.
  - name: text_density
    group: visual
    applies_to: [image, carousel]
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
    description: Logo, packaging or brand name visibility.
  - name: product_visible
    group: visual
    applies_to: [image, carousel]
    values: ["yes", "no"]
    description: Whether the product itself is shown.
  - name: people_present
    group: people
    applies_to: [image, carousel]
    values: [none, one, two_plus]
    description: Number of people visible.
  - name: face_closeup
    group: people
    applies_to: [image, carousel]
    values: ["yes", "no"]
    description: A face fills a large share of the frame at some point.
  - name: demographic_signal
    group: people
    applies_to: [image, carousel]
    values: [none, young_adult, adult, senior, family, other]
    description: Apparent target demographic shown.
  - name: tone
    group: tone
    applies_to: [image, carousel]
    values: [playful, serious, urgent, warm, authoritative, humorous, inspirational, neutral, other]
    description: Emotional register.
  - name: humor_present
    group: tone
    applies_to: [image, carousel]
    values: ["yes", "no"]
    description: Any humor.
  - name: aspect_ratio
    group: format
    applies_to: [image, video, carousel]
    values: ["1:1", "4:5", "9:16", "16:9", other]
    # For video this is computed from ffprobe width/height in code (spec §1 layer 0) and
    # never asked of the model; images and carousels still ask the model as before.
    computed_for: [video]
    description: Aspect ratio of the asset.
  - name: format_subtype
    group: format
    applies_to: [image, carousel]
    values: [static, gif_like, talking_head, montage, tutorial, unboxing, testimonial, comparison, listicle, other]
    description: Recognizable ad sub-format.
  - name: hook_summary
    group: summary
    applies_to: [image, carousel]
    values: null
    description: One sentence describing the hook verbatim or paraphrased.
  - name: message_summary
    group: summary
    applies_to: [image, carousel]
    values: null
    description: One sentence describing the main message.

  # =====================================================================================
  # video -- taxonomy v2 (spec §1). Layer 0 is computed in code.
  # =====================================================================================
  - name: duration_s
    group: form
    kind: numeric
    applies_to: [video]
    description: Length in seconds, from ffprobe.
  - name: cuts_per_10s
    group: form
    kind: numeric
    applies_to: [video]
    description: Scene cuts per 10 seconds, from ffmpeg scene detection at threshold 0.3.

  # ---- layer 1: closed single-value ----
  - name: messenger
    group: messenger
    kind: single
    applies_to: [video]
    values: [none, creator_on_camera, creator_off_camera, expert_on_camera, founder_on_camera, actor_scripted, multiple_people, graphic_only]
    description: Who carries the message. expert_on_camera only when a credential is stated on screen or in speech; creator_off_camera means hands, POV or body without a face; graphic_only when no person appears and the content is text or graphics.
  - name: narration
    group: messenger
    kind: single
    applies_to: [video]
    values: [none, on_camera_speech, voiceover, mixed]
    description: How speech is delivered, if any.
  - name: captions
    group: form
    kind: single
    applies_to: [video]
    values: [none, subtitles, text_cards, both]
    description: On-screen text treatment. subtitles transcribe speech; text_cards are headline overlays that are not a transcript.
  - name: opening_shot
    group: structure
    kind: single
    applies_to: [video]
    values: [text_card, face_to_camera, product_in_hand, product_only, scene_broll, screen_recording]
    description: What dominates the first three seconds.
  - name: production
    group: form
    kind: single
    applies_to: [video]
    values: [phone_ugc, polished_studio, motion_graphics, mixed_ugc_broll, screen_capture]
    description: Production style of the footage.
  - name: setting
    group: form
    kind: single
    applies_to: [video]
    values: [home, outdoor, studio_or_neutral, screen, none]
    description: Where the footage is shot; screen for recordings; none when there is no live footage.
  - name: product_reveal
    group: structure
    kind: single
    applies_to: [video]
    values: [first_3s, 3_to_10s, after_10s, never]
    description: When the product or its packaging first appears. A generic pill, gummy or capsule shown in hand counts as the product.
  - name: ending
    group: structure
    kind: single
    applies_to: [video]
    values: [cta_card, product_shot, speaker_signoff, hard_cut, loop]
    description: How the video ends.
  - name: music
    group: form
    kind: single
    applies_to: [video]
    values: [none, present]
    description: Whether music is audible at any point.

  # ---- layer 2: closed multi-value ----
  - name: hook_devices
    group: hook
    kind: multi
    timestamped: true
    applies_to: [video]
    values: [audience_callout, comparison_vs_alternative, warning_or_risk, confession_or_mistake, question, bold_claim, statistic, curiosity_gap, offer_lead, product_demo, result_reveal, listicle_promise, visual_pattern_interrupt]
    description: Every attention device used in the first five seconds, each with the second it starts.
  - name: proof_devices
    group: message
    kind: multi
    timestamped: true
    applies_to: [video]
    values: [personal_testimonial, expert_credential, study_or_citation, review_screenshot, lab_report_or_certificate, before_after, live_demo, comparison_table, guarantee, press_mention]
    description: Every kind of proof shown or spoken, each with the second it starts. expert_credential requires a stated credential.
  - name: angles
    group: message
    kind: multi
    applies_to: [video]
    values: [symptom_relief, risk_avoidance, natural_alternative, convenience, savings, quality_or_purity, social_proof, novelty, aspiration, identity]
    description: Every persuasive angle the ad leans on.
  - name: offer_elements
    group: message
    kind: multi
    timestamped: true
    applies_to: [video]
    values: [discount_percent, discount_amount, free_shipping, bundle, limited_time, guarantee, gift, subscribe_and_save]
    description: Every explicit offer element shown or spoken, each with the second it starts.
  - name: cta_modes
    group: cta
    kind: multi
    timestamped: true
    applies_to: [video]
    values: [spoken, on_screen_text, end_card]
    description: Every way the call to action is delivered, each with the second it starts.
  - name: registers
    group: message
    kind: multi
    applies_to: [video]
    values: [fear, relief, humor, warmth, authority, urgency, curiosity, aspiration]
    description: Every emotional register the ad uses.
  - name: beats
    group: structure
    kind: ordered
    applies_to: [video]
    values: [hook, problem, agitation, mechanism, solution_intro, demo, proof, offer, cta, signoff]
    description: The ad's sections in order of appearance with the second each starts. Every ad has at least hook. A beat may recur.

  # ---- layer 3: open vocabulary ----
  - name: concept_tags
    group: concept
    kind: open
    max_values: 6
    vocab_hint: 40
    applies_to: [video]
    description: The creative idea in a strategist's words, two to six short slugs (e.g. pharmacist_warning, benadryl_swap). Never a brand name.
  - name: topics
    group: concept
    kind: open
    vocab_hint: 40
    applies_to: [video]
    description: Subjects the ad discusses (e.g. sleep, magnesium, muscle_spasms).
  - name: comparison_targets
    group: concept
    kind: open
    vocab_hint: 40
    applies_to: [video]
    description: Named alternatives the ad positions against (e.g. benadryl, melatonin, otc_sleep_aids).
  - name: audience_callouts
    group: concept
    kind: open
    vocab_hint: 40
    applies_to: [video]
    description: Who the ad explicitly addresses (e.g. benadryl_users, women_group_chat, over_50).
  - name: claims
    group: concept
    kind: open
    vocab_hint: 40
    applies_to: [video]
    description: Specific benefit claims made (e.g. fall_asleep_naturally, wake_clear_headed, third_party_tested).
  - name: visual_motifs
    group: concept
    kind: open
    vocab_hint: 40
    applies_to: [video]
    description: Recognisable visual elements (e.g. kitchen, hand_holding_gummy, pharmacy_coat, lab_report_screenshot).

  # ---- layer 4: text ----
  - name: hook_verbatim
    group: text
    kind: text
    applies_to: [video]
    description: The exact first on-screen text, then the first spoken sentence, separated by " / ". Either part may be absent.
  - name: concept_summary
    group: text
    kind: text
    applies_to: [video]
    description: Two sentences. What the ad is, then what it says.
  - name: transcript
    group: text
    kind: text
    applies_to: [video]
    description: Verbatim speech with no timestamps. Empty string when there is no speech.
```

- [ ] **Step 5: Rewrite `taxonomy.py`**

Replace the whole of `src/cs/tagging/taxonomy.py` with:

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from cs.tagging.vocab import normalize

DEFAULT_PATH = Path(__file__).with_name("taxonomy.yaml")
KINDS = ("numeric", "single", "multi", "ordered", "open", "text")
EVIDENCE_MAX = 500
ROW_KEYS = ("dimension", "kind", "value", "raw_value", "confidence", "evidence", "t_start_s", "ordinal")


@dataclass(frozen=True)
class Dimension:
    name: str
    group: str
    applies_to: tuple[str, ...]
    values: tuple[str, ...] | None
    description: str
    kind: str = "single"
    timestamped: bool = False
    max_values: int | None = None
    vocab_hint: int = 0
    # Formats for which code, not the model, produces the value (spec §1 layer 0).
    computed_for: tuple[str, ...] = ()

    @property
    def computed(self) -> bool:
        return self.kind == "numeric" or bool(self.computed_for)

    def computed_in(self, fmt: str) -> bool:
        return self.kind == "numeric" or fmt in self.computed_for


@dataclass(frozen=True)
class Taxonomy:
    version: str
    sha256: str
    dimensions: tuple[Dimension, ...]
    nested_formats: tuple[str, ...] = ()

    def for_format(self, fmt: str) -> list[Dimension]:
        return [d for d in self.dimensions if fmt in d.applies_to]

    def is_nested(self, fmt: str) -> bool:
        return fmt in self.nested_formats

    def open_dimensions(self, fmt: str) -> list[Dimension]:
        return [d for d in self.for_format(fmt) if d.kind == "open"]

    def model_dimensions(self, fmt: str) -> list[Dimension]:
        """Dimensions the model is asked for: everything not computed in code for `fmt`."""
        return [d for d in self.for_format(fmt) if not d.computed_in(fmt)]

    # ---- schemas -----------------------------------------------------------------------

    def json_schema(self, fmt: str) -> dict:
        if self.is_nested(fmt):
            return self._nested_schema(fmt)
        return self._flat_schema(fmt)

    def _flat_schema(self, fmt: str) -> dict:
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

    def _nested_schema(self, fmt: str) -> dict:
        def obj(props: dict) -> dict:
            return {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            }

        def enum(d: Dimension) -> dict:
            return {"type": "string", "enum": list(d.values or ())}

        def items(props: dict) -> dict:
            return {"type": "array", "items": obj(props)}

        single, multi, ordered, open_, text = {}, {}, {}, {}, {}
        for d in self.model_dimensions(fmt):
            if d.kind == "single":
                single[d.name] = obj(
                    {"value": enum(d), "confidence": {"type": "number"}, "evidence": {"type": "string"}}
                )
            elif d.kind == "multi":
                props = {"value": enum(d), "evidence": {"type": "string"}}
                if d.timestamped:
                    props["t_start_s"] = {"type": "number"}
                multi[d.name] = items(props)
            elif d.kind == "ordered":
                ordered[d.name] = items({"value": enum(d), "t_start_s": {"type": "number"}})
            elif d.kind == "open":
                open_[d.name] = items({"slug": {"type": "string"}, "evidence": {"type": "string"}})
            elif d.kind == "text":
                text[d.name] = {"type": "string"}
        return obj(
            {"single": obj(single), "multi": obj(multi), "ordered": obj(ordered), "open": obj(open_), "text": obj(text)}
        )

    # ---- validation --------------------------------------------------------------------

    def validate(self, payload, fmt: str) -> list[dict]:
        if self.is_nested(fmt):
            return self._validate_nested(payload, fmt)
        return self._validate_flat(payload, fmt)

    def _validate_flat(self, tags: list[dict], fmt: str) -> list[dict]:
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
            out.append(
                {
                    "dimension": d.name,
                    "value": value,
                    "confidence": max(0.0, min(1.0, conf)),
                    "evidence": str(t.get("evidence", ""))[:EVIDENCE_MAX],
                }
            )
        best: dict[str, dict] = {}
        for tag in out:
            existing = best.get(tag["dimension"])
            if existing is None or tag["confidence"] > existing["confidence"]:
                best[tag["dimension"]] = tag
        return [best[name] for name in dict.fromkeys(t["dimension"] for t in out)]

    def _validate_nested(self, payload: dict, fmt: str) -> list[dict]:
        """Flatten the model's nested profile to tag rows. Raises ValueError on a rule break
        (spec §3): a missing single, a value outside its enum, empty or out-of-order beats."""
        if not isinstance(payload, dict):
            raise ValueError("profile payload is not an object")
        rows: list[dict] = []
        for d in self.model_dimensions(fmt):
            section = payload.get(_SECTION[d.kind]) or {}
            if d.kind == "single":
                rows.append(self._single_row(d, section.get(d.name)))
            elif d.kind == "multi":
                rows.extend(self._multi_rows(d, section.get(d.name) or []))
            elif d.kind == "ordered":
                rows.extend(self._ordered_rows(d, section.get(d.name) or []))
            elif d.kind == "open":
                rows.extend(self._open_rows(d, section.get(d.name) or []))
            elif d.kind == "text":
                rows.append(_row(d, str(section.get(d.name) or "")))
        return rows

    @staticmethod
    def _single_row(d: Dimension, item) -> dict:
        if not isinstance(item, dict):
            raise ValueError(f"{d.name}: missing")
        value = str(item.get("value", "")).strip()
        if value not in (d.values or ()):
            raise ValueError(f"{d.name}: value {value!r} not allowed")
        conf = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
        return _row(d, value, confidence=conf, evidence=item.get("evidence"))

    @staticmethod
    def _multi_rows(d: Dimension, items: list) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for item in items:
            value = str(item.get("value", "")).strip()
            if value not in (d.values or ()):
                raise ValueError(f"{d.name}: value {value!r} not allowed")
            if value in seen:
                continue
            seen.add(value)
            t = _seconds(item.get("t_start_s")) if d.timestamped else None
            out.append(_row(d, value, evidence=item.get("evidence"), t_start_s=t))
        return out

    @staticmethod
    def _ordered_rows(d: Dimension, items: list) -> list[dict]:
        if not items:
            raise ValueError(f"{d.name}: must not be empty")
        out: list[dict] = []
        last = float("-inf")
        for i, item in enumerate(items):
            value = str(item.get("value", "")).strip()
            if value not in (d.values or ()):
                raise ValueError(f"{d.name}: value {value!r} not allowed")
            t = _seconds(item.get("t_start_s"))
            if t is None or t < last:
                raise ValueError(f"{d.name}: t_start_s must be present and non-decreasing")
            last = t
            out.append(_row(d, value, t_start_s=t, ordinal=i))
        return out

    @staticmethod
    def _open_rows(d: Dimension, items: list) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for item in items:
            raw = str(item.get("slug", ""))
            slug = normalize(raw)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append(_row(d, slug, raw_value=raw, evidence=item.get("evidence")))
            if d.max_values is not None and len(out) >= d.max_values:
                break
        return out


_SECTION = {"single": "single", "multi": "multi", "ordered": "ordered", "open": "open", "text": "text"}


def _seconds(v) -> float | None:
    if v is None:
        return None
    return max(0.0, float(v))


def _row(
    d: Dimension,
    value: str,
    *,
    raw_value: str | None = None,
    confidence: float = 1.0,
    evidence: str | None = None,
    t_start_s: float | None = None,
    ordinal: int | None = None,
) -> dict:
    return {
        "dimension": d.name,
        "kind": d.kind,
        "value": value,
        "raw_value": raw_value,
        "confidence": confidence,
        "evidence": str(evidence or "")[:EVIDENCE_MAX],
        "t_start_s": t_start_s,
        "ordinal": ordinal,
    }


def computed_rows(taxonomy: Taxonomy, fmt: str, values: dict[str, str | float | None]) -> list[dict]:
    """Rows for the format's computed dimensions from `values` (name -> value); a None or
    missing value produces no row (spec §5: a failed cut probe omits cuts_per_10s)."""
    out: list[dict] = []
    for d in taxonomy.for_format(fmt):
        if not d.computed_in(fmt):
            continue
        v = values.get(d.name)
        if v is None:
            continue
        out.append(_row(d, str(v), evidence="computed"))
    return out


@lru_cache
def load_taxonomy(path: Path | None = None) -> Taxonomy:
    p = path or DEFAULT_PATH
    raw = p.read_bytes()
    data = yaml.safe_load(raw)
    dims = []
    for d in data["dimensions"]:
        values = tuple(str(v) for v in d["values"]) if d.get("values") else None
        kind = d.get("kind") or ("single" if values else "text")
        if kind not in KINDS:
            raise ValueError(f"{d['name']}: unknown kind {kind!r}")
        dims.append(
            Dimension(
                name=d["name"],
                group=d["group"],
                applies_to=tuple(d["applies_to"]),
                values=values,
                description=d["description"],
                kind=kind,
                timestamped=bool(d.get("timestamped", False)),
                max_values=d.get("max_values"),
                vocab_hint=int(d.get("vocab_hint", 0)),
                computed_for=tuple(d.get("computed_for", ())),
            )
        )
    return Taxonomy(
        version=str(data["version"]),
        sha256=hashlib.sha256(raw).hexdigest(),
        dimensions=tuple(dims),
        nested_formats=tuple(data.get("nested_formats", ())),
    )
```

Note for the implementer: `computed_rows` is a module-level function used by the runner in Task 6; `aspect_ratio` for video is produced there, not by the validator, because `computed_for: [video]` makes `computed_in("video")` true.

- [ ] **Step 6: Run the taxonomy and vocab tests**

Run: `uv run pytest tests/tagging/test_taxonomy.py tests/tagging/test_vocab.py -q`
Expected: PASS.

- [ ] **Step 7: Run the whole tagging and API suites to catch fallout**

Run: `uv run pytest tests/tagging tests/api tests/insights -q`
Expected: `tests/tagging/test_runner.py::test_tag_video_creative_passes_raw_video_to_tagger` and `..._cleans_up_assets_...` FAIL, because the runner still sends the flat `DEFAULT_TAGS` through the nested validator (`ValueError: messenger: missing`). Everything else passes. Those two are fixed in Task 6; do not patch them here.

If `tests/api/test_tags.py::test_tags_endpoints` fails on `hook_type` missing from `/tags/dimensions`, it should not (hook_type is still an image dimension); investigate before moving on.

- [ ] **Step 8: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/cs/tagging/taxonomy.yaml src/cs/tagging/taxonomy.py src/cs/tagging/vocab.py tests/tagging/test_taxonomy.py tests/tagging/test_vocab.py
git commit -m "feat(tagging): taxonomy v2 with layered video dimensions, nested schema and validator

Image and carousel keep the legacy flat schema. The two runner video tests fail until
the runner's v2 path lands.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Vocabulary table access

**Files:**
- Modify: `src/cs/tagging/vocab.py`
- Test: `tests/tagging/test_vocab.py`

**Interfaces:**
- Produces: `canonicalize(db, org_id, dimension, slugs: list[str]) -> dict[str, str]` (normalized slug to canonical slug; inserts unknown slugs as their own canonical).
- Produces: `bump_counts(db, org_id, dimension, canonical_slugs: list[str]) -> None`.
- Produces: `hints(db, org_id, dimension, n: int) -> list[tuple[str, int]]` (canonical slugs with count > 0, most common first, ties by slug).

- [ ] **Step 1: Write the failing tests**

Append to `tests/tagging/test_vocab.py`:

```python
from cs.tagging import vocab


async def test_canonicalize_inserts_new_and_reuses_existing(db, org_id):
    first = await vocab.canonicalize(db, org_id, "concept_tags", ["pharmacist_warning", "benadryl_swap", "pharmacist_warning"])
    assert first == {"pharmacist_warning": "pharmacist_warning", "benadryl_swap": "benadryl_swap"}
    await db.execute(
        "UPDATE tag_vocab SET canonical_slug='benadryl_swap' WHERE org_id=$1 AND dimension='concept_tags' AND slug='pharmacist_warning'",
        org_id,
    )
    second = await vocab.canonicalize(db, org_id, "concept_tags", ["pharmacist_warning", "new_idea", ""])
    assert second == {"pharmacist_warning": "benadryl_swap", "new_idea": "new_idea"}
    assert await db.fetchval("SELECT count(*) FROM tag_vocab WHERE org_id=$1", org_id) == 3


async def test_bump_counts_and_hints_order(db, org_id):
    await vocab.canonicalize(db, org_id, "topics", ["sleep", "magnesium", "zinc"])
    await vocab.bump_counts(db, org_id, "topics", ["sleep", "magnesium"])
    await vocab.bump_counts(db, org_id, "topics", ["sleep"])
    assert await vocab.hints(db, org_id, "topics", 10) == [("sleep", 2), ("magnesium", 1)]
    assert await vocab.hints(db, org_id, "topics", 1) == [("sleep", 2)]
    assert await vocab.hints(db, org_id, "claims", 10) == []


async def test_vocab_is_per_org_and_per_dimension(db, org_id):
    other = await db.fetchval("INSERT INTO org (id, name) VALUES (gen_random_uuid(), 'o') RETURNING id")
    await vocab.canonicalize(db, org_id, "topics", ["sleep"])
    await vocab.bump_counts(db, org_id, "topics", ["sleep"])
    assert await vocab.hints(db, other, "topics", 10) == []
    assert await vocab.hints(db, org_id, "claims", 10) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tagging/test_vocab.py -q`
Expected: FAIL with `AttributeError: module 'cs.tagging.vocab' has no attribute 'canonicalize'`.

- [ ] **Step 3: Implement the DB functions**

Append to `src/cs/tagging/vocab.py`:

```python
from uuid import UUID  # noqa: E402  (keep imports at top in the real file)

from cs.core.db import Database  # noqa: E402


async def canonicalize(
    db: Database, org_id: UUID, dimension: str, slugs: list[str]
) -> dict[str, str]:
    """Map each normalized slug to the org's canonical slug for it, registering unknown
    slugs as their own canonical. Empty slugs are ignored."""
    out: dict[str, str] = {}
    for slug in dict.fromkeys(s for s in slugs if s):
        canonical = await db.fetchval(
            "SELECT canonical_slug FROM tag_vocab WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id,
            dimension,
            slug,
        )
        if canonical is None:
            await db.execute(
                "INSERT INTO tag_vocab (org_id, dimension, slug, canonical_slug) "
                "VALUES ($1,$2,$3,$3) ON CONFLICT DO NOTHING",
                org_id,
                dimension,
                slug,
            )
            canonical = slug
        out[slug] = canonical
    return out


async def bump_counts(
    db: Database, org_id: UUID, dimension: str, canonical_slugs: list[str]
) -> None:
    for slug in set(canonical_slugs):
        await db.execute(
            "UPDATE tag_vocab SET count = count + 1 "
            "WHERE org_id=$1 AND dimension=$2 AND slug=$3 AND slug = canonical_slug",
            org_id,
            dimension,
            slug,
        )


async def hints(db: Database, org_id: UUID, dimension: str, n: int) -> list[tuple[str, int]]:
    rows = await db.fetch(
        "SELECT slug, count FROM tag_vocab "
        "WHERE org_id=$1 AND dimension=$2 AND slug = canonical_slug AND count > 0 "
        "ORDER BY count DESC, slug LIMIT $3",
        org_id,
        dimension,
        n,
    )
    return [(r["slug"], r["count"]) for r in rows]
```

Move the two imports to the top of the file with the others (the `noqa` comments above are only to show placement in this plan; the real file has one import block).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tagging/test_vocab.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/cs/tagging/vocab.py tests/tagging/test_vocab.py
git commit -m "feat(tagging): per-org canonical vocabulary for open tags

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Video probe and aspect label

**Files:**
- Modify: `src/cs/tagging/video.py`
- Test: `tests/tagging/test_video.py`

**Interfaces:**
- Produces: `VideoStats(duration_s: float, width: int, height: int, cut_count: int | None)`.
- Produces: `probe_video(video: bytes) -> VideoStats` (raises `RuntimeError("ffprobe failed: ...")` when ffprobe fails; `cut_count` is None when only the cut probe fails).
- Produces: `aspect_label(width: int, height: int) -> str`.
- Produces: `cuts_per_10s(stats: VideoStats) -> float | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tagging/test_video.py`:

```python
from cs.tagging.video import VideoStats, aspect_label, cuts_per_10s, probe_video


@pytest.fixture(scope="module")
def two_scene_video(tmp_path_factory) -> bytes:
    """Two seconds of solid blue then two seconds of solid red: exactly one scene cut."""
    p = tmp_path_factory.mktemp("v") / "two.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=blue:size=320x240:rate=10:duration=2",
            "-f", "lavfi", "-i", "color=c=red:size=320x240:rate=10:duration=2",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p),
        ],
        check=True,
    )
    return p.read_bytes()


def test_probe_video_reports_duration_size_and_cuts(two_scene_video):
    s = probe_video(two_scene_video)
    assert 3.8 <= s.duration_s <= 4.2
    assert (s.width, s.height) == (320, 240)
    assert s.cut_count == 1
    assert 2.4 <= cuts_per_10s(s) <= 2.6  # 1 cut over ~4s


def test_probe_video_single_scene_has_no_cuts(sample_video):
    assert probe_video(sample_video).cut_count == 0


def test_probe_video_rejects_a_non_video():
    with pytest.raises(RuntimeError, match="ffprobe failed"):
        probe_video(b"not a video")


def test_cuts_per_10s_is_none_without_a_cut_count():
    assert cuts_per_10s(VideoStats(4.0, 320, 240, None)) is None


@pytest.mark.parametrize(
    "w, h, label",
    [
        (1080, 1920, "9:16"),
        (1080, 1350, "4:5"),
        (1080, 1080, "1:1"),
        (1920, 1080, "16:9"),
        (1080, 1900, "9:16"),   # 1.6% off 9:16
        (320, 240, "other"),    # 4:3
        (1080, 1440, "other"),  # 3:4, 6.7% off 4:5
    ],
)
def test_aspect_label(w, h, label):
    assert aspect_label(w, h) == label
```

`sample_video` (testsrc) is the existing module fixture; testsrc's moving pattern must not register as a scene cut at threshold 0.3. If `test_probe_video_single_scene_has_no_cuts` fails with a small positive count, change the assertion to `<= 1` and note it in the commit message; do not change the threshold.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tagging/test_video.py -q`
Expected: FAIL with `ImportError: cannot import name 'VideoStats'`.

- [ ] **Step 3: Implement**

Append to `src/cs/tagging/video.py`:

```python
import re
from dataclasses import dataclass

SCENE_THRESHOLD = 0.3
_ASPECTS = {"9:16": 9 / 16, "4:5": 4 / 5, "1:1": 1.0, "16:9": 16 / 9}
_ASPECT_TOLERANCE = 0.03


@dataclass(frozen=True)
class VideoStats:
    duration_s: float
    width: int
    height: int
    cut_count: int | None  # None when the cut probe failed


def probe_video(video: bytes) -> VideoStats:
    """Duration, frame size and scene-cut count from the file itself (spec §5). ffprobe
    failure raises; a failed cut probe leaves cut_count None so the run still completes."""
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(video)
        f.flush()
        out = _run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration", "-of", "json", f.name,
            ]
        )
        info = json.loads(out)
        stream = (info.get("streams") or [{}])[0]
        duration_s = float(info["format"]["duration"])
        try:
            cut_count = _count_scene_cuts(f.name)
        except RuntimeError:
            cut_count = None
    return VideoStats(duration_s, int(stream.get("width", 0)), int(stream.get("height", 0)), cut_count)


def _count_scene_cuts(path: str) -> int:
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", path, "-an",
            "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-",
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode(errors='ignore')[-500:]}")
    # showinfo prints one "[Parsed_showinfo_1 @ ...] n:   0 pts: ..." line per selected frame.
    return len(re.findall(r"Parsed_showinfo\S* @ [^\]]+\] n:\s*\d+", proc.stderr.decode(errors="ignore")))


def cuts_per_10s(stats: VideoStats) -> float | None:
    if stats.cut_count is None or stats.duration_s <= 0:
        return None
    return round(stats.cut_count / stats.duration_s * 10, 1)


def aspect_label(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "other"
    r = width / height
    best = min(_ASPECTS.items(), key=lambda kv: abs(r - kv[1]) / kv[1])
    return best[0] if abs(r - best[1]) / best[1] <= _ASPECT_TOLERANCE else "other"
```

Move `import re` and `from dataclasses import dataclass` into the existing import block at the top of the file.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tagging/test_video.py -q`
Expected: PASS. If `cut_count == 1` fails with `0`, run the ffmpeg command by hand on the fixture file and inspect stderr for `Parsed_showinfo` lines; adjust the regex (not the threshold) until the line is matched.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/cs/tagging/video.py tests/tagging/test_video.py
git commit -m "feat(tagging): probe duration, frame size and scene cuts from the video file

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: v2 prompt, tagger payload, fake tagger

**Files:**
- Modify: `src/cs/tagging/prompts.py`
- Modify: `src/cs/tagging/tagger.py`
- Modify: `src/cs/tagging/fakes.py`
- Test: `tests/tagging/test_prompts.py` (new), `tests/tagging/test_tagger.py`

**Interfaces:**
- Produces: `SYSTEM_V2: str`.
- Produces: `user_text_nested(taxonomy, fmt, copy: dict, hints: dict[str, list[tuple[str, int]]]) -> str`.
- Produces: `Tagger.tag(images, user_text, schema, video=None, system=None) -> TagResult`; `TagResult.payload: dict` holds the parsed JSON object; `TagResult.tags` stays `payload.get("tags", [])`.
- Produces: `fakes.DEFAULT_V2_PAYLOAD: dict`; `FakeTagger(tags_by_call=None, payloads_by_call=None, refuse=False)` returns a nested payload when the schema has a `single` property, else the flat tags; records `system` in each call.

- [ ] **Step 1: Write the failing tests**

Create `tests/tagging/test_prompts.py`:

```python
from cs.tagging.prompts import SYSTEM, SYSTEM_V2, user_text, user_text_nested
from cs.tagging.taxonomy import load_taxonomy


def test_system_v2_states_the_rules():
    for phrase in ("first 5 seconds", "credential", "snake_case", "verbatim", "non-decreasing"):
        assert phrase in SYSTEM_V2
    assert SYSTEM_V2 != SYSTEM


def test_user_text_nested_lists_every_model_dimension_and_hints():
    t = load_taxonomy()
    text = user_text_nested(
        t,
        "video",
        {"headline": "H", "primary_text": "P"},
        {"concept_tags": [("pharmacist_warning", 3), ("benadryl_swap", 1)], "topics": []},
    )
    assert "- headline: H" in text and "- primary_text: P" in text
    assert "- messenger (single, messenger):" in text
    assert "Allowed values: none, creator_on_camera" in text
    assert "- hook_devices (multi, hook):" in text and "Each item carries t_start_s" in text
    assert "- angles (multi, message):" in text
    assert "t_start_s" not in text.split("- angles")[1].split("\n")[0]
    assert "- beats (ordered, structure):" in text
    assert "- concept_tags (open, concept):" in text and "At most 6." in text
    assert "Existing slugs (count): pharmacist_warning (3), benadryl_swap (1)" in text
    assert "- topics (open, concept):" in text
    assert "Existing slugs" not in text.split("- topics")[1].split("- comparison_targets")[0]
    assert "- transcript (text):" in text
    for computed in ("duration_s", "cuts_per_10s", "aspect_ratio"):
        assert f"- {computed}" not in text
    assert text.rstrip().endswith("Return JSON matching the schema exactly.")


def test_legacy_user_text_unchanged_for_images():
    text = user_text(load_taxonomy(), "image", {"headline": "H"})
    assert "- hook_type (hook):" in text and "messenger" not in text
```

Append to `tests/tagging/test_tagger.py` (read the file first; reuse its existing fake OpenAI client fixture if there is one, otherwise the pattern below stands alone):

```python
from cs.tagging.fakes import DEFAULT_V2_PAYLOAD, FakeTagger
from cs.tagging.taxonomy import load_taxonomy


async def test_fake_tagger_returns_nested_payload_for_nested_schema():
    t = load_taxonomy()
    tagger = FakeTagger()
    flat = await tagger.tag([], "u", t.json_schema("image"))
    nested = await tagger.tag([], "u", t.json_schema("video"), video=(b"v", "video/mp4"), system="S")
    assert flat.tags and flat.payload == {"tags": flat.tags}
    assert nested.payload == DEFAULT_V2_PAYLOAD and nested.tags == []
    assert tagger.calls[1]["system"] == "S" and tagger.calls[0]["system"] is None


def test_default_v2_payload_validates_against_the_taxonomy():
    rows = load_taxonomy().validate(DEFAULT_V2_PAYLOAD, "video")
    dims = {r["dimension"] for r in rows}
    assert {"messenger", "hook_devices", "beats", "concept_tags", "transcript"} <= dims
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tagging/test_prompts.py tests/tagging/test_tagger.py -q`
Expected: FAIL with `ImportError: cannot import name 'SYSTEM_V2'` and `cannot import name 'DEFAULT_V2_PAYLOAD'`.

- [ ] **Step 3: Add the v2 prompt**

Append to `src/cs/tagging/prompts.py`:

```python
SYSTEM_V2 = (
    "You are a senior performance-creative analyst. You decompose one paid-social video ad "
    "into a structured profile that will be used to cluster ads into creative-concept "
    "families, so precision and consistency matter more than nuance. You are given the "
    "actual video file: watch every frame and listen to the full audio track. Rules: "
    "(1) Timestamps are seconds from the start, as numbers. "
    "(2) hook_devices are judged on the first 5 seconds only. "
    "(3) messenger = expert_on_camera and proof_devices = expert_credential require a "
    "credential stated on screen or in speech (pharmacist, doctor, RD, PhD); years of "
    "experience or a professional's recommendation without a credential do not count. "
    "(4) creator_off_camera means hands, point-of-view or body without a face. "
    "(5) For product_reveal, a generic pill, gummy or capsule shown in hand counts as the "
    "product. "
    "(6) beats are the ad's sections in order of appearance with non-decreasing t_start_s; "
    "every ad has at least hook; add a beat only where the content changes role. "
    "(7) Open slugs are lowercase snake_case of 1-4 words. concept_tags name the creative "
    "idea the way a strategist would and never contain a brand name. When the prompt lists "
    "existing slugs for a field, reuse one that fits and coin a new one only when none does. "
    "(8) transcript is verbatim speech with no timestamps; an empty string when there is no "
    "speech. hook_verbatim is the exact first on-screen text, then the first spoken "
    "sentence, separated by ' / '. "
    "(9) Every list may be empty except beats and concept_tags. Emit every field of the "
    "schema; cite concrete visual or audio evidence for each value."
)


def user_text_nested(
    taxonomy: Taxonomy, fmt: str, copy: dict, hints: dict[str, list[tuple[str, int]]]
) -> str:
    lines = [f"Creative format: {fmt}", "", "Ad copy fields:"]
    for k in ("headline", "primary_text", "description", "cta_type", "link_url"):
        if copy.get(k):
            lines.append(f"- {k}: {copy[k]}")
    lines += ["", "Profile fields:"]
    for d in taxonomy.model_dimensions(fmt):
        vals = ", ".join(d.values) if d.values else ""
        if d.kind == "single":
            lines.append(f"- {d.name} (single, {d.group}): {d.description} Allowed values: {vals}")
        elif d.kind == "multi":
            ts = " Each item carries t_start_s." if d.timestamped else ""
            lines.append(f"- {d.name} (multi, {d.group}): {d.description} Allowed values: {vals}{ts}")
        elif d.kind == "ordered":
            lines.append(
                f"- {d.name} (ordered, {d.group}): {d.description} Allowed values: {vals} "
                "Each item carries t_start_s."
            )
        elif d.kind == "open":
            cap = f" At most {d.max_values}." if d.max_values else ""
            lines.append(f"- {d.name} (open, {d.group}): {d.description}{cap}")
            existing = hints.get(d.name) or []
            if existing:
                lines.append(
                    "  Existing slugs (count): "
                    + ", ".join(f"{slug} ({n})" for slug, n in existing)
                )
        elif d.kind == "text":
            lines.append(f"- {d.name} (text): {d.description}")
    lines += ["", "Return JSON matching the schema exactly."]
    return "\n".join(lines)
```

- [ ] **Step 4: Thread `system` and `payload` through the tagger**

In `src/cs/tagging/tagger.py`:

```python
@dataclass
class TagResult:
    tags: list[dict] = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    refused: bool = False


class Tagger(Protocol):
    async def tag(
        self,
        images: list[tuple[bytes, str]],
        user_text: str,
        schema: dict,
        video: tuple[bytes, str] | None = None,
        system: str | None = None,
    ) -> TagResult: ...
```

In `GeminiGatewayTagger.tag`, add the `system: str | None = None` parameter, use `{"role": "system", "content": system or SYSTEM}`, and replace the last two lines with:

```python
        text = choice.message.content or ""
        payload = json.loads(text) if text else {}
        result.payload = payload if isinstance(payload, dict) else {}
        result.tags = list(result.payload.get("tags", []))
        return result
```

- [ ] **Step 5: Extend the fake tagger**

Replace `src/cs/tagging/fakes.py` with:

```python
from __future__ import annotations

from cs.tagging.tagger import TagResult

DEFAULT_TAGS = [
    {"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "opens with a question"},
    {"dimension": "message_angle", "value": "pain_relief", "confidence": 0.8, "evidence": "mentions tiredness"},
    {"dimension": "visual_style", "value": "ugc", "confidence": 0.7, "evidence": "handheld look"},
    {"dimension": "hook_summary", "value": "Asks if you are tired of waking up tired", "confidence": 0.9, "evidence": "headline"},
]


def _single(value: str, confidence: float, evidence: str) -> dict:
    return {"value": value, "confidence": confidence, "evidence": evidence}


DEFAULT_V2_PAYLOAD = {
    "single": {
        "messenger": _single("expert_on_camera", 0.9, "white coat; says 'as a pharmacist'"),
        "narration": _single("on_camera_speech", 1.0, "speaks to camera throughout"),
        "captions": _single("text_cards", 0.9, "headline overlays, no subtitles"),
        "opening_shot": _single("face_to_camera", 1.0, "face fills 0-3s"),
        "production": _single("phone_ugc", 0.8, "handheld, home lighting"),
        "setting": _single("home", 0.9, "kitchen behind her"),
        "product_reveal": _single("after_10s", 1.0, "pouch at 0:55"),
        "brand_presence": _single("prominent", 1.0, "logo on pouch"),
        "ending": _single("cta_card", 1.0, "end card with button"),
        "music": _single("none", 1.0, "no music bed"),
    },
    "multi": {
        "hook_devices": [
            {"value": "warning_or_risk", "t_start_s": 0.0, "evidence": "'Worst OTC sleep aids'"},
            {"value": "audience_callout", "t_start_s": 1.0, "evidence": "'if you take benadryl'"},
            {"value": "warning_or_risk", "t_start_s": 2.0, "evidence": "duplicate on purpose"},
        ],
        "proof_devices": [
            {"value": "expert_credential", "t_start_s": 3.0, "evidence": "'as a pharmacist'"},
            {"value": "lab_report_or_certificate", "t_start_s": 73.0, "evidence": "CoA on screen"},
        ],
        "angles": [
            {"value": "risk_avoidance", "evidence": "dementia warning"},
            {"value": "natural_alternative", "evidence": "magnesium instead"},
        ],
        "offer_elements": [{"value": "discount_percent", "t_start_s": 80.0, "evidence": "20% off"}],
        "cta_modes": [
            {"value": "spoken", "t_start_s": 85.0, "evidence": "'tap below'"},
            {"value": "end_card", "t_start_s": 88.0, "evidence": "card"},
        ],
        "registers": [{"value": "fear", "evidence": "warning"}, {"value": "authority", "evidence": "credential"}],
    },
    "ordered": {
        "beats": [
            {"value": "hook", "t_start_s": 0.0},
            {"value": "problem", "t_start_s": 5.0},
            {"value": "proof", "t_start_s": 14.0},
            {"value": "solution_intro", "t_start_s": 47.0},
            {"value": "offer", "t_start_s": 80.0},
            {"value": "cta", "t_start_s": 85.0},
        ]
    },
    "open": {
        "concept_tags": [
            {"slug": "Pharmacist Warning", "evidence": "pharmacist warns against OTC aids"},
            {"slug": "otc_sleep_aid_risks", "evidence": "names diphenhydramine risks"},
        ],
        "topics": [{"slug": "sleep", "evidence": "throughout"}, {"slug": "magnesium", "evidence": "0:47"}],
        "comparison_targets": [{"slug": "benadryl", "evidence": "0:01"}, {"slug": "melatonin", "evidence": "0:20"}],
        "audience_callouts": [{"slug": "benadryl users", "evidence": "0:01"}],
        "claims": [{"slug": "third_party_tested", "evidence": "CoA at 1:13"}],
        "visual_motifs": [{"slug": "pharmacy coat", "evidence": "0:00"}, {"slug": "lab_report_screenshot", "evidence": "1:13"}],
    },
    "text": {
        "hook_verbatim": "Worst OTC Sleep Aids!! / As a pharmacist with almost ten years of experience",
        "concept_summary": "A pharmacist warns against common OTC sleep aids. She recommends magnesium gummies as a tested alternative.",
        "transcript": "As a pharmacist with almost ten years of experience, these are the worst sleep aids.",
    },
}


class FakeTagger:
    def __init__(
        self,
        tags_by_call: list[list[dict]] | None = None,
        payloads_by_call: list[dict] | None = None,
        refuse: bool = False,
    ):
        self.tags_by_call = list(tags_by_call or [])
        self.payloads_by_call = list(payloads_by_call or [])
        self.refuse = refuse
        self.calls: list[dict] = []

    async def tag(self, images, user_text, schema, video=None, system=None) -> TagResult:
        self.calls.append(
            {
                "n_images": len(images),
                "user_text": user_text,
                "schema": schema,
                "has_video": video is not None,
                "system": system,
            }
        )
        if self.refuse:
            return TagResult(model="fake", refused=True)
        nested = "single" in schema.get("properties", {})
        if nested:
            payload = self.payloads_by_call.pop(0) if self.payloads_by_call else DEFAULT_V2_PAYLOAD
            return TagResult(payload=dict(payload), input_tokens=100, output_tokens=50, model="fake")
        tags = self.tags_by_call.pop(0) if self.tags_by_call else DEFAULT_TAGS
        return TagResult(
            tags=list(tags), payload={"tags": list(tags)}, input_tokens=100, output_tokens=50, model="fake"
        )
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/tagging/test_prompts.py tests/tagging/test_tagger.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/cs/tagging/prompts.py src/cs/tagging/tagger.py src/cs/tagging/fakes.py tests/tagging/test_prompts.py tests/tagging/test_tagger.py
git commit -m "feat(tagging): v2 video prompt with vocabulary hints; tagger keeps the parsed payload

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Runner video v2 path

**Files:**
- Modify: `src/cs/tagging/runner.py`
- Test: `tests/tagging/test_runner.py`

**Interfaces:**
- Consumes: `probe_video`, `aspect_label`, `cuts_per_10s` (Task 4); `user_text_nested`, `SYSTEM_V2`, `prompt_sha` (Task 5); `vocab.hints/canonicalize/bump_counts` (Task 3); `taxonomy.validate`, `computed_rows` (Task 2).
- Produces: `profile_video(ctx, creative, taxonomy, tagger, hints) -> tuple[list[dict], TagResult, str, dict]` returning `(rows, result, user_text, schema)` with **no database writes**; open rows are normalized but not canonicalized. The consistency script (Task 9) calls this twice.
- Produces: `tag_creative` unchanged in signature; video creatives take the v2 path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tagging/test_runner.py`:

```python
from cs.tagging import vocab
from cs.tagging.prompts import SYSTEM_V2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_tag_video_creative_writes_the_v2_profile(db, blobs, settings, org_id, tmp_path):
    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)
    tagger = FakeTagger()
    run_id = await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), cid, tagger)
    assert run_id is not None
    call = tagger.calls[0]
    assert call["has_video"] and call["n_images"] == 0 and call["system"] == SYSTEM_V2
    assert "single" in call["schema"]["properties"]

    rows = [dict(r) for r in await repo.latest_tags(db, org_id, cid)]
    by_dim: dict[str, list[dict]] = {}
    for r in rows:
        by_dim.setdefault(r["dimension"], []).append(r)

    # layer 0 computed in code, never from the model
    assert 3.5 <= float(by_dim["duration_s"][0]["value"]) <= 4.5
    assert by_dim["duration_s"][0]["kind"] == "numeric"
    assert by_dim["aspect_ratio"][0]["value"] == "other"  # 320x240 is 4:3
    assert by_dim["aspect_ratio"][0]["evidence"] == "computed"
    assert float(by_dim["cuts_per_10s"][0]["value"]) >= 0.0
    # single / multi / ordered
    assert by_dim["messenger"][0]["value"] == "expert_on_camera"
    assert [r["value"] for r in by_dim["hook_devices"]] == ["warning_or_risk", "audience_callout"]
    assert [(r["value"], r["ordinal"]) for r in by_dim["beats"]][:2] == [("hook", 0), ("problem", 1)]
    # open: normalized, canonicalized, counted
    concept = {r["value"]: r["raw_value"] for r in by_dim["concept_tags"]}
    assert concept == {"pharmacist_warning": "Pharmacist Warning", "otc_sleep_aid_risks": "otc_sleep_aid_risks"}
    assert await vocab.hints(db, org_id, "concept_tags", 10) == [
        ("otc_sleep_aid_risks", 1), ("pharmacist_warning", 1)
    ]
    assert by_dim["visual_motifs"][0]["value"] == "lab_report_screenshot"
    # text
    assert by_dim["transcript"][0]["value"].startswith("As a pharmacist")
    run = await db.fetchrow("SELECT status, model, input_tokens FROM tag_run WHERE id=$1", run_id)
    assert run["status"] == "done" and run["model"] == settings.video_tagging_model
    assert run["input_tokens"] == 100


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_second_video_sees_vocabulary_hints_and_reuses_canonical(
    db, blobs, settings, org_id, tmp_path
):
    first = await _make_video_creative(db, blobs, settings, org_id, tmp_path / "a")
    await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), first, FakeTagger())
    # merge one slug by hand, as an operator would (spec §4)
    await db.execute(
        "UPDATE tag_vocab SET canonical_slug='pharmacist_warning' "
        "WHERE org_id=$1 AND dimension='concept_tags' AND slug='otc_sleep_aid_risks'",
        org_id,
    )
    second = await _make_video_creative(db, blobs, settings, org_id, tmp_path / "b")
    tagger = FakeTagger()
    await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), second, tagger)
    text = tagger.calls[0]["user_text"]
    assert "Existing slugs (count): pharmacist_warning (1)" in text.split("- concept_tags")[1].split("- topics")[0]
    rows = await repo.latest_tags(db, org_id, second)
    concept = [(r["value"], r["raw_value"]) for r in rows if r["dimension"] == "concept_tags"]
    assert concept == [("pharmacist_warning", "Pharmacist Warning")]
    assert await vocab.hints(db, org_id, "concept_tags", 10) == [("pharmacist_warning", 2)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_video_profile_rule_break_fails_the_run(db, blobs, settings, org_id, tmp_path):
    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)
    bad = {**DEFAULT_V2_PAYLOAD, "ordered": {"beats": []}}
    with pytest.raises(ValueError, match="beats"):
        await runner.tag_creative(
            _ctx(db, blobs, settings, org_id, {}), cid, FakeTagger(payloads_by_call=[bad])
        )
    run = await db.fetchrow("SELECT status, error FROM tag_run")
    assert run["status"] == "failed" and "beats" in run["error"]
    assert await db.fetchval("SELECT count(*) FROM tag_vocab") == 0
```

Add `from cs.tagging.fakes import DEFAULT_V2_PAYLOAD, FakeTagger` at the top (replacing the existing FakeTagger import). `_make_video_creative` is called with a sub-path twice in one test; it uses `tmp_path / "v.mp4"` so pass `tmp_path / "a"` and `tmp_path / "b"` and create them: change its first line inside to `tmp_path.mkdir(parents=True, exist_ok=True)` before `p = tmp_path / "v.mp4"`.

The two existing video tests (`test_tag_video_creative_passes_raw_video_to_tagger`, `test_video_tagging_cleans_up_assets_from_before_the_video_model_switch`) keep passing once the v2 path lands: FakeTagger now returns the nested payload for the nested schema.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tagging/test_runner.py -q`
Expected: the three new tests and the two existing video tests FAIL (`ValueError: messenger: missing` or `KeyError: 'duration_s'`); image and carousel tests PASS.

- [ ] **Step 3: Implement the v2 path**

In `src/cs/tagging/runner.py`, update the imports:

```python
from cs.tagging import repo, vocab
from cs.tagging.images import prepare_image
from cs.tagging.prompts import SYSTEM, SYSTEM_V2, prompt_sha, user_text, user_text_nested
from cs.tagging.tagger import TagResult, Tagger
from cs.tagging.taxonomy import Taxonomy, computed_rows, load_taxonomy
from cs.tagging.video import aspect_label, cuts_per_10s, probe_video
```

Add, above `tag_creative`:

```python
COPY_FIELDS = ("headline", "primary_text", "description", "cta_type", "link_url")


async def profile_video(
    ctx: JobContext,
    creative,
    taxonomy: Taxonomy,
    tagger: Tagger,
    hints: dict[str, list[tuple[str, int]]],
) -> tuple[list[dict], TagResult, str, dict]:
    """One model call for a video's v2 profile plus the computed layer. Writes nothing:
    open rows come back normalized but not canonicalized, so the consistency script can
    call this twice and compare. Raises on refusal or a rule break."""
    data = await ctx.blobs.get(creative["primary_asset_path"])
    stats = probe_video(data)
    copy = {k: creative[k] for k in COPY_FIELDS}
    text = user_text_nested(taxonomy, "video", copy, hints)
    schema = taxonomy.json_schema("video")
    result = await tagger.tag([], text, schema, video=(data, "video/mp4"), system=SYSTEM_V2)
    if result.refused:
        raise RuntimeError("model refused to tag creative")
    rows = taxonomy.validate(result.payload, "video")
    rows += computed_rows(
        taxonomy,
        "video",
        {
            "duration_s": round(stats.duration_s, 1),
            "cuts_per_10s": cuts_per_10s(stats),
            "aspect_ratio": aspect_label(stats.width, stats.height),
        },
    )
    return rows, result, text, schema


async def _canonicalize_open_rows(ctx: JobContext, taxonomy: Taxonomy, rows: list[dict]) -> list[dict]:
    """Map open slugs to the org's canonical slugs and drop duplicates that collapse."""
    org_id = ctx.job.org_id
    out: list[dict] = []
    for d in taxonomy.open_dimensions("video"):
        mine = [r for r in rows if r["dimension"] == d.name]
        mapping = await vocab.canonicalize(ctx.db, org_id, d.name, [r["value"] for r in mine])
        seen: set[str] = set()
        for r in mine:
            canonical = mapping[r["value"]]
            if canonical in seen:
                continue
            seen.add(canonical)
            out.append({**r, "value": canonical})
    return [r for r in rows if r["kind"] != "open"] + out


async def _tag_video(ctx: JobContext, creative, taxonomy: Taxonomy, tagger: Tagger, run_id: UUID) -> None:
    org_id = ctx.job.org_id
    await ingest_repo.delete_assets_by_kind(ctx.db, org_id, creative["id"], ("keyframe", "transcript"))
    hints = {
        d.name: await vocab.hints(ctx.db, org_id, d.name, d.vocab_hint)
        for d in taxonomy.open_dimensions("video")
        if d.vocab_hint
    }
    rows, result, text, schema = await profile_video(ctx, creative, taxonomy, tagger, hints)
    rows = await _canonicalize_open_rows(ctx, taxonomy, rows)
    await repo.finish_tag_run(
        ctx.db, org_id, run_id, rows, prompt_sha(SYSTEM_V2, text, schema),
        result.input_tokens, result.output_tokens,
    )
    for d in taxonomy.open_dimensions("video"):
        await vocab.bump_counts(
            ctx.db, org_id, d.name, [r["value"] for r in rows if r["dimension"] == d.name]
        )
```

Then change the body of `tag_creative`'s `try:` block to branch:

```python
    try:
        if taxonomy.is_nested(fmt):
            await _tag_video(ctx, creative, taxonomy, tagger, run_id)
            return run_id
        images, video = await _prepare_inputs(ctx, creative)
        copy = {k: creative[k] for k in COPY_FIELDS}
        text = user_text(taxonomy, fmt, copy)
        schema = taxonomy.json_schema(fmt)
        result = await tagger.tag(images, text, schema, video=video)
        if result.refused:
            raise RuntimeError("model refused to tag creative")
        tags = taxonomy.validate(result.tags, fmt)
        await repo.finish_tag_run(
            ctx.db, org_id, run_id, tags, prompt_sha(SYSTEM, text, schema),
            result.input_tokens, result.output_tokens,
        )
    except Exception as exc:
        await repo.fail_tag_run(ctx.db, org_id, run_id, str(exc))
        raise
    return run_id
```

`is_nested(fmt)` is true only for video, and `_tag_video` does its own keyframe/transcript cleanup, so `_prepare_inputs` keeps serving the legacy path unchanged.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tagging -q`
Expected: PASS, including the two pre-existing video tests.

- [ ] **Step 5: Run the wider suite**

Run: `uv run pytest -q`
Expected: PASS. If `tests/e2e` or `tests/insights` reference `FakeTagger` or tag shapes, they still pass because the flat path is unchanged.

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/cs/tagging/runner.py tests/tagging/test_runner.py
git commit -m "feat(tagging): video creatives get the v2 profile with computed layer and canonical open tags

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: API surface

**Files:**
- Modify: `src/cs/api/routers/tags.py`
- Test: `tests/api/test_tags.py`

**Interfaces:**
- Produces: `GET /tags/dimensions` items gain `"kind"`.
- Produces: `GET /creatives/{id}/tags` excludes rows whose dimension is `transcript`.
- Produces: `GET /creatives/{id}/transcript -> {"transcript": str | null}`.
- Produces: `GET /tags/dimensions/{name}/values -> [{"value": str, "count": int}]`, 404 when the dimension is not an open dimension.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_tags.py`:

```python
async def test_v2_tag_endpoints(client, db, settings):
    from uuid import UUID

    from cs.tagging import vocab

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD")
    cid, _ = await ingest_repo.upsert_creative(
        db, org_id, acct,
        PlatformCreative("v", "video", "H", "P", None, None, "u", None, None, {}),
        "raw", format_source="manual_review",
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    await repo.finish_tag_run(
        db, org_id, rid,
        [
            {"dimension": "messenger", "kind": "single", "value": "none", "confidence": 1.0, "evidence": ""},
            {"dimension": "transcript", "kind": "text", "value": "spoken words", "confidence": 1.0, "evidence": ""},
            {"dimension": "concept_tags", "kind": "open", "value": "benadryl_swap", "raw_value": "Benadryl swap", "confidence": 1.0, "evidence": ""},
        ],
        "s", 1, 1,
    )
    await vocab.canonicalize(db, org_id, "concept_tags", ["benadryl_swap", "pharmacist_warning"])
    await vocab.bump_counts(db, org_id, "concept_tags", ["benadryl_swap", "pharmacist_warning"])
    await vocab.bump_counts(db, org_id, "concept_tags", ["benadryl_swap"])

    r = await client.get(f"/creatives/{cid}/tags", headers=H)
    assert [t["dimension"] for t in r.json()] == ["concept_tags", "messenger"]
    assert r.json()[0]["kind"] == "open" and r.json()[0]["raw_value"] == "Benadryl swap"

    r = await client.get(f"/creatives/{cid}/transcript", headers=H)
    assert r.json() == {"transcript": "spoken words"}

    r = await client.get("/tags/dimensions", headers=H)
    kinds = {d["name"]: d["kind"] for d in r.json()}
    assert kinds["messenger"] == "single" and kinds["concept_tags"] == "open" and kinds["hook_type"] == "single"

    r = await client.get("/tags/dimensions/concept_tags/values", headers=H)
    assert r.json() == [{"value": "benadryl_swap", "count": 2}, {"value": "pharmacist_warning", "count": 1}]
    assert (await client.get("/tags/dimensions/messenger/values", headers=H)).status_code == 404
    assert (await client.get("/tags/dimensions/nope/values", headers=H)).status_code == 404


async def test_transcript_is_null_when_untagged(client, db, settings):
    cid = await _seed(client, db, settings)
    r = await client.get(f"/creatives/{cid}/transcript", headers=H)
    assert r.json() == {"transcript": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_tags.py -q`
Expected: FAIL. The tags list still includes `transcript`, and the two new routes return 404 or 405.

- [ ] **Step 3: Implement**

In `src/cs/api/routers/tags.py`:

Change `creative_tags` to:

```python
    return [
        {**dict(r), "confidence": round(float(r["confidence"]), 4)}
        for r in await repo.latest_tags(db, auth.org_id, creative_id)
        if r["dimension"] != "transcript"
    ]
```

Add after it:

```python
@router.get("/creatives/{creative_id}/transcript")
async def creative_transcript(
    creative_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    if not await ingest_repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    return {"transcript": await repo.latest_tag_value(db, auth.org_id, creative_id, "transcript")}
```

In `dimensions`, add `"kind": d.kind,` to each item. Then add:

```python
@router.get("/tags/dimensions/{name}/values")
async def dimension_values(
    name: str, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    dim = next((d for d in load_taxonomy().dimensions if d.name == name), None)
    if dim is None or dim.kind != "open":
        raise HTTPException(404, "not an open dimension")
    return [
        {"value": slug, "count": n} for slug, n in await vocab.hints(db, auth.org_id, name, 200)
    ]
```

with `from cs.tagging import repo, vocab` replacing the existing `from cs.tagging import repo`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/cs/api/routers/tags.py tests/api/test_tags.py
git commit -m "feat(api): dimension kinds, transcript endpoint and open-vocabulary values

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Web tag list and filters

**Files:**
- Modify: `web/src/lib/types.ts`, `web/src/lib/api.ts`
- Modify: `web/src/components/tag-list.tsx`
- Modify: `web/src/components/creative-filters.tsx`, `web/src/app/(app)/creatives/page.tsx`
- Test: `web/tests/tag-list.test.tsx`

**Interfaces:**
- Consumes: the API shapes from Task 7.
- Produces: `TagList({ tags, dimensions })` renders per kind; `CreativeFilters` gains an optional `openValues?: { value: string; count: number }[]` prop used when the selected dimension is open.

- [ ] **Step 1: Write the failing component test**

Create `web/tests/tag-list.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TagList } from '@/components/tag-list';
import type { Dimension, Tag } from '@/lib/types';

const dim = (name: string, group: string, kind: Dimension['kind']): Dimension => ({
  name, group, kind, applies_to: ['video'], values: null, description: '',
});
const tag = (partial: Partial<Tag> & Pick<Tag, 'dimension' | 'kind' | 'value'>): Tag => ({
  confidence: 1, evidence: '', raw_value: null, t_start_s: null, ordinal: null, ...partial,
});

describe('TagList', () => {
  it('renders each kind in its own shape', () => {
    render(
      <TagList
        dimensions={[
          dim('messenger', 'messenger', 'single'),
          dim('duration_s', 'form', 'numeric'),
          dim('hook_devices', 'hook', 'multi'),
          dim('beats', 'structure', 'ordered'),
          dim('concept_tags', 'concept', 'open'),
          dim('hook_verbatim', 'text', 'text'),
        ]}
        tags={[
          tag({ dimension: 'messenger', kind: 'single', value: 'expert_on_camera', confidence: 0.9 }),
          tag({ dimension: 'duration_s', kind: 'numeric', value: '93.2' }),
          tag({ dimension: 'hook_devices', kind: 'multi', value: 'warning_or_risk', t_start_s: 0 }),
          tag({ dimension: 'hook_devices', kind: 'multi', value: 'audience_callout', t_start_s: 1.5 }),
          tag({ dimension: 'beats', kind: 'ordered', value: 'hook', ordinal: 0, t_start_s: 0 }),
          tag({ dimension: 'beats', kind: 'ordered', value: 'problem', ordinal: 1, t_start_s: 5 }),
          tag({ dimension: 'concept_tags', kind: 'open', value: 'pharmacist_warning', raw_value: 'Pharmacist Warning' }),
          tag({ dimension: 'hook_verbatim', kind: 'text', value: 'Worst OTC Sleep Aids!!' }),
        ]}
      />,
    );
    expect(screen.getByText('90%')).toBeInTheDocument();
    expect(screen.getByText('93.2')).toBeInTheDocument();
    expect(screen.getByText('warning_or_risk')).toBeInTheDocument();
    expect(screen.getByText('@1.5s')).toBeInTheDocument();
    expect(screen.getByText('hook 0s → problem 5s')).toBeInTheDocument();
    const chip = screen.getByText('pharmacist_warning');
    expect(chip).toBeInTheDocument();
    expect(chip.closest('li')?.textContent).not.toMatch(/%/);
    expect(screen.getByText('Worst OTC Sleep Aids!!')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npm test -- tests/tag-list.test.tsx`
Expected: FAIL (type errors on `kind`, and missing `@1.5s` / arc text).

- [ ] **Step 3: Update types and API client**

In `web/src/lib/types.ts` replace the `Tag` and `Dimension` lines with:

```ts
export type TagKind = 'numeric' | 'single' | 'multi' | 'ordered' | 'open' | 'text';
export type Tag = { dimension: string; kind: TagKind; value: string; raw_value: string | null; confidence: number; evidence: string; t_start_s: number | null; ordinal: number | null };
export type Dimension = { name: string; group: string; kind: TagKind; applies_to: string[]; values: string[] | null; description: string };
export type DimensionValue = { value: string; count: number };
```

In `web/src/lib/api.ts` add after the `dimensions` line:

```ts
  dimensionValues: (name: string) => apiFetch<import('./types').DimensionValue[]>(`/tags/dimensions/${encodeURIComponent(name)}/values`),
  creativeTranscript: (id: string) => apiFetch<{ transcript: string | null }>(`/creatives/${id}/transcript`),
```

- [ ] **Step 4: Rewrite `tag-list.tsx`**

```tsx
import { Badge } from '@/components/ui/badge';
import type { Dimension, Tag } from '@/lib/types';

const secs = (t: number | null) => (t === null ? '' : `${Number.isInteger(t) ? t : t.toFixed(1)}s`);

function DimensionRow({ name, kind, tags }: { name: string; kind: Tag['kind']; tags: Tag[] }) {
  if (kind === 'ordered') {
    const arc = [...tags].sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0)).map((t) => `${t.value} ${secs(t.t_start_s)}`).join(' → ');
    return (
      <li className="w-full text-sm">
        <span className="text-muted-foreground">{name}</span> <span>{arc}</span>
      </li>
    );
  }
  if (kind === 'text') {
    return (
      <li className="w-full text-sm">
        <span className="text-muted-foreground">{name}</span>
        <p className="mt-0.5 whitespace-pre-wrap">{tags[0]?.value}</p>
      </li>
    );
  }
  return (
    <>
      {tags.map((t, i) => (
        <li key={`${t.dimension}-${t.value}-${i}`}>
          <Badge variant="outline" title={t.evidence || t.raw_value || undefined} className="gap-1.5 font-normal">
            <span className="text-muted-foreground">{name}</span>
            <span>{t.value}</span>
            {kind === 'multi' && t.t_start_s !== null && <span className="tabular-nums text-muted-foreground">@{secs(t.t_start_s)}</span>}
            {kind === 'single' && <span className="tabular-nums text-muted-foreground">{Math.round(t.confidence * 100)}%</span>}
          </Badge>
        </li>
      ))}
    </>
  );
}

export function TagList({ tags, dimensions }: { tags: Tag[]; dimensions: Dimension[] }) {
  const dimOf = Object.fromEntries(dimensions.map((d) => [d.name, d]));
  const groups = new Map<string, Map<string, Tag[]>>();
  for (const t of tags) {
    const g = dimOf[t.dimension]?.group ?? 'other';
    const byDim = groups.get(g) ?? new Map<string, Tag[]>();
    byDim.set(t.dimension, [...(byDim.get(t.dimension) ?? []), t]);
    groups.set(g, byDim);
  }
  return (
    <div className="space-y-4">
      {[...groups.entries()].map(([group, byDim]) => (
        <div key={group}>
          <h4 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">{group}</h4>
          <ul className="flex flex-wrap gap-1.5">
            {[...byDim.entries()].map(([name, items]) => (
              <DimensionRow key={name} name={name} kind={items[0].kind ?? dimOf[name]?.kind ?? 'single'} tags={items} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Run the component test**

Run: `cd web && npm test -- tests/tag-list.test.tsx`
Expected: PASS.

- [ ] **Step 6: Open-dimension values in the filter**

In `web/src/components/creative-filters.tsx`:
- Add `openValues?: { value: string; count: number }[]` to the props and destructure it.
- Change `const filterableDimensions = dimensions.filter((d) => d.values);` to `const filterableDimensions = dimensions.filter((d) => d.values || d.kind === 'open');`.
- Change the `dimensionValues` line to:

```ts
  const selected = filterableDimensions.find((d) => d.name === selectedDimension);
  const dimensionValues = selected?.kind === 'open' ? (openValues ?? []).map((v) => v.value) : (selected?.values ?? []);
```

In `web/src/app/(app)/creatives/page.tsx`, inside the page function after `dimensions` is fetched, add:

```ts
  const selectedDim = dimensions.find((d) => d.name === sp.dimension);
  const openValues = selectedDim?.kind === 'open' ? await api.dimensionValues(selectedDim.name) : undefined;
```

and pass `openValues={openValues}` to `<CreativeFilters …/>`. Read the file first: `sp` is the already-parsed search params object used on lines 22 and 32; put the two lines after the `Promise.all` that yields `dimensions`.

- [ ] **Step 7: Type-check and run all web tests**

Run: `cd web && npx tsc --noEmit && npm test`
Expected: both clean. If `tsc` reports other files reading `Tag` without `kind`, add the field where the object is constructed; do not widen the type.

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts web/src/components/tag-list.tsx web/src/components/creative-filters.tsx "web/src/app/(app)/creatives/page.tsx" web/tests/tag-list.test.tsx
git commit -m "feat(web): render v2 tag kinds and filter by open vocabulary

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Consistency scoring and script

**Files:**
- Create: `src/cs/tagging/consistency.py`
- Create: `scripts/tag_consistency.py`
- Test: `tests/tagging/test_consistency.py`

**Interfaces:**
- Consumes: `runner.profile_video` (Task 6).
- Produces: `agreement(rows_a: list[dict], rows_b: list[dict], taxonomy, fmt) -> dict[str, float | None]` per dimension: single/numeric equality (1.0 or 0.0), multi/open Jaccard, ordered exact-sequence (1.0 or 0.0), text None.
- Produces: `summarize(per_creative: list[dict[str, float | None]]) -> dict[str, float | None]` mean per dimension ignoring None.

- [ ] **Step 1: Write the failing tests**

Create `tests/tagging/test_consistency.py`:

```python
from cs.tagging.consistency import agreement, summarize
from cs.tagging.taxonomy import load_taxonomy


def _r(dimension, kind, value, ordinal=None):
    return {"dimension": dimension, "kind": kind, "value": value, "raw_value": None,
            "confidence": 1.0, "evidence": "", "t_start_s": None, "ordinal": ordinal}


def test_agreement_per_kind():
    t = load_taxonomy()
    a = [
        _r("messenger", "single", "expert_on_camera"),
        _r("duration_s", "numeric", "93.2"),
        _r("hook_devices", "multi", "warning_or_risk"),
        _r("hook_devices", "multi", "audience_callout"),
        _r("beats", "ordered", "hook", 0), _r("beats", "ordered", "problem", 1),
        _r("concept_tags", "open", "pharmacist_warning"),
        _r("transcript", "text", "words"),
    ]
    b = [
        _r("messenger", "single", "creator_on_camera"),
        _r("duration_s", "numeric", "93.2"),
        _r("hook_devices", "multi", "warning_or_risk"),
        _r("beats", "ordered", "hook", 0), _r("beats", "ordered", "problem", 1),
        _r("concept_tags", "open", "pharmacist_warning"),
        _r("concept_tags", "open", "benadryl_swap"),
        _r("transcript", "text", "other words"),
    ]
    got = agreement(a, b, t, "video")
    assert got["messenger"] == 0.0 and got["duration_s"] == 1.0
    assert got["hook_devices"] == 0.5
    assert got["beats"] == 1.0
    assert got["concept_tags"] == 0.5
    assert got["transcript"] is None
    assert got["angles"] == 1.0  # both empty


def test_ordered_disagrees_on_order_and_length():
    t = load_taxonomy()
    a = [_r("beats", "ordered", "hook", 0), _r("beats", "ordered", "problem", 1)]
    b = [_r("beats", "ordered", "problem", 0), _r("beats", "ordered", "hook", 1)]
    assert agreement(a, b, t, "video")["beats"] == 0.0
    assert agreement(a, a[:1], t, "video")["beats"] == 0.0


def test_summarize_means_and_ignores_none():
    out = summarize([{"messenger": 1.0, "transcript": None}, {"messenger": 0.0, "transcript": None}])
    assert out == {"messenger": 0.5, "transcript": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tagging/test_consistency.py -q`
Expected: FAIL with `ModuleNotFoundError: cs.tagging.consistency`.

- [ ] **Step 3: Implement the scoring**

Create `src/cs/tagging/consistency.py`:

```python
"""Agreement between two v2 profiles of the same creative (spec §9). Text is not scored."""

from __future__ import annotations

from cs.tagging.taxonomy import Taxonomy


def _values(rows: list[dict], dimension: str) -> list[dict]:
    return [r for r in rows if r["dimension"] == dimension]


def agreement(rows_a: list[dict], rows_b: list[dict], taxonomy: Taxonomy, fmt: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for d in taxonomy.for_format(fmt):
        a, b = _values(rows_a, d.name), _values(rows_b, d.name)
        if d.kind in ("single", "numeric"):
            va = a[0]["value"] if a else None
            vb = b[0]["value"] if b else None
            out[d.name] = 1.0 if va == vb else 0.0
        elif d.kind in ("multi", "open"):
            sa, sb = {r["value"] for r in a}, {r["value"] for r in b}
            out[d.name] = 1.0 if not sa and not sb else len(sa & sb) / len(sa | sb)
        elif d.kind == "ordered":
            seq_a = [r["value"] for r in sorted(a, key=lambda r: r["ordinal"] or 0)]
            seq_b = [r["value"] for r in sorted(b, key=lambda r: r["ordinal"] or 0)]
            out[d.name] = 1.0 if seq_a == seq_b else 0.0
        else:
            out[d.name] = None
    return out


def summarize(per_creative: list[dict[str, float | None]]) -> dict[str, float | None]:
    names: list[str] = list(dict.fromkeys(k for p in per_creative for k in p))
    out: dict[str, float | None] = {}
    for name in names:
        scores = [p[name] for p in per_creative if p.get(name) is not None]
        out[name] = round(sum(scores) / len(scores), 3) if scores else None
    return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tagging/test_consistency.py -q`
Expected: PASS.

- [ ] **Step 5: Write the script**

Create `scripts/tag_consistency.py`:

```python
"""Tag a sample of video creatives twice, in memory, and print per-dimension agreement.
Writes nothing: no tag_run, no tag rows, no vocabulary changes.

Usage: uv run python scripts/tag_consistency.py --org <uuid> [--n 12] [--seed 1]
Needs AI_GATEWAY_API_KEY and DATABASE_URL in the environment (or .env).
"""

import argparse
import asyncio
import random
from uuid import UUID

from cs.core import repo as ingest_repo
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.queue import Job
from cs.jobs.registry import JobContext
from cs.tagging import vocab
from cs.tagging.consistency import agreement, summarize
from cs.tagging.runner import profile_video
from cs.tagging.tagger import GeminiGatewayTagger
from cs.tagging.taxonomy import load_taxonomy


async def main(org_id: UUID, n: int, seed: int) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        ctx = JobContext(
            db=db, blobs=blob_store_from_settings(settings), settings=settings,
            job=Job(UUID(int=0), org_id, "tagging.consistency", {}, 1, 1),
        )
        rows = await db.fetch(
            "SELECT c.id FROM creative c WHERE c.org_id=$1 AND c.format='video' "
            "AND c.primary_asset_id IS NOT NULL ORDER BY c.id",
            org_id,
        )
        ids = [r["id"] for r in rows]
        random.Random(seed).shuffle(ids)
        taxonomy = load_taxonomy()
        tagger = GeminiGatewayTagger(settings)
        hints = {
            d.name: await vocab.hints(db, org_id, d.name, d.vocab_hint)
            for d in taxonomy.open_dimensions("video") if d.vocab_hint
        }
        scores = []
        for cid in ids[:n]:
            creative = await ingest_repo.get_creative(db, org_id, cid)
            a, *_ = await profile_video(ctx, creative, taxonomy, tagger, hints)
            b, *_ = await profile_video(ctx, creative, taxonomy, tagger, hints)
            s = agreement(a, b, taxonomy, "video")
            scores.append(s)
            low = [k for k, v in s.items() if v is not None and v < 1.0]
            print(f"{cid}: disagreements on {', '.join(low) or 'none'}")
        print()
        print(f"{'dimension':<22} {'kind':<8} agreement  (n={len(scores)})")
        for d in taxonomy.for_format("video"):
            v = summarize(scores).get(d.name)
            flag = "" if v is None else ("  <-- below bar" if (d.kind in ("single", "numeric", "ordered") and v < 0.8) or (d.kind in ("multi", "open") and v < 0.6) else "")
            print(f"{d.name:<22} {d.kind:<8} {'-' if v is None else f'{v:.2f}'}{flag}")
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True, type=UUID)
    p.add_argument("--n", type=int, default=12)
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()
    asyncio.run(main(args.org, args.n, args.seed))
```

Check `JobContext` and `Job` constructor signatures against `src/cs/jobs/registry.py` and `src/cs/core/queue.py` before running; `tests/tagging/test_runner.py::_ctx` shows the working call `Job(UUID(int=1), org_id, "tagging.tag_creative", payload, 1, 5)`.

- [ ] **Step 6: Smoke the script parses**

Run: `uv run python scripts/tag_consistency.py --help`
Expected: the usage text prints.

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/cs/tagging/consistency.py scripts/tag_consistency.py tests/tagging/test_consistency.py
git commit -m "feat(tagging): consistency scoring and a twice-tag script for the v2 profile

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Rollout on org 3

**Files:** none new. Operational; every step records its output.

**Interfaces:**
- Consumes: `scripts/enqueue_creative_insights.py` pattern (already in the repo, uncommitted) for enqueueing; add `scripts/enqueue_tagging.py` here.

- [ ] **Step 1: Commit the enqueue helper for tagging**

Create `scripts/enqueue_tagging.py`:

```python
"""Enqueue tagging.tag_creative for specific creatives (video included, regardless of
VIDEO_TAGGING_ENABLED, which only gates enqueueing at asset download).

Usage: uv run python scripts/enqueue_tagging.py <creative_id> [<creative_id> ...]
"""

import asyncio
import sys
from uuid import UUID

from cs.core import queue
from cs.core.config import get_settings
from cs.core.db import Database

TAGGABLE = ("image", "video", "carousel")


async def main(ids: list[UUID]) -> None:
    db = await Database.connect(get_settings().database_url)
    try:
        for cid in ids:
            row = await db.fetchrow("SELECT org_id, format FROM creative WHERE id=$1", cid)
            if row is None or row["format"] not in TAGGABLE:
                print(f"{cid}: skipped ({'missing' if row is None else row['format']})")
                continue
            jid = await queue.enqueue(
                db, row["org_id"], "tagging.tag_creative", {"creative_id": str(cid)},
                dedup_key=f"tag:{cid}",
            )
            print(f"{cid} ({row['format']}): {'queued ' + str(jid) if jid else 'already queued'}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main([UUID(a) for a in sys.argv[1:]]))
```

```bash
git add scripts/enqueue_tagging.py scripts/enqueue_creative_insights.py
git commit -m "chore(scripts): enqueue tagging or insight jobs for specific creatives

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 2: Full local test run and lint**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -q && (cd web && npx tsc --noEmit && npm test)
```
Expected: all green. Fix anything red before proceeding.

- [ ] **Step 3: Apply the migration to the primary database**

```bash
cd "/Users/anantchandra/Desktop/Custom LP/creative-strategist" && export $(grep -E '^DATABASE_URL=' .env | xargs) && dbmate --no-dump-schema --migrations-dir db/migrations up
```
Expected: `Applying: 20260916000003_tag_v2.sql`. Verify: `psql "$DATABASE_URL" -X -c "\d tag" | grep -E "kind|t_start_s|ordinal|raw_value"` shows the four columns.

- [ ] **Step 4: Push and deploy**

```bash
git push origin main
ssh -o BatchMode=yes -i key-pair.pem ec2-user@34.223.183.183 'sudo bash /opt/creative-strategist/deploy/ec2/setup.sh 2>&1 | tail -5; systemctl is-active cs-worker cs-api'
```
Expected: both services `active`. The deploy script re-runs migrations (already applied, no-op) and restarts the worker, which picks up the new taxonomy file.

- [ ] **Step 5: Re-tag the four insight videos under v2**

```bash
uv run python scripts/enqueue_tagging.py 01a0986e-916a-7000-9b2c-93275d41e2d1 01a0986e-75dc-7000-b851-38b9e1ac1e5b 01a0986e-84f9-7000-b8dd-08bf4997b8fa 01a0986e-99ac-7000-8f92-47756166ae17
```
Then wait for the four jobs to leave pending/running (poll `SELECT status, error FROM job WHERE job_type='tagging.tag_creative' ORDER BY created_at DESC LIMIT 4`), and print the profiles:

```bash
export $(grep -E '^DATABASE_URL=' .env | xargs) && psql "$DATABASE_URL" -X -c "
SELECT c.platform_creative_id AS meta, t.dimension, t.kind, t.value, t.t_start_s, t.ordinal
FROM tag t JOIN tag_run tr ON tr.id=t.tag_run_id AND tr.status='done'
JOIN taxonomy_version tv ON tv.id=tr.taxonomy_version_id AND tv.version='2.0'
JOIN creative c ON c.id=t.creative_id
WHERE t.dimension <> 'transcript'
ORDER BY 1, t.dimension, t.ordinal, t.t_start_s;"
```
Expected: four `done` runs with a `2.0` taxonomy version; every video has rows for `duration_s`, `cuts_per_10s`, `aspect_ratio` (`9:16`), the ten single dimensions, non-empty `beats` and `concept_tags`, and `hook_verbatim`. Record the concept tags for all four in the final report. A `failed` run's `error` names the rule that broke; fix the prompt rule or the validator, redeploy, and re-enqueue (the `UNIQUE` on `tag_run` lets a failed run be retried).

- [ ] **Step 6: Consistency check on twelve org 3 videos**

```bash
export $(grep -E '^(DATABASE_URL|AI_GATEWAY_API_KEY|AI_GATEWAY_BASE_URL|BLOB_BACKEND|S3_.*|AWS_.*)=' .env | xargs) && uv run python scripts/tag_consistency.py --org 01a089c9-231e-7000-bdaa-39277b4cc94b --n 12 --seed 1 2>&1 | tee "/private/tmp/claude-501/-Users-anantchandra-Desktop-Custom-LP-creative-strategist/7c6cd8e2-5ea1-4261-98cc-67d38e4001a2/scratchpad/consistency-run1.txt"
```
Check `.env` for the exact blob-store variable names first (`grep -E 'BLOB|S3|AWS' .env`) and export those. Expected: a table of 32 dimensions. Any `<-- below bar` line is a dimension to repair: tighten its rule in `SYSTEM_V2` or its description in `taxonomy.yaml`, add a unit test for the rule if it is code, re-run this step, and record both runs' numbers in the commit message. Do not drop a dimension without telling the user which one and why.

- [ ] **Step 7: Report**

Report to the user: the four videos' concept tags and arcs, the consistency table, which dimensions (if any) fell below the bar and what was changed, and the exact command to drain the remaining 569 org 3 videos:

```bash
uv run python -c "
import asyncio
from cs.core.config import get_settings
from cs.core.db import Database
from cs.tagging.reconcile import reenqueue_untagged
async def m():
    db = await Database.connect(get_settings().database_url)
    try: print(await reenqueue_untagged(db))
    finally: await db.close()
asyncio.run(m())"
```

Note in the report that `reenqueue_untagged` also picks up the 396 org 2 `unresolved`-turned-image creatives only if their format is now resolved, and that it does not touch `VIDEO_TAGGING_ENABLED`. Do not run the drain without the user's go.
