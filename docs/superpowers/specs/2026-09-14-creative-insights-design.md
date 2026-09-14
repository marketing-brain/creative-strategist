# Creative Insights: Record, Claims, Test Briefs

**Date:** 2026-09-14
**Status:** Design — approved in brainstorming, pending spec review (revised to the model-first version)
**Scope:** A per-creative insights system that is useful both to read and to feed AI generation: an objective, timestamped *creative record* of each ad; *claims* that tie record elements to diagnosis metrics; *test briefs* a generator can execute; and a new Insights section on the creative page. Builds on the diagnosis panel (`2026-09-12-creative-diagnosis-panel-design.md`) without changing it.

## Problem

Today each creative gets taxonomy tags and, since `202a2d9`, one free-text insight paragraph (`src/cs/insights/creative_insight.py`). Running that on real ads (org 3, 2026-09-14) showed the limits:

- **Tags are subjective and not generative.** `tone=warm` or `hook_type=question` cannot be executed by a generator and cannot be checked against the ad. The tagging model disagreed with a stronger model on 3 of 24 dimensions, all subjective visual ones.
- **The paragraph is not machine-usable.** Its "next test" is prose. Generation cannot execute "recut 00:00–00:03 to open on the gummy"; it has no shot boundaries, text layers or asset references.
- **Single-ad explanations are guesses.** Video 2 ("The magnesium that actually absorbs…") got "conversion is the weakest layer… a trust gap" when the data shows a trade-off: +14% CTR sending more, less-qualified clicks, the same purchases as its variant (465 vs 464) and a 14% better CPA.
- **Near-duplicates go unnoticed.** Videos 1 and 2 share footage (a natural experiment) and nothing links them.
- **Model timestamps are coarse by default.** Gemini samples video at 1 fps; cut positions can be off by about ±1 s. Sampling at 4 fps brings that to about ±250 ms, which is enough to read and brief from; frame-exact cuts are a generator's job at render time.

## Decisions taken

| Decision | Choice |
|---|---|
| Primary consumer | AI generation; people read the same objects |
| Generation output | Both edits of existing ads (real footage/images) and net-new concepts from reusable elements |
| Learning loop | Not now. Claims carry `status = untested`; the slot exists for later |
| Model coverage | Stay capped (`creative_insight_limit_per_format`, 2 per format per org) while record quality is validated |
| Approach | A: record (multimodal, once) → claims (text-only, refreshable) → paragraph (rendered from claims) |
| Division of labour | **Model-first.** Gemini produces the whole record (shots, text, speech, timings at 4 fps). Code does arithmetic, evidence strength, validation, near-duplicate hashing and history. No Whisper, no PySceneDetect; precision tooling is added only when a generator needs frame-exact cuts |
| Deterministic steps | ffprobe media facts and frame hashes (ffmpeg + PDQ) run on **all** creatives (no model cost) |
| Refresh | Record once per extractor version. Claims and paragraph regenerate on a material diagnosis change, at most once per day per creative |
| Funnel panel | **Untouched.** `diagnosis.py` and `diagnosis-panel.tsx` are not modified by this project. The same-length retention baseline shipped in `202a2d9` stays |
| Trade-off reading | Lives in the claims layer, not in the diagnosis. Where they differ, the Insights section says so explicitly |
| UI | Option D: a separate Insights section above the unchanged diagnosis panel |
| Tags | Taxonomy tagging, pattern insights and tag filters stay as they are. Derived facets are stored but do not replace tags until coverage expands and facets are validated |
| Models | `google/gemini-3-flash` for record extraction, claims and paragraph. No Claude calls (standing rule) |

## Architecture

Four layers, each independently useful:

1. **Facts** (code): metrics, baselines, the existing diagnosis. Read-only here.
2. **Record** (model): what is in the ad, with times (±250 ms) and positions.
3. **Claims** (model proposes, code validates and grades): element ↔ metric links with evidence basis, strength, confounds and a test.
4. **Test briefs** (code): each claim test rendered as a validated JSON contract for generators.

```
asset download ──► media.hash (all) ──► variants.link (all)
                         │
                         ├─(capped)─► record.extract ──► claims.derive ──► insight.render
                         │                                     ▲                ▲
nightly sweeps ──────────┘                diagnosis (existing) ┘  variant evidence ┘
```

## 1. Media facts and frame hashes (code, all creatives)

Job `media.hash`, per creative with a stored primary asset, all formats. No model calls.

- **ffprobe:** `duration_ms`, `fps` (stored as float), `width`, `height`, `aspect_ratio`. Updates `asset.duration_ms` if null.
- **Frame hashes (video):** ffmpeg samples one frame per second (`fps=1`); each frame gets a PDQ hash (`pdqhash`) with its `t_ms`. This is the vPDQ approach and needs no shot detection.
- **Images:** one PDQ hash. **Carousels:** one per card, with `card_index`.

## 2. Creative record (model, capped)

Job `record.extract`. Inputs to Gemini, media first and text last: the video file sampled at 4 fps (or the image / card images); ffprobe facts (duration, size); the Meta copy (headline, primary text, CTA, link). The model produces the whole record, including shot boundaries, transcript timings and text boxes. Output is one JSON document validated against a strict schema. After extraction, code extracts a keyframe at each shot's midpoint (`{org}/creatives/{creative}/shots/{shot_id}.jpg`) for the UI and test briefs.

### Common fields

| Field | Content |
|---|---|
| `extractor_version`, `sampling_fps`, `timing_precision_ms` | Provenance; `timing_precision_ms = 1000 ÷ sampling_fps` (250 at 4 fps) |
| `format`, `duration_ms`, `fps`, `width`, `height`, `aspect_ratio` | From ffprobe, not the model |
| `summary` | One objective sentence |
| `offer` | `[{id, verbatim, type: discount_percent\|discount_amount\|guarantee\|free_shipping\|bundle\|free_trial\|gift\|other, source: {kind: text_layer\|words\|copy, ref}}]` |
| `cta` | `[{id, verbatim, source}]` |
| `promises` | `[{id, verbatim, source}]`: product promises quoted exactly |
| `presence` | `[{id, kind: product\|packaging\|logo\|brand_name_text\|brand_name_spoken\|person, label, intervals: [{start_ms, end_ms, prominence: primary\|secondary\|background, bbox?}]}]` |
| `people` | `[{id, talent_type: creator\|customer\|founder\|expert\|actor\|unknown, celebrity: bool, direct_to_camera: bool, speaking: bool, intervals, product_interaction_intervals}]` |
| `music` | `[{start_ms, end_ms}]` intervals where music is present |

### Video

- `shots`: `[{id, in_ms, out_ms, keyframe_ref, pdq_hash, description, camera: selfie_handheld\|tripod\|screen_recording\|pov\|b_roll\|other, camera_note, setting, subject_bbox, transition_in: cut\|jump_cut\|dissolve\|other, speech_crosses_out: bool}]`. `in_ms`/`out_ms` come from the model (normalized by code, see Validation); `keyframe_ref` and `pdq_hash` are added by code after extraction; `speech_crosses_out` is computed by code from `words`.
- `beats`: `[{id, role: hook\|problem\|demo\|proof\|offer\|cta\|other, shot_ids}]`, contiguous, covering every shot exactly once.
- `text_layers`: `[{id, shot_ids, start_ms, end_ms, timing_source: shot\|model, verbatim, kind: caption\|super\|sticker\|ui, bbox, style: {weight: regular\|bold\|black, case: upper\|lower\|title\|mixed, color_hex, stroke_hex, background: none\|box\|highlight, background_hex, alignment: left\|center\|right}, animation: static\|word_by_word\|pop\|scroll\|unknown}]`. `timing_source = shot` when the text spans whole shots, `model` when it appears or disappears mid-shot.
- `words`: the model's transcript as `[{w, start_ms, end_ms, speaker: voiceover\|on_camera\|unknown}]`. Timings share `timing_precision_ms`.

### Image

- `regions`: `[{id, layer_type: text\|product\|packaging\|person\|background\|sticker\|logo\|ui, bbox, z_order, content, verbatim?, style?, preserve: [face\|packaging_label\|ui_chrome]}]`.
- `focal_region_id`, `setting`, `aesthetic: lofi_photo\|studio\|story_screenshot\|graphic\|meme\|other`.

### Carousel

- `cards`: `[{card_index, image_record}]` where `image_record` is the image section above.

### Conventions

- `bbox` is `[x0, y0, x1, y1]` normalized to 0–1 (Gemini's `box_2d` 0–1000 is converted in code).
- Every element has an `id` unique within the record (`s1`, `b1`, `t1`, `r1`, `p1`, `o1`, …).

### Validation (code; failure → record `rejected` with problems listed)

- IDs unique; every reference (`shot_ids`, `source.ref`, `focal_region_id`) resolves.
- Shots are ordered; code normalizes boundaries: the first shot starts at 0, the last ends at `duration_ms`, and gaps or overlaps between neighbours of up to `timing_precision_ms` are closed at the midpoint. Larger gaps or overlaps reject the record.
- Beats cover every shot exactly once, in order.
- All times within `[0, duration_ms]`; `start_ms < end_ms`; bboxes within 0–1 with positive area.
- Enum values valid.
- **Internal consistency:** caption text layers must match the transcript words over the same time range (normalized: case, punctuation, whitespace, ±`timing_precision_ms`); offer/CTA/promise sources of kind `copy` must appear verbatim in the Meta copy. Violations are listed as problems; a record with more than 20% of captions mismatched is rejected. Accuracy against the ad itself is covered by the gold-set review (§11).

## 3. Variants (deterministic, all creatives)

Job `variants.link`, after `media.hash`, scoped to the ad account.

- Two frames match when their PDQ Hamming distance is ≤ 31.
- **Shared footage:** for creatives A and B, `shared_ms` = 1000 × the number of A's sampled frames that match any frame of B.
- **Video variant pair:** `shared_ms` ≥ 50% of the shorter video's duration (checked in both directions, the larger share kept).
- **Image / carousel variant pair:** image (or any card) hash distance ≤ 31.
- Stored as `creative_variant(creative_a, creative_b, shared_ms, shared_share)` with `creative_a < creative_b`.
- The variant diff available without records: the order in which matched frames appear in each ad. With both records: shot order, text layer and presence differences, by mapping matched frames to shots.

## 4. Claims (text-only model call, capped)

Job `claims.derive`. Inputs: the record; the existing `Diagnosis` plus `DiagnosedCreative` sums (from `diagnose_creative`); for each variant pair, the variant's diagnosis metrics and the variant diff; the efficiency facts below. Output: up to 3 claims.

### Efficiency facts (code, added to the input; the diagnosis is not changed)

- `cpa`, `cpa_baseline`, `cpa_better`: from the conversion tier's CPA metric.
- `purchases_per_1k_impressions` for the creative and baseline.
- `tradeoff_candidate`: true when the conversion tier status is `weak` **and** CPA is not worse than baseline (signed CPA lift ≥ 0).

### Claim schema

```
{
  id, kind: strength | weakness | tradeoff,
  layer: attention | retention | conversion | fatigue,
  metrics: [{tier_key, metric_key | step_key}],          // must resolve in the diagnosis
  elements: [record element ids],                         // must resolve in the record
  mechanism: string,                                      // one sentence, the hypothesis
  basis: single_creative | variant_pair | account_pattern, // set by code from the inputs used
  variant_creative_id?: uuid,                             // when basis = variant_pair
  strength: weak | moderate | strong,                     // set by code
  confounds: [click_pool_dilution | placement_unknown | audience_unknown |
              budget_concentration | learning_phase | seasonality | other],
  keep: [record element ids],
  test: {
    type: edit | concept,
    edit_ops?: [op],                                      // see §6
    concept?: {reuse: [element ids], change: string, constraints: [string]},
    hypothesis: string,
    primary_metric: {tier_key, metric_key | step_key},
    guardrail_metric: {tier_key, metric_key | step_key, max_regression_pct: number}
  },
  status: untested
}
```

### Strength (code)

| basis ↓ / evidence tier of the claim's layer → | directional | reliable |
|---|---|---|
| single_creative | weak | moderate |
| variant_pair | moderate | strong |
| account_pattern | not emitted while coverage is capped | not emitted while coverage is capped |

Evidence tier uses the existing `evidence_tier()` in `creative_insight.py`.

### Rules (code; violating claims are dropped, and a set with zero valid claims is `rejected`)

- `kind` must be consistent with the layer's diagnosis status: `strength` needs `strong`; `weakness` needs `weak`; `tradeoff` needs `tradeoff_candidate`, or a variant pair where this creative's lift over the variant is ≥ +15% on one metric of the layer and ≤ −15% on another. Layers with an unmeasured status get no claims.
- When `tradeoff_candidate` is true, a `weakness` claim on conversion is rejected.
- Every claim has at least one `keep`; test edit ops never target a `keep` element.
- `primary_metric` and `guardrail_metric` resolve; the guardrail differs from the primary.
- `variant_pair` basis requires a stored variant and at least one metric cited from both creatives.
- The set contains at most 3 claims.

## 5. Paragraph (text-only model call)

Job `insight.render`. Input: the validated claims plus the metric values they cite. Output: one 90–150 word paragraph. The existing grounding check (`grounding_problems`) runs against a brief built from the cited values; retry once, then `rejected`. `creative_insight` gains `claim_set_id` and `divergence_note`.

**Divergence note (code, deterministic):** when a claim's `kind` differs from the funnel's reading of that layer (e.g. tier `weak`, claim `tradeoff`), the note states both: "The funnel marks Conversion weak (checkout −38%). Overall efficiency is better (CPA $47.59 vs $55.15)."

## 6. Test brief contract

`GET /creatives/{id}/claims/{claim_id}/brief` returns one validated JSON document; "Copy test brief" in the UI copies it. `brief_version = 1`.

```
{
  brief_version, creative_id, claim_id, type: edit | concept,
  source: {format, duration_ms, fps, width, height, asset_ref, timing_precision_ms,
           shots: [{id, in_ms, out_ms, keyframe_ref}], text_layers: [...], regions: [...], words: [...]},
  hypothesis, primary_metric, guardrail_metric,
  keep: [{id, kind, verbatim_or_description}],
  edit_ops: [op],
  concept: {reuse: [{id, kind, verbatim_or_description}], change, constraints},
  brand_rules: {prohibited_claims, colors, logo_ref}
}
```

### Edit ops (closed list)

| Op | Formats | Params |
|---|---|---|
| `move_shot` | video | `target` shot, `before` shot id or `end` |
| `trim_shot` | video | `target` shot, `in_ms`, `out_ms` within the shot |
| `remove_shot` | video | `target` shot |
| `insert_shot` | video | `source_shot` (a shot id in any record in the org), `before` shot id or `end` |
| `replace_text` | video, image | `target` text layer or text region, `verbatim` |
| `remove_text` | video, image | `target` |
| `add_text` | video, image | `verbatim`, `start_ms`/`end_ms` (video), `bbox`, `style_from` (an existing text element id) |
| `recrop` | video, image | `aspect_ratio`, `focus` element id |
| `remove_region` | image | `target` region |
| `restyle_region` | image | `target` region, `instruction` (one sentence), `preserve` flags inherited |

Cut points in a brief carry `timing_precision_ms`; a generator refines them to exact frames at render time (out of scope). Validation at brief build: targets resolve; no op touches `keep` elements or `preserve`-flagged regions; times within duration; `insert_shot` sources exist in the same org; new `verbatim` text passes the brand kit's prohibited-claims check.

## 7. Facets (derived, stored, not yet replacing tags)

Computed in code from a record; each row stores the element IDs that justify it.

| Facet | Definition |
|---|---|
| `has_offer`, `offer_type` | From `offer` |
| `product_first_seen` | `0-3s` / `3-10s` / `>10s` / `never` from `presence` kind product or packaging |
| `brand_on_screen_share` | Union of logo, packaging and brand_name_text intervals ÷ duration |
| `text_words_per_second` | On-screen words ÷ duration |
| `cuts_per_10s` | (shots − 1) ÷ duration × 10 |
| `has_voiceover` | Any word with `speaker = voiceover` |
| `direct_to_camera_share` | Direct-to-camera person intervals ÷ duration |
| `dominant_talent_type` | Talent type with the longest intervals |
| `first_beat_role` | Role of the first beat |
| `aesthetic` | Image and carousel records |

## 8. Storage

New migration `db/migrations/20260915000001_creative_insights_v2.sql`.

| Table | Columns |
|---|---|
| `creative_media` | `creative_id` PK, `org_id`, `status` (`done`/`failed`), `duration_ms`, `fps`, `width`, `height`, `error`, `created_at` |
| `creative_frame` | `creative_id`, `org_id`, `t_ms` (`card_index` × 1000 for carousels, 0 for images), `pdq_hash` BIT(256), PK (`creative_id`, `t_ms`) |
| `creative_variant` | `org_id`, `creative_a`, `creative_b`, `shared_ms`, `shared_share`, PK (`creative_a`, `creative_b`) |
| `creative_record` | `id` PK, `creative_id`, `org_id`, `extractor_version`, `status` (`done`/`rejected`), `record` JSONB, `problems` JSONB, `model`, `input_tokens`, `output_tokens`, `created_at`; unique (`creative_id`, `extractor_version`) |
| `creative_claim_set` | `id` PK, `creative_id`, `org_id`, `record_id`, `diagnosis_signature`, `status` (`done`/`rejected`), `claims` JSONB, `problems` JSONB, `model`, tokens, `created_at` |
| `creative_facet` | `creative_id`, `record_id`, `facet`, `value`, `element_ids` JSONB, PK (`creative_id`, `record_id`, `facet`) |
| `creative_insight` (existing) | add `claim_set_id` (nullable FK), `divergence_note` (nullable text) |

`diagnosis_signature` = SHA-256 of each tier's status and evidence tier, plus the sorted variant creative IDs.

## 9. Jobs and scheduling

| Job | Trigger | Scope |
|---|---|---|
| `media.hash` | Chained after asset download; nightly sweep for creatives with an asset and no `creative_media` row (limit 500/night) | All |
| `variants.link` | Chained after `media.hash` | All |
| `record.extract` | Nightly sweep: highest 30-day spend first, readable diagnosis, media done, within `creative_insight_limit_per_format` counted on `creative_record` rows | Capped |
| `claims.derive` | Chained after `record.extract`; nightly when `diagnosis_signature` changed and the last claim set is ≥ 24 h old | Creatives with a done record |
| `insight.render` | Chained after `claims.derive` | Same |

- All run on the single designed worker; max 3 attempts; dead jobs are not re-enqueued by sweeps (`queue.is_dead`).
- Once `insight.render` ships, the current `insights.creative_insight` job and its nightly sweep are retired. Existing `creative_insight` rows without `claim_set_id` stay visible until a claim-based paragraph replaces them.
- The video-length gate in the current runner is no longer needed: `media.hash` provides duration for every creative. The existing `assets.probe_video_duration` job stays until `media.hash` has run on every video, then is retired.

## 10. UI (Option D)

New component `web/src/components/creative-insights.tsx`, placed at the top of the right column on the creative page, above the unchanged `DiagnosisPanel`. It replaces the current `CreativeInsightCard` (`creative-insight.tsx`), and `GET /creatives/{id}/insight` is retired with it. Endpoint `GET /creatives/{id}/insights`.

- **Summary:** the paragraph, with the divergence note (amber) when present.
- **Claim cards:** kind badge, title from `mechanism`, layer label, basis and strength, cited metric values, `keep` elements, and for the test: human-readable edit ops (e.g. "Move shot 3 (hand + beadlet, 00:06–00:09) to the start"), primary metric, guardrail, and "Copy test brief".
- **Timeline** (video): rows for beats, shots, text layers, speech, brand/packaging, product; clicking a block seeks the player; hovering a claim highlights its elements. Images: the image with region boxes; carousels: cards in order.
- **Variants:** ads sharing footage, with shared share, shot order, hook rate, link CTR, CPA, purchases (from each creative's diagnosis window).

States: media only → variants shown, "Insights not available yet"; record without claims → timeline shown; rejected record or claim set → section hidden except variants.

## 11. Quality and evaluation

**Gold set:** the capped records, starting with the four creatives that already have paragraphs. `scripts/review_record.py <creative_id>` writes a local HTML review page (shot keyframes with bboxes drawn, timeline, transcript beside the video) and a JSON file of editable fields; corrections are saved to `tests/evals/records/<creative_id>.json`. `scripts/eval_records.py` scores an extractor version against the gold set.

**Targets (on the gold set):**

- Shot boundaries within ±500 ms of a real cut: ≥ 90% of cuts; no missed cut longer than 1 s.
- Text layers: exact normalized verbatim match ≥ 90%; bbox IoU ≥ 0.5 with the gold box ≥ 80%.
- Transcript: exact normalized match for every offer, CTA and promise sourced from `words`.
- Presence intervals: start and end within ±500 ms.
- Claims: 0 citing a missing ID or metric; 100% of test briefs validate.
- Human rubric per claim: mechanism plausible (yes/no); test executable and not contradicting its own `keep` (yes/no).
- A new `extractor_version` replaces the current one only if it scores at least as well on every target.

## 12. Errors

- Each job fails independently; a dead job never blocks other creatives.
- Unreadable media (ffprobe or ffmpeg error) → `creative_media.status = failed` with the error; no further steps for that creative.
- Record or claim-set validation failure → stored `rejected` with problems; never shown; not retried automatically (a new extractor version or a manual re-run can supersede it).

## 13. Testing

- **Variant fixture:** an ffmpeg-built video from three test clips, and a second video with the same clips reordered and one replaced; `variants.link` must pair them with the expected `shared_ms`, and must not pair either with an unrelated clip.
- **Unit:** record validator (each rule, including boundary normalization), bbox conversion, facet derivation, claim rules and strength table, divergence note, edit-op validation, brief assembly, PDQ matching thresholds.
- **Fakes:** Gemini record extractor, claims writer, paragraph writer.
- **DB:** job chain from asset to insight (`media.hash` → `variants.link`; `record.extract` → `claims.derive` → `insight.render`); sweeps respect caps, `is_dead`, and the 24 h claim refresh; variants link across an account.
- **API:** `/insights` states and cross-org 404; `/brief` validation.
- **Web:** `creative-insights.tsx` states (media only, record only, full, rejected), claim hover highlighting, copy brief. `diagnosis-panel` tests unchanged.

## 14. Rollout

1. Media facts, frame hashes and variants for all creatives (no model cost). The Insights section ships in its "media only" state, showing the variants table.
2. Records for the capped set, seeded with the four creatives that already have paragraphs (they count toward the cap); the sweep fills any remaining slots.
3. Hand review of those records; gold labels saved; targets checked.
4. Claims, paragraph, briefs and the full Insights section; retire the current `insights.creative_insight` job.

## Verify before implementation planning

- Gemini 3 Flash through AI Gateway's OpenAI-compatible `file` part accepts a sampling rate (4 fps). If it cannot be set, the record is extracted at the default 1 fps with `timing_precision_ms = 1000`, and the shot-boundary target in §11 is re-checked on the gold set before claims ship.
- `pdqhash` builds and runs on Amazon Linux 2023.

## Out of scope

- Generators (timeline rendering, image editing), frame-exact cut refinement, and any publish step.
- Deterministic extraction tooling (Whisper word alignment, PySceneDetect/TransNetV2 shot detection, OCR). Added only when a generator needs precision the model does not give.
- The learning loop (matching launched generated ads, claim status updates).
- Replacing taxonomy tags, pattern insights or tag filters with facets.
- `account_pattern` claims and model extraction beyond the cap.
- Any change to `diagnosis.py`, `diagnose_creative`'s outputs, or `diagnosis-panel.tsx`.
