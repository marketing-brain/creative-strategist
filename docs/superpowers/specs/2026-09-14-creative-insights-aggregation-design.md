# Creative Insights Addendum: Cross-Creative Facet Patterns

**Date:** 2026-09-14
**Status:** Design — addendum to `2026-09-14-creative-insights-design.md` (the parent spec); pending review
**Scope:** Cumulative insight across the creatives that have a record: which objective facets co-occur with which metric outcomes inside one ad account, computed in code from the record facets the parent spec already stores. Adds one cheap embedding call per record so "same kind of ad" can act as both a facet and a confound. Produces ranked, evidence-backed *hypotheses* that feed claims and test briefs. No graph store, no new model reasoning step.

## Problem

The parent spec builds every claim from one creative against its account baseline. Once tens to a hundred creatives in an account have records, the obvious next question is cumulative: "across our videos, does showing the product in the first 3 s coincide with better hook rate?" Today that question is answered only by the tag-based pattern insights (`patterns.py`), which inherit the tags' subjectivity and count byte-identical copies of one file as separate creatives.

The parent spec's spike also established two facts any cross-creative statistic must respect:

- **Identical files are common.** Six of org 3's top 16 videos share one 111 s file. Counting copies separately manufactures significance.
- **Sample sizes are small.** A hundred videos across ten facets with three to four values each leaves many cells in single digits. The output is hypothesis generation, not proof. Strong evidence still comes from the learning loop (parent spec, Deferred).

A knowledge graph was considered and rejected: the entities are homogeneous (creative, element, facet, metric), the question is one hop (facet value → metric outcome), and the record already carries typed IDs. The structure a graph would add already exists; the work is dedupe, statistics and confound flags.

## Decisions taken

| Decision | Choice |
|---|---|
| Unit of analysis | A **footage unit**: all creatives in the ad account whose primary asset shares one `asset.sha256`, with metrics pooled across the copies. One unit, one vote |
| Scope of comparison | Within one ad account and one format. Never across orgs; never video against image |
| Metrics | `hook_rate`, `link_ctr`, `hold_rate` (video only), `cpa`, `purchases_per_1k_impressions`. Signed lift so that higher is better, the diagnosis convention |
| Window | Trailing 30 days, the same window the record sweep uses to rank spend |
| Readability | A unit contributes a metric only if its diagnosis marks that layer measured (status not in the diagnosis's unmeasured set). Reuses the diagnosis floors; no new thresholds |
| Statistic | Per facet value: cell against the complement (all other units of the format with that facet computed). Effect = difference of median lifts. Test = two-sided Mann–Whitney U. Benjamini–Hochberg at q = 0.10 across every (facet, value, metric) test in the format per run |
| Minimums | Format has ≥ 20 units; cell n ≥ 5; complement n ≥ 5. Below these, nothing is emitted for that format or cell |
| Numeric facets | Binned into terciles within the format per run (`low` / `mid` / `high`); the cut values are stored with the pattern |
| "Same kind of ad" | `google/gemini-embedding-2` per record, agglomerative clustering at cosine ≥ 0.92 (the spike's threshold) per account per format. Cluster ID becomes a facet and a confound |
| Strength | Every pattern is `weak` and `untested` in v1, the same cap as claims. No pattern can raise a claim's strength |
| Model calls | One embedding per record (about $0.001). The pattern computation is code only. No Claude calls (standing rule) |
| Relation table | Not now. `same_file` is a join on `asset.sha256`; `same_cluster` is the cluster facet. An `edit_of` relation arrives with the learning loop |
| Tag-based pattern insights | Untouched. Facet patterns are shown beside them; retiring tag patterns is a later decision once facet coverage is validated |

## Architecture

```
record.extract ──► record.embed ──► creative_embedding
                        │
nightly per account:    ▼
   clusters.assign ──► creative_facet (cluster_id)
          │
          ▼
   patterns.compute ──► facet_pattern_run + facet_pattern
                                   │
                                   ▼
                  claims.derive (input: patterns for this record's facets)
```

Two new jobs plus one chained job. Everything downstream of `record.extract` in the parent spec is unchanged except the input to `claims.derive` (§5).

## 1. Footage units (code)

For an ad account and format, the units are the distinct `asset.sha256` values among creatives that have a `done` record. For each unit:

- `creative_ids`: every creative in the account sharing the hash, whether or not it has its own record. The unit's record is the earliest `done` record among them.
- Metric sums: `metric_daily` pooled across all `creative_ids` over the window.
- Lift per metric: computed from the pooled sums against the same account baseline the diagnosis uses for that metric. For `hold_rate` the same-length baseline applies, using the unit's duration.
- Readability per metric: the diagnosis of the unit's record creative decides. A unit whose attention layer is `not_enough_data` contributes nothing to `hook_rate` or `link_ctr`.

Units with no `metric_daily` rows in the window are excluded from the run.

## 2. Facets used

All facets from parent §6, read from `creative_facet` for the unit's record, plus `cluster_id` (§3). Numeric facets (`brand_on_screen_share`, `text_words_per_second`, `cuts_per_10s`, `direct_to_camera_share`) are binned per run; the stored pattern carries the bin edges so the value is reproducible. A facet that is null for a unit excludes that unit from both cell and complement for that facet.

## 3. Embeddings and clusters

**Job `record.embed`**, chained after a `done` `record.extract`. Calls `google/gemini-embedding-2` through AI Gateway's `POST /v3/ai/embedding-model` with the media in `providerOptions.google.content` (the spike confirmed the OpenAI-compatible endpoint rejects media). Stores one row in `creative_embedding`. One attempt per record version; a failure is stored with the error and the unit simply has no cluster.

**Job `clusters.assign`**, nightly per ad account per format, before `patterns.compute`. Average-linkage agglomerative clustering over unit embeddings (one embedding per unit, from its record creative) with a cosine similarity cut at 0.92. Every unit gets a `cluster_id` facet (`c1`, `c2`, …; singletons included). IDs are stable across runs by seeding with the previous run's assignment: a cluster keeps its ID if it retains a majority of its previous members.

Clusters are cheap enough to compute in code with a few hundred vectors; no pgvector, no index.

## 4. Pattern computation

**Job `patterns.compute`**, nightly per ad account per format, after `clusters.assign`. Code only.

For each facet, each value present, each metric:

1. Cell = units with that facet value and a readable metric; complement = other units in the format with that facet non-null and a readable metric.
2. Skip unless cell n ≥ 5 and complement n ≥ 5.
3. Effect = median(cell lifts) − median(complement lifts). Also stored: both medians, both interquartile ranges, and the share of cell units with lift > 0.
4. p = two-sided Mann–Whitney U on the two lift samples.

After all tests in the format: Benjamini–Hochberg at q = 0.10; only tests that pass are stored as patterns. If the format has fewer than 20 units, the run is stored with status `insufficient` and no patterns.

### Confounds (code, per pattern)

| Flag | Condition |
|---|---|
| `budget_concentration` | One unit contributes ≥ 50% of the cell's pooled impressions |
| `cluster_concentration` | ≥ 50% of cell units share one `cluster_id` (the pattern may be the template, not the facet). Not applied when the facet is `cluster_id` itself |
| `seasonality` | The median active-day midpoint of cell units and complement units differ by more than 15 days |
| `learning_phase` | ≥ 50% of cell units have a diagnosis fatigue status of `too_young` |

Flags do not suppress a pattern; they are stored and shown, and passed to claims.

### Pattern schema

```
{
  id, run_id, org_id, ad_account_id, format,
  facet, value, bin_edges?,                 // bin_edges for numeric facets
  metric,
  n_cell, n_complement,
  cell_median_lift, complement_median_lift, effect,
  cell_iqr: [q1, q3], complement_iqr: [q1, q3], cell_share_positive,
  p_value, p_adjusted,
  confounds: [...],
  cell_unit_ids: [sha256...], cell_creative_ids: [uuid...],   // evidence, every creative in every cell unit
  strength: weak, status: untested
}
```

Patterns are ranked for display by `|effect|` among those with the fewest confounds first.

## 5. Feeding claims

`claims.derive` (parent §3) gains one input: the current patterns for this ad account and format whose facet is non-null on this record, at most 10, ranked as above. The claim schema gains `patterns: [pattern ids]` (optional). Code validates that every cited pattern exists in the latest run and that the claim's record actually has the pattern's facet value. A cited pattern with `cluster_concentration` must be named as a confound on the claim. Strength is unchanged: a claim citing patterns is still at most `moderate`.

The paragraph writer (parent §4) may say "across N other videos in this account" only where a claim cites a pattern; the grounding check gains the pattern's `n_cell` and `effect` as citable values.

## 6. Storage

New migration `db/migrations/20260915000002_facet_patterns.sql`.

| Table | Columns |
|---|---|
| `creative_embedding` | `creative_id`, `record_id`, `org_id`, `model`, `vector DOUBLE PRECISION[]`, `status` (`done`/`failed`), `error`, `created_at`; PK (`creative_id`, `record_id`) |
| `facet_pattern_run` | `id` PK, `org_id`, `ad_account_id`, `format`, `window_start`, `window_end`, `n_units`, `status` (`done`/`insufficient`), `created_at` |
| `facet_pattern` | `id` PK, `run_id` FK, the schema in §4 as columns for the scalar fields and JSONB for `bin_edges`, `cell_iqr`, `complement_iqr`, `confounds`, `cell_unit_ids`, `cell_creative_ids` |
| `creative_facet` (existing) | Gains rows with `facet = 'cluster_id'`, `element_ids = []` |
| `creative_claim_set` (existing) | No change; `patterns` lives inside the `claims` JSONB |

The latest `done` run per (`ad_account_id`, `format`) is what the API serves. Older runs are kept for 30 days, then deleted by the nightly sweep.

## 7. Jobs and scheduling

| Job | Trigger | Scope |
|---|---|---|
| `record.embed` | Chained after a `done` `record.extract` | Capped, same as records |
| `clusters.assign` | Nightly per ad account per format, after the record sweep | Accounts with ≥ 2 embedded units |
| `patterns.compute` | Chained after `clusters.assign` | Same |

All on the single designed worker; max 3 attempts; dead jobs are not re-enqueued by sweeps. A `patterns.compute` run replaces nothing until it completes: the new run is inserted whole, then becomes the latest.

**Precondition on coverage.** With the parent's cap of 2 records per format per org, no format reaches 20 units, and every run is `insufficient`. The jobs ship anyway (they are cheap and idempotent) so that raising the cap after the gold-set review (parent §13, step 3) makes patterns appear without further code. Raising the cap is a config change in `creative_insight_limit_per_format`, decided per org after record targets pass.

## 8. API and UI

`GET /insights/facet-patterns?ad_account_id=&format=` returns the latest run and its patterns; 404 across orgs; `status: insufficient` with `n_units` when the format is below the floor.

On the Insights page (`web/src/app/(app)/insights/page.tsx`), a **Facet patterns** section beside the existing tag-based pattern list, per format tab:

- One row per pattern: facet and value (with bin edges for numeric facets), metric, effect as a signed percentage, `n_cell` / `n_complement`, confound chips, and a "weak · untested" badge.
- Expanding a row lists the cell creatives (thumbnail, name, that creative's own lift), so a reader can see the evidence and spot a copy-heavy cell.
- Below the floor: "Not enough recorded creatives yet (N of 20)".

## 9. Quality and evaluation

**Planted-effect fixture (unit test).** A synthetic account of 40 video units with random lifts, one facet value given a +20 point median lift on `hook_rate`, and six creatives sharing one sha256 inside that cell. Required: the planted pattern is found; the six copies count as one unit; no pattern is reported on a null-effect facet in more than 10 of 100 seeded runs.

**Confound fixtures.** A cell where one unit holds 60% of impressions must carry `budget_concentration`; a cell where 4 of 6 units share a cluster must carry `cluster_concentration`.

**Real data (org 3, once the cap is raised).** Hand-check the top 5 patterns per format: is the facet value actually present in the cited creatives (spot-check 3 per pattern against the record), and is the direction plausible? A pattern whose cell is dominated by copies of one file must not appear at all; if it does, dedupe is broken and the run is invalid.

**Clustering (org 3).** The labelled pairs in `tests/evals/variants/org3_labeled_pairs.json` must land in the same cluster for every `related` pair and in different clusters for every `unrelated` pair. A threshold other than 0.92 is adopted only if it scores at least as well on that set.

## 10. Errors

- An embedding failure marks that row `failed`; the unit is excluded from clustering and carries no `cluster_id`; patterns still run.
- Fewer than 20 units, or all cells below the minimums, store an `insufficient` or empty `done` run respectively; the UI states which.
- A run that fails mid-way leaves the previous latest run in place.

## 11. Testing

- **Unit:** unit construction and pooling by sha256; lift and readability reuse from the diagnosis; tercile binning with stored edges; Mann–Whitney and Benjamini–Hochberg against known values; each confound rule; cluster ID stability across runs; claim validation of `patterns` citations.
- **Fakes:** embedding client returning fixed vectors.
- **DB:** job chain `record.extract` → `record.embed`; nightly `clusters.assign` → `patterns.compute`; latest-run selection; 30-day run retention.
- **API:** `/facet-patterns` states and cross-org 404.
- **Web:** Facet patterns section states (insufficient, empty, populated), row expansion, confound chips.

## 12. Rollout

1. Migration, `record.embed`, `clusters.assign`, `patterns.compute`, API and UI, all behind the 20-unit floor. Ships alongside or after the parent's step 4.
2. Clustering checked against the labelled pairs on org 3's existing records.
3. After the parent's gold-set review passes, raise `creative_insight_limit_per_format` for org 3 to a value that yields ≥ 20 video units; let the sweep fill it over successive nights.
4. Hand-check the first populated run (§9); only then enable pattern citations in `claims.derive`.

## Out of scope

- A graph database or any relation table; `edit_of` lineage arrives with the learning loop.
- Cross-account or cross-org patterns.
- Any pattern with strength above `weak`, or any effect on claim strength.
- Interaction effects (two facets jointly) and any model-written explanation of a pattern beyond the paragraph's citation rule in §5.
- Retiring tag-based pattern insights or tag filters.
- Frame-level or shot-level cross-creative matching (same footage). The spike showed this cannot yet be verified; clusters mean "same kind of ad", nothing stronger.
