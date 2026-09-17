# Open-Vocabulary Merge

**Date:** 2026-09-16
**Status:** Draft for review. First run scoped to the vocabulary of the four org 3 videos with insights.
**Depends on:** video tag taxonomy v2 (`2026-09-16-video-tag-taxonomy-v2-design.md`), which stores open tags as `tag` rows with `kind='open'`, `value` = canonical slug, `raw_value` = the model's slug, and a per-org `tag_vocab (org_id, dimension, slug, canonical_slug, count)`.

## Problem

Open dimensions (concept_tags, topics, comparison_targets, audience_callouts, claims, visual_motifs, products_mentioned, features_mentioned) let the model coin slugs. Consistency run 1 measured 0.07–0.57 exact-slug agreement with an empty vocabulary; run 2 measured 0.54–0.88 once the prompt could show existing slugs. What remains are near-synonyms coined on different days: `glycinate` / `magnesium_glycinate`, `benadryl_swap` / `benadryl_sleep_alternative`, `gummy` / `easy_gummy_format`, `no_sugar` / `no_sugar_added`. Clustering on multi-hot open tags treats each as a different feature, so families split on spelling.

Merging must be: per org and per dimension (a comparison target and an audience callout never merge); re-runnable after every drain; reversible; and cheap. A pure cosine threshold on bare slugs is not precise enough (probe on 2026-09-16 through the gateway: `glycinate`~`magnesium glycinate` 0.92 should merge, `magnesium glycinate`~`magnesium` 0.86 must not, `benadryl swap`~`benadryl sleep alternative` 0.71 should).

## Decisions

| Decision | Choice |
|---|---|
| Embedding client | `google/gemini-embedding-2` through the gateway's OpenAI-compatible `/v1/embeddings`, `dimensions=1536` (pgvector HNSW limit is 2000). One client module, reused later for footage variants (media goes through `/v3/ai/embedding-model`, out of scope here) |
| What is embedded | `"{dimension label}: {slug with spaces}"` — the dimension name as context, so `benadryl` in comparison_targets and `benadryl_users` in audience_callouts embed differently even if they were ever compared |
| Recall | Per org and dimension, candidate pairs are canonical slugs with cosine ≥ 0.70 |
| Decision | A text-only `google/gemini-2.5-flash-lite` call per dimension: given the candidate pairs, each slug's count and up to three evidence strings from `tag` rows, return for each pair `same_concept: true/false` and a one-line reason. Pairs judged true with cosine ≥ 0.80 are applied; pairs judged true with cosine in [0.70, 0.80) go to the review list; pairs judged false are recorded and never re-asked for the same slug pair |
| Canonical choice | Within a merged group, the slug with the highest `count`; ties by shortest slug |
| Apply | Transaction per dimension: `tag_vocab.canonical_slug` updated for every member; `tag.value` rewritten for open rows whose `value` is a merged slug (`raw_value` untouched); `tag_vocab.count` re-aggregated onto the canonical; one `tag_vocab_merge` row per member with source `auto` |
| Review | `tag_vocab_merge` rows with status `review` are listed by a script; approving applies the same transaction with source `manual`; rejecting records a `rejected` pair so it is never proposed again |
| Reversal | `tag_vocab_merge` holds `slug, into_slug, similarity, reason, status, created_at`; a revert script re-points the slug to itself and rewrites `tag.value` back from `raw_value` for rows whose raw normalized slug equals it |
| Scope of first run | Org 3, the eight open dimensions, vocabulary as populated by the four insight videos (76 canonical slugs on 2026-09-16). Output: the proposed merges and the review list, printed for the user before anything is applied |
| Cost | Embeddings: ~5 tokens per slug, cents per thousand slugs. Decision calls: one flash-lite call per dimension per run, a few thousand tokens each. Re-runs embed only slugs without a stored vector |

## 1. Storage

Migration `20260916000004_vocab_merge.sql`:

```
CREATE TABLE tag_vocab_embedding (
  org_id UUID NOT NULL REFERENCES org(id),
  dimension TEXT NOT NULL,
  slug TEXT NOT NULL,
  model TEXT NOT NULL,
  embedding vector(1536) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, dimension, slug, model)
);

CREATE TABLE tag_vocab_merge (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  dimension TEXT NOT NULL,
  slug TEXT NOT NULL,
  into_slug TEXT NOT NULL,
  similarity REAL NOT NULL,
  reason TEXT,
  status TEXT NOT NULL CHECK (status IN ('applied','review','rejected','reverted')),
  source TEXT NOT NULL CHECK (source IN ('auto','manual')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);
CREATE INDEX tag_vocab_merge_org_dim_idx ON tag_vocab_merge (org_id, dimension, status);
```

No HNSW index yet: vocabularies are hundreds of rows and pairwise cosine is computed in Python. Add the index when a dimension passes ~5,000 slugs.

## 2. Modules

- `src/cs/core/embeddings.py` — `Embedder` protocol with `embed(texts: list[str]) -> list[list[float]]`; `GatewayEmbedder(settings)` using the OpenAI client at `settings.ai_gateway_base_url + "/v1"`, model `settings.embedding_model` (default `google/gemini-embedding-2`), `dimensions=settings.embedding_dimensions` (default 1536), batches of 100; `FakeEmbedder` in `cs.core.fakes` for tests (deterministic vectors from a seeded hash so cosine is stable).
- `src/cs/tagging/vocab_merge.py`
  - `embed_missing(db, embedder, org_id, dimension) -> int` — embeds canonical slugs without a stored vector.
  - `candidate_pairs(db, org_id, dimension, floor=0.70) -> list[Pair(slug_a, slug_b, cosine)]` among canonical slugs, excluding pairs already `rejected`.
  - `judge_pairs(judge, dimension, pairs, context) -> list[Verdict(pair, same_concept, reason)]` — one text-only call; `Judge` protocol with a `FakeJudge`.
  - `plan_merges(verdicts, counts, apply_floor=0.80) -> MergePlan(apply: list[Merge], review: list[Merge], rejected: list[Pair])` — union-find over pairs judged true; canonical by count then length; a group whose lowest applied-pair cosine is under `apply_floor` goes to review as a whole.
  - `apply_merges(db, org_id, dimension, merges, source) -> None` — the transaction in the Decisions table.
  - `revert(db, org_id, dimension, slug) -> None`.
- `scripts/merge_vocab.py --org <id> [--dimension <name>] [--apply] [--floor 0.70] [--apply-floor 0.80]` — default is a dry run that prints the plan (apply list, review list, rejected pairs with reasons). `--apply` runs `apply_merges` for the apply list and writes the review rows.
- `scripts/review_vocab_merges.py --org <id> [--approve <merge_id>...] [--reject <merge_id>...]` — lists `review` rows; approve/reject as above.

## 3. The judge prompt

System: "You decide whether two short tags from an ad-creative taxonomy name the same concept for the purpose of grouping ads. Same concept means a strategist would treat the two as interchangeable labels, not merely related. An ingredient and a product containing it are different. A brand and a category are different. A claim and its negation are different."

User: the dimension name and description from the taxonomy; for each slug, its count and up to three evidence strings; the candidate pairs with cosine. Strict JSON: `{"pairs": [{"a", "b", "same_concept": bool, "reason"}]}`.

## 4. First run (the four videos)

1. Apply the migration to the test DB (conftest) and the primary DB.
2. `scripts/merge_vocab.py --org 01a089c9-231e-7000-bdaa-39277b4cc94b` dry run over the eight dimensions. Print to the user: every proposed merge with its cosine and reason, the review list, and the rejected pairs.
3. Nothing is applied until the user says so. On approval, `--apply`, then re-read the four videos' open tags to show the effect.
4. After the org 3 drain, the same command re-runs over the full vocabulary; already-decided pairs are skipped.

## 5. Testing

- `tests/core/test_embeddings.py`: `GatewayEmbedder` batches and passes `dimensions`; `FakeEmbedder` is deterministic.
- `tests/tagging/test_vocab_merge.py` (DB-backed): `embed_missing` only embeds new slugs; `candidate_pairs` respects the floor and skips rejected pairs; `plan_merges` picks the canonical by count then length and routes low-cosine groups to review; `apply_merges` rewrites `tag_vocab.canonical_slug`, `tag.value` (not `raw_value`), re-aggregates counts, and logs rows; `revert` restores; a second dry run proposes nothing already decided.
- Judge prompt: a unit test that the prompt carries the dimension description, counts, evidence and cosines; the judge call itself is faked.

## Out of scope

Media embeddings for footage variants (same client, later piece); the nightly job (the script is run by hand until the drain has happened); an HNSW index; cross-org vocabularies.
