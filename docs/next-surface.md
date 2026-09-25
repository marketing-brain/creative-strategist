# `/next` — the standing answer to "what should I run next?"

**v2, written 2026-09-23.** Supersedes the 2026-09-20 draft, kept at `docs/next-surface-v1.md`.
v1 is built and live; this document describes what replaces it and why, and it is written the
way the user asked for it — ignoring what exists today and starting from what a marketer can
act on.

Every number in this document was measured against prod (org 3 video account
`01a09811-497e-7000-989e-4ffdf8aff83d`) on 2026-09-23 unless marked otherwise.
Sections marked **Correction, found while writing** are where the real data proved a draft of
this document wrong.

---

## 1. Why v1 has to be replaced

v1 works exactly as specified. The specification is the problem.

### 1.1 The headline number is 12.6× the opportunity behind it

The page's summary band reads **"Under a decision — $147,591, 37% of the last 30 days."** That
is the sum of 30-day spend over the ten rendered allocation rows.

Priced as *recoverable money* — what the same spend would buy if the measured leak were closed —
the same ten rows are worth:

| | |
|---|---|
| "Under a decision" as printed | **$147,591** |
| Recoverable on the same rows | **$11,707 / month** |
| Concentrated in one ad, one second | **$8,399 — 72%** |
| Rows with no early-retention deficit at all | **5 of 8 measurable** |

Spend-at-stake answers "how much money is near this decision". A marketer is asking "how much
do I get back, and what do I do on Tuesday". Those differ by an order of magnitude and they
rank the page differently: the row ranked #2 by stake ($27,379) is 3rd by recoverable ($304 —
its attention layer is fine, it breaks at Checkout), and the row ranked #3 becomes #2.

### 1.2 The unit of thought is an attribute; the unit of work is an edit

`Make` emits titles like `first product shown = never + spoken language = es`. That is a
specification. Nobody shoots a specification. Marketers iterate on assets they already own.

### 1.3 Combinatorial generation selects for weirdness, not opportunity

`make.py:233` enumerates pairs and triples of proven attributes and **discards any combination
already carried by more than `MAX_CARRIERS` files**. Untriedness is standing in as a proxy for
opportunity. It is not one — selecting for rarity selects for incoherence. Often nobody has
made "no product shown + Spanish + slow cuts" because it is not an ad.

### 1.4 The funnel from evidence to advice is lossy in an arbitrary place

| stage | n |
|---|---|
| tests in the run | 1,175 |
| graded strong/suggestive | 296 |
| survive `clean_main_effects` (unflagged) | 69 |
| positive and prescribable | 20 |
| **used in the 5 briefs** | **6** |

14 of 20 clean, positive, graded attributes appear in no brief — dropped by the
"each brief must introduce a new ingredient" rule (`make.py:261`). That rule exists to stop the
list reading as one idea re-cut, which is a real problem, but it is solving it by discarding
evidence rather than by choosing better.

### 1.5 Thirty-nine rows for a week with time for three

10 allocation + 5 briefs + 24 explore. The per-kind caps treat screen space as the constraint.
The constraint is the shoot calendar.

### 1.6 Statistics are on the surface

`suggestive`, `p`, `n_cell`, `binding ingredient`, `carriers`, `stake basis`, `spend on the
files that lack it`. Grades should govern **whether a row appears**, not be printed beside it.

### 1.7 Defects found in the audit, fixed by the rewrite or separately

- `typical_share` (`allocate.py:132`) is the median over the ≤40 diagnosed ads but renders as
  *"the median spending ad"*. Measured: 0.852% vs a true account median of 0.011%. Live copy.
- `account_spend_30d` has no format filter ($400,410) while rows can only come from image/video
  footage groups ($366,880), so every share on the page reads ~8% low.
- `MakeBrief.evidence` mints `P1/P2/P3` **local to each brief** (`make.py:269`), colliding with
  the run-level P-ids `/insights` prints. The same label means two things on two tabs.
- `scripts/rewrite_account_read.py` does not enqueue `insights.next_plan`, so a read produced by
  the rewrite path never reaches the plan. Observed 2026-09-23: read `01a0cceb` (06:20) with no
  plan job after 04:48. Harmless that day — the rewrite reused the stored statistics byte for
  byte, so only the `next_tests` prose diverged — but it is a silent break by luck, not design.

---

## 2. What carries over from v1 unchanged

- **§2 ownership rule.** `/next` owns *recommendation*; other pages own *state*. If a block
  cannot be the only place its content lives, it does not ship. Fatigue stays absorbed (v1 §2.1).
- **No demographic prescriptions.** `messenger_age_band`, `messenger_gender_presentation`,
  `demographic_signal` remain barred from anything the page prescribes, and remain available as
  reported evidence. `cast` and the language dimensions remain deliberately off that list — a
  Spanish-language cut is an ordinary creative decision (v1 §3), and that stays settled.
- **No predicted ROAS, no per-creative forecast.** See §4.2 for why recoverable money is not a
  forecast and stays inside this ruling.
- **The `next_item` ledger table**, which v1 shipped and never read back. §7.

---

## 3. The unit: one change to one asset

Every row on the page is **one change to one asset the account already owns**, in one of five
verbs:

| verb | the job |
|---|---|
| **Recut** | change a specific segment of an existing ad; keep the rest |
| **Reshoot** | same script, different talent, language or setting |
| **Re-angle** | same structure, different claim or hook |
| **Retire** | stop it, and name what replaces it |
| **Fund** | move budget to a winner |

A change anchored to an existing asset inherits everything already known about that asset,
which is what lets the instruction be specific. *"Shoot a Spanish version of your $61K ad"* is a
job. `spoken_language=es` is a column.

This also disposes of §1.3: candidates are no longer generated by combining attributes, but by
finding the largest measured gap on an asset that already spends. A one-change delta from
something that already works is coherent by construction.

---

## 4. Ranking: recoverable money

### 4.1 The arithmetic

For a retention gap: apply the matched control's hold through the deficit window, keep the
asset's **own** downstream conditional retention, and read off how many more deep viewers the
same spend buys.

```
ratio       = control_hold(t) / asset_hold(t)        # at the deepest early deficit
recoverable = spend_30d × (1 − 1/ratio)              # zero when ratio ≤ 1
rank        = recoverable × P(benefit)
```

Worked, on `stephanie.daily.dose`: hold at 3s is 40.5% against a 48.8% control →
ratio 1.20 → `$50,983 × 0.170 = $8,399/month`.

The same shape applies to the other verbs: a conversion-layer gap prices against the peer
purchase rate at the broken step; a decay prices `first_7d` CPA against `last_30d` at current
spend; a fund row prices the headroom to the peer median.

**Correction, found while writing.** The first draft priced the leak as the cumulative deficit
at 8 seconds. That produced **$0 recoverable for every fix row**, including the $50,983 one —
because that ad is *behind* the norm at 2–3s and *ahead* of it from 6s on (+10.0 points by 12s).
Viewers lost at second 2 are lost permanently; the survivors being unusually sticky does not
recover them, it makes each lost viewer more expensive. Price the trough, then apply the asset's
own downstream curve to the counterfactual survivors. An endpoint measure gets this exactly
backwards on precisely the ads worth fixing.

### 4.2 Why this is not a forecast

The ruling in v1 §3 bars predicted ROAS and per-creative forecasts. Recoverable money stays
inside it because every term is measured: the spend happened, the curve is observed, the control
is observed, and the counterfactual holds the asset's own downstream behaviour fixed rather than
assuming an improvement. It prices **an observed leak**, not a projected gain. The card says
*"this is what the leak costs"*, never *"fixing it will earn you"*.

### 4.3 Matched controls, and the window that had to change

**Correction, found while building.** This section originally specified the `first_7d` window,
so files launched months apart would be compared at the same age rather than by how long the
optimiser kept paying. The data rejected it. On org 3:

| | |
|---|---|
| Spending files with a `first_7d` curve clearing a usable play floor | **30 of 202** |
| Median `first_7d` plays across spending files | **347** |
| `first_7d` plays on the account's largest 30-day spender | **none — no first-week curve at all** |

Equal age is the right principle for ranking creative quality across launches. It is the wrong
window for deciding what to edit today. Step 1 reads the curve and the spend on `last_30d`,
off a clause-identical filter so both describe the same days, and `CURVE_WINDOWS` in
`cs/footage/refresh.py` gained `last_30d` to make that possible (212 curves where there were
none). Coverage went from 30 files to 53, and the account's biggest spender came back.

Controls are **duration-matched**: peers within a factor of 2 of the target's runtime, falling
back to the whole account when fewer than 8 match, and the card names which one it used. This
account runs 4.1s to 268.7s files, so an unmatched control compares a 5-second ad to a
109-second one at the 8-second mark.

Still open: within-campaign and iteration-family contrasts. Duration is a start, not the end.

---

## 5. The pipeline

**Statistics decide what to say. The model looks at the footage and says it. A second model
call tries to kill the card.**

This revises v1 §3's "no new model call for the plan itself", which the user lifted on
2026-09-23. The 2026-09-22 boundary holds unchanged: *the model is never the source of a claim.*

### Stage 1 — Candidates (pure statistics)

Each candidate is `(asset, change, measured gap)`:

| generator | verb | gap |
|---|---|---|
| segment event study | Recut | per-second curve vs matched control, worst window |
| layer comparison | Recut / Re-angle | layer rate vs matched peers |
| decay | Retire | `first_7d` vs `last_30d`, same asset |
| budget | Fund | healthy and below peer median share |

### Stage 2 — Attribute the gap

For the identified window only, against the **pre-registered ~40 attributes**, not 151.
Exposure-weighted, hierarchically shrunk. Output is probability of benefit and effect size —
not a grade.

### Stage 3 — Price and rank

§4. Everything below a recoverable floor is dropped, not demoted.

### Stage 4 — Look at the footage *(model, multimodal)*

For surviving candidates only — 5–10 per account, not a per-ad batch. Send the **specific
segment**: the target's deficit window plus the same window from two reference assets, with the
measured numbers attached. Ask for literal description only: what is on screen, what is said,
what physically differs.

**This is not the retired per-ad write-up.** That was a paragraph per creative, generated
nightly, that nothing displayed (retired 2026-09-22; only 7 ever existed). This is measurement
of a three-second clip, bounded by the number of cards, and its output is description feeding an
instruction — never a verdict. If it drifts back toward a per-creative essay, it is wrong.

### Stage 5 — Write the card *(model, text-only)*

Numbers arrive as placeholders and are substituted after generation, so no figure can be
invented. Output is the instruction, the one-line why, and the shoot spec.

### Stage 6 — Try to kill it *(model, text-only, separate call)*

Given the card and the supporting facts only:

1. Does every number in the card appear in the facts?
2. Does each cited attribute match the claim made about it?
3. Is the instruction physically doable on this asset?
4. Is the gap explained by a listed confound?

Kill or pass. This is the highest-value model call in the design, because it attacks the measured
failure mode: the 2026-09-22 audit found **3 of 4 live findings wrong** past the deterministic
gates — a citation/attribute mismatch, a claim with no test behind it, and a line contradicting
its own CPA figure. Those are what a critic holding the facts catches and a validator does not.

### Stage 7 — Close the loop

Card → `next_item`. When a new asset appears in the family carrying the change, measure it on
`first_7d` and report back **on the card that asked for it**. v1 wrote this table and never read
it. Nothing else on the page earns a marketer's trust as fast.

### Call budget

~30 calls per account per night (10 candidates × describe + write + critic), Flash-tier, mostly
text. Rounding error against a 470-file tagging drain, and it fits the one designed worker.
Single-shot, no retries, logged to the paid-call ledger (`bd19f3b` rules apply).

---

## 6. The card

**Anatomy.** Title is an instruction in the imperative. Then: one money line, one sentence on
what is wrong, one on what must not change, the likely cause with its evidence, two reference
assets from the account's own library, and the physical job. Everything statistical sits behind
a *"Why we believe this"* disclosure.

**Three cards. Nothing else on the page.**

The live example, assembled entirely from data already in the database:

> ### Put a new 3-second top on `stephanie.daily.dose`
> **$8,399/mo** · recut · high confidence
>
> You lose 11% more viewers in second 2 than your norm. That one second costs more than the
> whole rest of the video.
>
> **After second 6 this is your best-retaining ad** — 10 points above median and still widening
> at 12s. Don't touch it.
>
> **Likely cause:** your product is in frame at 0:00. Ads that open without it hold 56% at 3s;
> ads that open on it hold 38%. *(87 files, 13.7M plays — observational, not a designed test.)*
>
> **Copy:** `Riva_Pharmacist` (72% hold, $61K spend) and `AustinBrown` (72%, $50K). Both open on
> a face, no product.
>
> **The job:** 3 seconds. Talking head, no product in frame. Splice at 0:03.
>
> *Why we believe this ▾*

Supporting measurements, all from prod:

| sec | this ad | account median | gap |
|---|---|---|---|
| 1–2 | 87.4% | 89.8% | −2.3% |
| **2–3** | **54.8%** | **65.6%** | **−10.8%** |
| 3–4 | 40.5% | 48.8% | −8.2% |
| 6–7 | 27.9% | 26.5% | +1.3% |
| 11–12 | 23.3% | 13.3% | **+10.0%** |

| opening shot | n | median hold at 3s | plays-weighted |
|---|---|---|---|
| product in frame | 21 | 36.6% | 38.4% |
| no product | 66 | **53.2%** | **55.9%** |

Its first scene is `talking_head, shows_product=true` at 0.0–3.0s. It ranks 64th of 87 on hold
through 3s. The two reference assets are the account's 3rd and 4th best openings **and** its two
largest spenders ($61,427 and $50,592) — not obscure tests.

---

## 7. What gets deleted

- The combinatorial brief generator (`make.py` candidate/combination machinery).
- **Explore** entirely. 24 rows of never-tried taxonomy values is vocabulary browsing. If a value
  is worth trying it earns a card with a reference asset and a number; if it cannot, it does not
  belong on the page.
- The `Stake` vocabulary (`window`, `basis`, "spend on the files that lack it").
- The cross-block cap arithmetic (`MAX_PER_KIND`, `MAX_ROWS`, `MAX_COMBOS`) — replaced by a
  recoverable-money floor and a hard limit of three cards.
- The summary band's client-side total (`next-plan.tsx:93`), the one number on the page that no
  job computes and no probe covers.

What is deleted is larger than what is added.

---

## 8. Storage and the job

`next_plan_run` keeps its shape: one row per account per run, whole payload read in one query,
bounded by three cards. `next_item` gains the read path in §7 of the pipeline.

The job gains the four generators, the pricing step and the three model calls, and keeps its
nightly schedule plus the enqueue on account-read completion — **which `rewrite_account_read.py`
must also issue** (§1.7).

---

## 9. Build order

1. ~~**Segment event study + pricing, no model calls at all.** Cards rendered from templates.~~
   **Built 2026-09-23.** See §9.1.
2. ~~Matched controls beyond duration (within-campaign, within-family) and re-pricing.~~
   **Built 2026-09-23.** See §9.2.
3. ~~The three model calls: describe, write, critic.~~ **Built 2026-09-23.** See §9.3.
4. ~~The ledger read-back.~~ **Built 2026-09-23.** See §9.4.
5. ~~Delete Explore and the combinatorial generator.~~ **Built 2026-09-23.** See §9.5 — the
   step also had to wire the cards through the job, the API and the page, which the build
   order never listed anywhere.

### 9.1 What step 1 shipped, and what the gate changed

`segment.py` (event study and pricing), `cause.py` (conditioned opening contrasts),
`segment_repo.py` (two queries), `recut.py` (card assembly), `scripts/recut_probe.py` (the
gate). No model call anywhere. `CURVE_WINDOWS` gained `last_30d`.

Reading the real output changed four things, each of which looked fine in the abstract:

- **`first_7d` could not carry the curve.** §4.3.
- **A template asserted something false.** The bleed line ended "that one second costs you more
  than the whole rest of the video" — true of the file that motivated the design, false of most,
  including the top card (54% at 2s, 16% at 30s: the tail loses far more). Replaced with the
  excess over the comparison set, which is the part a recut wins back. *No sentence in a
  template may assert more than the arithmetic behind it.*
- **The cause finder was a tautology.** With `label` dropped for being unactionable ("opens on
  something else") and `shows_face` phrased in one direction only, `shows_product` was the sole
  candidate, and all ten cards "found" the same cause. Properties are now stated in both
  directions and compete.
- **The properties co-occur, so the widest raw gap names the wrong cause.** A face at 0:00
  usually has the product beside it. Every contrast is now conditioned on the other properties.
  Measured effect: the product contrast loses 8–10% of its gap (one grows), the face contrast
  loses **35–49%** — about half of "a face at 0:00 hurts" was the product in frame next to it.
  Five of eighteen candidate cards lost their cause entirely and are no longer shown.

A card with no surviving cause is dropped rather than softened: naming the second and the money
without naming the change is a diagnosis, and the diagnosis panel already exists.

**Result on org 3:** 13 cards qualify, $22,253/month recoverable; the top three are $9,874,
$3,100 and $1,925. Card 1 was reproduced from raw SQL independently of the code
($45,947 spend, 54.3% hold at 2s against 69.1% across 34 duration-matched peers, ratio 1.274).

**Not yet done:** nothing is wired into the job, the API or the page — step 1 is a gate, and
the question it exists to answer is whether these are the files you would have picked.

### 9.2 What step 2 shipped, and what the data refused to give

**Campaign confirmation, not campaign matching.** Campaign-and-duration leaves 8 or more peers
for only 23 of 53 eligible files, so making it the control would cost more than half the
account its card — to an empty stratum, not to evidence. It is used instead to *re-check* a
deficit at the second the duration control already found: same file, same second, peers that
ran beside it. Cards are priced on whichever of the two comparisons is less flattering.

| candidates with a deficit | 24 |
|---|---|
| confirmed inside their own campaign | 7 |
| **refuted and dropped** | **2** |
| campaign stratum too thin to check | 15 |

Cards went 13 → 11, $22,253 → $21,180/month. Every card says which of the three it is; a silent
absence would read as a pass, and "could not check" is not "held up".

**Two matchings the data would not support.**

- *Placement.* The breakdown was retired and `metric_daily.placement` is the single value
  `'all'`, so the one confound most likely to move a retention curve cannot be held fixed
  at all.
- *Iteration family.* Org 3's ad names carry real family structure
  (`ChrisJackson_…`, `stephanie.daily.dose`, `MagAshwa_v26_…`), and parsing one account's
  naming convention into the product is not a contract worth signing. Shared copy text is
  generic but sparse — a median of 0 same-copy peers, 8 or more for 16 of 53 files. Family
  matching waits for the embeddings work, as the 2026-09-22 direction assumed.

  There is also a trap to avoid when it arrives: matching on a family that shares an opening
  style destroys the very contrast `cause.py` needs. The deficit control wants tight matching;
  the cause contrast needs variation in the property under test. They cannot be the same set.

**A negative result worth keeping.** Lowering the play floor for *control membership* from
5,000 to 500 raises confirmations from 7 to 10 — and drops refutations from 2 to 0. More peers
pull the control median down, every gap widens, and nothing fails. A check that never refutes
is not a check, so the floor stays where it is.

**Split-half validation (not shipped, but the reason to trust the 15 unconfirmed cards).**
Each file's hold was recomputed from the Fivetran source over the two halves of its own 30-day
window, for the 18 deficits with at least 1,000 plays in both:

| | |
|---|---|
| median \|first half − second half\| | **1.7 points** |
| median deficit being claimed | **14.7 points** |
| worst case | 10.6 points, on a $1,207 file |

The top card moves 53.8% → 54.9%. The measurement is roughly nine times the size of its own
temporal noise, which is what makes a card believable where no campaign stratum exists to
confirm it.

Plays do not fully predict stability — the $11,364 file with 176,584 plays still moved 6.2
points — so a play floor is not a substitute for the check. Shipping it needs either two more
`CURVE_WINDOWS` entries and a migration to widen the `window_kind` constraint, or a
source-side query at build time. Step 3.

### 9.3 What step 3 shipped

`clip.py` (ffmpeg segment extraction), `narrate.py` (the three calls, the substitution gate and
the caches), migration `20260923000002_recut_notes.sql`, and `--with-model` on the probe.
Nothing is billed without that flag.

**Frugality is structural, not a budget.** Three levers, in order of size:

1. *Send the seconds, not the file.* Gemini charges video by duration. The top card's file runs
   111 seconds and its card is about the first three. `clip.py` cuts the segment to 480p/24fps
   before it is sent, which is roughly 35x cheaper and strictly more relevant — the model is
   not asked to find a moment we already measured.
2. *Cache a description on the segment, forever.* Pixels do not change. Reference files recur
   across cards, so the hit rate rises with use: the second and third cards on org 3 needed no
   new reference descriptions at all.
3. *Cache the prose on the rounded measurements.* A file whose hold drifts a tenth of a point
   reuses yesterday's sentence.

Measured on org 3 (three cards):

| | input | output |
|---|---|---|
| clip description (per clip, once ever) | ~1,060 | ~265 |
| write (per card, when the numbers move) | ~2,107 | ~264 |
| critic (per card, when the numbers move) | ~1,420 | ~56 |
| **a second run with nothing changed** | **0** | **0** |

**The model choice was worth 26x.** `gemini-3-flash` spent ~6,900 output tokens deliberating
over a 120-word card and ~7,100 on a verdict. Both calls moved to `gemini-3.1-flash-lite` —
the same choice `account_read_fix_model` made for the same reason — and the card now costs
~264 output tokens. Phrasing what the statistics already decided is not a reasoning task.

**The substitution gate.** The writer emits placeholders and never a digit; `narrate.render`
substitutes them and rejects the output if it contains a number that is not there, or a
placeholder that was never supplied. This is what makes "the model is never the source of a
claim" a property of the code rather than an instruction in a prompt.

Two refinements the real output forced:

- *Placeholder names contain digits.* `{REF1}` made naming a reference a rejection. The scan
  runs on the text with tokens removed.
- *Quoting is not inventing.* The first card asked to quote its references came back with an
  editor's own on-screen line, "me bc I'm 34 and...", and was rejected. A digit run is now
  allowed if that exact run appears in the source text the writer was given, and blocked
  otherwise. A rule that cannot tell a quote from a fabrication forbids quoting real ad copy.

**What the critic is for, and what it is not.** It began as free-form "kill or pass" and killed
three cards out of three, on objections that were partly real and partly invented — including
"the card claims 15 points, but FACTS state this difference is 15 points". It was doing
arithmetic that `render` and `_points` already guarantee. It is now told explicitly not to
check numbers, and asks four narrow boolean questions instead — unmeasured promise, cause
stated as certain, instruction not doable, words contradicting FACTS — with the verdict
computed in code from the answers. A free-form verdict invited the model to invent a generic
objection and then honour it.

It still earned its place before that change: on the first card that reached it, it caught that
the card said "67% against 39%, a 27 point difference". The components and the difference were
being rounded independently, so the card's own arithmetic did not close. That was a defect in
the deterministic layer, found by the critic, and `_points` now derives every difference from
the percentages actually printed.

**Result.** Three of three cards pass. The top card:

> **Replace the first 3 seconds of MagAshwa_v26_…doctor_3AMCortisolMechanism**
> It holds 54% of viewers at 2 seconds; 34 files of similar length hold 69%. This loss costs
> $9,874 in wasted media spend.
> At 30 seconds it maintains 16% of viewers against 10% for its comparison set, a 6 point lead.
> Likely cause: the product is in frame at 0:00. Across 33 files, those with no product in the
> first shot hold 67% at 2 seconds against 39% for files like this one.
> `…ThatGirlSaid34` opens with the text "me bc I'm 34 and that girl said this magnesium with
> ashwaganda made her stop waking up at 3am…" while moving through a city street.
> `…UGC Creators - Authority 2` opens with a man in a lab coat saying "Okay, so I've been
> thinking about this all day."
> Cut the first 3 seconds and replace them with a 3 second opening that removes the product
> from the first shot, joining the existing edit at 3 seconds.

**Not yet done:** still nothing wired into the job, the API or the page. Steps 4 and 5 remain.

### 9.4 What step 4 shipped, and the matcher that failed

`ledger.py` (pure read-back), `ledger_repo.py` (writes and loads), migration
`20260923000003_next_item_recut.sql`, and `--record` / `--backdate` on the probe. v1's
`next_item` table is finally read.

**The baseline has to be frozen.** `play_curve_point` is rewritten in place by every footage
refresh, so by the time anyone asks whether a file improved, the number it was judged against
is gone. `next_item.baseline` is written once on insert and kept with `COALESCE`; refreshing it
alongside the file would report "no change" forever, which is the failure this table exists to
prevent.

**The recut matcher was built and abandoned.** The plan was to recognise a re-edit directly:
a recut keeps the body, so a new file whose scene tail matches an old file's and whose opening
differs is that file, recut. Across 170 tagged files on org 3 it found **zero** such pairs.
The scene labels are too coarse to recognise a re-edit of a video they have already described.
Copy text is the only family signal the data supports -- 11 groups over 86 files, 5 of which
contain more than one opening style -- and it is not evidence of cause either.

So the read-back does not claim causation at all. It answers two separate questions and leaves
the join to the reader:

1. **Has this file stopped bleeding?** The same second, re-measured against the frozen
   baseline: `too_early` (under 7 days), `gone` (stopped spending), `unmeasurable` (too few
   plays), `improved`, `worse`, `unchanged`. `improved` sets the ledger status to `ran` and
   `gone` sets `expired` -- `ran` meaning the thing happened, never that the card caused it.
2. **Has the account started doing what the card asked?** Files whose `first_day` falls after
   the card was issued, split on the property the card named, plays-weighted.

**The read-back turned out to be a validation instrument.** Read as if issued 45 days ago, the
two product cards agree with their own cause -- of 51 newer files, the 38 that avoid the
product hold 68% at 2 seconds against 55% for the 13 that do not. The third card reverses:
its cause says face-free openings hold 53% against 43%, and among newer launches the 5
face-free files hold **37%** against **44%** for the 45 with a face.

That is the temporal holdout the account read has never had (audit, 2026-09-22: "nothing checks
whether a pattern holds on later launches"), arriving as a side effect. `ledger.contradicted`
prints the caveat where the claim is, once both sides hold at least 5 files. It does not
suppress the card: the populations differ, and an account that launches few face-free files
launches unusual ones. Whether a contradicted cause should be allowed to keep its card is §10.

**Not validated:** no card is old enough for a real outcome, so `improved` / `gone` / `worse`
have been exercised against constructed inputs only. `--backdate` measures adoption against
real history, which is the half that can be checked today.

### 9.5 What step 5 shipped

The build order said "delete Explore and the combinatorial generator" and never said who
wires the cards in. Deleting first would have left the page empty, so step 5 is both.

**Wired.** `next_plan` builds the recut block from the footage tables, records each card to
`next_item` with its frozen baseline, reads back the cards already on file, and narrates the
top three. `/insights/next` attaches posters to cards and their references. The page leads
with Recut, keeps Decide, and keeps the read's `next_tests`.

**Narration is gated, not assumed.** The statistics cost nothing and always run; the prose is
gated on `NARRATION_SCHEDULE` ("insights.next_plan_narration") being unpaused, the same switch
that holds the writer jobs since 2026-09-19. A paused account still gets every card, with the
template wording instead of the written wording — a difference in polish, not in content,
because both are rendered from the same numbers. Every narration failure degrades the same
way, and no paid call is retried.

**Deleted:** `make.py`, `explore.py`, `stake.py`, `scripts/next_plan_probe.py`,
`tests/…/test_make.py`, `tests/…/test_explore.py`, the `Evidence` / `Shape` / `Watch` /
`MakeBrief` / `ExploreRow` / `Ref` types, the `make` and `explore` blocks of the payload, and
the page's `BriefCard` and `ExploreRow` components.

**Kept deliberately:** `policy.py`. Make and Explore were the only things it guarded, and the
rule it encodes — the product may report a protected characteristic and may never prescribe
one — outlives them, because `cause.py` is now the only place `/next` turns a measurement into
an instruction. `cause.py` asserts its properties against it at import, so a new property
cannot be added past the bar quietly.

**The summary band changed meaning.** It used to sum allocation stakes and read "Under a
decision — $147,591", a figure 12.6x larger than anything the page could name (§1.1). It now
leads with recoverable media cost, which on org 3 is $15,804 across three cards.

**Checked:** the job runs end to end on org 3 and stores three written cards, ten allocation
rows, and no `make` or `explore` keys; three ledger rows carry their baselines; `tsc`,
`eslint` and `next build` are clean; and the page was read signed in on localhost and renders
the cards, the references, the job line and the read-back. The screenshot of it came back
blank and the browser extension then disconnected, so the visual check rests on the rendered
text rather than on an image.

## 9.6 The generator was rebuilt on the account read (2026-09-23, same day)

Steps 1-5 shipped a page whose every card was about timings and cuts. Asked why, the answer
was that the build order had produced it: the generator searched for the worst second, the
cause vocabulary was three properties of the first shot, and step 5 deleted Make -- the only
block that spoke about what an ad *said*. One verb of five, on the most mechanical layer.

**Two measurements settled it.** Across 53 spending files on org 3, early hold predicts clicks
(Spearman +0.55 against link CTR) and does not predict money (+0.13 against purchases per
1,000, **+0.22 against CPA** -- the wrong direction). The best-holding third of files runs a
pooled CPA of $57.79 against $51.72 for the worst-holding third. The page was pricing an
attention gap in dollars on an account where attention does not buy any.

And the read had already said so, in the same run, graded strong:

> **Failing to show the product at all destroys conversion efficiency.** CPA $92 against $45
> (P1). These files see a suggestive 41% lift in thruplay rate (P75) and fail to convert it.
> *"High retention in these files is a false signal; without a product reveal, the attention is
> commercially hollow."*

The generator was recommending the P75 side of exactly that trade.

**What the read already had, and the parallel engine rebuilt worse.** A cited test carries its
grade, p-value, confound flags, effect size, the metric it was measured on -- and `cell_shas`,
the list of files on each side. The asset anchoring that justified building a separate engine
was in the payload the whole time.

**The rebuild.** `finding.py` resolves each finding's `P<n>` ids against the run's tests, keeps
the main effects that are cleanly graded, unflagged, grade-stable and prescribable, and splits
each into the files that are on the wrong side of it and the files to copy. `card.py` aims the
strongest finding at the highest-spending file on its wrong side. `cause.py` and `recut.py` are
gone -- hand-rolled contrasts over three hardcoded scene properties, replaced by the read's own
19 actionable findings over the full taxonomy.

**The money gate is the point.** A card moves a file from one side of an attribute to the
other, so the purchase and CPA tests for that attribute are checked *in the direction of the
push*. On the first run it blocked two findings, including the one the old generator was built
on:

> BLOCKED P75 `first product shown = never`: graded worse downstream, CPA +108% (strong).
> Adding it would buy attention and cost money.

**Corrections found while rebuilding**, each caught by reading real output:

- *Contradictory cards.* Deduplicating by finding let `duration_s` low and high both ship --
  one file told to get shorter, another to get longer, each citing the other as the model.
  Dedupe is by **dimension**: two values of one single-valued dimension are alternatives. v1's
  Make had this guard and step 5 dropped it.
- *A timestamp on a change that is not made at a timestamp.* The moment was attached by the
  finding's layer, which put "the loss lands at second 2" on a card telling a 111-second file
  to become a short one. A moment is now attached only for `hook_rate` / `thruplay_rate`, never
  for `duration_s`, and is worded as context rather than as the instruction.
- *An edit that is not an edit.* You cannot cut a 111-second file into a 40-second one and call
  it the same file. `RESHOOT_DIMS` gives those the reshoot wording -- brief the next one --
  rather than "change this file and leave the rest alone". The list is conservative and
  additive, because over-claiming a reshoot costs less than instructing an impossible edit.
- *References that argued nothing.* Ranked by spend, the same two biggest spenders appeared on
  every card. Ranked by the cited metric alone, a $261 file became the thing to copy. They are
  now filtered to the median spend of their own group, then ranked on the metric the card
  cites, so a reference carries its own CPA and varies with the finding.

**What the ledger measures now.** "Is this file still losing viewers at second 2" only ever
fitted a recut. A card is "this file is on the wrong side of P<n>", so the read-back asks that
instead, answered from the nightly read's own tagging -- which works for a claim, a cut rate or
a duration. Three rows written by the retired generator were expired rather than deleted: the
ledger is a record of what was recommended, including what was recommended wrongly.

**Result on org 3.** Three cards, all graded on CPA, all citing a P-id: brief a shorter cut of
the $45,947 file (P112), cut the all-in-one claim from the $15,426 file (P20, strong), slow the
cut rate on the $2,677 file (P92). All three passed the critic.

**What survives from steps 1-4:** the per-second curve and duration-matched controls, the clip
and describe calls, the substitution gate, the boolean critic, the ledger and its frozen
baseline. What went: the parallel contrast engine and the "recoverable" pricing that dressed an
attention gap as money.

## 9.7 The card was redesigned for a reader, not a reviewer (2026-09-23)

The generator was right and the card was unreadable: seven stacked paragraphs at identical
weight (`text-sm text-muted-foreground`), so the instruction, the evidence, the hypothesis and
the caveat were typographically indistinguishable and none of them could be skimmed. Numbers
sat mid-sentence. The instruction appeared twice. Nothing was collapsed.

**The card is now ordered by what a marketer does with it:**

| | |
|---|---|
| header | verb chip, file name, and the instruction as the only heading |
| figures | at stake · this ad's CPA · ads doing it right — three cells, tabular, subtractable |
| copy these | two references as posters with their own CPA |
| job | the one concrete line |
| *Why we believe this* | `<details>` — evidence sentence, hypothesis, downstream note, moment |
| since this card | only when the ledger has news; `too_early` is not news |

Visible prose per card went from seven paragraphs to two lines. Everything removed is still
there, one click down, which is the only honest way to cut a page that carries citations.

**The disclosure is a `<details>` element**, not state: `/next` renders on the server and has
no client component, and it should stay that way.

**Corrections found while redesigning:**

- *The figure row did not reconcile.* It showed this ad's own CPA ($51.17) beside the finding's
  graded percentage (−12%), which compares two groups ($46.86 against $41.35). A reader
  subtracting the two numbers on screen gets −19%. The percentage moved under "Why", with its
  P-id; the row now shows three figures that are each other's comparison.
- *The title repeated the file name* the header already shows, spending the one line the reader
  is certain to read. The writer is told not to name it.
- *Taxonomy expressions leaked into the title* — "Create a new cut for duration s is low (under
  51.4s)". Asking politely did not hold, so `render` now rejects `{ATTRIBUTE}` and `{CHANGE_TO}`
  in the title field the way it rejects invented digits. Titles came back as "Brief a shorter
  video cut", "Remove the all-in-one product claim", "Slow the editing pace".
- *A hypothesis stated as fact* got a card killed by the critic. The cause field must now keep
  the read's own hedge rather than strip it.
- *An unmeasured benefit in the job line* — "to keep more people watching" on a card graded on
  CPA. The job line is action only: no reason, no benefit, no "so that" clause.

### 9.7.1 The comparison is the card

The first redesign fixed the hierarchy and still got the subject wrong. Reading what this
product category actually does -- Motion's pitch is that seeing the ad beside its metrics
"removes the abstraction layer that spreadsheets create" -- made the mistake obvious: a
creative analytics tool was rendering the creative at **52 pixels** and rendering the
comparison, which is the entire point, as a list of label/value pairs.

The card body is now the comparison itself:

```
This ad                 Ads with better CPA
[ 104px 4:5 frame ]  >  [ frame ] [ frame ]
$59.56                  $31.17    $46.59
```

The losing figure is `text-destructive`, the winners `text-success` -- the "conditional
formatting to highlight outliers" every dashboard guide names, applied to the one comparison
that matters. Group labels sit above the posters and values below, so each column reads
top-down. 4:5 is Meta's feed frame: it crops a 9:16 TikTok cut far less brutally than a square
and does not letterbox a landscape file into a stripe. No caption under a reference -- at that
width every file on this account renders as "MagAshwa_…", which identifies nothing; the frame
is the identity and the link carries the rest.

**Three writer leaks the redesign exposed**, each now a code gate rather than a prompt plea:

- *The title is the instruction slot*, so `{ATTRIBUTE}` and `{CHANGE_TO}` are rejected in it.
  The writer had been emitting "Create a new cut for duration s is low (under 51.4s)".
- *The job line is too*, for the same reason. It had been "Request a new cut that meets
  duration s is low (under 51.4s) for the next briefing."
- *The fallback headline was a claim.* When the critic killed a card, the template supplied the
  finding's title -- "Low cut rates match the product's calm positioning" -- which reads well
  and instructs nothing. The fallback is now blunt and imperative; the finding's title moved
  under "Why we believe this", where a claim belongs.

**`plain_value` and the digit gate.** Barring the attribute expression from the job line left
the writer unable to state a threshold at all, since every number must come from a placeholder.
`{TARGET}` supplies the change in plain words -- "under 51.4s", "all in one solution", "2 and
up cuts per 10s" -- keeping the dimension only when the range does not carry its own unit.
And the digit gate was extended: a run that appears in the supplied placeholder values is not
fabricated, so the writer typing "51.4s" instead of "{TARGET}" is allowed, while "under 30s" is
still rejected. Whether a real number is used in the *right place* stays the critic's job.

Result: "Brief a new cut under 51.4s", "Remove the all in one solution claims", "Slow the
editing pace of this ad", each with a one-line job and everything else one click down.

## 9.8 The page contradicted Insights, and the gate could not see it

A card read "Brief a new cut under 51.4s" while `/insights` said, graded **strong**, "Long-form
content earns significantly higher click-through rates without increasing CPA". Both came from
the same run. The whole dimension:

| | value | metric | |
|---|---|---|---|
| P2 | high | link CTR | **+70.6% strong** |
| P14 | high | thruplay | **+23.4% strong** |
| P115 | high | hook rate | +8.0% suggestive |
| P11 | low | link CTR | **−26.5% strong** |
| P112 | low | CPA | −11.8% *suggestive* |

The card asked a 111-second file (`duration_s = high`) to become short: giving up two strong
results and walking into a third, for one suggestive one.

**Why nothing caught it.** `money_blocks` checks the money tests for the value a file is moving
*to*. It cannot see the value it is moving *from*, and for a single-valued dimension the values
are alternatives -- moving to one *is* leaving another. `Rival` and `trade_blocks` close that:
a card is now judged against every graded result on its whole dimension, with the file's own
current value supplying the other half of the trade, which is why the check runs per file.

**Grade alone was the wrong test.** The first version blocked on grade, and all three cards
turned into attention results -- because strong CTR findings out-graded suggestive CPA ones.
That is a proxy overruling the outcome it stands in for, on an account where the proxy is
measurably disconnected from it (§9.6: early hold correlates +0.55 with link CTR and +0.22 with
CPA, the wrong way). A rival now blocks only when it is **at least as well graded and at least
as far down the funnel**.

**What survives is a trade, so the card says so.** `trade_note` puts the cost on the card face,
naming the rival by P-id. The reader meets the tension and the reasoning instead of two pages
that disagree. The read names it too, in that finding's own title: "Short-form content is
click-poor but suggestively purchase-efficient."

**The folded budget verdict was a second contradiction.** §9.7's fold-in pasted "Recut the
opening; keep the rest" onto a card asking for a whole new shorter cut -- two incompatible
instructions, one of them attention-priced. A `fix` verdict now contributes its layer as a
fact and not its prescription; `replace` and `scale` describe economics and fold in whole.

## 10. Open questions

- **What is the right matched control?** Duration-matching ships in step 1. Within-campaign is
  cleanest but thin on ASC campaigns, which are 14 of org 3's 17, and iteration family needs the
  embeddings work.
- **`footage_metric_window` anchors its 30-day window on `current_date`**, while the 30-day
  matviews anchor on each account's last day with delivery (migration `20260922000001`) exactly
  because the wall clock slides off the data when syncs stop. Data currently runs through
  2026-09-21, so the footage window is two days short. The new curve window copies the same
  clause deliberately, so the curve and the spend agree with each other; both should be moved to
  the delivery anchor in one change.
- **Should a contradicted cause keep its card?** §9.4 found one card whose cause reverses on
  the account's newer launches. Today it prints a caveat and still ships. The alternative is to
  demote or drop it, which would mean the ledger feeds back into card selection -- a bigger
  change than a warning line, and one that needs a rule for how much newer evidence outweighs
  the window it was measured on.
- **Reference picking ignores whether a reference is imitable.** It ranks by hold and filters
  by spend, and on the third card both references came back silent text-over-b-roll. They are
  correct — they lack the property the card says to drop — but a silent street scene is a poor
  template for an editor. The clip descriptions now make this visible on the card; whether
  they should also feed the ranking is undecided.
- **The two surfaces name the same file differently.** `/next` v1 picks a footage group's
  representative by 30-day spend; `footage.rep_creative_id` picks by lifetime spend. The top card
  and the v1 allocation row for the same file print different ad names.
- **Does the opening-product effect survive a designed test?** It is observational across 87
  files. Ads that open on the product may differ in other ways. The card says "likely cause" for
  that reason, and this is a first candidate for a designed batch.
- **Three cards, or three per verb?** Three total is what shipped (`jobs.MAX_CARDS`). It is a
  guess about the shoot calendar and should be checked with the user.
- **The duration trade is a real choice, not a bug.** Short cuts are cheaper per purchase
  (suggestive); long cuts earn far more clicks at the same CPA (strong). `/next` picks money
  and says what it costs. Whether that is the right default for this account is the user's
  call, and the rule that makes it -- outcome outranks proxy -- is one line in `finding._tier`.
- **Verb coverage.** §3 names Recut, Reshoot, Re-angle, Retire and Fund. After §9.6 a card
  picks its own verb from the attribute, so Recut, Reshoot and Re-angle all occur; Retire and
  Fund are still the surviving allocation block, which speaks v1's language.
- **The writer still leaks attribute expressions.** "the claims = all in one solution is on the
  wrong side of the CPA" made it past both gates. The facts give it `{ATTRIBUTE}` as a label
  and it sometimes repeats the label instead of the English. The gates check numbers and
  claims, not readability.
- **`next_item.kind` is still `'recut'`** for every card, including reshoots and edits. The
  column's CHECK was widened for the old generator and has not been revisited.
- **What happens when nothing clears the floor?** A page that says "nothing worth changing this
  week" is honest and probably correct some weeks. Confirm that is acceptable before building it.
