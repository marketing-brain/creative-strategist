# `/next` — the standing answer to "what should I run next?"

Written 2026-09-20. Steps 1-5 of §9 are built; the ledger (phase 2) and the production
run are not.
Sections marked **Correction, found while building** are where the real data proved the
first draft of this document wrong.

## 1. The problem

The product answers "what should I run next" in pieces spread across four pages:

- `/creatives` — what is live and how it is doing. Inventory, not a recommendation.
- `/creatives/{id}` — which layer is broken; a healthy verdict says "scale it, and use it as the
  reference when you brief the next batch" (`diagnosis.py:861`). Names the layer, never the change.
- `/fatigue` — which ad is declining. Says nothing about the replacement.
- `/insights` — graded attribute patterns, and `next_tests`, which the writer prompt already calls
  "the only place recommendations belong" (`writer.py:248`). Three to five sentences at the bottom
  of a long read.
- `/ask` — `untried` ("proven attributes never combined; what to make next") and `spend_gap`
  ("where the money goes versus where the evidence points") are the two kinds that answer the
  question most directly, and they are behind a chip the user has to know to click.

The user assembles the answer. Nothing in the product ranks it, and nothing records whether the
advice was taken.

## 2. The failure mode this must not repeat

The weekly brief was removed on 2026-09-20 (`6369fe0`) because it "restated fatigue insights the
Fatigue page already shows, and nothing read it." A page that aggregates other pages earns nothing.

**Ownership rule.** `/next` owns *recommendation*. The other pages own *state*. Concretely:

| Surface | After `/next` ships |
|---|---|
| `/insights` | Stops rendering the "N tests to run next" block (`account-read.tsx:322-355`). The writer keeps emitting `next_tests`; `/next` is where they render. Insights keeps overview, findings, entangled, no_evidence, data_needed. |
| `/fatigue` | **Removed 2026-09-20.** See §2.1. `compute_fatigue` and the `insight` rows stay; `/next` reads them. |
| `/ask` | Stays. `/next` is the standing answer; Ask is the follow-up question. The `untried` and `spend_gap` analyzers are shared code, not duplicated logic. |
| `/creatives/{id}` | Unchanged. `/next` rows link into it for the evidence. |

If a block on `/next` cannot be the only place its content lives, it does not ship.

### 2.1 Fatigue failed its own ownership test

The spec argued `/fatigue` could stay as "the unranked full queue" while `/next` showed the
spending ones, and flagged in §10.4 that the distinction might not hold. It did not.

- Every card rendered `Next test: …` — a recommendation, on the page that does not own them.
  And they were interchangeable: six consecutive org 3 rows read "Test a new creative variation
  that introduces a completely different…", "…a novel hook or visual…", "…a significantly
  different…".
- Its valuable rows were already Replace rows on `/next`, with better money: footage-grouped
  30-day spend against per-creative window spend.
- 74 rows over two pages that nothing acted on — exactly what `6369fe0` removed the weekly
  brief for. Fatigue had become the thing restating.
- `insights.periodic_fatigue` is paused on prod, so the tab served rows frozen at 2026-09-15
  with no indication.

What `/next` had to absorb first, and did: the decline magnitude. A Replace row now carries the
comparison as its pill, in the slot a Fix row uses for its broken layer — both answer "what is
wrong with this ad". Ranking stays spend, because one ordering rule governs the whole page.

Two rules came out of the data. `signed_lift` divides by the baseline, and the fatigue job has
a spend floor but no purchase floor, so org 3 holds a **−1386% CPA** row — one purchase in a
trailing week, not a measurement. So the row reports `observed / baseline` as a multiple, which
is how a cost reads, and past `DECLINE_CAP` (5×) it says "5×+" and stops quoting a figure. And
because a group holds several creatives each with a row per metric, `choose_decline` prefers a
usable cost row, falls back to click-through when every cost row is capped, and only then shows
a capped cost row.

## 3. Non-goals

- **No demographic prescriptions.** `messenger_age_band`, `messenger_gender_presentation` and
  `demographic_signal` are barred from Make ingredients and from Explore (`next_plan/policy.py`).
  They stay in the graded tests, the account read, the ask box and any evidence line a brief
  cites. Reporting "the files with a man on camera got 18% more link clicks" is a measured fact
  about this account's creative; a brief saying "cast a man" is the product directing an
  advertiser to select people by a protected characteristic. `cast` and the language dimensions
  are deliberately not on the list -- shooting a couple or a Spanish-language cut is an ordinary
  creative decision.

- **No predicted ROAS, no per-creative forecast.** Standing ruling; the ask box carries no revenue
  metric and is retrospective. `/next` is retrospective too: every number on it is measured.
- **No auto-grading of a recommendation's outcome in v1.** The ledger records that a brief was run
  and links to the resulting ad's diagnosis. Judging whether the advice worked needs a second read
  cycle — phase 2.
- **No new tagging, no new model call for the plan itself.** The plan is assembled from the stored
  account-read run plus the 30-day rollups. The only model text it shows is `next_tests`, already
  written.

## 4. Ranking: spend at stake

Ranking by p-value ranks by confidence, not by consequence. `untried` currently sorts by summed
`|change|` across metrics that are not commensurable (`analyzers.py:198`). Neither is a business
quantity, and prediction is off the table. The honest substitute is money already committed.

**Correction, found while building.** There is no single "30-day stake". A `Unit` pools every
day `metric_daily` holds for its creatives (`units.py`, no day filter), so unit spend is
lifetime within the read's window — org 3's is 2026-05-07 to 2026-09-14, not 30 days. Only the
allocation block, which reads `creative_metric_30d`, is genuinely trailing-30. So `Stake`
carries its own `window`, each block ranks within itself, and no row prints a bare number the
reader could mistake for the other window.

For a graded, unflagged test `t` with grade in (`strong`, `suggestive`):

```
wrong_side(t) = complement units, if the cell is the good side
              = cell units,       if the cell is the bad side
stake(t)      = Σ window spend over wrong_side(t)   # 30-day only for allocation rows
```

`is_good` and `LOWER_IS_BETTER` already exist in `analyzers.py` / `types.py`, and `spend_gap`
computes cell spend this way today (`analyzers.py:128`). Rendered as currency and as a share of
the same window's account spend: *"$511,259 — 59% of window spend, on the files that lack it."*

Per block:

- **Scale / cut** — stake is that ad's own 30-day spend. Literally the money being decided.
- **Make** — the combo has no carriers by construction, so it has no spend of its own. Stake is
  `min(stake(t))` over the combo's constituent patterns: the least-supported ingredient is the
  binding one. Conservative on purpose.
- **Explore** — no stake, and it is not ranked. Untested is not unpromising; pretending to order it
  would be the one dishonest number on the page.

## 5. The three blocks

### 5.1 Scale / cut (ad-level allocation)

The gap today: `spend_gap` allocates at attribute level, diagnosis judges one ad at a time, and
nothing says which live ad gets budget tomorrow.

Input: for each creative with 30-day spend above `STAKE_FLOOR`, the stored diagnosis verdict, its
`creative_metric_30d` row, and any open fatigue insight.

| Row | Condition | Says |
|---|---|---|
| Scale | verdict `healthy`, spend share below half the **median** ad's share | On par or better at every measured step and under-funded. |
| Fix | verdict `broken`, spending | Broken layer: *name*. Carries `tier.decision` verbatim. |
| Replace | open fatigue insight, spending | Declining. Links to the Make block's brief when one shares its attributes. |
| — | verdict `too_early` | Excluded. Not a decision yet. |

Each row: poster, ad name, 30-day spend, the verdict sentence, link to the creative page.

**Three corrections, found while building.**

- *Under-funded is measured against the median ad, not the mean.* The first draft compared spend
  share to impression share, which is nearly constant by construction, and the first fix compared
  it to the mean share, which is `1/n` whatever the distribution looks like. In an account where
  one file holds most of the budget -- common enough that `stats._confounds` flags it -- the mean
  sits above every ad but the whale, so almost every healthy ad reads as starved. The median moves
  with the account.
- *Caps are per kind, not per page.* Fix-first plus one cap of 12 starved the entire upside: org
  3's first real run produced 7 fix rows and 3 replace rows, so **not one scale row was shown** --
  and scale is the half of the question the product is worst at answering today. Each kind now
  keeps `MAX_PER_KIND` slots.
- *Rows are titled by ad name, falling back to copy*, exactly as the creatives grid does. Five of
  org 3's top twelve spenders carry no headline, so titling by headline printed "(no copy)" on
  five of twelve budget decisions.

Ranking is within a kind, never across: a scale candidate is small *because* it has not been
scaled, so one mixed spend ordering would bury it under every broken ad.

### 5.2 Make (the brief)

The gap today: `next_tests` is "vary attribute X, expect metric Y to move" and `untried` is a
2–3 attribute combo. Neither is shootable, though the material for a shootable spec is already
stored on every `Unit`: `hook_verbatim`, `concept_summary`, `duration_s`, `first_product_s`,
`headline`, plus the scene rows behind `opening_shows` / `first_product_shown`.

Three to five briefs, each assembled from one `untried` combo or one `next_tests` line:

```
Make: expert on camera + proof in the first 5 seconds
Stake     $18,400 (23% of window spend) — binding ingredient: proof_devices=demonstration (P7)
Evidence  P4 hook rate +31% strong · P7 thruplay +18% suggestive · downstream: CPA no clear difference
Hook      Carrier hooks to write against (verbatim, from the files that hold each ingredient):
          "Three weeks in and I stopped waking up at 3am"
          "I'm a sleep physician. Here's what I actually take."
Refs      2–3 carrier files, poster + headline + link to creative page
Shape     Length 22–34s (carrier median 27s) · product on screen by 4s · subtitles present
Hold      Keep the offer and the CTA as they are — the only change is the two ingredients above.
Watch     Hook rate; it must beat the account median of 28.4% to count.
Caveat    Neither ingredient is graded on purchases. This is an attention test, not a CPA test.
```

Rules, inherited from the writer prompt so the page cannot outrun the evidence:

- Grade-gated: only `clean_main_effects` that are `is_good`, unflagged, grade `strong` or
  `suggestive`. `directional` never produces a brief.
- A brief whose constituents carry no graded purchase line must say so in `Caveat`. Buyer words
  ("converts", "qualified", "intent") are forbidden in the title unless a purchases or CPA line is
  good and at least suggestive (`writer.py` SYSTEM rules, reused verbatim).
- Tagger-disagreement rows (`reliability.FLAG`) never produce a brief. `patterns_for_file` already
  drops them for per-file claims; same bar here.
- `Watch` names one metric and the account median it must beat. Both measured, neither predicted.

Four rules the first real-data run forced in (org 3, 56 files, `scripts/next_plan_probe.py`):

- **Each brief must introduce an ingredient no earlier brief used.** Without it the top five were
  one idea re-cut — a pair, then that pair plus a third attribute, then that pair again with a
  fourth. Five rows the reader would read as five tests.
- **`Shape` comes from the files nearest the whole combination**, not from every file carrying any
  one ingredient. The latter describes the account and contradicts the brief: a combination naming
  *duration under 51.5s* printed `length ~76s`, dragged there by the long files carrying its other
  ingredients. It now reads ~41s over carriers 31–49s.
- **A numeric ingredient prescribes its own range** from `bin_edges`, rather than whatever the
  nearest files average.
- **A shape needs `MIN_SHAPE_FILES` (3) to be a shape.** An untried combination is untried because
  few files come close, so the nearest set is often one file — an example, not a central tendency.
  Widen a step at a time and report how many files it took.

Ordering is addressable spend, then the weakest ingredient's p, then fewest carriers. The
tiebreak is not cosmetic: on org 3 the top three stakes land at 59%, 50% and 49% of window spend,
because every proven ingredient is rare and so every complement is nearly the whole account.
Stake orders the list honestly but separates it weakly; evidence strength does the rest.

### 5.3 Explore (the exploitation ceiling)

`untried` seeds from attributes that *already have positive evidence*. An angle the account has
never made has no test, so it can never be proposed. The highest-value next ad is structurally
invisible to the product today.

Fix, and it is cheap: for every closed-vocabulary dimension in `taxonomy.yaml` (`kind: single`
with an explicit `values:` list, `applies_to` including the account's format), list the values with
zero units, or fewer than `MIN_CELL` (5) — the floor below which `compare` refuses to test at all.

Rendered flat, grouped by taxonomy `group` **and dimension**, unranked, labelled *never tried* or
*too few files to test*.

**Correction, found while building.** The multi-valued dimensions are closed too — `hook_devices`,
`proof_devices`, `angles`, `offer_elements`, `registers` all carry explicit `values:` lists — and
they are the most interesting ones to find empty. They are in. What is excluded is the open and
free-text kinds, `facets.SKIP_DIMS`, the numeric terciles, and values naming an absence (`none`,
`other`, …): "never tried no offer" is not a brief. Grouping by taxonomy `group` alone also
collided two dimensions that share values — org 3 printed `fr, de, pt, it, hi` and `es (4)` twice
— so a row keeps its dimension.

## 6. Storage and the job

`/next` must **not** compute on request. `diagnose_creative` issues several queries per creative
(placements, audience, siblings); running it across every live ad on page load is the shape that
pinned the database twice (2026-09-18, 2026-09-20). The account-read `page` column exists for
exactly this reason.

- New job `insights.next_plan`, one `cs-worker` task, no added parallelism.
- Triggers: `compute_account_read` enqueues it on a `done` run, **and** a daily schedule.
  Not redundant -- the read's enqueue gets new patterns onto the page the moment they exist,
  and the daily run keeps the allocation block honest as the trailing-30-day window slides (an
  ad crosses the fatigue line on days when no read runs). They collapse on collision: the
  `dedup_key` blocks a second pending job, and the handler always builds on the latest *done*
  read, so a plan built mid-read uses the previous one rather than half of the new one.

  **Correction, found while building.** The first draft had the plan on its own nightly
  schedule, timed to land after the read. The scheduler has no ordering between schedules --
  each fires on its own `every_seconds` from its own `last_run_at` -- so that was a race
  dressed up as a sequence.
- Bounded: diagnoses at most `MAX_DIAGNOSED` creatives, chosen by 30-day spend descending.
- Writes one small `plan` payload. The web page reads only that payload — never `result`.

```sql
-- db/migrations/20260921000001_next_plan.sql
CREATE TABLE next_plan_run (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL REFERENCES ad_account(id),
  format text NOT NULL DEFAULT 'video',
  account_read_run_id uuid REFERENCES account_read_run(id),
  status text NOT NULL CHECK (status IN ('done','insufficient','failed')),
  plan jsonb NOT NULL,
  error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX next_plan_run_latest_idx
  ON next_plan_run (ad_account_id, format, created_at DESC);
```

Constants (`src/cs/insights/next_plan/`): `STAKE_FLOOR`, `MAX_ROWS`, `MAX_DIAGNOSED`, `MAX_BRIEFS`.
Reuse `MIN_CELL`, `GOOD_GRADES`, `is_good`, `clean_main_effects`, `label`, `METRIC_NAME`.

## 7. The ledger (phase 2, table shipped in phase 1)

`next_tests` is generated, rendered, and forgotten. The product cannot learn whether its own advice
worked, and the user cannot see what they already ran.

Each emitted item gets a stable `key` — a hash over its kind plus its sorted attribute spec — so the
same brief keeps its identity across runs.

```sql
CREATE TABLE next_item (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL REFERENCES ad_account(id),
  key text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('scale','fix','replace','make','explore')),
  spec jsonb NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','ran','expired')),
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  ran_at timestamptz,
  ran_creative_ids uuid[] NOT NULL DEFAULT '{}',
  UNIQUE (ad_account_id, key)
);
```

Phase 1 writes and upserts rows. Phase 2 closes them: on each run, any unit first seen after
`first_seen_at` whose facets are a superset of a `make` item's spec marks it `ran`, and the page
shows *"ran — 2 ads, first live 12 days ago"* with a link to their diagnoses. An item whose
supporting pattern stops being graded goes `expired`, not deleted.

## 8. API and web

- `GET /insights/next?ad_account_id=...` → the stored `plan` payload, or `{status: 'none'}`.

  **Correction, found while building.** The spec guessed `/accounts/{id}/next`; the codebase
  serves account-scoped insight reads as `/insights/<thing>?ad_account_id=`
  (`/insights/account-read`, `/insights/account-read/ask`), so the route follows that instead.
  It needs no page slice -- the payload is bounded by `MAX_PER_KIND`, `MAX_COMBOS` and the
  explore list -- and it resolves every poster the plan names, allocation rows and brief
  references alike, in one lookup rather than a query per row.
- `web/src/app/(app)/next/page.tsx` — server component, no client JS, like `/insights`. View model
  in `web/src/lib/next-view.ts`, matching `account-read-view.ts`.
- Nav order is `Creatives → Insights → Next → Ask`, and the root redirect sends an active
  account to `/creatives`.

  **Reversed 2026-09-20.** Step 4 put `/next` first and made it the landing page. The better
  argument is the opposite one: evidence, then interpretation, then recommendation is both the
  order the data is built in and the order of trust, and for a product whose differentiation is
  grade-gating and "76 of these would look suggestive by luck alone", leading with the
  conclusion is tonally the thing it is trying not to be. `/next` shows its own evidence inline,
  so nav order was never carrying that weight anyway. The two settings are coupled — a first
  nav item and a front door that disagree means one of them is wrong — so they moved together.
- Empty states: no account read yet → same copy as `/ask`. Read exists but no graded pattern clears
  the bar → say that plainly, and still show Scale/cut and Explore, which do not need patterns.

## 9. Build order

1. ~~`next_plan` package: stake computation + the three block builders, pure, against a stored
   run. Verify with a script against org 3's latest run.~~ **Done.** `src/cs/insights/next_plan/`
   (`types`, `stake`, `policy`, `make`, `explore`, `build`), `scripts/next_plan_probe.py`,
   10 tests in `tests/insights/next_plan/`. Allocation is not in it -- it needs the job.
   `patterns._kinds` / `_scope` are now public `dimension_kinds` / `scope_units`.
2. ~~Migration + job + repo, wired to the account-read completion.~~ **Done.**
   `20260921000001_next_plan.sql` (`next_plan_run` + the `next_item` ledger, both verified up
   and down), `next_plan/{allocate,repo,jobs}.py`, registered in `all_handlers`. 20 tests in
   `tests/insights/next_plan/`, full suite 611 green. The allocation block was run read-only
   against org 3 (`--allocate`): 4 fix, 3 replace, 1 scale, correct ad names.
3. ~~`GET /accounts/{id}/next`.~~ **Done**, as `GET /insights/next?ad_account_id=` (see §8).
   3 route tests including cross-org isolation; full suite 614 green.
4. ~~The page, the nav item, the root redirect.~~ **Done.** `app/(app)/next/page.tsx`,
   `components/next-plan.tsx`, `lib/next-view.ts`, `lib/types.ts` + `api.nextPlan`, nav item
   first in the group. 9 web tests; suite 93 green, typecheck and lint clean.

   **§10.2 settled, then reversed:** the root redirects to `/creatives`. See §8.
5. ~~Remove the `next_tests` block from `/insights` and link to `/next`.~~ **Done**, in the
   same change as 4 so the content was never in two places. The Insights header keeps the
   "N tests to run next" link; it now points at `/next` instead of scrolling down the page.
6. Phase 2: close the ledger.

## 10. Open questions

1. **`STAKE_FLOOR` and `MAX_DIAGNOSED`** — needs org 3's spend distribution. If the account's spend
   is concentrated in a handful of ads, `MAX_DIAGNOSED` can be small and the job stays cheap.
2. ~~**Root redirect to `/next`**~~ **Answered: no.** The library is the front door and `/next`
   is arrived at. See §8.
3. **Hook lines in a brief are verbatim from carrier files.** Writing *new* hook copy would need a
   model call and would put unverified text on a page whose whole claim is that every line is
   measured. Recommend verbatim-only in v1.
4. **Does `replace` duplicate `/fatigue` enough to violate the ownership rule?** Argument it does
   not: `/fatigue` is the unranked full queue, `/next` shows only the spending ones and attaches a
   replacement brief. If that distinction does not hold up in the built page, cut the Replace row
   and link to `/fatigue` from the block header instead.
