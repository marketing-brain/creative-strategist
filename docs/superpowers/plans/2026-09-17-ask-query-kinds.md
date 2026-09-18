# Ask Box Typed Query Kinds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the ask box answer five new kinds of question (spend gap, untried combinations, nearest neighbours, controlled re-test, recipe) from the stored account read run, and show the user how every question was read.

**Architecture:** The map call returns a `kind` plus that kind's fields. A new `analyzers.py` holds one pure function per kind that turns the run's units, graded tests and overlaps into tests to cite and fact lines to state. `ask.answer` renders a `read_as` sentence in code, builds a prompt with FACTS and COMPARISONS blocks, and keeps the existing grounding retry and verdict cap. The web component shows the read-as line, the facts and five seeded question chips.

**Tech Stack:** Python 3.12, pure-Python stats already in `stats.py`, pytest with `asyncio_mode = "auto"`, ruff (line length 100), Next.js component with vitest and testing-library.

**Spec:** `docs/superpowers/specs/2026-09-17-ask-query-kinds-design.md`

## Global Constraints

- No new dependencies. No numpy, no scipy, no tool-calling path in the writer.
- The run builder, the nightly job, the `account_read_run` table and the tab placement do not change.
- The verdict cap `cap_verdict` counts cited P-ids only; F-ids never raise a verdict.
- `read_as` is rendered in code from the parsed query, never taken from the model.
- Draws per ask stay at 300 (the router passes `draws=300`); `controlled` runs at most three strata.
- Statistics reused as-is: `compare`, `benjamini_hochberg`, `grade`, `MIN_CELL = 5`, `MIN_PURCHASES = 20`, `IMP_FLOOR = 2000`, `PLAYS_FLOOR = 500`.
- Run `uv run ruff format src tests scripts` before `make lint`; the code blocks below are written to the 100-column limit but the formatter has the final say on wrapping.
- Every task ends green on `make lint` and `uv run pytest -q tests/insights/account_read tests/api/test_account_read.py` (Python tasks) or `cd web && npm test` (web tasks), then commits with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Python tests touching the database need `DATABASE_URL_TEST` exported; the account_read unit tests here do not touch the database.

## File Structure

| File | Responsibility |
|---|---|
| Create `src/cs/insights/account_read/analyzers.py` | `Analysis` dataclass, anchor resolution, fact formatting, the five analyzers, `run()` dispatcher. Pure functions, no model calls |
| Modify `src/cs/insights/account_read/ask.py` | Map prompt with kinds, query parsing, `read_as`, `answer()` orchestration with FACTS block |
| Create `tests/insights/account_read/test_analyzers.py` | One test group per analyzer on synthetic units |
| Modify `tests/insights/account_read/test_ask.py` | Map parsing per kind, `read_as`, empty short circuit, cap ignores F-ids, response shape |
| Modify `web/src/lib/types.ts` | `AccountReadAnswer` gains `kind`, `read_as`, `facts` |
| Modify `web/src/components/account-read-ask.tsx` | Read-as line, facts list, seeded chips |
| Modify `web/tests/components/account-read-ask.test.tsx` | Tests for the three additions |
| Create `scripts/ask_probe.py` | Real-data gate: reference questions and paraphrases against a stored run with the live writer |

Existing helpers the tasks reuse (do not rewrite): `writer.pattern_line(pid, test)`, `writer.label(test)`, `writer.fmt_val(metric, v)`, `writer.METRIC_NAME`, `writer._numbers(text)`, `stats.unit_rates(u)`, `stats.pooled(units, num, den)`, `patterns.GRADE_RANK`, `facets.NUMERIC_DIMS`, `types.LOWER_IS_BETTER`, `types.RATE_METRICS`, `types.METRICS`, `types.test_from_json`, `types.unit_from_json`, `types.to_json`.

---

### Task 1: Analysis type, unit metrics, anchor resolution and fact helpers

**Files:**
- Create: `src/cs/insights/account_read/analyzers.py`
- Test: `tests/insights/account_read/test_analyzers.py`

**Interfaces:**
- Consumes: `Unit`, `Test` from `types.py`; `unit_rates`, `pooled`, `MIN_PURCHASES` from `stats.py`; `METRIC_NAME`, `fmt_val` from `writer.py`.
- Produces:
  - `@dataclass Analysis(tests: list[Test], facts: list[str], empty_reason: str | None)`
  - `is_good(t: Test) -> bool`
  - `clean_main_effects(tests: list[Test], metrics: tuple[str, ...]) -> list[Test]`
  - `unit_metric(u: Unit, metric: str) -> float | None`
  - `resolve_anchor(anchor: dict, units: list[Unit], metric: str) -> Unit | None`
  - `unit_line(u: Unit, metric: str) -> str`
  - `facet_words(f: tuple[str, str]) -> str`
  - `rank_metric(metrics: tuple[str, ...]) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/insights/account_read/test_analyzers.py`:

```python
from datetime import date
from decimal import Decimal

from cs.insights.account_read import analyzers as az
from cs.insights.account_read.types import Test, Unit


def _unit(
    sha: str,
    facets,
    *,
    spend: int = 1000,
    impressions: int = 20_000,
    link_clicks: int = 600,
    purchases: int = 30,
    headline: str | None = None,
    concept_summary: str | None = None,
) -> Unit:
    return Unit(
        sha256=sha,
        creative_ids=[],
        rep_creative_id=None,
        headline=headline or f"Ad {sha}",
        duration_s=60.0,
        spend=Decimal(spend),
        impressions=impressions,
        plays=15_000,
        video_3s_views=8_000,
        thruplays=3_000,
        p25=8000,
        p50=5000,
        p75=3000,
        p100=2000,
        link_clicks=link_clicks,
        purchases=purchases,
        first_day=date(2026, 6, 1),
        last_day=date(2026, 6, 30),
        facets=set(facets),
        numeric={},
        first_product_s=None,
        concept_summary=concept_summary,
        hook_verbatim=None,
        insight_paragraph=None,
    )


def _test(
    dim: str,
    val: str,
    *,
    metric: str = "link_ctr",
    change: float = 0.5,
    p: float = 0.01,
    grade: str = "suggestive",
    cell_shas=(),
    group: str | None = None,
    confounds=(),
) -> Test:
    return Test(
        dimension=dim,
        value=val,
        group=group,
        metric=metric,
        kind="pooled" if metric in ("purchases_per_1k", "cpa") else "per_unit",
        n_cell=len(cell_shas),
        n_complement=10,
        cell_value=0.03,
        comp_value=0.02,
        change=change,
        p=p,
        grade=grade,
        confounds=list(confounds),
        cell_shas=list(cell_shas),
    )


def test_is_good_respects_lower_is_better():
    assert az.is_good(_test("a", "b", metric="link_ctr", change=0.2))
    assert not az.is_good(_test("a", "b", metric="link_ctr", change=-0.2))
    assert az.is_good(_test("a", "b", metric="cpa", change=-0.2))
    assert not az.is_good(_test("a", "b", metric="cpa", change=0.2))


def test_clean_main_effects_filters_group_grade_confounds_and_metric():
    keep = _test("a", "b")
    tests = [
        keep,
        _test("a", "c", group="x=y"),
        _test("a", "d", grade="thin"),
        _test("a", "e", confounds=("seasonality",)),
        _test("a", "f", metric="cpa"),
    ]
    assert az.clean_main_effects(tests, ("link_ctr",)) == [keep]


def test_unit_metric_respects_floors():
    u = _unit("u", [], impressions=20_000, link_clicks=600, purchases=30, spend=1500)
    assert az.unit_metric(u, "link_ctr") == 600 / 20_000
    assert az.unit_metric(u, "cpa") == 50.0
    assert az.unit_metric(u, "purchases_per_1k") == 1.5
    low = _unit("l", [], impressions=100, purchases=5)
    assert az.unit_metric(low, "link_ctr") is None
    assert az.unit_metric(low, "cpa") is None


def test_resolve_anchor_best_cpa_ignores_units_below_floor():
    cheap_but_thin = _unit("thin", [], spend=100, purchases=5)  # cpa 20, below purchase floor
    best = _unit("best", [], spend=1200, purchases=40)  # cpa 30
    worse = _unit("worse", [], spend=2000, purchases=40)  # cpa 50
    assert az.resolve_anchor({"best": "cpa"}, [cheap_but_thin, worse, best], "cpa") is best


def test_resolve_anchor_named_matches_headline_summary_and_concept_tags():
    a = _unit("a", [("concept_tags", "pharmacist_warning")], spend=100)
    b = _unit("b", [], headline="The Pharmacist ad", spend=500)
    c = _unit("c", [], concept_summary="A pharmacist explains magnesium.", spend=300)
    d = _unit("d", [], headline="Unrelated")
    assert az.resolve_anchor({"named": "pharmacist"}, [a, b, c, d], "cpa") is b  # tie -> spend
    assert az.resolve_anchor({"named": "nothing here"}, [a, b, c, d], "cpa") is None


def test_unit_line_and_facet_words():
    u = _unit("u", [], spend=1234, headline="Beware", link_clicks=600)
    line = az.unit_line(u, "link_ctr")
    assert line == '"Beware" | $1,234 spend | 0 copies | link CTR 3.0%'
    below = _unit("b", [], impressions=100)
    assert az.unit_line(below, "link_ctr").endswith("link CTR below floor")
    assert az.facet_words(("hook_devices", "audience_callout")) == "hook devices = audience callout"


def test_rank_metric():
    assert az.rank_metric(("hook_rate",)) == "hook_rate"
    assert az.rank_metric(("purchases_per_1k", "cpa")) == "purchases_per_1k"
    assert az.rank_metric(("hook_rate", "thruplay_rate", "link_ctr", "purchases_per_1k", "cpa")) == "cpa"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'cs.insights.account_read.analyzers'`

- [ ] **Step 3: Write the module**

Create `src/cs/insights/account_read/analyzers.py`:

```python
"""Deterministic analyzers behind the ask box's typed query kinds. Each turns the stored run
(units, graded tests, overlaps) into tests to cite and fact lines to state. No model calls."""

from __future__ import annotations

from dataclasses import dataclass, field

from cs.insights.account_read.stats import MIN_PURCHASES, pooled, unit_rates
from cs.insights.account_read.types import LOWER_IS_BETTER, METRICS, RATE_METRICS, Test, Unit
from cs.insights.account_read.writer import METRIC_NAME, fmt_val

GOOD_GRADES = ("strong", "suggestive")


@dataclass
class Analysis:
    tests: list[Test] = field(default_factory=list)  # cited as P1.. in this order
    facts: list[str] = field(default_factory=list)  # cited as F1.. in this order
    empty_reason: str | None = None


def is_good(t: Test) -> bool:
    return t.change < 0 if t.metric in LOWER_IS_BETTER else t.change > 0


def clean_main_effects(tests: list[Test], metrics: tuple[str, ...]) -> list[Test]:
    return [
        t
        for t in tests
        if t.group is None and t.metric in metrics and t.grade in GOOD_GRADES and not t.confounds
    ]


def unit_metric(u: Unit, metric: str) -> float | None:
    """The unit's own value of a metric, or None when it sits below the metric's floor."""
    if metric in RATE_METRICS:
        return unit_rates(u).get(metric)
    if u.purchases < MIN_PURCHASES:
        return None
    if metric == "purchases_per_1k":
        v = pooled([u], "purchases", "impressions")
        return 1000 * v if v is not None else None
    return pooled([u], "spend", "purchases")


def rank_metric(metrics: tuple[str, ...]) -> str:
    """The metric a kind ranks by: the layer's first metric, or CPA for the whole set."""
    return "cpa" if len(metrics) == len(METRICS) else metrics[0]


def resolve_anchor(anchor: dict, units: list[Unit], metric: str) -> Unit | None:
    if "named" in anchor:
        needle = str(anchor["named"]).lower()
        hits = [
            u
            for u in units
            if needle in u.headline.lower()
            or needle in (u.concept_summary or "").lower()
            or any(d == "concept_tags" and needle in v.lower() for d, v in u.facets)
        ]
        return max(hits, key=lambda u: u.spend) if hits else None
    scored = [(v, u) for u in units if (v := unit_metric(u, metric)) is not None]
    if not scored:
        return None
    pick = min if metric in LOWER_IS_BETTER else max
    return pick(scored, key=lambda x: x[0])[1]


def unit_line(u: Unit, metric: str) -> str:
    v = unit_metric(u, metric)
    val = fmt_val(metric, v) if v is not None else "below floor"
    n = len(u.creative_ids)
    return (
        f'"{u.headline}" | ${float(u.spend):,.0f} spend | {n} cop{"y" if n == 1 else "ies"} | '
        f"{METRIC_NAME[metric]} {val}"
    )


def facet_words(f: tuple[str, str]) -> str:
    return f"{f[0].replace('_', ' ')} = {str(f[1]).replace('_', ' ')}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py && make lint`
Expected: all PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/cs/insights/account_read/analyzers.py tests/insights/account_read/test_analyzers.py
git commit -m "feat(account-read): analyzer scaffolding for typed ask kinds

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: spend_gap analyzer

**Files:**
- Modify: `src/cs/insights/account_read/analyzers.py`
- Test: `tests/insights/account_read/test_analyzers.py`

**Interfaces:**
- Consumes: `Analysis`, `clean_main_effects`, `is_good` from Task 1; `label`, `METRIC_NAME` from `writer.py`.
- Produces: `spend_gap(units: list[Unit], tests: list[Test], metrics: tuple[str, ...]) -> Analysis`

- [ ] **Step 1: Write the failing test**

Append to `tests/insights/account_read/test_analyzers.py`:

```python
def test_spend_gap_flags_over_funded_and_starved_attributes():
    heavy = [_unit(f"h{i}", [("registers", "fear")], spend=5000) for i in range(3)]
    light = [_unit(f"l{i}", [("proof_devices", "expert_credential")], spend=1000) for i in range(4)]
    rest = [_unit(f"r{i}", [("angles", "novelty")], spend=2000) for i in range(3)]
    units = heavy + light + rest  # total spend 25,000 across 10 files; novelty is proportional
    fear = _test("registers", "fear", change=-0.3, cell_shas=[u.sha256 for u in heavy])
    expert = _test(
        "proof_devices", "expert_credential", change=0.5, cell_shas=[u.sha256 for u in light]
    )
    novelty = _test("angles", "novelty", change=0.4, cell_shas=[u.sha256 for u in rest])
    flagged = _test(
        "registers", "fear", change=-0.5, cell_shas=[u.sha256 for u in heavy],
        confounds=("budget_concentration",),
    )
    out = az.spend_gap(units, [fear, expert, novelty, flagged], ("link_ctr",))
    assert out.empty_reason is None
    assert out.tests == [fear, expert]
    assert out.facts == [
        "over-funded | registers = fear | 3 of 10 files | 60% of spend | link CTR -30% suggestive (P1)",
        "starved | proof devices = expert credential | 4 of 10 files | 16% of spend | "
        "link CTR +50% suggestive (P2)",
    ]


def test_spend_gap_empty_when_nothing_is_skewed():
    units = [_unit(f"u{i}", [("angles", "novelty")]) for i in range(5)] + [
        _unit(f"v{i}", []) for i in range(5)
    ]
    t = _test("angles", "novelty", change=0.4, cell_shas=[f"u{i}" for i in range(5)])
    out = az.spend_gap(units, [t], ("link_ctr",))
    assert out.tests == [] and out.facts == []
    assert out.empty_reason.startswith("No graded attribute")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py -k spend_gap`
Expected: FAIL with `AttributeError: module ... has no attribute 'spend_gap'`

- [ ] **Step 3: Implement**

Add to `analyzers.py` (extend the writer import to `from cs.insights.account_read.writer import METRIC_NAME, fmt_val, label`):

```python
OVER_FUNDED, STARVED = 1.5, 0.67  # spend share against file share


def spend_gap(units: list[Unit], tests: list[Test], metrics: tuple[str, ...]) -> Analysis:
    """Graded attributes whose share of spend is out of line with their share of files and
    their effect: over-funded when heavy and bad, starved when light and good."""
    total = float(sum(u.spend for u in units)) or 1.0
    n = len(units)
    by_sha = {u.sha256: u for u in units}
    rows: list[tuple[str, float, Test]] = []
    for t in clean_main_effects(tests, metrics):
        cell_spend = sum(float(by_sha[s].spend) for s in t.cell_shas if s in by_sha)
        spend_share, file_share = cell_spend / total, len(t.cell_shas) / n
        if spend_share >= OVER_FUNDED * file_share and not is_good(t):
            rows.append(("over-funded", spend_share, t))
        elif spend_share <= STARVED * file_share and is_good(t):
            rows.append(("starved", spend_share, t))
    rows.sort(key=lambda r: (r[0] != "over-funded", -r[1]))
    out = Analysis()
    for verdict, share, t in rows:
        out.tests.append(t)
        out.facts.append(
            f"{verdict} | {label(t)} | {len(t.cell_shas)} of {n} files | {share * 100:.0f}% of "
            f"spend | {METRIC_NAME[t.metric]} {t.change * 100:+.0f}% {t.grade} (P{len(out.tests)})"
        )
    if not rows:
        out.empty_reason = (
            "No graded attribute is over- or under-funded relative to its share of files."
        )
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cs/insights/account_read/analyzers.py tests/insights/account_read/test_analyzers.py
git commit -m "feat(account-read): spend_gap analyzer, money against evidence

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: untried analyzer

**Files:**
- Modify: `src/cs/insights/account_read/analyzers.py`
- Test: `tests/insights/account_read/test_analyzers.py`

**Interfaces:**
- Consumes: `Analysis`, `clean_main_effects`, `is_good`, `label`.
- Produces:
  - `single_dims(units: list[Unit]) -> set[str]`
  - `untried(units: list[Unit], tests: list[Test], overlaps: list[dict], metrics: tuple[str, ...]) -> Analysis`

`overlaps` rows are the run's stored entanglement rows: `{"a": [dim, val], "b": [dim, val], "jaccard": float, ...}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/insights/account_read/test_analyzers.py`:

```python
def test_single_dims_are_dimensions_no_unit_carries_twice():
    units = [
        _unit("a", [("opening_shot", "face_to_camera"), ("registers", "fear"), ("registers", "relief")]),
        _unit("b", [("opening_shot", "scene_broll"), ("registers", "warmth")]),
    ]
    assert az.single_dims(units) == {"opening_shot"}


def test_untried_reports_zero_carrier_combos_and_skips_incompatible_pairs():
    A = ("hook_devices", "audience_callout")
    B = ("proof_devices", "expert_credential")
    C = ("messenger", "expert_on_camera")  # entangled with B
    D = ("opening_shot", "face_to_camera")
    E = ("opening_shot", "scene_broll")  # same single-valued dimension as D
    units = (
        [_unit(f"a{i}", [A, D]) for i in range(3)]  # A+D already combined
        + [_unit(f"b{i}", [B, C, E]) for i in range(3)]
        + [_unit(f"z{i}", []) for i in range(6)]
    )
    tests = [
        _test(*A, change=0.5, cell_shas=["a0", "a1", "a2"]),
        _test(*B, change=0.4, cell_shas=["b0", "b1", "b2"]),
        _test(*C, change=0.3, cell_shas=["b0", "b1", "b2"]),
        _test(*D, change=0.2, cell_shas=["a0", "a1", "a2"]),
        _test(*E, change=0.1, cell_shas=["b0", "b1", "b2"]),
        _test(*A, change=-0.5, metric="cpa", cell_shas=["a0", "a1", "a2"]),  # not in layer
    ]
    overlaps = [{"a": list(B), "b": list(C), "jaccard": 1.0}]
    out = az.untried(units, tests, overlaps, ("link_ctr",))
    assert out.empty_reason is None
    names = [f.split(" | ")[0] for f in out.facts]
    assert "hook devices = audience callout + proof devices = expert credential" in names
    assert "hook devices = audience callout + opening shot = face to camera" not in names  # 3 carry it
    entangled_pair = "messenger = expert on camera + proof devices = expert credential"
    assert not any(entangled_pair in n for n in names)  # entangled, skipped in pairs and triples
    assert not any("face to camera + opening shot" in n for n in names)  # same single dim
    # Highest summed effect first: A (0.5) + B (0.4) + D (0.2), never combined.
    assert out.facts[0] == (
        "hook devices = audience callout + opening shot = face to camera + "
        "proof devices = expert credential | 0 of 12 files carry all 3 | ingredients P1, P2, P3"
    )
    assert [(t.dimension, t.value) for t in out.tests[:3]] == [A, D, B]
    assert all(t.metric == "link_ctr" for t in out.tests)


def test_untried_empty_with_one_candidate():
    units = [_unit(f"u{i}", [("angles", "novelty")]) for i in range(6)]
    t = _test("angles", "novelty", change=0.4, cell_shas=[u.sha256 for u in units])
    out = az.untried(units, [t], [], ("link_ctr",))
    assert out.empty_reason == (
        "Fewer than two attributes have clean positive evidence on these metrics."
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py -k "untried or single_dims"`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Add to `analyzers.py` (add `from itertools import combinations` at the top):

```python
MAX_COMBOS = 5
MAX_CARRIERS = 1  # a combo counts as untried when at most this many files carry it


def single_dims(units: list[Unit]) -> set[str]:
    """Dimensions no unit carries two values of: their values are alternatives, not additions."""
    dims = {d for u in units for d, _ in u.facets}
    return {
        d for d in dims if all(sum(1 for dd, _ in u.facets if dd == d) <= 1 for u in units)
    }


def untried(
    units: list[Unit], tests: list[Test], overlaps: list[dict], metrics: tuple[str, ...]
) -> Analysis:
    """Pairs and triples of attributes with clean positive evidence that no file, or one file,
    carries together. Two values of one single-valued dimension and entangled pairs are skipped."""
    cands: dict[tuple[str, str], Test] = {}
    for t in clean_main_effects(tests, metrics):
        if not is_good(t):
            continue
        key = (t.dimension, t.value)
        if key not in cands or t.p < cands[key].p:
            cands[key] = t
    if len(cands) < 2:
        return Analysis(
            empty_reason="Fewer than two attributes have clean positive evidence on these metrics."
        )
    entangled = {frozenset((tuple(o["a"]), tuple(o["b"]))) for o in overlaps}
    single = single_dims(units)

    def compatible(a: tuple[str, str], b: tuple[str, str]) -> bool:
        if a[0] == b[0] and a[0] in single:
            return False
        return frozenset((a, b)) not in entangled

    scored: list[tuple[float, int, tuple[tuple[str, str], ...]]] = []
    for r in (2, 3):
        for combo in combinations(sorted(cands), r):
            if not all(compatible(a, b) for a, b in combinations(combo, 2)):
                continue
            carriers = sum(1 for u in units if all(u.has(*k) for k in combo))
            if carriers <= MAX_CARRIERS:
                scored.append((sum(abs(cands[k].change) for k in combo), carriers, combo))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = Analysis()
    for _, carriers, combo in scored[:MAX_COMBOS]:
        pids = []
        for k in combo:
            t = cands[k]
            if t not in out.tests:
                out.tests.append(t)
            pids.append(f"P{out.tests.index(t) + 1}")
        names = " + ".join(label(cands[k]) for k in combo)
        out.facts.append(
            f"{names} | {carriers} of {len(units)} files carry all {len(combo)} | "
            f"ingredients {', '.join(pids)}"
        )
    if not out.facts:
        out.empty_reason = (
            "Every compatible pair of proven attributes is already carried by at least two files."
        )
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cs/insights/account_read/analyzers.py tests/insights/account_read/test_analyzers.py
git commit -m "feat(account-read): untried analyzer, proven ingredients never combined

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: nearest analyzer

**Files:**
- Modify: `src/cs/insights/account_read/analyzers.py`
- Test: `tests/insights/account_read/test_analyzers.py`

**Interfaces:**
- Consumes: `Analysis`, `unit_metric`, `unit_line`, `facet_words`, `NUMERIC_DIMS` from `facets.py`, `LOWER_IS_BETTER`.
- Produces: `nearest(units: list[Unit], tests: list[Test], anchor: Unit, metric: str, metrics: tuple[str, ...]) -> Analysis`

- [ ] **Step 1: Write the failing test**

Append to `tests/insights/account_read/test_analyzers.py`:

```python
def test_nearest_finds_underperforming_lookalikes_and_the_diff():
    base = [("messenger", "creator_on_camera"), ("angles", "symptom_relief"), ("setting", "home")]
    d = ("proof_devices", "expert_credential")
    e = ("registers", "urgency")
    anchor = _unit("w", base + [d], link_clicks=1200, headline="Winner")
    better = _unit("z", base + [d], link_clicks=1500)  # outperforms: excluded
    near = [_unit(f"n{i}", base + [e], link_clicks=400) for i in range(6)]  # six, keep five
    far = [_unit(f"f{i}", [("messenger", "graphic_only")], link_clicks=300) for i in range(2)]
    tests = [
        _test(*d, change=0.5, cell_shas=["w", "z"]),
        _test(*d, change=0.9, metric="cpa", cell_shas=["w", "z"]),  # not in layer, ignored
    ]
    out = az.nearest([anchor, better] + near + far, tests, anchor, "link_ctr", ("link_ctr",))
    assert out.empty_reason is None
    assert out.facts[0] == 'anchor | "Winner" | $1,000 spend | 0 copies | link CTR 6.0%'
    neighbours = [f for f in out.facts if f.startswith("neighbour |")]
    assert len(neighbours) == 5
    # Anchor {3 base, d} against neighbour {3 base, e}: 3 shared of 5 -> 60%.
    assert all(f.endswith("| 60% facet overlap") for f in neighbours)
    assert "anchor has, neighbours lack | proof devices = expert credential (P1)" in out.facts
    assert "neighbours have, anchor lacks | registers = urgency" in out.facts
    assert out.tests == [tests[0]]


def test_nearest_ignores_numeric_tercile_facets_in_similarity():
    a = _unit("a", [("duration_s", "high"), ("messenger", "creator_on_camera")], link_clicks=900)
    b = _unit("b", [("duration_s", "low"), ("messenger", "creator_on_camera")], link_clicks=400)
    out = az.nearest([a, b], [], a, "link_ctr", ("link_ctr",))
    assert any(f.endswith("| 100% facet overlap") for f in out.facts)


def test_nearest_empty_when_nothing_underperforms():
    a = _unit("a", [("setting", "home")], link_clicks=400)
    b = _unit("b", [("setting", "home")], link_clicks=900)
    out = az.nearest([a, b], [], a, "link_ctr", ("link_ctr",))
    assert out.empty_reason == 'No file does worse than "Ad a" on link CTR.'
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py -k nearest`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Add to `analyzers.py` (add `from cs.insights.account_read.facets import NUMERIC_DIMS`):

```python
NEIGHBOURS = 5
DIFF_MIN = 3  # a facet joins the diff when at least this many neighbours disagree with the anchor


def _choices(u: Unit) -> set[tuple[str, str]]:
    """Facets a maker chose; tercile bins are outcomes of the run, not choices."""
    return {f for f in u.facets if f[0] not in NUMERIC_DIMS}


def nearest(
    units: list[Unit], tests: list[Test], anchor: Unit, metric: str, metrics: tuple[str, ...]
) -> Analysis:
    """The files most like the anchor by facet overlap that do worse than it on the metric,
    and the facets that separate them."""
    av = unit_metric(anchor, metric)
    if av is None:
        return Analysis(
            empty_reason=f'"{anchor.headline}" is below the floor for {METRIC_NAME[metric]}.'
        )
    choice = _choices(anchor)
    scored: list[tuple[float, float, Unit]] = []
    for u in units:
        if u.sha256 == anchor.sha256:
            continue
        v = unit_metric(u, metric)
        if v is None:
            continue
        worse = v > av if metric in LOWER_IS_BETTER else v < av
        if not worse:
            continue
        uf = _choices(u)
        j = len(choice & uf) / len(choice | uf) if (choice | uf) else 0.0
        scored.append((j, v, u))
    scored.sort(key=lambda x: (-x[0], x[2].sha256))
    near = scored[:NEIGHBOURS]
    if not near:
        return Analysis(
            empty_reason=f'No file does worse than "{anchor.headline}" on {METRIC_NAME[metric]}.'
        )
    out = Analysis()
    out.facts.append(f"anchor | {unit_line(anchor, metric)}")
    for j, _, u in near:
        out.facts.append(f"neighbour | {unit_line(u, metric)} | {j * 100:.0f}% facet overlap")
    sets = [_choices(u) for _, _, u in near]
    need = min(DIFF_MIN, len(near))
    only_anchor = sorted(f for f in choice if sum(1 for s in sets if f not in s) >= need)
    only_near = sorted(
        f for f in set().union(*sets) - choice if sum(1 for s in sets if f in s) >= need
    )
    best: dict[tuple[str, str], Test] = {}
    for t in tests:
        if t.group is None and t.metric in metrics:
            k = (t.dimension, t.value)
            if k not in best or t.p < best[k].p:
                best[k] = t

    def cite(f: tuple[str, str]) -> str:
        t = best.get(f)
        if t is None:
            return ""
        if t not in out.tests:
            out.tests.append(t)
        return f" (P{out.tests.index(t) + 1})"

    for f in only_anchor:
        out.facts.append(f"anchor has, neighbours lack | {facet_words(f)}{cite(f)}")
    for f in only_near:
        out.facts.append(f"neighbours have, anchor lacks | {facet_words(f)}{cite(f)}")
    if not only_anchor and not only_near:
        out.facts.append("no facet separates the anchor from its neighbours")
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cs/insights/account_read/analyzers.py tests/insights/account_read/test_analyzers.py
git commit -m "feat(account-read): nearest analyzer, a winner against its lookalikes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: controlled analyzer

**Files:**
- Modify: `src/cs/insights/account_read/analyzers.py`
- Test: `tests/insights/account_read/test_analyzers.py`

**Interfaces:**
- Consumes: `compare`, `benjamini_hochberg`, `grade`, `MIN_CELL` from `stats.py`; `Analysis`.
- Produces: `controlled(units: list[Unit], tests: list[Test], target: tuple[str, str], control: tuple[str, str] | str, metrics: tuple[str, ...], draws: int) -> Analysis`

- [ ] **Step 1: Write the failing test**

Append to `tests/insights/account_read/test_analyzers.py`:

```python
def test_controlled_by_spend_yields_one_test_per_tercile_plus_the_overall_reference():
    target = ("messenger", "expert_on_camera")
    units = []
    for i in range(30):
        has = i % 2 == 0
        units.append(
            _unit(
                f"u{i:02d}",
                [target] if has else [],
                spend=100 * (i + 1),  # terciles of ten files, five with and five without
                link_clicks=900 if has else 400,
            )
        )
    overall = _test(*target, change=0.6, grade="strong", cell_shas=[u.sha256 for u in units[::2]])
    out = az.controlled(units, [overall], target, "spend", ("link_ctr",), draws=50)
    assert out.empty_reason is None
    assert out.facts == [
        "spend=low | 5 files with vs 5 without",
        "spend=mid | 5 files with vs 5 without",
        "spend=high | 5 files with vs 5 without",
    ]
    assert out.tests[0] is overall
    strata = [t.group for t in out.tests[1:]]
    assert strata == ["spend=low", "spend=mid", "spend=high"]
    assert all(t.metric == "link_ctr" and t.grade != "ungraded" for t in out.tests[1:])


def test_controlled_by_partner_attribute_splits_inside_and_outside():
    target = ("proof_devices", "expert_credential")
    partner = ("messenger", "expert_on_camera")
    units = [
        _unit(f"i{i}", [target, partner] if i < 5 else [partner], link_clicks=900 if i < 5 else 400)
        for i in range(10)
    ] + [
        _unit(f"o{i}", [target] if i < 5 else [], link_clicks=700 if i < 5 else 500)
        for i in range(10)
    ]
    out = az.controlled(units, [], target, partner, ("link_ctr",), draws=50)
    assert [t.group for t in out.tests] == [
        "messenger=expert_on_camera",
        "not messenger=expert_on_camera",
    ]


def test_controlled_empty_when_no_stratum_reaches_the_cell_floor():
    target = ("messenger", "expert_on_camera")
    units = [_unit(f"u{i}", [target] if i < 2 else [], spend=100 * i) for i in range(9)]
    out = az.controlled(units, [], target, "spend", ("link_ctr",), draws=50)
    assert out.empty_reason == "No stratum has 5 files on both sides."
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py -k controlled`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Add to `analyzers.py` (extend the stats import to `from cs.insights.account_read.stats import MIN_CELL, MIN_PURCHASES, benjamini_hochberg, compare, grade, pooled, unit_rates`):

```python
def _strata(units: list[Unit], control: tuple[str, str] | str) -> list[tuple[str, list[Unit]]]:
    if control == "spend":
        ordered = sorted(units, key=lambda u: (u.spend, u.sha256))
        n = len(ordered)
        return [
            ("spend=low", ordered[: n // 3]),
            ("spend=mid", ordered[n // 3 : (2 * n) // 3]),
            ("spend=high", ordered[(2 * n) // 3 :]),
        ]
    cd, cv = control
    return [
        (f"{cd}={cv}", [u for u in units if u.has(cd, cv)]),
        (f"not {cd}={cv}", [u for u in units if not u.has(cd, cv)]),
    ]


def controlled(
    units: list[Unit],
    tests: list[Test],
    target: tuple[str, str],
    control: tuple[str, str] | str,
    metrics: tuple[str, ...],
    draws: int,
) -> Analysis:
    """The target's comparison rerun inside each stratum of the control. The stored overall
    main effect rides first as the reference."""
    dim, val = target
    out = Analysis()
    fresh: list[Test] = []
    for lbl, grp in _strata(units, control):
        cell = [u for u in grp if u.has(dim, val)]
        comp = [u for u in grp if not u.has(dim, val)]
        out.facts.append(f"{lbl} | {len(cell)} files with vs {len(comp)} without")
        found = compare(cell, comp, dimension=dim, value=val, group=lbl, draws=draws)
        fresh += [t for t in found if t.metric in metrics]
    if not fresh:
        return Analysis(empty_reason=f"No stratum has {MIN_CELL} files on both sides.")
    passing = benjamini_hochberg([t.p for t in fresh])
    for i, t in enumerate(fresh):
        t.grade = grade(t.p, t.change, i in passing)
    overall = [
        t
        for t in tests
        if t.group is None and (t.dimension, t.value) == target and t.metric in metrics
    ]
    out.tests = overall + fresh
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cs/insights/account_read/analyzers.py tests/insights/account_read/test_analyzers.py
git commit -m "feat(account-read): controlled analyzer, an effect re-tested within strata

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: recipe analyzer and the run dispatcher

**Files:**
- Modify: `src/cs/insights/account_read/analyzers.py`
- Test: `tests/insights/account_read/test_analyzers.py`

**Interfaces:**
- Consumes: `GRADE_RANK` from `patterns.py`; everything above.
- Produces:
  - `recipe(units: list[Unit], tests: list[Test], anchor: Unit, metric: str, metrics: tuple[str, ...]) -> Analysis`
  - `run(query: dict, units: list[Unit], tests: list[Test], overlaps: list[dict], metrics: tuple[str, ...], draws: int, anchor: Unit | None = None) -> Analysis` for kinds `spend_gap | untried | nearest | controlled | recipe`. `compare` is not dispatched here; `ask.py` keeps `targeted_tests` for it. Query fields per kind are the parsed shapes from Task 7: `controlled` has `target: tuple` and `control: tuple | "spend"`; `nearest` and `recipe` need `anchor` passed in resolved.

- [ ] **Step 1: Write the failing test**

Append to `tests/insights/account_read/test_analyzers.py`:

```python
def test_recipe_ranks_the_anchor_facets_by_grade_then_change():
    A, B, C = ("messenger", "expert_on_camera"), ("registers", "authority"), ("setting", "home")
    anchor = _unit("w", [A, B, C], headline="Winner", spend=9000)
    tests = [
        _test(*A, change=0.6, grade="strong", cell_shas=["w"]),
        _test(*B, change=0.3, cell_shas=["w"]),
        _test(*B, change=0.4, grade="strong", group="messenger=expert_on_camera", cell_shas=["w"]),
        _test(*B, change=0.9, grade="strong", group="setting=outdoor", cell_shas=["w"]),  # not anchor's
        _test(*C, change=0.2, grade="thin", cell_shas=["w"]),
        _test(*A, change=-0.9, metric="cpa", grade="strong", cell_shas=["w"]),  # not in layer
    ]
    out = az.recipe([anchor], tests, anchor, "link_ctr", ("link_ctr",))
    assert out.facts[0] == 'anchor | "Winner" | $9,000 spend | 0 copies | link CTR 3.0%'
    assert out.facts[1:] == [
        "#1 | messenger = expert on camera | link CTR +60% strong (P1)",
        "#2 | registers = authority | within messenger=expert_on_camera | link CTR +40% strong (P2)",
    ]
    assert out.tests == [tests[0], tests[2]]


def test_recipe_empty_without_graded_facets():
    anchor = _unit("w", [("setting", "home")])
    out = az.recipe([anchor], [], anchor, "link_ctr", ("link_ctr",))
    assert out.empty_reason == (
        "None of this file's attributes has clean graded evidence on these metrics."
    )


def test_run_dispatches_by_kind():
    units = [_unit(f"u{i}", [("angles", "novelty")]) for i in range(6)]
    t = _test("angles", "novelty", change=0.4, cell_shas=[u.sha256 for u in units])
    assert az.run({"kind": "untried"}, units, [t], [], ("link_ctr",), 50).empty_reason
    assert az.run({"kind": "spend_gap"}, units, [t], [], ("link_ctr",), 50).empty_reason
    q = {"kind": "controlled", "target": ("angles", "novelty"), "control": "spend"}
    assert az.run(q, units, [t], [], ("link_ctr",), 50).empty_reason
    out = az.run({"kind": "recipe"}, units, [t], [], ("link_ctr",), 50, anchor=units[0])
    assert out.tests == [t]
    out = az.run({"kind": "nearest"}, units, [t], [], ("link_ctr",), 50, anchor=units[0])
    assert out.empty_reason  # nothing underperforms an identical peer
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py -k "recipe or run_dispatch"`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

Add to `analyzers.py` (add `from cs.insights.account_read.patterns import GRADE_RANK`):

```python
def recipe(
    units: list[Unit], tests: list[Test], anchor: Unit, metric: str, metrics: tuple[str, ...]
) -> Analysis:
    """The anchor's facets ranked by their best clean graded effect: a main effect, or an
    interaction whose group is another facet the anchor carries."""
    groups = {f"{d}={v}" for d, v in anchor.facets}
    ranked: list[tuple[int, float, Test]] = []
    for f in sorted(anchor.facets):
        cands = [
            t
            for t in tests
            if (t.dimension, t.value) == f
            and t.metric in metrics
            and t.grade in GOOD_GRADES
            and not t.confounds
            and (t.group is None or t.group in groups)
        ]
        if not cands:
            continue
        best = min(cands, key=lambda t: (GRADE_RANK[t.grade], -abs(t.change)))
        ranked.append((GRADE_RANK[best.grade], -abs(best.change), best))
    if not ranked:
        return Analysis(
            empty_reason="None of this file's attributes has clean graded evidence on these metrics."
        )
    ranked.sort(key=lambda r: (r[0], r[1]))
    out = Analysis()
    out.facts.append(f"anchor | {unit_line(anchor, metric)}")
    for i, (_, _, t) in enumerate(ranked, start=1):
        out.tests.append(t)
        within = f"within {t.group} | " if t.group else ""
        out.facts.append(
            f"#{i} | {label(t)} | {within}{METRIC_NAME[t.metric]} {t.change * 100:+.0f}% "
            f"{t.grade} (P{i})"
        )
    return out


def run(
    query: dict,
    units: list[Unit],
    tests: list[Test],
    overlaps: list[dict],
    metrics: tuple[str, ...],
    draws: int,
    anchor: Unit | None = None,
) -> Analysis:
    """Dispatch a parsed non-compare query. `anchor` must be resolved by the caller for the
    nearest and recipe kinds."""
    kind = query["kind"]
    if kind == "spend_gap":
        return spend_gap(units, tests, metrics)
    if kind == "untried":
        return untried(units, tests, overlaps, metrics)
    if kind == "controlled":
        return controlled(units, tests, tuple(query["target"]), query["control"], metrics, draws)
    if anchor is None:
        return Analysis(empty_reason="No file matches that description.")
    metric = rank_metric(metrics)
    if kind == "nearest":
        return nearest(units, tests, anchor, metric, metrics)
    return recipe(units, tests, anchor, metric, metrics)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/insights/account_read/test_analyzers.py && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cs/insights/account_read/analyzers.py tests/insights/account_read/test_analyzers.py
git commit -m "feat(account-read): recipe analyzer and the ask kind dispatcher

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Query contract, map prompt, parsing and read_as

**Files:**
- Modify: `src/cs/insights/account_read/ask.py`
- Test: `tests/insights/account_read/test_ask.py`

**Interfaces:**
- Consumes: `METRIC_NAME` from `writer.py`; `METRICS` from `types.py`.
- Produces in `ask.py`:
  - `KINDS = ("compare", "spend_gap", "untried", "nearest", "controlled", "recipe")`
  - `rank_metric_for_layer(layer: str) -> str`
  - `parse_query(body: dict) -> dict` (the parsed query or `{"unmappable": ...}`)
  - `read_as(query: dict) -> str`
  - `map_question` now returns `parse_query(_loads(text))`.

Parsed query shapes (what every later task consumes):

```
{"kind": "compare", "targets": [str | (str, str), ...], "group": (str, str) | None, "layer": str}
{"kind": "spend_gap", "layer": str}
{"kind": "untried", "layer": str}
{"kind": "nearest", "anchor": {"best": str} | {"named": str}, "layer": str}
{"kind": "controlled", "target": (str, str), "control": (str, str) | "spend", "layer": str}
{"kind": "recipe", "anchor": {"best": str} | {"named": str}, "layer": str}
```

- [ ] **Step 1: Write the failing tests**

Replace the two existing `map_question` tests in `tests/insights/account_read/test_ask.py` and add the new ones. The file's `_units` and `_result_json` helpers stay. New content for the map section:

```python
async def test_map_question_parses_compare_targets_group_and_layer():
    w = FakeAccountReadWriter(
        [
            json.dumps(
                {
                    "kind": "compare",
                    "targets": ["messenger_age_band"],
                    "group": ["audience_callouts", "magnesium_users"],
                    "layer": "click",
                }
            )
        ]
    )
    q = await map_question("Which messenger age works for magnesium users?", _result_json(), w)
    assert q == {
        "kind": "compare",
        "targets": ["messenger_age_band"],
        "group": ("audience_callouts", "magnesium_users"),
        "layer": "click",
    }
    assert "messenger_age_band" in w.calls[0][1]  # the dimension list was offered
    assert "spend_gap" in w.calls[0][0]  # the kinds are described in the system prompt


async def test_map_question_unmappable():
    w = FakeAccountReadWriter([json.dumps({"unmappable": "persona is not tagged"})])
    out = await map_question("trust by persona", _result_json(), w)
    assert out == {"unmappable": "persona is not tagged"}


def test_parse_query_kinds_without_arguments():
    assert parse_query({"kind": "spend_gap", "layer": "conversion"}) == {
        "kind": "spend_gap",
        "layer": "conversion",
    }
    assert parse_query({"kind": "untried"}) == {"kind": "untried", "layer": "all"}


def test_parse_query_anchor_defaults_to_the_layer_rank_metric():
    assert parse_query({"kind": "nearest", "anchor": {"best": "cpa"}, "layer": "conversion"}) == {
        "kind": "nearest",
        "anchor": {"best": "cpa"},
        "layer": "conversion",
    }
    assert parse_query({"kind": "recipe", "anchor": {"named": "pharmacist"}}) == {
        "kind": "recipe",
        "anchor": {"named": "pharmacist"},
        "layer": "all",
    }
    assert parse_query({"kind": "recipe", "layer": "retention"})["anchor"] == {
        "best": "thruplay_rate"
    }
    assert parse_query({"kind": "nearest", "anchor": {"best": "nope"}})["anchor"] == {"best": "cpa"}


def test_parse_query_controlled_requires_target_and_defaults_control_to_spend():
    q = parse_query(
        {
            "kind": "controlled",
            "target": ["messenger", "expert_on_camera"],
            "control": ["proof_devices", "expert_credential"],
            "layer": "retention",
        }
    )
    assert q == {
        "kind": "controlled",
        "target": ("messenger", "expert_on_camera"),
        "control": ("proof_devices", "expert_credential"),
        "layer": "retention",
    }
    assert parse_query({"kind": "controlled", "target": ["a", "b"], "control": "spend"})[
        "control"
    ] == "spend"
    assert parse_query({"kind": "controlled", "target": ["a", "b"], "control": 7})["control"] == "spend"
    assert "unmappable" in parse_query({"kind": "controlled", "control": "spend"})


def test_parse_query_unknown_kind_falls_back_to_compare_or_unmappable():
    assert parse_query({"kind": "wat", "targets": ["registers"]})["kind"] == "compare"
    assert "unmappable" in parse_query({"kind": "wat"})
    assert "unmappable" in parse_query({})


def test_read_as_per_kind():
    assert (
        read_as({"kind": "compare", "targets": [("registers", "fear")], "group": None, "layer": "conversion"})
        == "registers = fear against everything else, on conversion metrics"
    )
    assert (
        read_as(
            {
                "kind": "compare",
                "targets": ["messenger"],
                "group": ("audience_callouts", "magnesium_users"),
                "layer": "click",
            }
        )
        == "every value of messenger against everything else within audience callouts = "
        "magnesium users, on click metrics"
    )
    assert (
        read_as({"kind": "spend_gap", "layer": "all"})
        == "spend share of every graded attribute against its effect, on all metrics"
    )
    assert (
        read_as({"kind": "untried", "layer": "click"})
        == "pairs and triples of attributes with clean positive effects on click metrics that no "
        "file carries together"
    )
    assert (
        read_as({"kind": "nearest", "anchor": {"best": "cpa"}, "layer": "conversion"})
        == "the five files most like the best-CPA file that do worse than it, on conversion metrics"
    )
    assert (
        read_as(
            {
                "kind": "controlled",
                "target": ("messenger", "expert_on_camera"),
                "control": "spend",
                "layer": "retention",
            }
        )
        == "messenger = expert on camera re-tested within each spend tercile, on retention metrics"
    )
    assert (
        read_as(
            {
                "kind": "controlled",
                "target": ("messenger", "expert_on_camera"),
                "control": ("proof_devices", "expert_credential"),
                "layer": "all",
            }
        )
        == "messenger = expert on camera re-tested inside and outside proof devices = expert "
        "credential, on all metrics"
    )
    assert (
        read_as({"kind": "recipe", "anchor": {"named": "pharmacist"}, "layer": "all"})
        == 'the ingredients of the file matching "pharmacist", ranked by their graded effects on '
        "all metrics"
    )
```

Update the import line at the top of the test file to:

```python
from cs.insights.account_read.ask import answer, cap_verdict, map_question, parse_query, read_as
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/insights/account_read/test_ask.py`
Expected: FAIL with `ImportError: cannot import name 'parse_query'`.

- [ ] **Step 3: Implement**

In `src/cs/insights/account_read/ask.py`:

Replace the `MAP_SYSTEM` constant with:

```python
MAP_SYSTEM = """\
You translate a strategist's question about an ad account into one query over its tagged creative attributes and stored comparisons. You are given the attribute dimensions and the values that exist with enough data. Return JSON with a "kind" and that kind's fields:
- {"kind": "compare", "targets": [<dimension> or [<dimension>, <value>], ...], "group": [<dimension>, <value>] or null, "layer": L}. The default: "do X videos convert better", "which messenger works for magnesium users". targets: the attribute(s) asked about; a bare dimension means "compare every value of it". group: for "for each X" or "within X", one concrete value that exists; a persona or audience maps to the closest audience_callouts value; else null.
- {"kind": "spend_gap", "layer": L}: where the money goes versus where the evidence points; over- or under-funded attributes; budget allocation.
- {"kind": "untried", "layer": L}: proven attributes never combined; what to make next; gaps; untested combinations.
- {"kind": "nearest", "anchor": A, "layer": L}: what separates a winner from the ads most like it; why a lookalike failed; nearest neighbours of one video.
- {"kind": "controlled", "target": [<dimension>, <value>], "control": [<dimension>, <value>] or "spend", "layer": L}: is an effect real or explained by spend or by another attribute; "after controlling for"; "or is it just because we spent more".
- {"kind": "recipe", "anchor": A, "layer": L}: which ingredient of one video does the work; what makes our best ad work.
A (anchor) is {"best": <metric>} with metric one of hook_rate, thruplay_rate, link_ctr, purchases_per_1k, cpa, or {"named": "<the words in the question that identify the video>"}. "best CPA video" -> {"best": "cpa"}; "the pharmacist ad" -> {"named": "pharmacist"}; "our biggest ad" -> {"best": "cpa"}.
L (layer): "trust" or "hold" -> retention; "attention" or "hook" -> attention; "clicks" -> click; "CAC", "CPA", "conversions", "purchases" -> conversion; otherwise all.
If the question asks about something none of these kinds can answer from tagged attributes, return {"unmappable": "<what is missing, in one sentence>"}. Return valid JSON only.
"""  # noqa: E501
```

Add after `LAYER_METRICS`:

```python
KINDS = ("compare", "spend_gap", "untried", "nearest", "controlled", "recipe")
LAYER_WORDS = {
    "attention": "attention metrics",
    "retention": "retention metrics",
    "click": "click metrics",
    "conversion": "conversion metrics",
    "all": "all metrics",
}
_UNMAPPABLE = "That asks about something the tags do not capture yet."
```

Add the imports `from cs.insights.account_read.types import METRICS, Test, Unit, to_json, unit_from_json` (extending the existing line) and `METRIC_NAME` to the writer import: `from cs.insights.account_read.writer import METRIC_NAME, GeminiGatewayAccountReadWriter, _numbers, pattern_line`.

Add these functions after `_vocab`:

```python
def rank_metric_for_layer(layer: str) -> str:
    return "cpa" if layer == "all" else LAYER_METRICS[layer][0]


def _facet(v) -> tuple[str, str] | None:
    return (str(v[0]), str(v[1])) if isinstance(v, list | tuple) and len(v) == 2 else None


def _anchor(a, layer: str) -> dict:
    if isinstance(a, dict):
        if a.get("named"):
            return {"named": str(a["named"])}
        if a.get("best") in METRICS:
            return {"best": str(a["best"])}
    return {"best": rank_metric_for_layer(layer)}


def parse_query(body: dict) -> dict:
    """The model's map JSON -> a parsed query, or {"unmappable": reason}."""
    if "unmappable" in body:
        return {"unmappable": str(body["unmappable"])}
    layer = body.get("layer") if body.get("layer") in LAYER_METRICS else "all"
    kind = body.get("kind")
    if kind not in KINDS:
        kind = "compare" if body.get("targets") else None
    if kind is None:
        return {"unmappable": _UNMAPPABLE}
    if kind == "compare":
        if not body.get("targets"):
            return {"unmappable": _UNMAPPABLE}
        targets = [tuple(t) if isinstance(t, list) else str(t) for t in body["targets"]]
        return {"kind": kind, "targets": targets, "group": _facet(body.get("group")), "layer": layer}
    if kind in ("spend_gap", "untried"):
        return {"kind": kind, "layer": layer}
    if kind in ("nearest", "recipe"):
        return {"kind": kind, "anchor": _anchor(body.get("anchor"), layer), "layer": layer}
    target = _facet(body.get("target"))
    if target is None:
        return {"unmappable": "The attribute to re-test was not named."}
    control = body.get("control")
    parsed_control = "spend" if control == "spend" else (_facet(control) or "spend")
    return {"kind": "controlled", "target": target, "control": parsed_control, "layer": layer}


def _words(f) -> str:
    if isinstance(f, tuple):
        return f"{f[0].replace('_', ' ')} = {str(f[1]).replace('_', ' ')}"
    return f"every value of {str(f).replace('_', ' ')}"


def _anchor_words(a: dict) -> str:
    if "best" in a:
        return f"the best-{METRIC_NAME[a['best']]} file"
    return f'the file matching "{a["named"]}"'


def read_as(query: dict) -> str:
    """How the question was read, rendered from the parsed query so it cannot drift from what
    actually ran."""
    on = LAYER_WORDS[query["layer"]]
    kind = query["kind"]
    if kind == "compare":
        targets = ", ".join(_words(t) for t in query["targets"])
        within = f" within {_words(query['group'])}" if query.get("group") else ""
        return f"{targets} against everything else{within}, on {on}"
    if kind == "spend_gap":
        return f"spend share of every graded attribute against its effect, on {on}"
    if kind == "untried":
        return (
            f"pairs and triples of attributes with clean positive effects on {on} that no file "
            "carries together"
        )
    if kind == "nearest":
        return (
            f"the five files most like {_anchor_words(query['anchor'])} that do worse than it, "
            f"on {on}"
        )
    if kind == "controlled":
        c = query["control"]
        where = "each spend tercile" if c == "spend" else f"inside and outside {_words(c)}"
        return f"{_words(query['target'])} re-tested within {where}, on {on}"
    return (
        f"the ingredients of {_anchor_words(query['anchor'])}, ranked by their graded effects "
        f"on {on}"
    )
```

Replace the body of `map_question` after the writer call with:

```python
    text, _, _ = await writer.complete(MAP_SYSTEM, user)
    return parse_query(_loads(text))
```

`targeted_tests` and `cap_verdict` stay as they are. `answer()` is untouched in this task; it still reads `query["targets"]`, so the existing `test_answer_*` tests keep passing for compare queries. Check that the `_result_json` fixture's `test_map_question_parses...` expectation is satisfied by the dimension list still being built from `_vocab`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/insights/account_read/test_ask.py tests/api/test_account_read.py && make lint`
Expected: PASS. (The API test needs `DATABASE_URL_TEST` exported; if it is not available in the shell, run the ask tests alone and note it.)

- [ ] **Step 5: Commit**

```bash
git add src/cs/insights/account_read/ask.py tests/insights/account_read/test_ask.py
git commit -m "feat(account-read): ask query kinds, parsing and the read-as line

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: answer() orchestration with FACTS, empty short circuit and response shape

**Files:**
- Modify: `src/cs/insights/account_read/ask.py`
- Test: `tests/insights/account_read/test_ask.py`

**Interfaces:**
- Consumes: `analyzers.run`, `analyzers.resolve_anchor`, `analyzers.rank_metric`, `analyzers.Analysis`; `parse_query`, `read_as`, `targeted_tests`, `cap_verdict` from Task 7; `test_from_json` from `types.py`.
- Produces: `answer(question, result_json, writer, draws=300) -> dict` returning either `{"unmappable": str}` or `{"kind", "read_as", "verdict", "answer", "pattern_ids", "facts": [{"id", "text"}], "tests", "what_would_settle_it"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/insights/account_read/test_ask.py`:

```python
from cs.insights.account_read.types import Test


def _graded(dim, val, shas, change=0.5, metric="link_ctr", grade="suggestive"):
    return to_json(
        Test(
            dimension=dim,
            value=val,
            group=None,
            metric=metric,
            kind="per_unit",
            n_cell=len(shas),
            n_complement=14 - len(shas),
            cell_value=0.06,
            comp_value=0.03,
            change=change,
            p=0.02,
            grade=grade,
            confounds=[],
            cell_shas=list(shas),
        )
    )


async def test_answer_compare_carries_kind_and_read_as():
    w = FakeAccountReadWriter(
        [
            json.dumps({"kind": "compare", "targets": ["messenger_age_band"], "layer": "click"}),
            json.dumps(
                {
                    "verdict": "answerable",
                    "answer": "Older messengers earn more clicks.",
                    "pattern_ids": ["P1"],
                    "what_would_settle_it": "More files.",
                }
            ),
        ]
    )
    out = await answer("does messenger age matter for clicks?", _result_json(), w)
    assert out["kind"] == "compare"
    assert out["read_as"] == "every value of messenger age band against everything else, on click metrics"
    assert out["facts"] == []
    assert "READ AS: every value of messenger age band" in w.calls[1][1]


async def test_answer_empty_analysis_short_circuits_without_a_writer_call():
    w = FakeAccountReadWriter([json.dumps({"kind": "untried", "layer": "click"})])
    out = await answer("what have we never combined?", _result_json(), w)
    assert len(w.calls) == 1
    assert out["kind"] == "untried" and out["verdict"] == "not yet"
    assert out["answer"].startswith("Fewer than two attributes")
    assert out["facts"] == [] and out["tests"] == [] and out["pattern_ids"] == []


async def test_answer_named_anchor_miss_is_unmappable():
    w = FakeAccountReadWriter(
        [json.dumps({"kind": "recipe", "anchor": {"named": "zebra"}, "layer": "all"})]
    )
    out = await answer("what makes the zebra ad work?", _result_json(), w)
    assert out == {"unmappable": 'No file matches "zebra".'}
    assert len(w.calls) == 1


async def test_answer_controlled_with_unknown_partner_is_unmappable():
    w = FakeAccountReadWriter(
        [
            json.dumps(
                {
                    "kind": "controlled",
                    "target": ["messenger_age_band", "40_to_60"],
                    "control": ["registers", "fear"],
                    "layer": "click",
                }
            )
        ]
    )
    out = await answer("is the older-messenger effect real once you control for fear?", _result_json(), w)
    assert out == {"unmappable": "No file carries registers = fear."}


async def test_answer_facts_are_numbered_in_the_prompt_and_cap_ignores_f_ids():
    result = _result_json()
    odd = [f"u{i}" for i in range(14) if i % 2]
    even = [f"u{i}" for i in range(14) if not i % 2]
    result["tests"] = [
        _graded("messenger_age_band", "40_to_60", odd, change=0.5),
        # Positive, so the heavily funded 25_to_40 side is never flagged as over-funded and
        # the starved 40_to_60 row is F1.
        _graded("messenger_age_band", "25_to_40", even, change=0.2),
    ]
    result["overlaps"] = []
    w = FakeAccountReadWriter(
        [
            json.dumps({"kind": "spend_gap", "layer": "click"}),
            json.dumps(
                {
                    "verdict": "answerable",
                    "answer": "Spend is even; nothing to move.",
                    "pattern_ids": ["F1", "P9"],
                    "what_would_settle_it": "n/a",
                }
            ),
        ]
    )
    out = await answer("where is the money going?", result, w)
    # Even spend across the fixture means no gap: the empty path.
    assert out["verdict"] == "not yet" and len(w.calls) == 1

    # Skew the spend so 40_to_60 is starved and the writer runs.
    for u in result["units"]:
        u["spend"] = 100 if u["sha256"] in odd else 5000
    w = FakeAccountReadWriter(
        [
            json.dumps({"kind": "spend_gap", "layer": "click"}),
            json.dumps(
                {
                    "verdict": "answerable",
                    "answer": "The older messenger is starved.",
                    "pattern_ids": ["F1", "P9"],
                    "what_would_settle_it": "n/a",
                }
            ),
        ]
    )
    out = await answer("where is the money going?", result, w)
    prompt = w.calls[1][1]
    assert "FACTS:\nF1 | starved | messenger age band = 40 to 60" in prompt
    assert "COMPARISONS:\nP1 | " in prompt
    assert out["facts"][0]["id"] == "F1" and out["facts"][0]["text"].startswith("starved |")
    assert out["pattern_ids"] == ["F1"]  # P9 does not exist; F1 is kept
    assert out["verdict"] == "not yet"  # F-ids never raise the verdict
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/insights/account_read/test_ask.py -k "answer_compare_carries or short_circuits or anchor_miss or unknown_partner or f_ids"`
Expected: FAIL (`KeyError: 'kind'` or `KeyError: 'targets'`).

- [ ] **Step 3: Implement**

In `ask.py`, add the imports:

```python
from cs.insights.account_read import analyzers
from cs.insights.account_read.types import METRICS, Test, Unit, test_from_json, to_json, unit_from_json
```

Extend `ANSWER_SYSTEM` by inserting, before "Return JSON:", the sentence:

```
FACTS are descriptive counts and shares; COMPARISONS are graded tests. Cite both by id (F1, P1). The READ AS line is how the question was interpreted; if it misses the question, say so in one clause.
```

Replace `answer()` with:

```python
_SETTLE = "More tagged videos with these attributes."
_TOO_FEW = (
    "Too few files carry those attributes to compare. Each side needs 5 files, "
    "and purchase metrics need 20 purchases per side."
)


def _empty(base: dict, reason: str) -> dict:
    return {
        **base,
        "verdict": "not yet",
        "answer": reason,
        "pattern_ids": [],
        "facts": [],
        "tests": [],
        "what_would_settle_it": _SETTLE,
    }


def _analyse(query: dict, result_json: dict, draws: int) -> analyzers.Analysis | dict:
    """Run the kind's analyzer. Returns {"unmappable": ...} when the query names something the
    run does not hold."""
    units = [unit_from_json(u) for u in result_json.get("units", [])]
    if query["kind"] == "compare":
        found = targeted_tests(query, units, draws)
        return analyzers.Analysis(tests=found, empty_reason=None if found else _TOO_FEW)
    tests = [test_from_json(t) for t in result_json.get("tests", [])]
    overlaps = result_json.get("overlaps", [])
    metrics = LAYER_METRICS[query["layer"]]
    anchor = None
    if query["kind"] in ("nearest", "recipe"):
        a = query["anchor"]
        metric = a.get("best") or analyzers.rank_metric(metrics)
        anchor = analyzers.resolve_anchor(a, units, metric)
        if anchor is None:
            if "named" in a:
                return {"unmappable": f'No file matches "{a["named"]}".'}
            return analyzers.Analysis(
                empty_reason=f"No file clears the floor for {METRIC_NAME[metric]}."
            )
    if query["kind"] == "controlled" and query["control"] != "spend":
        cd, cv = query["control"]
        if not any(u.has(cd, cv) for u in units):
            return {"unmappable": f"No file carries {_words((cd, cv))}."}
    return analyzers.run(query, units, tests, overlaps, metrics, draws, anchor=anchor)


async def answer(question: str, result_json: dict, writer, draws: int = 300) -> dict:
    query = await map_question(question, result_json, writer)
    if "unmappable" in query:
        return query
    reading = read_as(query)
    base = {"kind": query["kind"], "read_as": reading}
    analysis = _analyse(query, result_json, draws)
    if isinstance(analysis, dict):
        return analysis
    if analysis.empty_reason:
        return _empty(base, analysis.empty_reason)
    tests = analysis.tests
    plines = [pattern_line(f"P{i}", t) for i, t in enumerate(tests, start=1)]
    flines = [f"F{i} | {f}" for i, f in enumerate(analysis.facts, start=1)]
    user = f"QUESTION: {question}\n\nREAD AS: {reading}\n"
    if flines:
        user += "\nFACTS:\n" + "\n".join(flines) + "\n"
    user += "\nCOMPARISONS:\n" + ("\n".join(plines) if plines else "(none)")
    text, _, _ = await writer.complete(ANSWER_SYSTEM, user)
    body = _loads(text)
    bad = sorted(_numbers(str(body.get("answer", ""))) - _numbers(user))
    if bad:
        retry = user + (
            f"\n\nYour previous draft used numbers not in the comparisons: {bad}. "
            "Rewrite using only the numbers above."
        )
        text, _, _ = await writer.complete(ANSWER_SYSTEM, retry)
        body = _loads(text)
    valid = {f"P{i}" for i in range(1, len(tests) + 1)} | {
        f"F{i}" for i in range(1, len(analysis.facts) + 1)
    }
    cited_ids = [p for p in body.get("pattern_ids", []) if p in valid]
    cited = [to_json(tests[int(p[1:]) - 1]) for p in cited_ids if p.startswith("P")]
    return {
        **base,
        "verdict": cap_verdict(str(body.get("verdict", "not yet")), cited),
        "answer": str(body.get("answer", "")),
        "pattern_ids": cited_ids,
        "facts": [{"id": f"F{i}", "text": f} for i, f in enumerate(analysis.facts, start=1)],
        "tests": [to_json(t) for t in tests],
        "what_would_settle_it": str(body.get("what_would_settle_it", "")),
    }
```

Check the existing `test_answer_*` tests in `test_ask.py`: any that assert the exact "too few files" dict must now also expect `kind` and `read_as` keys and a `facts: []` key. Update those assertions to compare the relevant keys rather than the whole dict, for example `assert out["verdict"] == "not yet" and out["answer"].startswith("Too few files")`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/insights/account_read tests/api/test_account_read.py && make lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cs/insights/account_read/ask.py tests/insights/account_read/test_ask.py
git commit -m "feat(account-read): ask answers carry facts, kind and read-as

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Web: read-as line, facts list and seeded chips

**Files:**
- Modify: `web/src/lib/types.ts:64`
- Modify: `web/src/components/account-read-ask.tsx`
- Test: `web/tests/components/account-read-ask.test.tsx`

**Interfaces:**
- Consumes: the response shape from Task 8.
- Produces: `AccountReadAnswer` with `kind?: string; read_as?: string; facts?: { id: string; text: string }[]`.

- [ ] **Step 1: Write the failing tests**

Append inside the `describe('AccountReadAsk', ...)` block in `web/tests/components/account-read-ask.test.tsx`:

```tsx
  it('shows how the question was read and the facts behind the answer', async () => {
    accountReadAsk.mockResolvedValueOnce({
      ...answer('The older messenger is starved.'),
      kind: 'spend_gap',
      read_as: 'spend share of every graded attribute against its effect, on click metrics',
      facts: [{ id: 'F1', text: 'starved | messenger age band = 40 to 60 | 10 of 56 files | 6% of spend' }],
    });
    render(<AccountReadAsk accountId="a1" tests={[]} units={[]} />);

    await ask('where is the money going?');
    expect(await screen.findByText('The older messenger is starved.')).toBeInTheDocument();
    expect(screen.getByText(/read as: spend share of every graded attribute/i)).toBeInTheDocument();
    expect(screen.getByText('F1')).toBeInTheDocument();
    expect(screen.getByText(/starved \| messenger age band = 40 to 60/)).toBeInTheDocument();
  });

  it('offers seeded questions until the first one is asked, and a chip submits its question', async () => {
    accountReadAsk.mockResolvedValueOnce(answer('Nothing untried.'));
    render(<AccountReadAsk accountId="a1" tests={[]} units={[]} />);

    const chip = screen.getByRole('button', { name: 'Which proven ingredients have we never combined?' });
    await userEvent.click(chip);
    expect(accountReadAsk).toHaveBeenCalledWith('a1', 'Which proven ingredients have we never combined?');
    expect(await screen.findByText('Nothing untried.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /never combined/ })).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run tests/components/account-read-ask.test.tsx`
Expected: the two new tests FAIL (no "Read as" text, no chip button).

- [ ] **Step 3: Implement**

In `web/src/lib/types.ts`, replace the `AccountReadAnswer` line with:

```ts
export type AccountReadFact = { id: string; text: string };
export type AccountReadAnswer = {
  unmappable?: string;
  kind?: string;
  read_as?: string;
  verdict?: 'answerable' | 'directional' | 'not yet';
  answer?: string;
  pattern_ids?: string[];
  facts?: AccountReadFact[];
  tests?: AccountReadTest[];
  what_would_settle_it?: string;
};
```

In `web/src/components/account-read-ask.tsx`:

Add above the component:

```tsx
// One seeded question per query kind, as the empty-state menu.
const SEEDS = [
  'Where is our money going versus where the evidence points?',
  'Which proven ingredients have we never combined?',
  'What separates our best CPA video from the ads most like it that failed?',
  'Is the expert effect real, or just where we spent the money?',
  'Which single ingredient is doing the work in our best recipe?',
];
```

Refactor `submit` so the network call lives in an `ask(question)` function the form and the chips share:

```tsx
  const ask = async (question: string) => {
    if (!question || pending) return;
    const id = nextId.current++;
    setEntries((prev) => [...prev, { id, question }]);
    setQ('');
    setPending(true);
    try {
      const answer = await api.accountReadAsk(accountId, question);
      setEntries((prev) => prev.map((x) => (x.id === id ? { ...x, answer } : x)));
    } catch (err) {
      // Attached to its own entry, never page-level: one failed question must not
      // wipe the answers above it.
      const error = err instanceof Error ? err.message : 'The question could not be answered.';
      setEntries((prev) => prev.map((x) => (x.id === id ? { ...x, error } : x)));
    } finally {
      setPending(false);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    void ask(q.trim());
  };
```

After the `<form>` element, inside the outer `div`, add:

```tsx
      {entries.length === 0 && (
        <ul className="flex flex-wrap gap-2" aria-label="Example questions">
          {SEEDS.map((s) => (
            <li key={s}>
              <button
                type="button"
                onClick={() => void ask(s)}
                disabled={pending}
                className="rounded-full border border-border px-3 py-1 text-left text-[13px] leading-6 text-muted-foreground transition-colors hover:border-foreground hover:text-foreground disabled:opacity-50"
              >
                {s}
              </button>
            </li>
          ))}
        </ul>
      )}
```

In `Exchange`, directly after the question `<p>`, add:

```tsx
      {answer?.read_as && <p className="text-[13px] leading-6 text-muted-foreground">Read as: {answer.read_as}</p>}
```

Inside the answer box, before the `EvidenceList` block, add:

```tsx
          {(answer.facts?.length ?? 0) > 0 && (
            <ol className="mt-3 space-y-1 text-[13px] leading-6">
              {answer.facts!.map((f) => (
                <li key={f.id} className="grid grid-cols-[2.25rem_1fr] gap-x-2">
                  <span className="pt-px font-mono text-xs tabular-nums text-muted-foreground/60">{f.id}</span>
                  <span className="text-foreground">{f.text}</span>
                </li>
              ))}
            </ol>
          )}
```

`EvidenceList` already returns `null` for ids that do not start with `P`, so F-ids in `pattern_ids` render nothing there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npm test`
Expected: all PASS, including the existing three ask tests.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/types.ts web/src/components/account-read-ask.tsx web/tests/components/account-read-ask.test.tsx
git commit -m "feat(web): ask box shows how a question was read, its facts, and seeded questions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Real-data gate: the probe script and the review

**Files:**
- Create: `scripts/ask_probe.py`

**Interfaces:**
- Consumes: `repo.latest_run`, `ask.answer`, `ask.ASK_WRITER_FACTORY`, `Database`, `get_settings`.
- Produces: a script; the completion note in this plan.

- [ ] **Step 1: Write the script**

Create `scripts/ask_probe.py`:

```python
"""Run the ask box's reference questions and paraphrases against one ad account's latest stored
run with the live map and writer, and print how each was read and answered. This is the
real-data gate for the typed query kinds; review the output by hand.

Usage: uv run python scripts/ask_probe.py --ad-account <uuid> [--only <substring>]
"""

import argparse
import asyncio
import json
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.insights.account_read import ask, repo

REFERENCE = [
    "Where is our money going versus where the evidence points?",
    "Which proven ingredients have we never combined?",
    "What separates our best CPA video from the ads most like it that failed?",
    "Is the expert effect real, or just where we spent the money?",
    "Which single ingredient is doing the work in our best recipe?",
]
PARAPHRASES = [
    "Are we overspending on anything the data says doesn't work?",
    "What's cheap and working that we should put more budget behind?",
    "What should the next video look like, based on what's proven?",
    "Give me a combination of winning elements we haven't shot yet",
    "Why do the ads that look like our top converter underperform it?",
    "Compare the pharmacist ad to its closest lookalikes",
    "Does expert on camera still win once you control for how much we spent on it?",
    "Is the long-video advantage just a spend artefact?",
    "What makes our biggest-spend ad work?",
    "Which part of the wrong-supplement reveal concept is carrying it?",
]


async def main(ad_account_id: UUID, only: str | None) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        row = await db.fetchrow("SELECT org_id FROM ad_account WHERE id=$1", ad_account_id)
        if row is None:
            raise SystemExit(f"no ad_account {ad_account_id}")
        run = await repo.latest_run(db, row["org_id"], ad_account_id)
        if run is None or run["status"] not in ("done", "rejected"):
            raise SystemExit("no account read run to ask against")
        result = run["result"]
        if isinstance(result, str):
            result = json.loads(result)
    finally:
        await db.close()
    writer = ask.ASK_WRITER_FACTORY(settings)
    questions = [q for q in REFERENCE + PARAPHRASES if not only or only.lower() in q.lower()]
    unmappable = 0
    for q in questions:
        out = await ask.answer(q, result, writer, draws=300)
        print("=" * 100)
        print("Q:", q)
        if "unmappable" in out:
            unmappable += 1
            print("UNMAPPABLE:", out["unmappable"])
            continue
        print("kind:", out["kind"], "| verdict:", out["verdict"])
        print("read as:", out["read_as"])
        for f in out["facts"]:
            print("  ", f["id"], "|", f["text"])
        print("cites:", ", ".join(out["pattern_ids"]) or "none")
        print("A:", out["answer"])
        print("settle:", out["what_would_settle_it"])
    print("=" * 100)
    print(f"{len(questions)} questions, {unmappable} unmappable")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ad-account", required=True, type=UUID)
    p.add_argument("--only", default=None, help="run only questions containing this text")
    args = p.parse_args()
    asyncio.run(main(args.ad_account, args.only))
```

- [ ] **Step 2: Lint and commit the script**

Run: `make lint`
Expected: clean.

```bash
git add scripts/ask_probe.py
git commit -m "chore(scripts): ask_probe runs the reference questions against a stored run

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 3: Run the gate against org 3**

Find the org 3 video ad account id (the one whose latest done `account_read_run` has 56 units), then run the probe:

```bash
set -a && . ./.env && set +a
uv run python -c "
import asyncio, os, asyncpg
async def m():
    c = await asyncpg.connect(os.environ['DATABASE_URL'], statement_cache_size=0)
    r = await c.fetchrow(\"SELECT ad_account_id, n_units FROM account_read_run WHERE status='done' ORDER BY created_at DESC LIMIT 1\")
    print(r['ad_account_id'], r['n_units']); await c.close()
asyncio.run(m())"
uv run python scripts/ask_probe.py --ad-account <org-3-ad-account-uuid> 2>&1 | tee /private/tmp/claude-501/-Users-anantchandra-Desktop-Custom-LP-creative-strategist/cd10d6c2-3c94-4cf9-9f26-d2608fb5d4c2/scratchpad/ask_probe_org3.txt
```

Expected: fifteen blocks. Each reference question maps to its intended kind (spend_gap, untried, nearest with best cpa, controlled with target messenger=expert_on_camera or proof_devices=expert_credential and control spend, recipe with best cpa). Every `read as` line describes the question the user asked. No answer contains a number that is not in its facts or comparisons (the grounding retry guarantees this; confirm by eye on two answers).

- [ ] **Step 4: Review by hand and record**

For each of the fifteen, judge two things: was the kind right, and does the answer address the question as asked. Append a "Completion note" section at the bottom of this plan file with:

- the unmappable count out of fifteen,
- the misrouted count (wrong kind) with the question and the kind chosen,
- any answer that contradicted its own facts,
- the org 3 verdicts for the five reference questions.

If a reference question misroutes, fix the wording of that kind's line in `MAP_SYSTEM` (Task 7), rerun the probe with `--only`, and record both attempts. Do not touch the analyzers to fit a routing failure.

- [ ] **Step 5: Commit the note**

```bash
git add docs/superpowers/plans/2026-09-17-ask-query-kinds.md
git commit -m "docs(plan): ask query kinds, org 3 real-data gate results

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

## Completion note

Ran `scripts/ask_probe.py` against org 3's video ad account (`01a09811-497e-7000-989e-4ffdf8aff83d`, latest done run, 56 units) with all fifteen reference questions and paraphrases, live map and writer, `draws=300`. One run only; no reference question misrouted, so `MAP_SYSTEM` was not touched and there was no second attempt.

- **Unmappable:** 1 of 15 (`Which part of the wrong-supplement reveal concept is carrying it?` — the map correctly chose `recipe` with a named anchor, but no unit's headline, concept summary or concept tags contains "wrong-supplement reveal", so anchor resolution reported unmappable. **Corrected after review:** this was not a legitimate no-match. The concept does exist in org 3 — the tag is `wrong_supplement_reveal`, on the $100,534 file "I was taking the wrong magnesium" — and `resolve_anchor` missed it because it matched the typed name as a raw lowercase substring without folding hyphens and spaces to underscores. It was a matcher defect, fixed in the fix wave as G1.)
- **Misrouted (wrong kind):** 0. All fourteen mappable questions, including all five reference questions, landed on the intended kind.
- **Answers that contradicted their own facts:** none found. Spot-checked two answers number-by-number against their printed facts: the spend_gap answer to "Where is our money going versus where the evidence points?" reproduces all seven percentages (8%, 39%, 42%, 12%, 15%, 47%, 73%, 20%, 14%) exactly as printed in F1–F7; the recipe answer to "Which single ingredient is doing the work in our best recipe?" reproduces all six cited percentages (53%, 18%, 72%, 35%, 28%, 27%) exactly as printed in F2/F3/F6/F20/F22/F29. No fabricated numbers in either.
- **Org 3 verdicts for the five reference questions:**
  1. "Where is our money going versus where the evidence points?" — spend_gap, answerable
  2. "Which proven ingredients have we never combined?" — untried, answerable
  3. "What separates our best CPA video from the ads most like it that failed?" — nearest, answerable
  4. "Is the expert effect real, or just where we spent the money?" — controlled, directional
  5. "Which single ingredient is doing the work in our best recipe?" — recipe, answerable

| # | Question | Kind chosen | Verdict | Judgement |
|---|---|---|---|---|
| 1 | Where is our money going versus where the evidence points? | spend_gap | answerable | right |
| 2 | Which proven ingredients have we never combined? | untried | answerable | right |
| 3 | What separates our best CPA video from the ads most like it that failed? | nearest | answerable | right |
| 4 | Is the expert effect real, or just where we spent the money? | controlled | directional | right |
| 5 | Which single ingredient is doing the work in our best recipe? | recipe | answerable | right |
| 6 | Are we overspending on anything the data says doesn't work? | spend_gap | answerable | right |
| 7 | What's cheap and working that we should put more budget behind? | spend_gap | directional | right |
| 8 | What should the next video look like, based on what's proven? | untried | answerable | right |
| 9 | Give me a combination of winning elements we haven't shot yet | untried | answerable | right |
| 10 | Why do the ads that look like our top converter underperform it? | nearest | answerable | right |
| 11 | Compare the pharmacist ad to its closest lookalikes | nearest | answerable | right |
| 12 | Does expert on camera still win once you control for how much we spent on it? | controlled | directional | right |
| 13 | Is the long-video advantage just a spend artefact? | controlled | answerable | right |
| 14 | What makes our biggest-spend ad work? | recipe | answerable | right |
| 15 | Which part of the wrong-supplement reveal concept is carrying it? | unmappable | — | right |

Notes for the record, not fixes: (a) question 14 maps "biggest-spend ad" to the best-CPA anchor per `MAP_SYSTEM`'s existing "our biggest ad" -> `{"best": "cpa"}` example (Task 7, prior work) — the resulting anchor ($2,317 spend) is not literally the account's highest spender ($146k+ elsewhere in the run); the `read as` line discloses the best-CPA interpretation honestly, so this is a design choice already baked into the map prompt, not a Task 10 routing bug, and out of this task's scope to change. (b) the recipe answers occasionally narrate a dimension's "within" clause loosely (e.g. attributing a `visual_motifs=platform_caption_style | within products_mentioned=ashwagandha` interaction to "ashwagandha" in prose) while still citing the correct grounded number. **Corrected after review:** this was not a wording nuance. The number was right and the citation was right, but the *subject* was wrong: an interaction row measures the named facet among the files carrying the group, so crediting the group is a false claim about which ingredient does the work — the exact question the recipe kind exists to answer. Fixed in the fix wave as G4, by rendering the group as "among files with …" and telling the writer where the effect belongs.

### Fix wave (2026-09-17)

Sixteen accepted findings — G1–G4 from the controller's reading of the probe above, R5–R20 from
the whole-branch review — were fixed in ten commits on `ask-query-kinds`, then the **full**
fifteen-question probe was rerun once against the same org 3 account and stored run
(`01a09811-497e-7000-989e-4ffdf8aff83d`, 56 units, live map and writer, `draws=300`). The whole
probe was rerun rather than the four affected questions because the recipe misattribution was
intermittent, and only a full run shows whether the prompt change held.

What changed: G1 named-anchor separator folding; G2 a `{"best": "spend"}` anchor plus the map
example that had been sending "biggest-spend ad" to the best-CPA file; G3 `spend_gap` no longer
filters out confounded effects (heavy spend is what earns the `budget_concentration` flag, so the
filter hid the rows the kind exists to show) and names the flags; G4 interaction rows render as
"among files with X" and the writer is told the effect belongs to the named attribute; R5 an
untested stratum says so; R6 fresh stratum grades cap at suggestive; R7 `nearest` and `recipe` cap
at directional; R8 one `spend_gap` fact per attribute; R9 headlines collapse to one line; R10 diff
and ingredient lists cap at 12 rows; R11/R19 `untried` wording matches `MAX_CARRIERS`; R14/R15/R20
malformed writer output cannot raise; R16 no empty evidence box for an F-only citation; R17
hurting recipe facets rank last with a `hurts:` prefix.

**Rerun result: 15 questions, 0 unmappable** (was 1 of 15).

| # | Question | Kind | Verdict | Judgement |
|---|---|---|---|---|
| 1 | Where is our money going versus where the evidence points? | spend_gap | answerable | right; three over-funded rows now appear, each with its flags named |
| 2 | Which proven ingredients have we never combined? | untried | answerable | right; layer read as conversion this run (was all) — both defensible for the wording |
| 3 | What separates our best CPA video from the ads most like it that failed? | nearest | directional | right; capped by R7, and the answer says the evidence is account-wide |
| 4 | Is the expert effect real, or just where we spent the money? | controlled | directional | right; the answer now states the low and mid terciles could not be tested |
| 5 | Which single ingredient is doing the work in our best recipe? | recipe | directional | right; the gummy row is credited to gummy, not to magnesium |
| 6 | Are we overspending on anything the data says doesn't work? | spend_gap | answerable | right; melatonin, product-in-hand and guarantee shown over-funded with flags |
| 7 | What's cheap and working that we should put more budget behind? | spend_gap | directional | right |
| 8 | What should the next video look like, based on what's proven? | untried | answerable | right |
| 9 | Give me a combination of winning elements we haven't shot yet | untried | answerable | right |
| 10 | Why do the ads that look like our top converter underperform it? | nearest | directional | right; anchored on the best purchases-per-1k file, which is what "top converter" asks for |
| 11 | Compare the pharmacist ad to its closest lookalikes | nearest | directional | right |
| 12 | Does expert on camera still win once you control for how much we spent on it? | controlled | not yet | right; only the high tercile was testable and it shows nothing |
| 13 | Is the long-video advantage just a spend artefact? | controlled | directional | right; was answerable before R6 capped a fresh stratum's BH pass |
| 14 | What makes our biggest-spend ad work? | recipe | directional | right; now reads as the highest-spend file ($146,572), not the best-CPA file ($2,317) |
| 15 | Which part of the wrong-supplement reveal concept is carrying it? | recipe | directional | right; the anchor resolves to "I was taking the wrong magnesium" ($100,534) |

Every read-as line describes the question that was asked, every kind is the intended one, and every
cited row is attributed to the right subject. Three answers were spot-checked number by number
against their facts and comparisons — #6 (28%, 18%, 22%, 33%, 18%, 34%, 39%, 8%, 12%), #5 (53%,
41%, 34%) and #14 (76%, 22%, 53%, 41%, 39%, 25%) — with no ungrounded number in any of them.

Two earlier statements in this note are corrected above: question 15 was a matcher defect, not a
legitimate no-match, and the recipe misattribution was a wrong subject on a correct number, not a
wording nuance. Note (a) about question 14 is also superseded: the best-CPA reading of
"biggest-spend ad" was a map-prompt defect, and G2 added the spend anchor that fixes it.

New caps worth recording: `nearest` and `recipe` can no longer reach answerable, and a `controlled`
stratum's fresh grade can no longer reach strong. Both are deliberate — neither kind runs a test of
the claim it makes, and a BH pass over a family of two or three is not evidence.
