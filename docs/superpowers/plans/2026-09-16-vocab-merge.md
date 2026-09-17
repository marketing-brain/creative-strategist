# Open-Vocabulary Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge near-synonym open-vocabulary slugs per org and dimension using embeddings for candidate recall and a text-only model for the decision, with a review band, a reversible log, and a dry-run script; first run over the four org 3 videos' vocabulary.

**Architecture:** A gateway embedding client (`cs.core.embeddings`) embeds `"{dimension label}: {slug with spaces}"` at 1536 dimensions into `tag_vocab_embedding`. `cs.tagging.vocab_merge` finds candidate pairs by cosine in Python, asks a flash-lite judge per dimension, builds a union-find merge plan (apply / review / rejected), and applies it transactionally: `tag_vocab.canonical_slug`, `tag.value` for open rows (never `raw_value`), re-aggregated counts, and one `tag_vocab_merge` log row per member. Two scripts drive it by hand.

**Tech Stack:** Python 3.12, asyncpg, pgvector 0.8 (vector literal as text, parsed back with `json.loads`), OpenAI-compatible client against Vercel AI Gateway (`/v1/embeddings`, `/v1/chat/completions`), dbmate, pytest (asyncio auto).

**Spec:** `docs/superpowers/specs/2026-09-16-vocab-merge-design.md`

## Global Constraints

- Embedding model `google/gemini-embedding-2`, `dimensions=1536`, through `settings.ai_gateway_base_url + "/v1"` with the same API key the tagger uses; batches of 100 inputs per call.
- Embedded text is exactly `f"{dimension_label}: {slug.replace('_', ' ')}"` where `dimension_label` is the dimension name with underscores replaced by spaces (e.g. `concept tags: benadryl swap`).
- Candidate floor `0.70`; apply floor `0.80`; both are script arguments with those defaults.
- Merges are per `(org_id, dimension)`; never across dimensions or orgs.
- Canonical within a group: highest `tag_vocab.count`, ties by shortest slug, then alphabetical.
- `apply_merges` runs in one transaction per dimension: update every member's `canonical_slug`; `UPDATE tag SET value = canonical WHERE org_id AND dimension AND kind='open' AND value IN (members)`; set the canonical's `count` to the sum of the group's counts and members' `count` to 0; insert `tag_vocab_merge` rows with `status='applied'`. `raw_value` is never modified.
- A pair judged `same_concept=false` is logged as `status='rejected'` and excluded from future candidate lists (in either slug order).
- Nothing in this plan changes `taxonomy.yaml`, the tagging prompt, or any tag_run.
- Tests run against the shared Supabase test database `cs_test_diagnosis`, never `cs_test`. Prefix every pytest run with:

  ```bash
  cd "/Users/anantchandra/Desktop/Custom LP/creative-strategist" && export DATABASE_URL_TEST="$(grep '^DATABASE_URL_TEST=' .env | cut -d= -f2- | sed 's#/cs_test?#/cs_test_diagnosis?#')"
  ```

- Lint before every commit: `uv run ruff check . && uv run ruff format .`
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- No real model calls from tests; the scripts are only smoke-tested with `--help` until Task 6.

## File map

| File | Responsibility |
|---|---|
| `db/migrations/20260916000004_vocab_merge.sql` | `tag_vocab_embedding`, `tag_vocab_merge` |
| `src/cs/core/config.py` | `embedding_model`, `embedding_dimensions` settings |
| `src/cs/core/embeddings.py` | `Embedder` protocol, `GatewayEmbedder`, `FakeEmbedder`, `cosine` |
| `src/cs/tagging/vocab_merge.py` | embed_missing, candidate_pairs, judge prompt + `GatewayJudge`/`FakeJudge`, plan_merges, apply_merges, revert, list_review, decide_review |
| `scripts/merge_vocab.py` | dry run / apply |
| `scripts/review_vocab_merges.py` | list / approve / reject |

---

### Task 1: Migration, settings, embedding client

**Files:**
- Create: `db/migrations/20260916000004_vocab_merge.sql`
- Modify: `src/cs/core/config.py` (after `insights_model`)
- Create: `src/cs/core/embeddings.py`
- Test: `tests/core/test_embeddings.py`

**Interfaces:**
- Produces: `Settings.embedding_model: str = "google/gemini-embedding-2"`, `Settings.embedding_dimensions: int = 1536`.
- Produces: `class Embedder(Protocol): async def embed(self, texts: list[str]) -> list[list[float]]`.
- Produces: `GatewayEmbedder(settings, client=None)` batching 100 texts per request; `FakeEmbedder(seed=0)` deterministic; `cosine(a: list[float], b: list[float]) -> float`.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_embeddings.py`:

```python
from types import SimpleNamespace

import pytest

from cs.core.embeddings import FakeEmbedder, GatewayEmbedder, cosine


class _FakeEmbeddings:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(kwargs["input"])
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[float(i), 1.0]) for i in range(n)],
            usage=SimpleNamespace(prompt_tokens=n * 5, total_tokens=n * 5),
        )


async def test_gateway_embedder_batches_and_passes_dimensions(settings):
    fake = _FakeEmbeddings()
    emb = GatewayEmbedder(settings, client=SimpleNamespace(embeddings=fake))
    vectors = await emb.embed([f"t{i}" for i in range(250)])
    assert len(vectors) == 250 and vectors[0] == [0.0, 1.0] and vectors[249] == [49.0, 1.0]
    assert [len(c["input"]) for c in fake.calls] == [100, 100, 50]
    assert all(c["model"] == settings.embedding_model for c in fake.calls)
    assert all(c["dimensions"] == settings.embedding_dimensions for c in fake.calls)


async def test_gateway_embedder_empty_input_makes_no_call(settings):
    fake = _FakeEmbeddings()
    emb = GatewayEmbedder(settings, client=SimpleNamespace(embeddings=fake))
    assert await emb.embed([]) == [] and fake.calls == []


async def test_fake_embedder_is_deterministic_and_similar_for_similar_text():
    a = await FakeEmbedder().embed(["concept tags: benadryl swap"])
    b = await FakeEmbedder().embed(["concept tags: benadryl swap"])
    assert a == b and len(a[0]) == 64
    same, other = await FakeEmbedder().embed(["x: glycinate", "x: melatonin"])
    assert cosine(a[0], a[0]) == pytest.approx(1.0)
    assert cosine(same, other) < 0.999


def test_cosine():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 0]) == 0.0


def test_settings_defaults(settings):
    assert settings.embedding_model == "google/gemini-embedding-2"
    assert settings.embedding_dimensions == 1536
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_embeddings.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cs.core.embeddings'`.

- [ ] **Step 3: Migration**

Create `db/migrations/20260916000004_vocab_merge.sql`:

```sql
-- migrate:up
-- Open-vocabulary merge (spec 2026-09-16-vocab-merge-design.md).
-- One embedding per canonical slug per model; vectors are compared in Python, so no
-- HNSW index until a dimension passes ~5,000 slugs.
CREATE TABLE tag_vocab_embedding (
  org_id UUID NOT NULL REFERENCES org(id),
  dimension TEXT NOT NULL,
  slug TEXT NOT NULL,
  model TEXT NOT NULL,
  embedding vector(1536) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, dimension, slug, model)
);

-- Every merge decision, applied or not, so runs are idempotent and reversible.
CREATE TABLE tag_vocab_merge (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  dimension TEXT NOT NULL,
  slug TEXT NOT NULL,
  into_slug TEXT NOT NULL,
  similarity REAL NOT NULL,
  reason TEXT,
  status TEXT NOT NULL CHECK (status IN ('applied', 'review', 'rejected', 'reverted')),
  source TEXT NOT NULL CHECK (source IN ('auto', 'manual')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);
CREATE INDEX tag_vocab_merge_org_dim_idx ON tag_vocab_merge (org_id, dimension, status);

-- migrate:down
DROP INDEX tag_vocab_merge_org_dim_idx;
DROP TABLE tag_vocab_merge;
DROP TABLE tag_vocab_embedding;
```

- [ ] **Step 4: Settings**

In `src/cs/core/config.py`, directly after the `insights_model` line, add:

```python
    # Open-vocabulary merge (spec 2026-09-16-vocab-merge-design.md): slug embeddings for
    # candidate recall. pgvector HNSW caps at 2000 dims; 1536 keeps the index option open.
    embedding_model: str = "google/gemini-embedding-2"
    embedding_dimensions: int = 1536
```

- [ ] **Step 5: Embedding client**

Create `src/cs/core/embeddings.py`:

```python
"""Text embeddings through the AI Gateway's OpenAI-compatible endpoint, plus a
deterministic fake for tests and a cosine helper."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

import openai

from cs.core.config import Settings

BATCH = 100


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class GatewayEmbedder:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            chunk = texts[start : start + BATCH]
            resp = await self.client.embeddings.create(
                model=self.model, input=chunk, dimensions=self.dimensions
            )
            out.extend([list(d.embedding) for d in resp.data])
        return out


class FakeEmbedder:
    """64-dim vectors from character trigram hashes: identical text embeds identically and
    texts sharing trigrams are closer than unrelated ones. No network."""

    def __init__(self, seed: int = 0):
        self.seed = seed

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            v = [0.0] * 64
            padded = f"  {text.lower()}  "
            for i in range(len(padded) - 2):
                h = hashlib.blake2b(
                    f"{self.seed}:{padded[i : i + 3]}".encode(), digest_size=2
                ).digest()
                v[h[0] % 64] += 1.0 if h[1] % 2 else -1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v])
        return vectors


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/core/test_embeddings.py tests/core/test_config.py -q`
Expected: PASS. (The conftest applies the new migration to the test DB at session start.)

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add db/migrations/20260916000004_vocab_merge.sql src/cs/core/config.py src/cs/core/embeddings.py tests/core/test_embeddings.py
git commit -m "feat(core): gateway embedding client and vocab-merge tables

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Embedding storage and candidate pairs

**Files:**
- Create: `src/cs/tagging/vocab_merge.py`
- Test: `tests/tagging/test_vocab_merge.py`

**Interfaces:**
- Consumes: `Embedder`, `cosine` (Task 1); `tag_vocab` from taxonomy v2.
- Produces: `embed_text(dimension: str, slug: str) -> str`; `embed_missing(db, embedder, org_id, dimension, model) -> int` (rows inserted); `load_vectors(db, org_id, dimension, model) -> dict[str, list[float]]` for canonical slugs only; `@dataclass(frozen=True) Pair(a: str, b: str, cosine: float)` with `a < b`; `candidate_pairs(db, org_id, dimension, model, floor=0.70) -> list[Pair]` sorted by cosine descending, excluding pairs with a `rejected` log row in either order.

- [ ] **Step 1: Write the failing tests**

Create `tests/tagging/test_vocab_merge.py`:

```python
import json

from cs.core.embeddings import FakeEmbedder
from cs.core.ids import new_id
from cs.tagging import vocab, vocab_merge

MODEL = "fake-embed"


async def _seed_vocab(db, org_id, dimension, counts: dict[str, int]):
    await vocab.canonicalize(db, org_id, dimension, list(counts))
    for slug, n in counts.items():
        await db.execute(
            "UPDATE tag_vocab SET count=$4 WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id, dimension, slug, n,
        )


def test_embed_text_uses_dimension_label_and_spaces():
    assert vocab_merge.embed_text("concept_tags", "benadryl_swap") == "concept tags: benadryl swap"


async def test_embed_missing_only_embeds_new_canonical_slugs(db, org_id):
    await _seed_vocab(db, org_id, "topics", {"sleep": 3, "magnesium": 2, "melatonin": 1})
    n = await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "topics", MODEL)
    assert n == 3
    assert await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "topics", MODEL) == 0
    # a merged (non-canonical) slug is not embedded
    await db.execute(
        "UPDATE tag_vocab SET canonical_slug='sleep' WHERE org_id=$1 AND dimension='topics' AND slug='melatonin'",
        org_id,
    )
    await vocab.canonicalize(db, org_id, "topics", ["insomnia"])
    assert await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "topics", MODEL) == 1
    stored = await db.fetchval(
        "SELECT embedding::text FROM tag_vocab_embedding WHERE org_id=$1 AND dimension='topics' AND slug='sleep'",
        org_id,
    )
    assert len(json.loads(stored)) == 64


async def test_load_vectors_returns_canonical_slugs_only(db, org_id):
    await _seed_vocab(db, org_id, "topics", {"sleep": 3, "melatonin": 1})
    await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "topics", MODEL)
    await db.execute(
        "UPDATE tag_vocab SET canonical_slug='sleep' WHERE org_id=$1 AND dimension='topics' AND slug='melatonin'",
        org_id,
    )
    vectors = await vocab_merge.load_vectors(db, org_id, "topics", MODEL)
    assert set(vectors) == {"sleep"} and len(vectors["sleep"]) == 64


async def test_candidate_pairs_respects_floor_and_rejections(db, org_id):
    await _seed_vocab(
        db, org_id, "features_mentioned", {"glycinate": 2, "magnesium_glycinate": 1, "sugar_free": 1}
    )
    await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "features_mentioned", MODEL)
    pairs = await vocab_merge.candidate_pairs(db, org_id, "features_mentioned", MODEL, floor=-1.0)
    assert len(pairs) == 3 and all(p.a < p.b for p in pairs)
    assert [p.cosine for p in pairs] == sorted((p.cosine for p in pairs), reverse=True)
    top = pairs[0]
    assert {top.a, top.b} == {"glycinate", "magnesium_glycinate"}
    high = await vocab_merge.candidate_pairs(db, org_id, "features_mentioned", MODEL, floor=top.cosine)
    assert high == [top]
    await db.execute(
        "INSERT INTO tag_vocab_merge (id, org_id, dimension, slug, into_slug, similarity, status, source) "
        "VALUES ($1,$2,'features_mentioned',$3,$4,$5,'rejected','auto')",
        new_id(), org_id, top.b, top.a, top.cosine,
    )
    assert await vocab_merge.candidate_pairs(db, org_id, "features_mentioned", MODEL, floor=-1.0) == [
        p for p in pairs if p != top
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tagging/test_vocab_merge.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cs.tagging.vocab_merge'`.

- [ ] **Step 3: Implement**

Create `src/cs/tagging/vocab_merge.py`:

```python
"""Merge near-synonym open-vocabulary slugs (spec 2026-09-16-vocab-merge-design.md).

Embeddings find candidate pairs; a text-only judge decides; a union-find plan applies
or routes to review; every decision is logged in tag_vocab_merge and reversible."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from uuid import UUID

from cs.core.db import Database
from cs.core.embeddings import Embedder, cosine
from cs.core.ids import new_id


def embed_text(dimension: str, slug: str) -> str:
    return f"{dimension.replace('_', ' ')}: {slug.replace('_', ' ')}"


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


async def embed_missing(
    db: Database, embedder: Embedder, org_id: UUID, dimension: str, model: str
) -> int:
    """Embed every canonical slug of the dimension that has no stored vector for `model`."""
    rows = await db.fetch(
        """
        SELECT v.slug FROM tag_vocab v
        LEFT JOIN tag_vocab_embedding e
          ON e.org_id = v.org_id AND e.dimension = v.dimension AND e.slug = v.slug AND e.model = $3
        WHERE v.org_id = $1 AND v.dimension = $2 AND v.slug = v.canonical_slug AND e.slug IS NULL
        ORDER BY v.slug
        """,
        org_id,
        dimension,
        model,
    )
    slugs = [r["slug"] for r in rows]
    if not slugs:
        return 0
    vectors = await embedder.embed([embed_text(dimension, s) for s in slugs])
    await db.executemany(
        "INSERT INTO tag_vocab_embedding (org_id, dimension, slug, model, embedding) "
        "VALUES ($1, $2, $3, $4, $5::vector) ON CONFLICT DO NOTHING",
        [(org_id, dimension, s, model, _literal(v)) for s, v in zip(slugs, vectors, strict=True)],
    )
    return len(slugs)


async def load_vectors(
    db: Database, org_id: UUID, dimension: str, model: str
) -> dict[str, list[float]]:
    rows = await db.fetch(
        """
        SELECT e.slug, e.embedding::text AS embedding
        FROM tag_vocab_embedding e
        JOIN tag_vocab v ON v.org_id = e.org_id AND v.dimension = e.dimension AND v.slug = e.slug
        WHERE e.org_id = $1 AND e.dimension = $2 AND e.model = $3 AND v.slug = v.canonical_slug
        """,
        org_id,
        dimension,
        model,
    )
    return {r["slug"]: json.loads(r["embedding"]) for r in rows}


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    cosine: float


async def rejected_pairs(db: Database, org_id: UUID, dimension: str) -> set[tuple[str, str]]:
    rows = await db.fetch(
        "SELECT slug, into_slug FROM tag_vocab_merge "
        "WHERE org_id = $1 AND dimension = $2 AND status = 'rejected'",
        org_id,
        dimension,
    )
    return {tuple(sorted((r["slug"], r["into_slug"]))) for r in rows}


async def candidate_pairs(
    db: Database, org_id: UUID, dimension: str, model: str, floor: float = 0.70
) -> list[Pair]:
    vectors = await load_vectors(db, org_id, dimension, model)
    rejected = await rejected_pairs(db, org_id, dimension)
    out: list[Pair] = []
    for a, b in itertools.combinations(sorted(vectors), 2):
        if (a, b) in rejected:
            continue
        c = cosine(vectors[a], vectors[b])
        if c >= floor:
            out.append(Pair(a, b, c))
    out.sort(key=lambda p: (-p.cosine, p.a, p.b))
    return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tagging/test_vocab_merge.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/cs/tagging/vocab_merge.py tests/tagging/test_vocab_merge.py
git commit -m "feat(tagging): embed canonical slugs and find merge candidates by cosine

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The judge

**Files:**
- Modify: `src/cs/tagging/vocab_merge.py`
- Test: `tests/tagging/test_vocab_merge.py`

**Interfaces:**
- Consumes: `Pair` (Task 2); `load_taxonomy()` for the dimension description; `tag` rows for evidence.
- Produces: `@dataclass(frozen=True) Verdict(pair: Pair, same_concept: bool, reason: str)`; `class Judge(Protocol): async def judge(self, dimension: str, description: str, pairs: list[Pair], context: dict[str, SlugContext]) -> list[Verdict]`; `@dataclass SlugContext(count: int, evidence: list[str])`; `slug_context(db, org_id, dimension, slugs) -> dict[str, SlugContext]` (up to 3 evidence strings per slug from open `tag` rows, plus `tag_vocab.count`); `JUDGE_SYSTEM: str`; `judge_user_text(dimension, description, pairs, context) -> str`; `judge_schema() -> dict`; `GatewayJudge(settings, client=None)`; `FakeJudge(same: set[tuple[str, str]])` returning `same_concept=True` for listed pairs (either order) and False otherwise, reason `"fake"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tagging/test_vocab_merge.py`:

```python
from types import SimpleNamespace

from cs.connectors.base import PlatformCreative
from cs.core import repo as ingest_repo
from cs.tagging import repo
from cs.tagging.taxonomy import load_taxonomy
from cs.tagging.vocab_merge import (
    JUDGE_SYSTEM,
    FakeJudge,
    GatewayJudge,
    Pair,
    SlugContext,
    judge_schema,
    judge_user_text,
    slug_context,
)


async def _open_rows(db, org_id, dimension, values_with_evidence):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD")
    cid, _ = await ingest_repo.upsert_creative(
        db, org_id, acct,
        PlatformCreative("v", "video", "H", "P", None, None, "u", None, None, {}),
        "raw", format_source="manual_review",
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    rows = [
        {"dimension": dimension, "kind": "open", "value": v, "raw_value": v, "confidence": 1.0, "evidence": e}
        for v, e in values_with_evidence
    ]
    await repo.finish_tag_run(db, org_id, rid, rows, "s", 1, 1)


async def test_slug_context_counts_and_up_to_three_evidence(db, org_id):
    await _seed_vocab(db, org_id, "claims", {"fall_asleep_naturally": 4, "stay_asleep": 1})
    await _open_rows(
        db, org_id, "claims",
        [("fall_asleep_naturally", f"e{i}") for i in range(5)] + [("stay_asleep", "s1")],
    )
    ctx = await slug_context(db, org_id, "claims", ["fall_asleep_naturally", "stay_asleep", "nope"])
    assert ctx["fall_asleep_naturally"].count == 4
    assert len(ctx["fall_asleep_naturally"].evidence) == 3
    assert ctx["stay_asleep"] == SlugContext(count=1, evidence=["s1"])
    assert ctx["nope"] == SlugContext(count=0, evidence=[])


def test_judge_prompt_and_schema():
    pairs = [Pair("glycinate", "magnesium_glycinate", 0.92)]
    ctx = {
        "glycinate": SlugContext(2, ["'glycinate' overlay at 0:12"]),
        "magnesium_glycinate": SlugContext(1, ["spoken at 0:40"]),
    }
    text = judge_user_text("features_mentioned", "Product features the ad calls out.", pairs, ctx)
    assert "Dimension: features_mentioned" in text and "Product features the ad calls out." in text
    assert "- glycinate (count 2): 'glycinate' overlay at 0:12" in text
    assert "- magnesium_glycinate (count 1): spoken at 0:40" in text
    assert "glycinate | magnesium_glycinate | cosine 0.92" in text
    assert "ingredient and a product containing it are different" in JUDGE_SYSTEM
    schema = judge_schema()
    item = schema["properties"]["pairs"]["items"]
    assert item["required"] == ["a", "b", "same_concept", "reason"]
    assert item["properties"]["same_concept"] == {"type": "boolean"}
    assert schema["additionalProperties"] is False


class _FakeCompletions:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=self.text, refusal=None))]
        )


async def test_gateway_judge_parses_verdicts_in_pair_order(settings):
    pairs = [Pair("a_slug", "b_slug", 0.9), Pair("c_slug", "d_slug", 0.8)]
    text = '{"pairs": [{"a": "c_slug", "b": "d_slug", "same_concept": false, "reason": "different"}, {"a": "a_slug", "b": "b_slug", "same_concept": true, "reason": "same"}]}'
    fake = _FakeCompletions(text)
    judge = GatewayJudge(settings, client=SimpleNamespace(chat=SimpleNamespace(completions=fake)))
    verdicts = await judge.judge("topics", "desc", pairs, {})
    assert [(v.pair, v.same_concept, v.reason) for v in verdicts] == [
        (pairs[0], True, "same"), (pairs[1], False, "different"),
    ]
    assert fake.calls[0]["model"] == settings.tagging_model
    assert fake.calls[0]["response_format"]["json_schema"]["strict"] is True


async def test_gateway_judge_missing_pair_is_a_rejection(settings):
    pairs = [Pair("a_slug", "b_slug", 0.9)]
    fake = _FakeCompletions('{"pairs": []}')
    judge = GatewayJudge(settings, client=SimpleNamespace(chat=SimpleNamespace(completions=fake)))
    verdicts = await judge.judge("topics", "desc", pairs, {})
    assert verdicts[0].same_concept is False and "no verdict" in verdicts[0].reason


async def test_fake_judge():
    pairs = [Pair("a_slug", "b_slug", 0.9), Pair("c_slug", "d_slug", 0.8)]
    verdicts = await FakeJudge({("b_slug", "a_slug")}).judge("topics", "d", pairs, {})
    assert [v.same_concept for v in verdicts] == [True, False]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tagging/test_vocab_merge.py -q`
Expected: FAIL with `ImportError: cannot import name 'JUDGE_SYSTEM'`.

- [ ] **Step 3: Implement**

Append to `src/cs/tagging/vocab_merge.py` (add `from typing import Protocol`, `import openai`, and `from cs.core.config import Settings` to the imports):

```python
@dataclass(frozen=True)
class SlugContext:
    count: int
    evidence: list[str]


async def slug_context(
    db: Database, org_id: UUID, dimension: str, slugs: list[str]
) -> dict[str, SlugContext]:
    counts = {
        r["slug"]: r["count"]
        for r in await db.fetch(
            "SELECT slug, count FROM tag_vocab WHERE org_id=$1 AND dimension=$2 AND slug = ANY($3)",
            org_id,
            dimension,
            slugs,
        )
    }
    rows = await db.fetch(
        """
        SELECT value, evidence FROM (
          SELECT value, evidence, row_number() OVER (PARTITION BY value ORDER BY id) AS rn
          FROM tag WHERE org_id=$1 AND dimension=$2 AND kind='open' AND value = ANY($3)
            AND evidence <> ''
        ) t WHERE rn <= 3
        """,
        org_id,
        dimension,
        slugs,
    )
    evidence: dict[str, list[str]] = {}
    for r in rows:
        evidence.setdefault(r["value"], []).append(r["evidence"])
    return {s: SlugContext(counts.get(s, 0), evidence.get(s, [])) for s in slugs}


@dataclass(frozen=True)
class Verdict:
    pair: Pair
    same_concept: bool
    reason: str


class Judge(Protocol):
    async def judge(
        self, dimension: str, description: str, pairs: list[Pair], context: dict[str, SlugContext]
    ) -> list[Verdict]: ...


JUDGE_SYSTEM = (
    "You decide whether two short tags from an ad-creative taxonomy name the same concept "
    "for the purpose of grouping ads. Same concept means a strategist would treat the two "
    "as interchangeable labels, not merely related. An ingredient and a product containing "
    "it are different. A brand and a category are different. A claim and its negation are "
    "different. A specific value and its general category are different. Answer every pair."
)


def judge_user_text(
    dimension: str, description: str, pairs: list[Pair], context: dict[str, SlugContext]
) -> str:
    lines = [f"Dimension: {dimension}", description, "", "Tags (count): evidence from the ads"]
    for slug in sorted({s for p in pairs for s in (p.a, p.b)}):
        c = context.get(slug, SlugContext(0, []))
        ev = " | ".join(c.evidence) if c.evidence else "(no evidence stored)"
        lines.append(f"- {slug} (count {c.count}): {ev}")
    lines += ["", "Candidate pairs:"]
    for p in pairs:
        lines.append(f"{p.a} | {p.b} | cosine {p.cosine:.2f}")
    lines += ["", "Return JSON matching the schema exactly, one entry per candidate pair."]
    return "\n".join(lines)


def judge_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "pairs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "string"},
                        "b": {"type": "string"},
                        "same_concept": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["a", "b", "same_concept", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["pairs"],
        "additionalProperties": False,
    }


class GatewayJudge:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.tagging_model  # text-only, the cheap tier

    async def judge(
        self, dimension: str, description: str, pairs: list[Pair], context: dict[str, SlugContext]
    ) -> list[Verdict]:
        if not pairs:
            return []
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": judge_user_text(dimension, description, pairs, context)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "verdicts", "schema": judge_schema(), "strict": True},
            },
        )
        text = resp.choices[0].message.content or "{}"
        answered = {}
        for item in json.loads(text).get("pairs", []):
            key = tuple(sorted((str(item.get("a", "")), str(item.get("b", "")))))
            answered[key] = (bool(item.get("same_concept")), str(item.get("reason", "")))
        out: list[Verdict] = []
        for p in pairs:
            same, reason = answered.get((p.a, p.b), (False, "no verdict returned"))
            out.append(Verdict(p, same, reason))
        return out


class FakeJudge:
    def __init__(self, same: set[tuple[str, str]]):
        self.same = {tuple(sorted(s)) for s in same}

    async def judge(self, dimension, description, pairs, context) -> list[Verdict]:
        return [Verdict(p, (p.a, p.b) in self.same, "fake") for p in pairs]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tagging/test_vocab_merge.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/cs/tagging/vocab_merge.py tests/tagging/test_vocab_merge.py
git commit -m "feat(tagging): text-only judge for merge candidates

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Plan, apply, revert, review

**Files:**
- Modify: `src/cs/tagging/vocab_merge.py`
- Test: `tests/tagging/test_vocab_merge.py`

**Interfaces:**
- Consumes: `Verdict`, `Pair` (Task 3); `tag_vocab`, `tag`, `tag_vocab_merge`.
- Produces: `@dataclass(frozen=True) Merge(slug: str, into_slug: str, similarity: float, reason: str)`; `@dataclass MergePlan(apply: list[Merge], review: list[Merge], rejected: list[Verdict])`; `plan_merges(verdicts, counts: dict[str, int], apply_floor=0.80) -> MergePlan`; `apply_merges(db, org_id, dimension, merges, source) -> None`; `record_review(db, org_id, dimension, merges) -> None`; `record_rejections(db, org_id, dimension, verdicts) -> None`; `list_review(db, org_id) -> list[asyncpg.Record]`; `decide_review(db, merge_id, approve: bool) -> None`; `revert(db, org_id, dimension, slug) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/tagging/test_vocab_merge.py`:

```python
from cs.tagging.vocab_merge import (
    Merge,
    Verdict,
    apply_merges,
    decide_review,
    list_review,
    plan_merges,
    record_rejections,
    record_review,
    revert,
)


def _v(a, b, cos, same):
    return Verdict(Pair(*sorted((a, b)), cos), same, "r")


def test_plan_merges_union_find_canonical_by_count_then_length():
    counts = {"benadryl_swap": 3, "benadryl_sleep_alternative": 3, "benadryl_alternative": 1, "melatonin": 5}
    verdicts = [
        _v("benadryl_swap", "benadryl_sleep_alternative", 0.85, True),
        _v("benadryl_alternative", "benadryl_sleep_alternative", 0.90, True),
        _v("melatonin", "benadryl_swap", 0.75, False),
    ]
    plan = plan_merges(verdicts, counts)
    # tie on count 3 -> shortest slug wins
    assert sorted((m.slug, m.into_slug) for m in plan.apply) == [
        ("benadryl_alternative", "benadryl_swap"),
        ("benadryl_sleep_alternative", "benadryl_swap"),
    ]
    assert plan.review == [] and [v.pair.a for v in plan.rejected] == ["benadryl_swap"]


def test_plan_merges_routes_low_cosine_groups_to_review():
    counts = {"gummy": 2, "easy_gummy_format": 4}
    plan = plan_merges([_v("gummy", "easy_gummy_format", 0.74, True)], counts)
    assert plan.apply == [] and [(m.slug, m.into_slug) for m in plan.review] == [("gummy", "easy_gummy_format")]


def test_plan_merges_same_concept_false_is_rejected_even_when_similar():
    plan = plan_merges([_v("magnesium", "magnesium_glycinate", 0.86, False)], {"magnesium": 5, "magnesium_glycinate": 1})
    assert plan.apply == [] and plan.review == [] and len(plan.rejected) == 1


async def test_apply_merges_rewrites_vocab_and_open_tag_values_but_not_raw(db, org_id):
    await _seed_vocab(db, org_id, "features_mentioned", {"glycinate": 2, "magnesium_glycinate": 1})
    await _open_rows(
        db, org_id, "features_mentioned",
        [("glycinate", "e1"), ("magnesium_glycinate", "e2")],
    )
    await apply_merges(
        db, org_id, "features_mentioned",
        [Merge("magnesium_glycinate", "glycinate", 0.92, "same compound")], source="auto",
    )
    vocab_rows = {
        r["slug"]: (r["canonical_slug"], r["count"])
        for r in await db.fetch("SELECT slug, canonical_slug, count FROM tag_vocab WHERE org_id=$1", org_id)
    }
    assert vocab_rows == {"glycinate": ("glycinate", 3), "magnesium_glycinate": ("glycinate", 0)}
    tags = await db.fetch(
        "SELECT value, raw_value FROM tag WHERE org_id=$1 AND dimension='features_mentioned' ORDER BY raw_value",
        org_id,
    )
    assert [(t["value"], t["raw_value"]) for t in tags] == [
        ("glycinate", "glycinate"), ("glycinate", "magnesium_glycinate"),
    ]
    log = await db.fetchrow("SELECT slug, into_slug, status, source, reason FROM tag_vocab_merge WHERE org_id=$1", org_id)
    assert tuple(log) == ("magnesium_glycinate", "glycinate", "applied", "auto", "same compound")
    # hints now show the merged count on the canonical only
    assert await vocab.hints(db, org_id, "features_mentioned", 10) == [("glycinate", 3)]


async def test_review_records_approve_and_reject(db, org_id):
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 2, "easy_gummy_format": 4})
    await _open_rows(db, org_id, "concept_tags", [("gummy", "e")])
    await record_review(db, org_id, "concept_tags", [Merge("gummy", "easy_gummy_format", 0.74, "maybe")])
    await record_rejections(db, org_id, "concept_tags", [_v("gummy", "melatonin", 0.7, False)])
    pending = await list_review(db, org_id)
    assert [(r["slug"], r["into_slug"], r["status"]) for r in pending] == [("gummy", "easy_gummy_format", "review")]
    await decide_review(db, pending[0]["id"], approve=True)
    row = await db.fetchrow("SELECT status, source, decided_at FROM tag_vocab_merge WHERE id=$1", pending[0]["id"])
    assert row["status"] == "applied" and row["source"] == "manual" and row["decided_at"] is not None
    assert await db.fetchval(
        "SELECT canonical_slug FROM tag_vocab WHERE org_id=$1 AND dimension='concept_tags' AND slug='gummy'", org_id
    ) == "easy_gummy_format"
    assert await db.fetchval("SELECT value FROM tag WHERE org_id=$1 AND dimension='concept_tags'", org_id) == "easy_gummy_format"
    assert await list_review(db, org_id) == []
    rejected = await db.fetchval("SELECT count(*) FROM tag_vocab_merge WHERE org_id=$1 AND status='rejected'", org_id)
    assert rejected == 1


async def test_decide_review_reject_and_revert(db, org_id):
    await _seed_vocab(db, org_id, "concept_tags", {"gummy": 2, "easy_gummy_format": 4})
    await _open_rows(db, org_id, "concept_tags", [("gummy", "e"), ("easy_gummy_format", "f")])
    await record_review(db, org_id, "concept_tags", [Merge("gummy", "easy_gummy_format", 0.74, "maybe")])
    (pending,) = await list_review(db, org_id)
    await decide_review(db, pending["id"], approve=False)
    assert await db.fetchval("SELECT status FROM tag_vocab_merge WHERE id=$1", pending["id"]) == "rejected"
    # a rejected pair no longer surfaces as a candidate (Task 2 contract)
    await vocab_merge.embed_missing(db, FakeEmbedder(), org_id, "concept_tags", MODEL)
    assert await vocab_merge.candidate_pairs(db, org_id, "concept_tags", MODEL, floor=-1.0) == []
    # apply then revert restores vocab and tag values from raw_value
    await apply_merges(db, org_id, "concept_tags", [Merge("gummy", "easy_gummy_format", 0.9, "r")], source="manual")
    await revert(db, org_id, "concept_tags", "gummy")
    assert await db.fetchval(
        "SELECT canonical_slug FROM tag_vocab WHERE org_id=$1 AND dimension='concept_tags' AND slug='gummy'", org_id
    ) == "gummy"
    values = sorted(r["value"] for r in await db.fetch("SELECT value FROM tag WHERE org_id=$1 AND dimension='concept_tags'", org_id))
    assert values == ["easy_gummy_format", "gummy"]
    counts = {r["slug"]: r["count"] for r in await db.fetch("SELECT slug, count FROM tag_vocab WHERE org_id=$1", org_id)}
    assert counts == {"gummy": 1, "easy_gummy_format": 1}  # recomputed: one run carries each
    assert await db.fetchval("SELECT status FROM tag_vocab_merge WHERE org_id=$1 AND slug='gummy' AND status='reverted'", org_id) == "reverted"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tagging/test_vocab_merge.py -q`
Expected: FAIL with `ImportError: cannot import name 'Merge'`.

- [ ] **Step 3: Implement**

Append to `src/cs/tagging/vocab_merge.py` (add `import asyncpg` to imports):

```python
@dataclass(frozen=True)
class Merge:
    slug: str
    into_slug: str
    similarity: float
    reason: str


@dataclass
class MergePlan:
    apply: list[Merge]
    review: list[Merge]
    rejected: list[Verdict]


def _canonical(group: list[str], counts: dict[str, int]) -> str:
    return sorted(group, key=lambda s: (-counts.get(s, 0), len(s), s))[0]


def plan_merges(
    verdicts: list[Verdict], counts: dict[str, int], apply_floor: float = 0.80
) -> MergePlan:
    """Union-find over pairs judged the same concept. A group whose weakest accepted pair is
    under apply_floor goes to review as a whole; everything else is applied."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    accepted = [v for v in verdicts if v.same_concept]
    for v in accepted:
        parent[find(v.pair.a)] = find(v.pair.b)
    groups: dict[str, list[str]] = {}
    for slug in parent:
        groups.setdefault(find(slug), []).append(slug)
    weakest: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for v in accepted:
        root = find(v.pair.a)
        weakest[root] = min(weakest.get(root, 1.0), v.pair.cosine)
        reasons.setdefault(root, v.reason)
    plan = MergePlan(apply=[], review=[], rejected=[v for v in verdicts if not v.same_concept])
    for root, members in groups.items():
        canonical = _canonical(members, counts)
        target = plan.apply if weakest[root] >= apply_floor else plan.review
        for slug in sorted(members):
            if slug != canonical:
                target.append(Merge(slug, canonical, weakest[root], reasons[root]))
    return plan


async def apply_merges(
    db: Database, org_id: UUID, dimension: str, merges: list[Merge], source: str
) -> None:
    if not merges:
        return
    async with db.transaction() as tx:
        for m in merges:
            await tx.execute(
                "UPDATE tag_vocab SET canonical_slug=$4 WHERE org_id=$1 AND dimension=$2 AND slug=$3",
                org_id, dimension, m.slug, m.into_slug,
            )
            await tx.execute(
                "UPDATE tag SET value=$4 WHERE org_id=$1 AND dimension=$2 AND kind='open' AND value=$3",
                org_id, dimension, m.slug, m.into_slug,
            )
            await tx.execute(
                "INSERT INTO tag_vocab_merge (id, org_id, dimension, slug, into_slug, similarity, reason, "
                "status, source, decided_at) VALUES ($1,$2,$3,$4,$5,$6,$7,'applied',$8,now())",
                new_id(), org_id, dimension, m.slug, m.into_slug, m.similarity, m.reason, source,
            )
        for canonical in {m.into_slug for m in merges}:
            await tx.execute(
                """
                WITH total AS (
                  SELECT sum(count) AS n FROM tag_vocab
                  WHERE org_id=$1 AND dimension=$2 AND canonical_slug=$3
                )
                UPDATE tag_vocab v SET count = CASE WHEN v.slug=$3 THEN (SELECT n FROM total) ELSE 0 END
                WHERE v.org_id=$1 AND v.dimension=$2 AND v.canonical_slug=$3
                """,
                org_id, dimension, canonical,
            )


async def record_review(db: Database, org_id: UUID, dimension: str, merges: list[Merge]) -> None:
    for m in merges:
        exists = await db.fetchval(
            "SELECT 1 FROM tag_vocab_merge WHERE org_id=$1 AND dimension=$2 AND slug=$3 AND into_slug=$4 "
            "AND status='review'",
            org_id, dimension, m.slug, m.into_slug,
        )
        if exists:
            continue
        await db.execute(
            "INSERT INTO tag_vocab_merge (id, org_id, dimension, slug, into_slug, similarity, reason, status, source) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'review','auto')",
            new_id(), org_id, dimension, m.slug, m.into_slug, m.similarity, m.reason,
        )


async def record_rejections(
    db: Database, org_id: UUID, dimension: str, verdicts: list[Verdict]
) -> None:
    for v in verdicts:
        await db.execute(
            "INSERT INTO tag_vocab_merge (id, org_id, dimension, slug, into_slug, similarity, reason, status, "
            "source, decided_at) VALUES ($1,$2,$3,$4,$5,$6,$7,'rejected','auto',now())",
            new_id(), org_id, dimension, v.pair.a, v.pair.b, v.pair.cosine, v.reason,
        )


async def list_review(db: Database, org_id: UUID) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT id, dimension, slug, into_slug, similarity, reason, created_at FROM tag_vocab_merge "
        "WHERE org_id=$1 AND status='review' ORDER BY dimension, similarity DESC, slug",
        org_id,
    )


async def decide_review(db: Database, merge_id: UUID, approve: bool) -> None:
    row = await db.fetchrow(
        "SELECT org_id, dimension, slug, into_slug, similarity, reason FROM tag_vocab_merge "
        "WHERE id=$1 AND status='review'",
        merge_id,
    )
    if row is None:
        raise ValueError(f"no pending review merge {merge_id}")
    if approve:
        # apply_merges writes its own 'applied' log row; mark the review row as decided by
        # turning it into that applied row instead of leaving two.
        await db.execute("DELETE FROM tag_vocab_merge WHERE id=$1", merge_id)
        await apply_merges(
            db, row["org_id"], row["dimension"],
            [Merge(row["slug"], row["into_slug"], row["similarity"], row["reason"] or "")],
            source="manual",
        )
        await db.execute(
            "UPDATE tag_vocab_merge SET id=$1 WHERE org_id=$2 AND dimension=$3 AND slug=$4 AND into_slug=$5 "
            "AND status='applied' AND source='manual' AND id <> $1",
            merge_id, row["org_id"], row["dimension"], row["slug"], row["into_slug"],
        )
    else:
        await db.execute(
            "UPDATE tag_vocab_merge SET status='rejected', source='manual', decided_at=now() WHERE id=$1",
            merge_id,
        )


async def revert(db: Database, org_id: UUID, dimension: str, slug: str) -> None:
    """Point `slug` back at itself and restore tag.value from raw_value for the rows whose
    raw slug normalizes to it. Counts are recomputed from the restored rows."""
    from cs.tagging.vocab import normalize

    async with db.transaction() as tx:
        into = await tx.fetchval(
            "SELECT canonical_slug FROM tag_vocab WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id, dimension, slug,
        )
        await tx.execute(
            "UPDATE tag_vocab SET canonical_slug=slug WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id, dimension, slug,
        )
        rows = await tx.fetch(
            "SELECT id, raw_value FROM tag WHERE org_id=$1 AND dimension=$2 AND kind='open' AND value=$3",
            org_id, dimension, into,
        )
        restored = [r["id"] for r in rows if normalize(r["raw_value"] or "") == slug]
        if restored:
            await tx.execute("UPDATE tag SET value=$2 WHERE id = ANY($1)", restored, slug)
        for s in (slug, into):
            await tx.execute(
                """
                UPDATE tag_vocab SET count = (
                  SELECT count(DISTINCT tag_run_id) FROM tag
                  WHERE org_id=$1 AND dimension=$2 AND kind='open' AND value=$3
                ) WHERE org_id=$1 AND dimension=$2 AND slug=$3
                """,
                org_id, dimension, s,
            )
        await tx.execute(
            "UPDATE tag_vocab_merge SET status='reverted', decided_at=now() "
            "WHERE org_id=$1 AND dimension=$2 AND slug=$3 AND status='applied'",
            org_id, dimension, slug,
        )
```

`revert` recomputes `tag_vocab.count` as the number of distinct runs carrying the slug, which is what `bump_counts` accumulates; the seeded counts in the test are artificial, so the post-revert assertion expects the recomputed values.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tagging/test_vocab_merge.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/cs/tagging/vocab_merge.py tests/tagging/test_vocab_merge.py
git commit -m "feat(tagging): plan, apply, review and revert vocabulary merges

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Scripts

**Files:**
- Create: `scripts/merge_vocab.py`
- Create: `scripts/review_vocab_merges.py`

**Interfaces:**
- Consumes: everything in `vocab_merge`, `GatewayEmbedder`, `GatewayJudge`, `load_taxonomy`.

- [ ] **Step 1: merge script**

Create `scripts/merge_vocab.py`:

```python
"""Propose (and with --apply, apply) open-vocabulary merges for one org.

Dry run by default: prints the apply list, the review list and the rejected pairs with
the judge's reasons, and writes nothing. --apply applies the apply list, records the
review list for scripts/review_vocab_merges.py, and records rejections so they are never
proposed again.

Usage: uv run python scripts/merge_vocab.py --org <uuid> [--dimension <name>] [--apply]
       [--floor 0.70] [--apply-floor 0.80]
"""

import argparse
import asyncio
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.embeddings import GatewayEmbedder
from cs.tagging import vocab_merge
from cs.tagging.taxonomy import load_taxonomy


async def main(org_id: UUID, dimension: str | None, apply: bool, floor: float, apply_floor: float) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        embedder = GatewayEmbedder(settings)
        judge = vocab_merge.GatewayJudge(settings)
        taxonomy = load_taxonomy()
        dims = [d for d in taxonomy.open_dimensions("video") if dimension in (None, d.name)]
        for d in dims:
            n = await vocab_merge.embed_missing(db, embedder, org_id, d.name, settings.embedding_model)
            pairs = await vocab_merge.candidate_pairs(db, org_id, d.name, settings.embedding_model, floor)
            print(f"\n== {d.name}: embedded {n} new slug(s), {len(pairs)} candidate pair(s)", flush=True)
            if not pairs:
                continue
            slugs = sorted({s for p in pairs for s in (p.a, p.b)})
            context = await vocab_merge.slug_context(db, org_id, d.name, slugs)
            verdicts = await judge.judge(d.name, d.description, pairs, context)
            counts = {s: c.count for s, c in context.items()}
            plan = vocab_merge.plan_merges(verdicts, counts, apply_floor)
            for m in plan.apply:
                print(f"  APPLY   {m.slug} -> {m.into_slug}  ({m.similarity:.2f}) {m.reason}", flush=True)
            for m in plan.review:
                print(f"  REVIEW  {m.slug} -> {m.into_slug}  ({m.similarity:.2f}) {m.reason}", flush=True)
            for v in plan.rejected:
                print(f"  reject  {v.pair.a} | {v.pair.b}  ({v.pair.cosine:.2f}) {v.reason}", flush=True)
            if apply:
                await vocab_merge.apply_merges(db, org_id, d.name, plan.apply, source="auto")
                await vocab_merge.record_review(db, org_id, d.name, plan.review)
                await vocab_merge.record_rejections(db, org_id, d.name, plan.rejected)
                print(f"  applied {len(plan.apply)}, queued {len(plan.review)} for review, recorded {len(plan.rejected)} rejections", flush=True)
        if not apply:
            print("\nDry run: nothing written. Re-run with --apply to apply the APPLY lines and queue REVIEW lines.", flush=True)
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True, type=UUID)
    p.add_argument("--dimension")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--floor", type=float, default=0.70)
    p.add_argument("--apply-floor", type=float, default=0.80)
    a = p.parse_args()
    asyncio.run(main(a.org, a.dimension, a.apply, a.floor, a.apply_floor))
```

- [ ] **Step 2: review script**

Create `scripts/review_vocab_merges.py`:

```python
"""List pending vocabulary merges, and approve or reject them by id.

Usage: uv run python scripts/review_vocab_merges.py --org <uuid>
       uv run python scripts/review_vocab_merges.py --org <uuid> --approve <id> [<id> ...]
       uv run python scripts/review_vocab_merges.py --org <uuid> --reject <id> [<id> ...]
"""

import argparse
import asyncio
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.tagging import vocab_merge


async def main(org_id: UUID, approve: list[UUID], reject: list[UUID]) -> None:
    db = await Database.connect(get_settings().database_url)
    try:
        for mid in approve:
            await vocab_merge.decide_review(db, mid, approve=True)
            print(f"approved {mid}", flush=True)
        for mid in reject:
            await vocab_merge.decide_review(db, mid, approve=False)
            print(f"rejected {mid}", flush=True)
        pending = await vocab_merge.list_review(db, org_id)
        print(f"\n{len(pending)} pending review", flush=True)
        for r in pending:
            print(f"  {r['id']}  {r['dimension']:<20} {r['slug']} -> {r['into_slug']}  ({r['similarity']:.2f}) {r['reason']}", flush=True)
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True, type=UUID)
    p.add_argument("--approve", type=UUID, nargs="*", default=[])
    p.add_argument("--reject", type=UUID, nargs="*", default=[])
    a = p.parse_args()
    asyncio.run(main(a.org, a.approve, a.reject))
```

- [ ] **Step 3: Smoke and commit**

Run: `uv run python scripts/merge_vocab.py --help && uv run python scripts/review_vocab_merges.py --help`
Expected: both print usage.

```bash
uv run ruff check . && uv run ruff format .
git add scripts/merge_vocab.py scripts/review_vocab_merges.py
git commit -m "chore(scripts): propose, apply and review vocabulary merges

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: First run on the four videos (operational)

- [ ] **Step 1: Migrate the primary database**

```bash
cd "/Users/anantchandra/Desktop/Custom LP/creative-strategist" && export $(grep -E '^DATABASE_URL=' .env | xargs) && dbmate --no-dump-schema --migrations-dir db/migrations up
```
Expected: `Applying: 20260916000004_vocab_merge.sql`.

- [ ] **Step 2: Dry run**

```bash
export $(grep -E '^(DATABASE_URL|AI_GATEWAY_API_KEY|AI_GATEWAY_BASE_URL)=' .env | xargs) && uv run python scripts/merge_vocab.py --org 01a089c9-231e-7000-bdaa-39277b4cc94b 2>&1 | tee "/private/tmp/claude-501/-Users-anantchandra-Desktop-Custom-LP-creative-strategist/7c6cd8e2-5ea1-4261-98cc-67d38e4001a2/scratchpad/vocab-merge-dryrun.txt"
```

Report every APPLY, REVIEW and reject line to the user, grouped by dimension, and stop. Do not pass `--apply` without the user's go. Push and deploy are not needed for the scripts (they run locally against the primary DB), but push main so the box has the migration file for its next deploy.
