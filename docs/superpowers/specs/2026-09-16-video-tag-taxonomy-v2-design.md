# Video Tag Taxonomy v2

**Date:** 2026-09-16
**Status:** Approved in conversation; video only for this piece.
**Scope:** Replace the video tag set with one designed for clustering videos into creative-concept families. Images and carousels keep their current dimensions under the same taxonomy file until a later rollout. The clustering job itself is a separate piece that consumes what this one stores.

## Problem

The current taxonomy is 31 single-value closed dimensions plus two free-text sentences. Measured on the four org 3 videos tagged on 2026-09-16:

- 10 dimensions were constant across all four videos.
- Interpretive dimensions collapse: hook_type was `problem_statement` for all four, including a "Worst OTC Sleep Aids" warning and an "I've been taking the wrong one" confession.
- Single values force false choices: a video with a personal testimonial and a chiropractor mention got `proof_type = expert`.
- `other` swallows real categories: `visual_style = other` for a talking head with overlays, `format_subtype = other` for a text-over-b-roll ad.
- The JSON schema constrains the dimension name but not the value, so the model can emit values outside the enum.
- The same visual (a hand holding a gummy at 6s) was tagged `product_visible = yes` on one creative and `no` on its near-duplicate.
- Video tagging is off in production (`VIDEO_TAGGING_ENABLED=false`); 565 of 573 org 3 videos have never been tagged.

Clustering needs tags that are reliably observable, vary across the corpus, and sit on the axes a strategist iterates: idea, angle, messenger, form, structure, offer.

## Decisions

| Decision | Choice |
|---|---|
| Shape | Five layers: computed numerics, closed single-value, closed multi-value with timestamps, open vocabulary, free text |
| Model call | Still one call with the video file; response is a nested object with enums enforced per field; code flattens to `tag` rows |
| Storage | Existing `tag` table plus four nullable columns; new `tag_vocab` table; new taxonomy version |
| Open-tag canonicalization | v1: slug normalization, exact merge, and prompt-side reuse of the org's existing vocabulary. Embedding-based merge is deferred to the clustering piece, which builds the embedding client this repo does not yet have |
| Confidence | Kept on the row (NOT NULL today). Model gives it for single-value dimensions; multi-value items, open tags, text, and computed rows store 1.0. Not used for clustering |
| Rollout | Video only. `applies_to` on the old dimensions drops `video`; the new dimensions apply to `video` only. Image and carousel tagging is byte-for-byte unchanged |
| Validation | A consistency script tags a sample twice without persisting and reports per-dimension agreement; dimensions under 80% are repaired or cut before the account is drained |
| Production switch | `VIDEO_TAGGING_ENABLED` stays false. The account is drained with the existing enqueue path after the consistency check passes |

## 1. Taxonomy

`src/cs/tagging/taxonomy.yaml` becomes version `2.0`. Each dimension gains `kind`. Existing image and carousel dimensions are unchanged except that `video` is removed from their `applies_to`.

### Dimension kinds

| kind | Rows per creative | Value source | Extra fields |
|---|---|---|---|
| `numeric` | 1 | Computed in code | none |
| `single` | 1 | Model, enum enforced | `values` |
| `multi` | 0..n | Model, enum enforced, one row per item | `values`, `timestamped: true` for `t_start_s` |
| `ordered` | 0..n | Model, enum enforced, `ordinal` and `t_start_s` per row | `values` |
| `open` | 0..n | Model, free slug, canonicalized | `vocab_hint: 40` (how many existing slugs to show) |
| `text` | 1 | Model, free text | none |

### Layer 0: computed (`numeric`, video)

| Dimension | Definition |
|---|---|
| `duration_s` | ffprobe format duration |
| `cuts_per_10s` | ffmpeg `select='gt(scene,0.3)'` frame count, `(cuts) / duration_s * 10`, rounded to 1 decimal |

`aspect_ratio` stays a `single` dimension but is computed from ffprobe width and height in code, not asked of the model: `9:16`, `4:5`, `1:1`, `16:9`, `other` (nearest within 3% tolerance, else `other`).

Computed rows are inserted with `confidence = 1.0` and `evidence = 'computed'`.

### Layer 1: closed single-value (`single`, video)

| Dimension | Values | Rule in the prompt |
|---|---|---|
| `messenger` | none, creator_on_camera, creator_off_camera, expert_on_camera, founder_on_camera, actor_scripted, multiple_people, graphic_only | `expert_on_camera` only when a credential is stated on screen or in speech. `creator_off_camera` = hands, POV or body without face |
| `narration` | none, on_camera_speech, voiceover, mixed | |
| `captions` | none, subtitles, text_cards, both | subtitles transcribe speech; text_cards are headline overlays |
| `opening_shot` | text_card, face_to_camera, product_in_hand, product_only, scene_broll, screen_recording | What dominates 0–3s |
| `production` | phone_ugc, polished_studio, motion_graphics, mixed_ugc_broll, screen_capture | |
| `setting` | home, outdoor, studio_or_neutral, screen, none | |
| `product_reveal` | first_3s, 3_to_10s, after_10s, never | First moment the product or its packaging is on screen; a generic pill or gummy in hand counts as the product |
| `brand_presence` | none, subtle, prominent | Logo, packaging or brand name on screen |
| `ending` | cta_card, product_shot, speaker_signoff, hard_cut, loop | |
| `music` | none, present | |

### Layer 2: closed multi-value (`multi` and `ordered`, video)

| Dimension | kind | Values |
|---|---|---|
| `hook_devices` | multi, timestamped, window 0–5s | audience_callout, comparison_vs_alternative, warning_or_risk, confession_or_mistake, question, bold_claim, statistic, curiosity_gap, offer_lead, product_demo, result_reveal, listicle_promise, visual_pattern_interrupt |
| `proof_devices` | multi, timestamped | personal_testimonial, expert_credential, study_or_citation, review_screenshot, lab_report_or_certificate, before_after, live_demo, comparison_table, guarantee, press_mention |
| `angles` | multi | symptom_relief, risk_avoidance, natural_alternative, convenience, savings, quality_or_purity, social_proof, novelty, aspiration, identity |
| `offer_elements` | multi, timestamped | discount_percent, discount_amount, free_shipping, bundle, limited_time, guarantee, gift, subscribe_and_save |
| `cta_modes` | multi, timestamped | spoken, on_screen_text, end_card |
| `registers` | multi | fear, relief, humor, warmth, authority, urgency, curiosity, aspiration |
| `beats` | ordered, timestamped | hook, problem, agitation, mechanism, solution_intro, demo, proof, offer, cta, signoff |

An empty list is valid for every multi dimension and stores no rows. `beats` must be non-empty and in ascending `t_start_s`; the validator rejects a run whose beats are out of order.

### Layer 3: open vocabulary (`open`, video)

| Dimension | What it names | Example slugs |
|---|---|---|
| `concept_tags` | The idea, in a strategist's words. 2–6 per video | pharmacist_warning, benadryl_swap, wrong_magnesium_confession, text_over_broll_recommendation |
| `topics` | Subjects discussed | sleep, magnesium, ashwagandha, muscle_spasms |
| `comparison_targets` | Named alternatives the ad positions against | benadryl, melatonin, otc_sleep_aids |
| `audience_callouts` | Who the ad explicitly addresses | benadryl_users, women_group_chat, over_50 |
| `claims` | Specific benefit claims made | fall_asleep_naturally, wake_clear_headed, third_party_tested |
| `visual_motifs` | Recognisable visual elements | kitchen, hand_holding_gummy, pharmacy_coat, lab_report_screenshot |

No upper bound on count except `concept_tags` (6). Each item carries `evidence`.

### Layer 4: text (`text`, video)

| Dimension | Content |
|---|---|
| `hook_verbatim` | Exact first on-screen text plus the first spoken sentence, in order, separated by ` / ` |
| `concept_summary` | Two sentences: what the ad is and what it says |
| `transcript` | Verbatim speech, no timestamps. Empty string when there is no speech |

### Removed for video

tone, claim_specificity, urgency, demographic_signal, color_palette, format_subtype, storyline, text_density, humor_present, face_closeup, people_present, pacing, presenter_type, first_3s_content, product_timing, product_visible, voiceover, caption_style, hook_type, hook_text_present, message_angle, offer_type, proof_type, cta_style, cta_verb, visual_style, layout, hook_summary, message_summary. Each is subjective, computed, or replaced by a dimension above. They remain in the file for image and carousel.

## 2. Model call

`prompts.py` gains a second builder for the v2 video prompt. Rules stated in the system text:

- Timestamps are seconds from the start as a number.
- Hook devices are judged on 0–5s only.
- `expert_on_camera` and `expert_credential` require a stated credential.
- A generic pill or gummy in hand counts as the product for `product_reveal`.
- Beats are the ad's sections in order; every ad has at least `hook`.
- Open slugs are lowercase snake_case, 1–4 words, no brand names in `concept_tags`.
- For each open dimension the prompt lists up to `vocab_hint` existing canonical slugs for the org with their counts, most common first, and says to reuse one when it fits and coin a new one only when none does.
- The transcript is verbatim.

The JSON schema is nested and strict:

```
{
  "single":  { "<dim>": { "value": <enum>, "confidence": number, "evidence": string }, ... },
  "multi":   { "<dim>": [ { "value": <enum>, "t_start_s"?: number, "evidence": string } ], ... },
  "ordered": { "beats": [ { "value": <enum>, "t_start_s": number } ] },
  "open":    { "<dim>": [ { "slug": string, "evidence": string } ], ... },
  "text":    { "hook_verbatim": string, "concept_summary": string, "transcript": string }
}
```

Every enum is enforced in the schema. `Taxonomy.json_schema(fmt)` returns this shape when the format's dimensions carry kinds other than the legacy flat list; image and carousel keep the legacy flat schema untouched.

The model stays `settings.video_tagging_model` (`google/gemini-2.5-flash`). Changing it is a settings change, not a code change.

## 3. Flattening and validation

`Taxonomy.validate(payload, fmt)` for v2 returns a list of row dicts:

`{dimension, kind, value, raw_value, confidence, evidence, t_start_s, ordinal}`

- `single`: one row; value must be in the enum.
- `multi`: one row per item, deduplicated on value; `t_start_s` kept when the dimension is timestamped.
- `ordered`: one row per item with `ordinal` 0..n-1; rejected when `t_start_s` is not non-decreasing or the list is empty.
- `open`: one row per item after canonicalization (§4); `raw_value` is the model's slug, `value` the canonical one; deduplicated on canonical value.
- `text`: one row; the value is the text.
- `numeric` and `aspect_ratio`: appended by the runner from ffprobe and ffmpeg, not from the model.

A run that fails validation is stored as `failed` with the reason, same as today.

## 4. Open-tag canonicalization

New module `src/cs/tagging/vocab.py`.

- `normalize(slug)`: lowercase, non-alphanumerics to `_`, collapse repeats, strip ends, cap at 48 chars.
- `canonicalize(db, org_id, dimension, slugs) -> dict[raw, canonical]`: for each normalized slug, look up `tag_vocab`; if present, use its `canonical_slug`; else insert it as its own canonical and use it.
- `bump_counts(db, org_id, dimension, canonical_slugs)`: increment `count` for the canonical rows used in a finished run.
- `hints(db, org_id, dimension, n)`: top `n` canonical slugs by count for the prompt.

`tag_vocab` table:

```
tag_vocab (
  org_id UUID NOT NULL REFERENCES org(id),
  dimension TEXT NOT NULL,
  slug TEXT NOT NULL,
  canonical_slug TEXT NOT NULL,
  count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, dimension, slug)
)
```

Merging two canonical slugs is a manual SQL update of `canonical_slug` for now, plus an `UPDATE tag SET value = ... WHERE kind = 'open' AND value = ...`. The clustering piece adds embedding-based merge and a review list.

## 5. Computed features

`src/cs/tagging/video.py` gains `probe_video(video: bytes) -> VideoStats(duration_s, width, height, cut_count)`. `cut_count` is the number of frames selected by `ffmpeg -i f -vf "select='gt(scene,0.3)',showinfo" -f null -` (count of `showinfo` lines on stderr). Failure of the cut probe stores `cuts_per_10s` as absent rather than failing the run; failure of ffprobe fails the run, as `probe_duration_ms` does today.

`aspect_ratio` from width and height: nearest of 9:16, 4:5, 1:1, 16:9 within 3%, else `other`.

## 6. Storage

Migration `db/migrations/20260916000003_tag_v2.sql`:

```
ALTER TABLE tag
  ADD COLUMN kind TEXT NOT NULL DEFAULT 'single',
  ADD COLUMN t_start_s REAL,
  ADD COLUMN ordinal INT,
  ADD COLUMN raw_value TEXT;
CREATE TABLE tag_vocab (...);   -- §4
CREATE INDEX tag_vocab_count_idx ON tag_vocab (org_id, dimension, count DESC);
```

Down: drop the index and table, drop the four columns.

`creative_latest_tag` is unchanged. It exposes `dimension, value, confidence` for the newest done run; multi-value dimensions appear as several rows. Pattern insights and the creative filter keep working; a creative can now fall in several cells of one dimension, which is correct for set-valued dimensions.

`repo.finish_tag_run` writes the four new columns. `repo.latest_tags` returns them.

## 7. Runner

`run_tagging` branches on `taxonomy.kind_set(fmt)`: legacy flat path for image and carousel, v2 nested path for video. The video path:

1. Load creative and asset; `probe_video`.
2. Build the prompt with vocabulary hints for each open dimension.
3. One tagger call with the video file and the nested schema.
4. `validate` → rows; canonicalize open rows; append computed rows.
5. `finish_tag_run`; `bump_counts`.

`prompt_sha` covers the system text, the user text (which includes the hints, so it changes as the vocabulary grows) and the schema, as today.

## 8. API and UI

- `GET /tags/dimensions` adds `kind` to each dimension.
- `GET /creatives/{id}/tags` returns the new columns; `transcript` rows are excluded from this endpoint and served by a new `GET /creatives/{id}/transcript`.
- `tag-list.tsx`: multi-value rows render as one badge each under the dimension label; `ordered` renders `beats` as an arc string `hook → problem → …` with the seconds; `open` renders as chips without a confidence percentage; `text` shows `hook_verbatim` and `concept_summary` as two short paragraphs; `numeric` rows render as `dimension: value`.
- The creative filter's value list for an `open` dimension comes from `tag_vocab` canonical slugs ordered by count, via `GET /tags/dimensions/{name}/values`.

## 9. Consistency check

`scripts/tag_consistency.py --org <id> --n 12 --seed 1`: picks `n` video creatives with a primary asset, runs the v2 tagger twice per creative in memory, and prints per-dimension agreement:

- `single`, `numeric`: share of creatives where both runs agree.
- `multi`, `open`: mean Jaccard of the two sets (open sets compared after normalization).
- `ordered`: share where the beat sequence is identical, plus mean normalized edit distance.
- `text`: not scored.

Nothing is persisted. The report is pasted into the PR. Dimensions below 0.8 on `single` or 0.6 Jaccard on `multi` are repaired (prompt rule or values) or dropped before the account drain.

## 10. Rollout

1. Migration applied to the primary DB and deployed with the code.
2. The four insight videos on org 3 are re-tagged under v2 by enqueueing `tagging.tag_creative` (the `UNIQUE (creative_id, taxonomy_version_id, model)` on `tag_run` allows it because the taxonomy version changed).
3. Consistency check on 12 org 3 videos; repair; re-run.
4. Drain org 3's 573 videos through the existing enqueue path with `VIDEO_TAGGING_ENABLED` left false, so ingest of new videos stays off until the user flips it.

## 11. Testing

- `tests/tagging/test_taxonomy.py`: v2 file loads; kinds parse; `json_schema('video')` nests and enforces every enum; `json_schema('image')` is unchanged from today's output; `validate` flattens each kind; beats out of order reject; open dedup on canonical.
- `tests/tagging/test_vocab.py`: normalize cases; canonicalize inserts new and reuses existing; hints order.
- `tests/tagging/test_video.py`: `probe_video` on a synthetic ffmpeg clip (the existing fixture pattern) with two distinct scenes returns duration, dimensions and a cut count of 1; aspect mapping table.
- `tests/tagging/test_runner.py`: video path with a fake tagger writes computed rows, canonical open rows, ordered beats; image path unchanged.
- `tests/tagging/test_repo.py`: new columns round-trip; `latest_tags` returns multi rows.
- API tests for the transcript endpoint and open values endpoint.
- Web: there is no component test harness; `tag-list` is checked by rendering a v2-tagged creative in the running app.

## Out of scope

The clustering job, feature-matrix builder, embedding client and embedding-based vocabulary merge, image and carousel migration to the new layers, and changes to pattern-insight statistics for set-valued dimensions.
