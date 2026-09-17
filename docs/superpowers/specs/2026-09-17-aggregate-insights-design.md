# Aggregate Insights: the account creative read

**Date:** 2026-09-17
**Status:** Approved in conversation; first cut, built for speed. Hardening is a later spec.
**Scope:** Turn one ad account's tagged videos plus their metrics into (a) a stored, graded table of cross-creative patterns, (b) a prose account read in the voice of the per-creative insight, and (c) an ask box that answers a strategist's plain-language question from the same data. Video only. No clustering. Replaces nothing.

## Problem

The per-creative insight explains one ad. The user wants the account-level equivalent: which kinds of videos win attention, hold it, earn clicks and convert, answered at the complexity of "which messenger builds trust for each persona" or "which angle gives the lowest CAC per product." Tonight's throwaway scripts proved the method on org 3's top 100 videos (56 distinct files): file-hash dedupe, graded comparisons, generic interactions, an entanglement check, and a grounded prose writer. This spec makes that a product feature with the least code that works.

The user's four example questions are test cases, not the feature. Nothing in the build is specific to them.

## Decisions

| Decision | Choice |
|---|---|
| Unit | Footage unit: every video creative in the account sharing one primary-asset `sha256`. Metrics pooled across copies, lifetime. Facets from the highest-spend copy's latest done v2 tag run |
| Facets | Every taxonomy 2.5 video dimension of kind single, multi, open (canonical slug), numeric (terciles per run), plus two derived: `first_product_shown` bin from scene timestamps flagged product/packaging, and `opening_scene` from the first ordered scene. Text and series kinds skipped |
| Metrics | `hook_rate`, `thruplay_rate`, `link_ctr` per unit (medians, Mann-Whitney U, 2,000-impression floor); `purchases_per_1k`, `cpa` pooled per group (permutation test, 20 purchases per side) |
| Grades | strong = Benjamini-Hochberg q 0.10 across the run; suggestive = p < 0.10; directional = p < 0.25 and change ≥ 15%; thin otherwise. Everything with 5+ units per side is stored |
| Interactions | Generic, bounded: for each value of each *grouping dimension* with ≥ 10 units, every facet is tested within the group. Grouping dimensions are a setting, default `audience_callouts, products_mentioned, features_mentioned, messenger, duration_s` |
| Entanglement | Jaccard ≥ 0.6 between any two graded (strong/suggestive, unflagged, 8–48 units) attributes; for each pair, the effect is recomputed within and outside the partner. Stored beside the patterns |
| Confounds | `budget_concentration` (one unit ≥ 50% of the cell's impressions), `seasonality` (active-day midpoints differ > 15 days). Stored, never suppress |
| Writer | One `creative_insight_model` call with the graded tables as a text brief. JSON out. Number-grounding check (every number in prose appears in the brief) with one retry. Verdicts capped in code at the grade of cited evidence |
| Ask box | Two model calls: map the question to `{targets, group, layer}` against the run's dimension list, then compute from the stored units and write the answer with the same rules |
| Storage | One table, JSON payloads. No per-pattern rows in this cut |
| Surface | "Account read" section at the top of the Insights page, plus the ask box. Explorer table deferred; the evidence lives in expandable lists under each paragraph |
| Statistics | Pure Python, ported from the throwaway scripts. No numpy or scipy dependency |

## Architecture

```
nightly / script
  insights.compute_account_read (per ad_account, video)
     units.build  ─►  facets.extract  ─►  stats.main_effects
                                       ─►  stats.interactions (bounded)
                                       ─►  stats.entanglement
                                       ─►  writer.account_read  ─►  account_read_run (one row)

GET  /insights/account-read?ad_account_id=      latest done run
POST /insights/account-read/ask                  question → mapped query → compute on run.units → answer
```

## 1. Module layout

`src/cs/insights/account_read/`

| Module | Responsibility |
|---|---|
| `units.py` | Query creatives, assets, metric sums, latest v2 tag run per creative; group by sha256; pick the facet source copy; compute per-unit rates and retention shares; compute active-day midpoint. Output: `list[Unit]` |
| `facets.py` | `Unit.tags` → `set[(dimension, value)]`, dimension kinds, tercile binning with stored edges, the two derived facets |
| `stats.py` | `mann_whitney_p`, `permutation_p`, `benjamini_hochberg`, `compare(cell, comp) -> list[Test]`, `grade` |
| `patterns.py` | `run(units, grouping_dims) -> RunResult` = main effects, interactions, entanglement, retention by reveal bin, summary |
| `writer.py` | `brief(run) -> str`, `SYSTEM`, `write(run) -> Read`, grounding check, verdict cap. Same client construction as `creative_insight.py` |
| `ask.py` | `map_question(question, run) -> Query`, `answer(query, run) -> Answer` |
| `repo.py` | insert run, latest run per account, purge older than 30 days |
| `jobs.py` | handler `insights.compute_account_read`; scheduler entry nightly per active ad account with ≥ 10 units |

The throwaway scripts in the job scratchpad are the reference implementation for `stats.py`, `patterns.py` and `writer.py`. Port, do not import.

## 2. Data shapes

```python
@dataclass
class Unit:
    sha256: str; creative_ids: list[UUID]; rep_creative_id: UUID; headline: str
    duration_s: float | None; spend: Decimal; impressions: int; plays: int
    video_3s_views: int; thruplays: int; p25: int; p50: int; p75: int; p100: int
    link_clicks: int; purchases: int; first_day: date; last_day: date
    facets: set[tuple[str, str]]; numeric: dict[str, float]; first_product_s: float | None

@dataclass
class Test:
    dimension: str; value: str; group: str | None        # group = "audience_callouts=magnesium_users" or None
    metric: str; kind: Literal["per_unit", "pooled"]
    n_cell: int; n_complement: int; cell_value: float; comp_value: float; change: float
    p: float; grade: str; confounds: list[str]; cell_shas: list[str]; bin_edges: tuple | None
```

`RunResult` holds `units`, `tests` (main + interactions, one list, `group` distinguishes), `entanglement` (overlaps and stratified rows), `retention_by_reveal`, `summary`. It serializes to one JSON document.

`Read` (writer output): `overview`, `findings[]` (title, layer, paragraph, pattern_ids, confidence), `entangled`, `no_evidence`, `next_tests[]`, `data_needed[]`. Pattern IDs are `P<n>` indexes into `tests` in stored order, so the UI can resolve them without a join.

## 3. Statistics

As proved tonight, ported verbatim:

- Rate metrics: unit contributes if impressions ≥ 2,000 (thruplay: plays ≥ 500). Effect = median(cell) / median(complement) − 1. Two-sided Mann-Whitney U with normal approximation and tie correction.
- Pooled metrics: cell and complement each need 20 purchases. Effect = pooled(cell) / pooled(complement) − 1. Permutation test over unit labels, 1,000 draws (2,000 in the scripts; halved for runtime).
- Both sides need 5 units.
- One BH pass over every test in the run, main and interaction together.

Interaction bound: grouping values with fewer than 10 units are skipped. At 56 units this produced 545 interaction tests in about a minute in pure Python; at 350 units the bound on grouping dimensions keeps it under roughly ten minutes, which is acceptable for a nightly job. If it is not, halve the permutation draws before adding dependencies.

## 4. Writer

System prompt as in the throwaway script, minus the fixed question list. It asks for `overview`, 4–7 `findings` chosen by evidence rank including interaction findings, `entangled`, `no_evidence`, `next_tests`, `data_needed`. Rules carried over: numbers verbatim, cite IDs, grades bound titles, flags named or omitted, entanglement stated, hypothesis language, layers named.

Brief construction: account context and baselines; graded main effects unflagged first (cap 70) then flagged (cap 30); interactions grouped by grouping value, non-thin only, with "too few to test" lists; entanglement overlaps and stratified rows for graded attributes; retention by reveal bin with the length caveat.

Grounding: the `_numbers` regex from `creative_insight.py`, applied to every prose field against the brief. One retry with the problems named; a second failure stores the read with `status='rejected'` and the page shows the tables without prose.

## 5. Ask box

`POST /insights/account-read/ask` with `{ad_account_id, question}`.

1. `map_question`: one `creative_insight_model` call with the run's dimension names and value lists (values with ≥ 5 units only). Returns `{targets: [(dimension, value) | dimension], group: (dimension, value) | null, layer: attention | retention | click | conversion | all}`. If the model returns `unmappable` with a reason, the answer says which attribute is not tagged and stops.
2. Compute: `compare` for each target within the group (or whole account) on the layer's metrics, using `run.units`. BH across these tests only. Same grades.
3. Write: one `creative_insight_model` call, same rules, output `{verdict, answer, pattern_ids, what_would_settle_it}`; verdict capped in code.

Not stored in this cut. Runs synchronously; expected 10 to 20 seconds. The UI shows a spinner and the answer in the same prose style with an evidence list.

## 6. Storage

Migration `db/migrations/20260917000001_account_read.sql`:

```sql
CREATE TABLE account_read_run (
  id uuid PRIMARY KEY, org_id uuid NOT NULL REFERENCES org(id), ad_account_id uuid NOT NULL,
  format text NOT NULL DEFAULT 'video',
  window_start date, window_end date, n_units int NOT NULL,
  status text NOT NULL CHECK (status IN ('done','insufficient','rejected','failed')),
  result jsonb NOT NULL,         -- RunResult (units, tests, entanglement, retention, summary)
  read jsonb,                    -- Read, null when insufficient/rejected/failed
  error text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_read_run_latest_idx ON account_read_run (ad_account_id, format, created_at DESC);
```

`result` for 56 units is about 1.5 MB. Acceptable for this cut; per-pattern rows come with hardening. Purge runs older than 30 days in the existing nightly sweep.

## 7. Jobs and scheduling

`insights.compute_account_read` `{ad_account_id}`: build units; fewer than 10 → store `insufficient` and return; else run patterns, write read, store `done` (or `rejected`). Max 2 attempts. Failures store `failed` with the error; the previous latest run stays what the API serves.

Scheduler: nightly per active ad account with video creatives. `scripts/compute_account_read.py --ad-account <id>` enqueues on demand.

## 8. API

- `GET /insights/account-read?ad_account_id=` → latest run: `{status, n_units, created_at, read, summary, tests (graded only), entanglement, retention_by_reveal, units (headline, spend, copies, rep_creative_id, rates)}`. 404 cross-org. `insufficient` returns `n_units` and no read.
- `POST /insights/account-read/ask` → `{verdict, answer, pattern_ids, tests, what_would_settle_it}` or `{unmappable: reason}`.

## 9. UI

Insights page, new top section **Account read** (`web/src/components/account-read.tsx`):

- Header line: files, creatives pooled, spend, purchases, run date.
- Overview paragraph, then findings as titled paragraphs with layer and confidence pills. Superscript citations open an inline evidence list: attribute, metric, with vs without, change, n, p, grade, flags, and the cell's creatives with links.
- Entangled, no evidence, tests, data needed as short sections.
- **Ask** box above the findings: text input, submit, answer rendered in the same shape. Loading and unmappable states.
- Below 10 units: "Account read needs 10 tagged video files. This account has N."

Styling follows the existing insight card components. No new design system.

## 10. Testing (minimum for this cut)

- `stats.py`: Mann-Whitney and BH against known values; permutation p on a planted difference; grade boundaries.
- `facets.py`: extraction from a fixture tag list including terciles, first-product bin, opening scene.
- `units.py` (DB): six creatives sharing one sha256 pool into one unit with summed metrics; facet source is the highest-spend copy.
- `patterns.py`: planted-effect fixture of 40 synthetic units; the plant is found; the null facet is thin or directional in ≥ 90 of 100 seeded runs; entanglement detects two attributes on the same units.
- `writer.py`: grounding check rejects an invented number; verdict cap lowers an over-claimed verdict. Model calls use a fake.
- API: latest-run selection, insufficient state, cross-org 404, ask with a fake mapper and writer.
- Web: section states (insufficient, done, rejected), citation expansion, ask flow with a mocked response.

Real-data check: run on org 3, read the top findings against the throwaway report from tonight; they should agree in direction.

## 11. Rollout

1. Migration, modules, job, API, UI on branch `aggregate-insights`.
2. `scripts/compute_account_read.py` on org 3 from the local machine against the primary DB.
3. Merge to main; deploy when the user is available for the box.
4. Nightly schedule on.

## Out of scope for this cut

Per-pattern storage rows and the explorer table in the app; clustering and cluster facets; retention-curve facets beyond the reveal-bin table; trailing windows; storing ask answers; feeding patterns into per-creative claims; image and carousel formats; persona as a first-class tag (the audience-callout proxy is used and named as such in the brief).
