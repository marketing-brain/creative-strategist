# Ask box: typed query kinds

**Date:** 2026-09-17
**Status:** Approved in conversation.
**Scope:** Let the ask box answer five kinds of question a strategist would not think to compute by hand, all from the stored account read run, and show the user how each question was read. No change to the run builder, the nightly job, or the tab placement.

## Problem

Every ask today compresses to one shape: compare the files that carry attribute V against the files that do not, on metric layer L. That shape cannot express the questions that serve the actual job, which is deciding what to make next. Five of those questions are answerable from the run as it is stored:

| # | Question, in the strategist's words | What the answer needs |
|---|---|---|
| 1 | Where is our money going versus where the evidence points? | Spend share per graded attribute against its effect |
| 2 | Which proven ingredients have we never combined? | Positive clean effects, paired, checked against every unit's facet set |
| 3 | What separates our best CPA video from the ads most like it that failed? | Nearest units by facet overlap, then the attribute diff |
| 4 | Is the expert effect real, or just where we spent the money? | The same comparison rerun within strata of a control |
| 5 | Which single ingredient is doing the work in our best recipe? | One unit's facets ranked by their graded effects |

A second, independent defect: the response never shows the mapped query, so a silent misread yields a confident, number-checked answer to a question the user did not ask.

On org 3 (56 units, 35 dimensions, 1 strong and 132 suggestive main effects, top five units carrying 58% of spend) every one of these has material to work with.

## Decisions

| Decision | Choice |
|---|---|
| Routing | The existing map call returns a `kind` plus the fields that kind needs. One prompt, six kinds. No tool calling: the inference path stays in code |
| Kinds | `compare` (unchanged), `spend_gap`, `untried`, `nearest`, `controlled`, `recipe` |
| Analyzers | One pure function per kind in `analyzers.py`. Each returns graded `Test` rows to cite and descriptive fact lines. No new statistics: `compare`, `benjamini_hochberg`, `grade` are reused |
| Evidence blocks | The answer prompt carries COMPARISONS (P-ids, `pattern_line` as today) and FACTS (F-ids, one line each). Number grounding runs over both |
| Verdict cap | Unchanged. It counts cited P-ids only, so a descriptive kind cannot reach `answerable` without graded evidence behind it |
| Read-as | Rendered in code from the parsed query, never by the model. Returned as `read_as` and shown under the question |
| Interface | "Read as" line, facts list above the evidence list, five seeded question chips in the empty state. Nothing else moves |
| Real-data gate | Five reference questions plus ten paraphrases run against the org 3 stored run with the live writer, reviewed by hand, unmappable rate recorded, before the work is called done |

## 1. Query contract

`map_question` returns one of:

```
{"unmappable": "<one sentence>"}
{"kind": "compare",    "targets": [...], "group": [d, v] | null, "layer": L}
{"kind": "spend_gap",  "layer": L}
{"kind": "untried",    "layer": L}
{"kind": "nearest",    "anchor": A, "layer": L}
{"kind": "controlled", "target": [d, v], "control": [d, v] | "spend", "layer": L}
{"kind": "recipe",     "anchor": A, "layer": L}
```

- `L` is one of `attention | retention | click | conversion | all`, resolved through `LAYER_METRICS` as today. Kinds that need one metric to rank by (`nearest`, `recipe` default anchor) use the layer's first metric; `all` resolves to `cpa` for ranking.
- `A` is `{"best": "<metric>"}` or `{"named": "<substring>"}`. `best` picks the unit with the best value of that metric among units that clear the metric's floor (`IMP_FLOOR`, `PLAYS_FLOOR`, or `MIN_PURCHASES` purchases for pooled metrics). `named` matches case-insensitively against headline, concept summary and `concept_tags` values; ties go to the highest spend.
- The map prompt lists the dimensions and values with enough data (as now) and adds one line per kind describing when to choose it, with the reference question as the example.
- Unknown `kind` with `targets` present falls back to `compare`. Unknown `kind` without targets is unmappable.

`read_as(query) -> str` renders the parsed query in plain words. Examples:

| Query | Read as |
|---|---|
| compare, targets `[registers=fear]`, layer conversion | fear register against everything else, on conversion metrics |
| spend_gap, layer all | spend share of every graded attribute against its effect, on all metrics |
| untried, layer click | pairs and triples of attributes with clean positive click effects that no file carries together |
| nearest, best cpa, layer conversion | the five files most like the best-CPA file that convert worse than it |
| controlled, target `messenger=expert_on_camera`, control spend, layer retention | expert on camera re-tested within each spend tercile, on retention metrics |
| recipe, named "pharmacist", layer all | the ingredients of the file matching "pharmacist", ranked by their graded effects |

## 2. Analyzers

`analyzers.py` exposes `run(query, units, tests, overlaps, draws) -> Analysis` where

```
@dataclass
class Analysis:
    tests: list[Test]      # cited as P1.. in prompt order
    facts: list[str]       # cited as F1.. in prompt order
    empty_reason: str | None   # set when the kind has nothing to say
```

`tests` in the input are the run's stored main effects and interactions (`result["tests"]`), so no comparison is recomputed unless the kind needs a stratum that the run does not hold.

**spend_gap.** For every main effect (group `None`) whose metric is in the layer, grade is strong or suggestive, and confounds are empty: spend share = cell spend / total spend, file share = `n_cell` / units. Over-funded when spend share ≥ 1.5 × file share and the effect is bad (negative on higher-is-better, positive on `cpa`). Starved when spend share ≤ 0.67 × file share and the effect is good. One fact per flagged attribute: `F1 | expert credential | 20 of 56 files | 31% of spend | link CTR +52% suggestive (P3)`. Tests: the flagged effects. Empty reason: "no graded attribute is over- or under-funded".

**untried.** Candidates: main effects on the layer, strong or suggestive, unflagged, good direction, one per `(dimension, value)` keeping the best p. Two candidates are compatible when they are not two values of one single-valued dimension and are not an entangled pair in `overlaps` (Jaccard ≥ 0.6). Every compatible pair and triple is counted against unit facet sets. Report combos carried by 0 or 1 file, ranked by summed |change|, top five. One fact per combo: `F1 | expert credential + second-half reveal + 40 to 60 messenger | 0 files carry all three | ingredients P2, P5, P9`. Tests: the ingredients. Empty reason when fewer than two candidates exist.

**nearest.** Resolve the anchor. Rank other units by Jaccard over facets (excluding the numeric tercile dimensions, which are outcomes of the run rather than choices). Keep the five most similar that are worse than the anchor on the ranking metric and clear its floor. Facts: the anchor record (headline, spend, metric value), one line per neighbour (headline, similarity, metric value), then the diff: facets in the anchor absent from at least three neighbours, and facets in at least three neighbours absent from the anchor. Tests: the main effect of every diff facet, when one exists. Empty reason when no neighbour underperforms.

**controlled.** Target `(d, v)`. Strata: for `"spend"`, spend terciles over all units; for a partner attribute, inside and outside it. Within each stratum, `compare(cell, comp, group=<label>, draws=draws)` on the layer's metrics, then `benjamini_hochberg` and `grade` across the produced tests. Facts: one line per stratum with its file counts. Tests: the stratum tests plus the overall main effect for reference. Empty reason when no stratum reaches `MIN_CELL` on both sides.

**recipe.** Resolve the anchor. For each facet the anchor carries, find the best clean main effect on the layer, and any interaction whose group is another facet of the anchor. Rank by grade then |change|. Facts: the anchor record, one line per facet with its rank. Tests: the effects found. Empty reason when no facet has a graded effect.

## 3. Answer step and response

`answer()` becomes: map → `read_as` → analyzer → prompt → writer → grounding retry → cap. The prompt:

```
QUESTION: ...
READ AS: ...
FACTS:
F1 | ...
COMPARISONS:
P1 | ...
```

`ANSWER_SYSTEM` gains: "FACTS are descriptive counts and shares; COMPARISONS are graded tests. Cite both by id. The READ AS line is how the question was interpreted; if it misses the question, say so in one clause." `pattern_ids` may hold F-ids and P-ids; the cap sees P-ids only.

Response:

```
{kind, read_as, verdict, answer, pattern_ids, facts: [{id, text}], tests, what_would_settle_it}
```

or `{unmappable}`. An analyzer with `empty_reason` short-circuits to `{kind, read_as, verdict: "not yet", answer: <reason>, ...}` without a writer call, mirroring today's "too few files" path.

## 4. Interface

`account-read-ask.tsx`:

- Under each question, once the answer lands: `Read as: <read_as>` in the muted small style.
- Above the evidence list, when `facts` is non-empty: a compact list, one line per fact, monospace id then text.
- Empty state (no entries yet): five chips beneath the input, one per new kind, each the reference question from the table above. Clicking a chip submits it.

`types.ts`: `AccountReadAnswer` gains `kind?`, `read_as?`, `facts?: {id: string; text: string}[]`.

## 5. Errors and limits

- Anchor not found → `{unmappable: "no file matches \"<substring>\""}`.
- `controlled` with a control that no unit carries → unmappable naming it.
- Draws stay at 300 per ask; `controlled` is the only kind that runs permutations, and at most three strata.
- Questions are still capped at 500 characters at the router.

## 6. Testing

- `tests/insights/account_read/test_analyzers.py`: synthetic units extending the ask fixture. spend_gap flags an over-funded and a starved attribute and ignores a flagged one; untried finds a zero-count pair, skips a same-dimension pair and an entangled pair; nearest returns the anchor, five neighbours and a correct diff; controlled yields group-labelled tests and honours `MIN_CELL`; recipe ranks facets by grade then change.
- `test_ask.py`: map tests per kind with `FakeAccountReadWriter`; `read_as` per kind; fallback for unknown kind; the empty-reason short circuit makes no writer call; the cap ignores F-ids.
- `web/tests/components/account-read-ask.test.tsx`: read-as line, facts list, chip submits its question.
- Real-data gate: `scripts/ask_probe.py` takes an ad account, runs the five reference questions and ten paraphrases through the live map and writer against the latest stored run, and prints `read_as`, verdict and answer for each. Reviewed by hand; unmappable rate recorded in the plan's completion note.

## Out of scope

Temporal and fatigue questions, beat-order and drop-off questions, order value and ROAS, placement and audience breakdowns (one week and one month of data), tab placement, storing ask answers, and the unverified ask-input behaviour on the EC2 box.
