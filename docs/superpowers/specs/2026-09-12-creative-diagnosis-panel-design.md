# Creative Diagnosis Panel

**Date:** 2026-09-12
**Status:** Design — approved for implementation planning
**Scope:** Per-creative diagnostic funnel (attention → retention → conversion → fatigue → context) on the creative detail page, plus the ingest and modeling it needs. Account-level overview is a later sub-project that reuses the tier computation defined here.

## Problem

The creative detail page shows a CTR/CPA line and tags. It never says *which layer of the creative is broken* or what to edit. The product thesis (see the metrics North Star) is that every metric earns its place only by isolating one of five diagnostic tiers, each mapped to an editable creative decision:

| Tier | Question | Decision it unlocks |
|---|---|---|
| 1 Attention | Is the opening 1–3s stopping the scroll? | Recut the thumbnail / hook |
| 2 Retention | Where in the video does attention drop? | Fix pacing, a bad cut, a late CTA |
| 3 Conversion | Which funnel step under-delivers, spend-normalized? | Landing page vs creative promise mismatch |
| 4 Fatigue | Is this creative wearing out? | Refresh or rotate before efficiency drops |
| 5 Context | Does it need a native recut per placement? | Reels vs Feed recut |

Audit of production data on 2026-09-12 (org `01a089c9…`, schema `org_01a089c9_231e_7000_bdaa_39277b4cc94b__meta_ads`):

- Tiers 1–4 have **real, ad-level, daily** raw data already landed by Fivetran: `basic_ad` (impressions, reach, frequency, link clicks, spend), `basic_ad_actions` (3s `video_view`, `landing_page_view`, `view_content`, `add_to_cart`, `initiate_checkout`, `purchase`, `post_reaction`, `comment`, `onsite_conversion.post_save`, …), and a working custom report `video_retention_ad` with child tables `video_retention_ad_video_{play,p_25_watched,p_50_watched,p_75_watched,p_95_watched,p_100_watched,thruplay_watched,avg_time_watched}_actions` (6,951 ad-days, 732 ads, Aug 13 – Sep 12; counts decrease monotonically p25 → p100).
- Tier 5 has **no** placement breakdown table anywhere.
- Our `metric_daily` table has **0 rows** for every org. The `video_thruplay/p50/p100` columns are hardcoded to 0 in ingest; step conversions, reach and engagement are never folded.

So the work is: widen ingest, model the tiers deterministically, serve one endpoint, render one panel. No proxies: Context renders as an honest locked state.

## Decisions taken

| Decision | Choice |
|---|---|
| Placement | Creative detail page, above "Performance (90 days)". Overview page later, same tier function |
| Empty tiers | Real data for 1–4; Context shows "Not measured" with the reason. No proxies anywhere |
| Row content | Verdict + evidence: status pill, key ratio vs baseline with lift, decision line when weak |
| Compute location | Widen `metric_daily`, compute on read (Approach 1). No snapshot table, no new job |
| Window | Trailing 30 days ending today (server UTC date) |
| Baseline | Pooled sums of every *other* creative in the same ad account with the same `format`, same window |
| Thresholds | Lift ±15% (reuses `patterns.py` `lift_threshold`) with per-tier volume floors |
| Verdict text | Deterministic templates. No LLM call (standing rule: no new Claude calls; nothing here needs generation) |
| Images/carousels | Retention = `not_applicable`; Attention uses link CTR |

## 1. Data model

New migration `db/migrations/20260912000001_diagnosis_metrics.sql` adds to `metric_daily` (all `NOT NULL DEFAULT 0`; counts `BIGINT`, seconds `NUMERIC(14,4)`):

| Column | Source | Tier |
|---|---|---|
| `reach BIGINT` | `basic_ad.reach` | 4 |
| `video_plays BIGINT` | `…video_play_actions.value` | 2 (curve denominator) |
| `video_p25 BIGINT`, `video_p75 BIGINT`, `video_p95 BIGINT` | `…video_p_NN_watched_actions.value` | 2 |
| `video_watch_seconds NUMERIC(14,4)` | `avg_time_watched.value × video_plays` (made additive; avg = Σsec ÷ Σplays) | 2 |
| `landing_page_views BIGINT` | actions | 3 |
| `content_views BIGINT` | actions | 3 |
| `add_to_carts BIGINT` | actions | 3 |
| `checkouts BIGINT` | actions | 3 |
| `purchases BIGINT` | actions | 3 |
| `post_reactions BIGINT`, `comments BIGINT`, `saves BIGINT` | actions | 4 |

Existing `video_3s_views`, `video_thruplay`, `video_p50`, `video_p100`, `conversions`, `conversion_value` keep their meaning. `conversions` stays the CPA/ROAS numerator (may be leads); `purchases` is purchase-only for the step chain.

`MetricRow` (`connectors/base.py`) gains the same fields with defaults of 0 so existing constructors keep working. `upsert_metrics` writes them.

Frequency is not additive across days. We store daily `reach` and define **window frequency = Σimpressions ÷ Σdaily reach**, i.e. the impressions-weighted mean of daily frequency. The UI labels it "daily frequency".

## 2. Ingest (`src/cs/ingest/fivetran/insights.py`)

- `schema.py` adds `VIDEO_RETENTION_AD = "video_retention_ad"` and `VIDEO_RETENTION_CHILD_TABLES` (the eight child suffixes → column mapping).
- Action-type preference lists (first match wins, `omni_*` preferred because Meta dedups across channels there):

```python
LANDING_PAGE_VIEW = ("omni_landing_page_view", "landing_page_view")
CONTENT_VIEW = ("omni_view_content", "view_content", "offsite_conversion.fb_pixel_view_content")
ADD_TO_CART = ("omni_add_to_cart", "add_to_cart", "offsite_conversion.fb_pixel_add_to_cart")
CHECKOUT = ("omni_initiated_checkout", "initiate_checkout", "offsite_conversion.fb_pixel_initiate_checkout")
PURCHASE = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
POST_REACTION = ("post_reaction",); COMMENT = ("comment",); SAVE = ("onsite_conversion.post_save",)
VIDEO_3S = ("video_view",)
```

- `sync_insights` reads `basic_ad.reach` (column may be absent in old fixture schemas → treat as 0; production has it).
- Retention: if `video_retention_ad` exists in the schema (`information_schema.tables` check), aggregate each child table `SELECT ad_id, date, sum(value) … WHERE action_type='video_view' GROUP BY 1,2` into a `{(ad_id, date): {...}}` map and fold into the row. If absent, every retention column is 0 and the summary reports `retention_tables: false`. Org 1's schema has no retention tables today, so this branch is exercised in production.
- `video_watch_seconds = avg_time_watched × plays` at fold time.
- The uncommitted working-tree change that folds `video_view` into `video_3s_views` is subsumed here.

## 3. Diagnosis model (`src/cs/insights/diagnosis.py`)

Pure functions, no I/O. Reusable by a future overview job.

```python
@dataclass(frozen=True)
class Sums:  # additive window totals
    impressions: int; reach: int; link_clicks: int; spend: Decimal
    video_3s_views: int; video_plays: int; video_p25: int; video_p50: int
    video_p75: int; video_p95: int; video_p100: int; video_thruplay: int
    video_watch_seconds: Decimal
    landing_page_views: int; content_views: int; add_to_carts: int; checkouts: int; purchases: int
    post_reactions: int; comments: int; saves: int
    conversions: Decimal; conversion_value: Decimal

Status = Literal["strong", "on_par", "weak", "not_applicable", "not_enough_data", "too_young", "not_measured"]

def diagnose(*, fmt: str, window: Sums, baseline: Sums, baseline_creatives: int,
             first14: Sums | None, trailing7: Sums | None, days_live: int | None,
             duration_ms: int | None) -> Diagnosis
```

Output shape (also the API JSON):

```
Diagnosis {
  window_start, window_end, baseline_creatives, format,
  broken_tier: 1..5 | null,
  verdict: { kind: "broken" | "healthy" | "too_early", headline, detail },
  tiers: [ Tier × 5 ]
}
Tier { tier, key: "attention"|"retention"|"conversion"|"fatigue"|"context",
       name, question, status, decision: str | null, reason: str | null,
       metrics: [ Metric ], curve: Curve | null, steps: [ Step ] | null }
Metric { key, label, value: float|null, baseline: float|null, lift: float|null, note }
Curve  { points: [{ p: 0|25|50|75|95|100, share: float, baseline_share: float }],
         drop_segment: [p_from, p_to] | null, avg_watch_s, duration_s | null }
Step   { key, label, rate: float|null, baseline_rate: float|null, lift: float|null,
         numerator: int, denominator: int, readable: bool }
```

`lift = signed_lift(...)` from `patterns.py` (already sign-flips cost metrics). Status from lift: `≥ +0.15 → strong`, `≤ −0.15 → weak`, else `on_par`; `None` lift → `not_enough_data`.

### Tier rules

**1 Attention.** Video: primary = hook rate (`video_3s_views ÷ impressions`), secondary = link CTR (`link_clicks ÷ impressions`). Image/carousel: primary = link CTR only. Floor: 1,000 impressions on the creative *and* on the baseline. Status from the primary metric. Decision when weak: "The first three seconds are not stopping the scroll. Recut the opening or swap the thumbnail; keep the rest."

**2 Retention.** `fmt != "video"` → `not_applicable` with reason. Primary = hold rate (`video_thruplay ÷ video_3s_views`). Curve shares = `video_pNN ÷ video_plays` (p0 = 1.0). Floor: 100 plays. `drop_segment` = the consecutive pair `(p_i, p_{i+1})` with the largest `(creative_share_i − creative_share_{i+1}) − (baseline_share_i − baseline_share_{i+1})`, only reported when status is weak. If `duration_ms` is known, the decision names the second range (`p × duration`). Decision when weak: "Attention drops between {p_from}% and {p_to}% ({s_from}s–{s_to}s). Fix pacing or the cut there, or move the CTA earlier."

**3 Conversion.** Steps in order: `link_clicks → landing_page_views → content_views → add_to_carts → checkouts → purchases`; each rate = this ÷ previous. A step is `readable` when its denominator ≥ 100 for both creative and baseline; once a step is unreadable, all later steps are unreadable. Status: `not_enough_data` if no step readable; `weak` if any readable step lift ≤ −0.15; `strong` if end-to-end (`purchases ÷ link_clicks`, readable) lift ≥ +0.15 and no step weak; else `on_par`. Decision when weak names the worst step: "People drop at {step}. Check that the landing page delivers what the creative promises."

**4 Fatigue.** Needs `days_live` (days since the creative's first day with impressions, not window-limited). `days_live < 21 → too_young`. Compare `first14` (first 14 live days) vs `trailing7` (last 7 days ending today); floor 1,000 impressions in each, else `not_enough_data`. Metrics: frequency (`Σimp ÷ Σreach`), hook rate for video / link CTR otherwise, engagement rate (`(post_reactions + comments + saves) ÷ impressions`). Lifts are trailing vs first (frequency lift is *not* sign-flipped; it is reported raw). Status `weak` ("Early decay" in UI) when frequency rose ≥ +30% **and** (hook/CTR fell ≤ −15% **or** engagement fell ≤ −15%); otherwise `on_par` ("Stable"). Never `strong`. Decision when weak: "Frequency is climbing faster than the hook is holding. Refresh the opening or rotate a variant in."

**5 Context.** Always `not_measured`, reason: "Placement breakdown is not connected for this account yet." No metrics.

### Verdict

`broken_tier` = the lowest-numbered tier with status `weak`. Verdict:
- `broken`: headline = the broken tier's decision sentence prefixed "Broken layer: {name}."; detail notes which earlier tiers passed.
- `too_early`: every tier in {not_enough_data, too_young, not_applicable, not_measured}; headline "Too early to diagnose.", detail states the impressions and the 1,000 floor.
- `healthy`: otherwise. "No broken layer. This creative is on par or better at every measured step."

## 4. API

`GET /creatives/{creative_id}/diagnosis` (`src/cs/api/routers/creatives.py`) → `Diagnosis` as JSON. 404 on unknown creative. Queries (all in `core/repo/creatives.py`):

1. `creative_window_sums(org, creative, since, until)` → one `Sums` row.
2. `baseline_window_sums(org, ad_account, fmt, exclude_creative, since, until)` → `Sums` + `count(DISTINCT creative_id)`.
3. `creative_first_metric_day(org, creative)` → `min(day) WHERE impressions > 0`.
4. `creative_window_sums` again for `first14` and `trailing7` when `days_live ≥ 21`.
5. `duration_ms` from the primary asset (`asset.duration_ms` via `get_creative`).

Four to five small aggregate queries; fine for a detail page. The endpoint is cheap enough that no caching is added.

## 5. Web

- `types.ts`: `Diagnosis`, `DiagnosisTier`, `DiagnosisMetric`, `DiagnosisCurve`, `DiagnosisStep`.
- `api.ts`: `creativeDiagnosis(id)`.
- `components/diagnosis-panel.tsx` (server-compatible, no client state): renders exactly the approved mockup (`Creative Diagnosis Panel` canvas, artifact `3e74e6f2`): verdict strip (destructive tint for broken, success tint for healthy, muted for too early), five rows in a bordered `rounded-xl` container with the numbered rail, status pill (`Badge`-style, colored per status), metrics with value / `vs baseline` / lift, retention curve as inline SVG with dashed baseline and shaded drop segment, conversion step chain, fatigue before/after triple, locked context row. Status labels: fatigue `on_par` → "Stable", `weak` → "Early decay"; elsewhere Strong / On par / Weak / Not applicable / Not enough data / Too young / Not measured.
- `creatives/[id]/page.tsx`: fetch diagnosis in the existing `Promise.all`, render `<DiagnosisPanel>` above the Performance section.
- Retention weak row gets a "Generate a recut from this" link to `/generate?creative={id}` only if that route already accepts a creative param; otherwise omit the link (verify during implementation; do not invent a route).

## 6. Testing

- `tests/insights/test_diagnosis.py`: pure-function tests for every status branch per tier, drop-segment selection, verdict selection, floors, image vs video, too-young fatigue, sign conventions.
- `tests/ingest/test_fivetran_insights.py`: fixture schema gains `reach` on `basic_ad` and the nine retention tables; assert every new column folds; assert a schema without retention tables still syncs with zeros and `retention_tables: false`.
- `tests/api/…`: `/diagnosis` returns the shape, 404s cross-org, baseline excludes the creative itself.
- `web/tests/components/diagnosis-panel.test.tsx`: broken, healthy, too-early, not-applicable, locked context.
- `make lint`, `uv run pytest -q`, `npm run lint`, `tsc --noEmit`, `npm test`.

## 7. Out of scope

Account-level overview page; changing the existing `fatigue.py` hourly job (it still uses CTR/CPA decay only); placement breakdown enablement in Fivetran; any LLM explanation; retention snapshots.
