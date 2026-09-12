# Creative Classification Contract

**Date:** 2026-09-11
**Status:** Design — approved for implementation planning
**Scope:** Meta creative format classification (image / video / carousel) in the Fivetran ingest path

## Problem

`_format_and_urls` (`src/cs/ingest/fivetran/transform.py:69-79`) classifies a creative as `image` whenever it cannot prove otherwise:

```python
return "image", row["image_url"] or row["thumbnail_url"], None
```

For `object_type='SHARE'` creatives — boosted organic Page and Instagram posts — Meta never populates `video_id` or `video_video_id`. Every boosted video post therefore falls through to this branch, downloads Meta's auto-generated preview frame from `thumbnail_url`, and stores it as `original_image`.

Measured against production (org 2, `org_01a089c9_231e_7000_bdaa_39277b4cc94b__meta_ads`):

- **408 of 572 served ads labeled `image` carry `video_view` actions — 477,212 video views.** A static image cannot accrue a video view.
- The downloaded file is a valid JPEG, so no error is raised anywhere. The failure is silent by construction.

### Consequence beyond the label

`tag_creative` (`src/cs/tagging/runner.py:47`) selects the model and taxonomy from `creative.format`:

```python
model = ctx.settings.video_tagging_model if fmt == "video" else ctx.settings.tagging_model
```

A misclassified video is tagged by the **image** model against the **image** dimension set (`taxonomy.for_format`). The existing tags on these creatives are not merely attached to a mislabeled row — they are invalid output, produced by the wrong model from a single arbitrary video frame. Remediation must re-tag, not just relabel.

## Root cause

Two layers, both required for the bug to exist:

1. **Schema.** `creative.format` is `TEXT NOT NULL CHECK (format IN ('image','video','carousel'))` (`db/migrations/20260904000003_ingest.sql:22`). "Not yet determined" is unrepresentable. The schema requires a value, so the code must invent one.
2. **Code.** The classifier has a default branch. Absence of video evidence is treated as presence of image evidence.

Fixing only the code leaves the schema forcing the next author back into a guess. Both change.

## Constraints

Set by the product owner, 2026-09-11:

- **No Meta OAuth tokens**, now or later. Resolution uses Fivetran-replicated data and Apify only.
- **Org 2 is the source of truth.** Org 1 is out of scope for this work.
- **Correctness bar:** a video must never be classified as an image.

## What is achievable, and what is not

Investigation established an asymmetry that determines the whole design:

> Fivetran's replicated data can **prove video**. It can never **prove image**.

Evidence for this claim:

- All 109 `creative_history` columns were profiled, split by proven-video vs. not. None separates them. `image_url` is populated on 11 proven videos, one with 1,126 video views — so `image_url` presence is not evidence of an image.
- `ad_video_history` (the ad account's video library) matches **zero** SHARE creatives by permalink. Correct: a boosted post's video belongs to the Page or creator, not the advertiser's library.
- `effective_instagram_media_id` is a 17-digit Graph API identifier; Instagram shortcodes decode to 19-digit media PKs. Different namespaces — verified by conversion test. Apify cannot be pointed at creatives lacking `instagram_permalink_url`.

Therefore **100% correct classification is not reachable under these constraints.** 131 creatives-with-ads in org 2 have no resolution path: no video metric, absent from `ad_asset_feed_spec_video`, no scrapeable permalink.

The requirement is met in the form that matters. The contract guarantees **soundness, not completeness**: the system never asserts a classification it cannot prove. Unresolved creatives remain `unresolved` and are excluded from the image bucket rather than defaulted into it.

### Validated coverage (org 2, 1,103 creatives that have ads)

Buckets below are validation measurements, not the rule numbering in §2.

| Resolved by | Creatives | Impressions |
|---|---|---|
| `object_type = 'VIDEO'` | 417 | 13,304,429 |
| present in `ad_asset_feed_spec_video` | 494 | 1,444,620 |
| `video_view > 0` on any of its ads | 22 | 523,682 |
| unresolved, Apify-reachable | 39 | 461,327 |
| unresolved, human review | 131 | 429,041 |

**933 of 1,103 (84.6%) resolve with certainty from Fivetran alone** — no API calls, no Apify spend. Silently wrong: **0**.

Rule 1 was validated for trustworthiness: **zero** `object_type='VIDEO'` creatives have ≥1,000 impressions and no video views, and 374 of 377 served ones are corroborated by metrics.

## Design

### 1. Schema: make a guess unrepresentable

Migration adds to `creative`:

```sql
ALTER TABLE creative DROP CONSTRAINT creative_format_check;
ALTER TABLE creative ADD CONSTRAINT creative_format_check
  CHECK (format IN ('image','video','carousel','unresolved'));

ALTER TABLE creative ADD COLUMN format_source TEXT NOT NULL DEFAULT 'legacy_guess';
ALTER TABLE creative ADD COLUMN format_verified_at TIMESTAMPTZ;

ALTER TABLE creative ADD CONSTRAINT creative_format_source_check
  CHECK (format_source IN (
    'meta_object_type','asset_feed_spec_video','video_view_metric',
    'meta_video_id','carousel_attachments','apify_media_type',
    'manual_review','unresolved','legacy_guess'
  ));

-- The core invariant: 'unresolved' and a real evidence source are mutually exclusive.
ALTER TABLE creative ADD CONSTRAINT creative_unresolved_consistency
  CHECK ((format = 'unresolved') = (format_source = 'unresolved'));
```

`legacy_guess` exists solely to admit pre-migration rows. It is a remediation queue marker, never written by new code. When the backfill completes, zero rows should carry it; a monitor asserts this.

### 2. Classification contract

`_format_and_urls` is replaced by a classifier returning `(format, format_source, asset_url, child_urls)`. Rules evaluate in precedence order; **there is no default branch**:

| # | Condition | Verdict | Source |
|---|---|---|---|
| 1 | `object_type = 'VIDEO'` | `video` | `meta_object_type` |
| 2 | `object_type = 'PHOTO'` | `image` | `meta_object_type` |
| 3 | `object_story_link_data_child_attachments` parses to ≥1 URL | `carousel` | `carousel_attachments` |
| 4 | `video_video_id` or `video_id` resolves in `ad_video_history` | `video` | `meta_video_id` |
| 5 | `creative_id` present in `ad_asset_feed_spec_video` | `video` | `asset_feed_spec_video` |
| 6 | any of the creative's ads has `video_view > 0` | `video` | `video_view_metric` |
| 7 | Apify returns an explicit media type | per type | `apify_media_type` |
| 8 | otherwise | `unresolved` | `unresolved` |

Any `object_type` value outside the known set falls to rule 8. `PRIVACY_CHECK_FAIL` (75 creatives in org 2, none with ads) lands there harmlessly.

**Why carousel (rule 3) outranks every video rule.** `video_view > 0` proves the ad *contained* playable video — it does not prove the creative *is* a single video. A carousel with one video card satisfies rule 6. Ordering the metric rule first would classify that carousel as `video`, which is exactly the class of error this design exists to eliminate. Child attachments are positive structural evidence of a carousel, so they outrank inference from a metric. For the same reason the contradiction detector (§5) treats `image` + video views as a violation but `carousel` + video views as legitimate.

This ordering is untested against real data: org 2 has 69 carousel creatives, none with ads, so no carousel-containing-video case exists there today. The rule is written to be correct when one appears rather than to fit current data.

### 3. Unresolved handling

An `unresolved` creative:

- **downloads no asset.** We do not know which URL is correct, and `thumbnail_url` is precisely the trap. No `meta.download_creative` job is enqueued.
- **is not tagged.** `tag_creative` already raises when `primary_asset_path` is absent; the enqueue is skipped instead, so no failing jobs accumulate.
- **is excluded from format filters.** Filtering for `image` must never return it. `list_creatives` / `count_creatives` (`src/cs/core/repo/creatives.py`) already filter on exact `format`, so this holds automatically — but a test pins it.
- **is surfaced** in an admin review queue with its ad count and impressions, so the highest-spend ones get resolved first.

`tagging/repo.py:162` defaults `formats=("image","carousel")`, so unresolved creatives are already excluded from generation reference lookup. A test pins that too.

### 4. Apify hardening

`resolve_video_url` (`src/cs/ingest/instagram_scrape.py:44`) currently infers "not a video post" from a missing `videoUrl`. That is the same silent-wrongness pattern one layer down: a scraper that breaks returns no `videoUrl`, and the pipeline concludes "image."

Replace with a positive assertion. Read Instagram's explicit type (`GraphImage` / `GraphVideo` / `GraphSidecar`, or the actor's equivalent `type` field) and map it. A missing or unrecognized type yields `unresolved`, never a format. Apify is thereby incapable of producing a wrong answer — only an unresolved one.

The actor's exact response shape is **unverified** (no `APIFY_API_KEY` configured during design). The first implementation task is a throwaway probe against a handful of known-video and known-image permalinks to confirm the field name and value set. If Apify offers no positive type assertion, rule 7 is dropped and those 39 creatives move to human review — the contract holds either way.

### 5. Contradiction detector

The component that makes the contract strong rather than merely correct. A scheduled job asserts, using only Fivetran data:

- no creative with `format='image'` has any ad with `video_view > 0`
- no creative with `format='image'` appears in `ad_asset_feed_spec_video`
- no creative retains `format_source='legacy_guess'` after backfill

A violation quarantines the creative (`format='unresolved'`, `format_source='unresolved'`) and alerts. This uses a signal independent of whatever produced the original verdict, so it catches a broken Apify actor, a Meta semantics change, or a future regression in the classifier itself. Run against today's data it would flag all 408 known cases.

### 6. Connector table requirement

`ad_asset_feed_spec_video` is **absent from `REQUIRED_TABLES`** (`src/cs/connectors/fivetran/client.py:11`), and `lock_down_and_activate` sets `schema_change_handling: "ALLOW_COLUMNS"`, which blocks tables discovered later. Org 2 has the table by historical accident; no new connector will get it.

That table is rule 4 — 494 creatives, the second-largest bucket. Without it the contract silently degrades to roughly 40% coverage for every future connection while continuing to look correct on org 2. Add it to `REQUIRED_TABLES`.

A startup or sync-time precondition check asserts each required table exists for the connection's schema and fails loudly rather than degrading.

## Remediation

Existing bad data must be corrected, not just prevented.

1. **Reclassify** every creative through the new contract, writing `format` + `format_source` + `format_verified_at`.
2. **For creatives whose verdict changes `image` → `video`:** delete the `original_image` asset (a video frame), clear `primary_asset_id`, and re-enqueue `meta.download_creative` with `kind='original_video'` so the real video is fetched.
3. **Invalidate tags** produced under the wrong format. These were generated by the image model against the image taxonomy and are not salvageable. Re-tagging is gated on the corrected asset existing.
4. **Order by impressions descending** so the highest-spend creatives are corrected first.

Note: re-tagging ~500 newly-identified videos through `video_tagging_model` has a real AI Gateway cost. `video_tagging_enabled` defaults to `True` (`src/cs/core/config.py:45`), so this will run once the assets land. Sequence the backfill deliberately rather than letting it fire on the next sync.

## Testing

- **Classifier unit tests, table-driven:** one case per rule, each asserting both the verdict and the `format_source`.
- **The regression test that matters:** a creative row with no signals at all must return `unresolved`. This is the test that fails if anyone reintroduces a default branch.
- **Unknown-input tests:** an unrecognized `object_type` and a `PRIVACY_CHECK_FAIL` row both yield `unresolved`.
- **Precedence test:** a creative with both child attachments and `video_view > 0` classifies as `carousel`, not `video`.
- **Contradiction detector tests:** `image` + video views → violation; `carousel` + video views → no violation.
- **Apify tests:** explicit video type → `video`; explicit image type → `image`; missing or unknown type → `unresolved` (never `image`).
- **Downstream exclusion tests:** filtering creatives by `image` excludes `unresolved`; generation reference lookup excludes `unresolved`.
- **Constraint test:** inserting `format='image'` with `format_source='unresolved'` is rejected by the database.

Fixtures extend `src/cs/ingest/fivetran/fixtures.py`, whose `creative_history` definition currently lacks `object_type`, `effective_object_story_id`, and the asset-feed-spec columns. It needs updating to match the real 109-column table for the columns the contract reads.

## Out of scope

- Org 1 and its missing `basic_ad` / `basic_ad_actions` tables (no insights data at all — tracked separately; likely more urgent than this work).
- Any Meta Graph API or OAuth integration.
- Video retention metrics (`action_video_view_type_video_p_25_watched_actions` exists in org 2 and is unused — separate work).

## Open questions

1. Does the Apify actor return a positive media-type field? Resolved by the first implementation task; the contract holds either way.
2. Should the admin review queue allow a human verdict to be recorded (`format_source='manual_review'`), or only display the queue? The schema supports it; the UI is a scoping decision.
