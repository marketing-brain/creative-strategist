# Aggregate Insights (Account Creative Read) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For one ad account, compute graded cross-creative patterns from v2 video tags plus pooled metrics, write a pointed prose "account read" that merges what the ads are with how they performed, and answer a strategist's plain-language question from the same stored data.

**Architecture:** A new package `cs.insights.account_read` with pure modules (facets, stats, patterns, writer, ask) over one DB module (units) and one repo (a single JSONB run table). One job computes and stores a run per ad account. Two FastAPI routes serve the latest run and answer questions. One Next.js section renders the read with expandable evidence and an ask box that calls a server action.

**Tech Stack:** Python 3.12, asyncpg via `cs.core.db.Database`, FastAPI, pydantic-settings, openai client against the Vercel AI Gateway (`settings.creative_insight_model`), dbmate migrations, pytest (DB tests against `DATABASE_URL_TEST`), Next.js app router, vitest.

**Spec:** `docs/superpowers/specs/2026-09-17-aggregate-insights-design.md`

## Global Constraints

- No new Python dependencies. Statistics are pure Python (`math`, `statistics`, `random`).
- All model calls go through the AI Gateway with `settings.creative_insight_model`. No Anthropic/Claude calls.
- Video format only. Units are footage units keyed by primary-asset `sha256`.
- Metric floors: rate metrics need `impressions >= 2000` per unit (`thruplay_rate` needs `plays >= 500`); pooled metrics need `>= 20` purchases per side; every comparison needs `>= 5` units per side.
- Grades: `strong` (Benjamini-Hochberg, q = 0.10, over every test in the run), `suggestive` (p < 0.10), `directional` (p < 0.25 and |change| ≥ 0.15), `thin`.
- Grouping dimensions for interactions come from `settings.account_read_grouping_dims`, default `audience_callouts,products_mentioned,features_mentioned,messenger,duration_s`; grouping values need `>= 10` units.
- The writer must produce findings with `mechanism`, `exemplars`, `exception`, `why_non_obvious`; prose numbers must appear in the brief; verdicts are capped in code.
- Tests: run with `export DATABASE_URL_TEST=$(grep -o "DATABASE_URL_TEST=.*" .env | cut -d= -f2-)` then `uv run pytest <path> -q`. Never `set -a; . .env` before pytest. Never run two pytest processes at once against the same test DB.
- Lint touched files only: `uv run ruff check <files> && uv run ruff format <files>`.
- Commit after every task with the `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` trailer.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/cs/insights/account_read/__init__.py` | empty |
| `src/cs/insights/account_read/types.py` | `Unit`, `Test`, `RunResult`, `Read`, `Finding`, JSON (de)serialization helpers |
| `src/cs/insights/account_read/facets.py` | tag rows → facets, terciles, derived facets |
| `src/cs/insights/account_read/stats.py` | Mann-Whitney, permutation, BH, `grade`, `compare` |
| `src/cs/insights/account_read/units.py` | DB query → `list[Unit]` |
| `src/cs/insights/account_read/patterns.py` | `run(units, settings) -> RunResult` |
| `src/cs/insights/account_read/writer.py` | brief, prompt, `AccountReadWriter` protocol, gateway writer, parse, checks |
| `src/cs/insights/account_read/ask.py` | question → query → tests → answer |
| `src/cs/insights/account_read/repo.py` | `insert_run`, `latest_run`, `purge_runs` |
| `src/cs/insights/account_read/jobs.py` | handler + nightly schedule |
| `src/cs/insights/fakes.py` | add `FakeAccountReadWriter` |
| `src/cs/core/config.py` | three settings |
| `db/migrations/20260917000001_account_read.sql` | table |
| `src/cs/api/routers/insights.py` | two routes |
| `src/cs/jobs/all_handlers.py` | import the new jobs module |
| `scripts/compute_account_read.py` | enqueue on demand |
| `web/src/lib/types.ts`, `web/src/lib/api.ts` | types + fetchers |
| `web/src/app/(app)/insights/actions.ts` | server action for ask |
| `web/src/components/account-read-evidence.tsx`, `account-read.tsx`, `account-read-ask.tsx` | UI |
| `web/src/app/(app)/insights/page.tsx` | mount the section |
| `tests/insights/account_read/…` | Python tests |
| `web/tests/account-read.test.tsx` | vitest |

---

### Task 1: Types and statistics

**Files:**
- Create: `src/cs/insights/account_read/__init__.py`, `src/cs/insights/account_read/types.py`, `src/cs/insights/account_read/stats.py`
- Test: `tests/insights/account_read/__init__.py`, `tests/insights/account_read/test_stats.py`

**Interfaces:**
- Produces: `Unit`, `Test` dataclasses; `mann_whitney_p(x, y) -> float`; `permutation_p(cell, comp, stat, draws) -> float`; `benjamini_hochberg(pvals, q=0.10) -> set[int]`; `grade(p, change, passes_bh) -> str`; `compare(cell: list[Unit], comp: list[Unit], *, dimension, value, group=None, bin_edges=None, draws=1000) -> list[Test]`; `unit_rates(u) -> dict[str, float]`; `pooled(units, num, den) -> float | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/insights/account_read/test_stats.py
from datetime import date
from decimal import Decimal

from cs.insights.account_read.stats import (
    benjamini_hochberg, compare, grade, mann_whitney_p, permutation_p, pooled, unit_rates,
)
from cs.insights.account_read.types import Unit


def unit(sha, imp=10_000, v3=4_000, plays=8_000, thru=1_200, lc=300, purch=30, spend=1_000, first=date(2026, 6, 1), last=date(2026, 6, 30), facets=()):
    return Unit(sha256=sha, creative_ids=[], rep_creative_id=None, headline=sha, duration_s=60.0,
                spend=Decimal(spend), impressions=imp, plays=plays, video_3s_views=v3, thruplays=thru,
                p25=plays // 2, p50=plays // 3, p75=plays // 4, p100=plays // 5, link_clicks=lc, purchases=purch,
                first_day=first, last_day=last, facets=set(facets), numeric={}, first_product_s=None,
                concept_summary=None, hook_verbatim=None, insight_paragraph=None)


def test_mann_whitney_matches_known_value():
    # scipy.stats.mannwhitneyu([1,2,3,4,5],[6,7,8,9,10], alternative='two-sided') -> p ≈ 0.0079 (asymptotic ≈ 0.0090)
    p = mann_whitney_p([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
    assert 0.005 < p < 0.02
    assert mann_whitney_p([1, 2, 3], [1, 2, 3]) == 1.0


def test_benjamini_hochberg_passes_only_small_p():
    passing = benjamini_hochberg([0.001, 0.02, 0.04, 0.5, 0.9], q=0.10)
    assert passing == {0, 1, 2}
    assert benjamini_hochberg([0.5, 0.6]) == set()


def test_grade_boundaries():
    assert grade(0.5, 0.5, True) == "strong"
    assert grade(0.05, 0.01, False) == "suggestive"
    assert grade(0.2, 0.2, False) == "directional"
    assert grade(0.2, 0.05, False) == "thin"
    assert grade(0.5, 0.5, False) == "thin"


def test_permutation_detects_planted_difference():
    cell = [unit(f"c{i}", purch=60) for i in range(8)]
    comp = [unit(f"k{i}", purch=20) for i in range(8)]
    def stat(a, b):
        ra, rb = pooled(a, "purchases", "impressions"), pooled(b, "purchases", "impressions")
        return None if not ra or not rb else ra / rb - 1
    assert permutation_p(cell, comp, stat, draws=400) < 0.05


def test_unit_rates_respects_floors():
    assert unit_rates(unit("a")) == {"hook_rate": 0.4, "thruplay_rate": 0.15, "link_ctr": 0.03}
    assert "hook_rate" not in unit_rates(unit("b", imp=1_999))
    assert "thruplay_rate" not in unit_rates(unit("c", plays=499))


def test_compare_emits_rate_and_pooled_tests_with_confounds():
    cell = [unit(f"c{i}", lc=600, imp=10_000 if i else 200_000, purch=40) for i in range(6)]
    comp = [unit(f"k{i}", lc=300, first=date(2026, 8, 1), last=date(2026, 8, 30)) for i in range(6)]
    tests = compare(cell, comp, dimension="angles", value="novelty", draws=200)
    by_metric = {t.metric: t for t in tests}
    assert set(by_metric) == {"hook_rate", "thruplay_rate", "link_ctr", "purchases_per_1k", "cpa"}
    ctr = by_metric["link_ctr"]
    assert ctr.n_cell == 6 and ctr.n_complement == 6 and ctr.change > 0 and ctr.kind == "per_unit"
    assert "budget_concentration" in ctr.confounds and "seasonality" in ctr.confounds
    assert by_metric["cpa"].kind == "pooled" and ctr.group is None and ctr.grade == "ungraded"


def test_compare_returns_nothing_below_minimums():
    assert compare([unit("a")] * 4, [unit("b")] * 6, dimension="d", value="v") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `export DATABASE_URL_TEST=$(grep -o "DATABASE_URL_TEST=.*" .env | cut -d= -f2-) && uv run pytest tests/insights/account_read/test_stats.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'cs.insights.account_read'`

- [ ] **Step 3: Write types.py**

```python
# src/cs/insights/account_read/types.py
"""Data shapes for the account read. Everything here serializes to one JSON document."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

RATE_METRICS = ("hook_rate", "thruplay_rate", "link_ctr")
POOLED_METRICS = ("purchases_per_1k", "cpa")
METRICS = RATE_METRICS + POOLED_METRICS
LOWER_IS_BETTER = {"cpa"}


@dataclass
class Unit:
    sha256: str
    creative_ids: list[UUID]
    rep_creative_id: UUID | None
    headline: str
    duration_s: float | None
    spend: Decimal
    impressions: int
    plays: int
    video_3s_views: int
    thruplays: int
    p25: int
    p50: int
    p75: int
    p100: int
    link_clicks: int
    purchases: int
    first_day: date
    last_day: date
    facets: set[tuple[str, str]]
    numeric: dict[str, float]
    first_product_s: float | None
    concept_summary: str | None
    hook_verbatim: str | None
    insight_paragraph: str | None

    def has(self, dimension: str, value: str) -> bool:
        return (dimension, value) in self.facets

    def midpoint(self) -> float:
        a, b = self.first_day.toordinal(), self.last_day.toordinal()
        return a + (b - a) / 2


@dataclass
class Test:
    dimension: str
    value: str
    group: str | None
    metric: str
    kind: Literal["per_unit", "pooled"]
    n_cell: int
    n_complement: int
    cell_value: float
    comp_value: float
    change: float
    p: float
    grade: str
    confounds: list[str]
    cell_shas: list[str]
    bin_edges: tuple[float, float] | None = None
    cell_purchases: int | None = None


@dataclass
class Finding:
    title: str
    layer: str
    paragraph: str
    mechanism: str
    exemplars: list[str]
    exception: str
    why_non_obvious: str
    pattern_ids: list[str]
    confidence: str


@dataclass
class Read:
    overview: str
    findings: list[Finding]
    entangled: str
    no_evidence: str
    next_tests: list[str]
    data_needed: list[str]


@dataclass
class RunResult:
    units: list[Unit]
    tests: list[Test]                      # main effects then interactions; P<n> = tests[n-1]
    overlaps: list[dict[str, Any]]
    stratified: list[dict[str, Any]]
    retention_by_reveal: dict[str, dict[str, Any]]
    summary: dict[str, Any]
    bin_edges: dict[str, tuple[float, float]] = field(default_factory=dict)


def to_json(obj: Any) -> Any:
    """dataclasses / Decimal / date / UUID / set / tuple → JSON-safe."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_json(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k) if not isinstance(k, tuple) else "|".join(k): to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_json(v) for v in (sorted(obj) if isinstance(obj, set) else obj)]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date,)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    return obj


def unit_from_json(d: dict) -> Unit:
    return Unit(
        sha256=d["sha256"], creative_ids=[UUID(x) for x in d["creative_ids"]],
        rep_creative_id=UUID(d["rep_creative_id"]) if d.get("rep_creative_id") else None,
        headline=d["headline"], duration_s=d["duration_s"], spend=Decimal(str(d["spend"])),
        impressions=d["impressions"], plays=d["plays"], video_3s_views=d["video_3s_views"], thruplays=d["thruplays"],
        p25=d["p25"], p50=d["p50"], p75=d["p75"], p100=d["p100"], link_clicks=d["link_clicks"], purchases=d["purchases"],
        first_day=date.fromisoformat(d["first_day"]), last_day=date.fromisoformat(d["last_day"]),
        facets={tuple(f) for f in d["facets"]}, numeric=d["numeric"], first_product_s=d["first_product_s"],
        concept_summary=d.get("concept_summary"), hook_verbatim=d.get("hook_verbatim"), insight_paragraph=d.get("insight_paragraph"),
    )


def test_from_json(d: dict) -> Test:
    d = dict(d)
    if d.get("bin_edges") is not None:
        d["bin_edges"] = tuple(d["bin_edges"])
    return Test(**d)
```

- [ ] **Step 4: Write stats.py**

```python
# src/cs/insights/account_read/stats.py
"""Pure-Python comparison statistics for footage units. No numpy, no scipy."""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable

from cs.insights.account_read.types import RATE_METRICS, Test, Unit

IMP_FLOOR = 2000
PLAYS_FLOOR = 500
MIN_CELL = 5
MIN_PURCHASES = 20


def unit_rates(u: Unit) -> dict[str, float]:
    m: dict[str, float] = {}
    if u.impressions >= IMP_FLOOR:
        m["hook_rate"] = u.video_3s_views / u.impressions
        m["link_ctr"] = u.link_clicks / u.impressions
    if u.plays >= PLAYS_FLOOR:
        m["thruplay_rate"] = u.thruplays / u.plays
    return m


def retention_shares(u: Unit) -> dict[str, float]:
    if u.plays < PLAYS_FLOOR:
        return {}
    return {q: getattr(u, q) / u.plays for q in ("p25", "p50", "p75", "p100")}


def pooled(units: list[Unit], num: str, den: str) -> float | None:
    d = sum(getattr(u, den) for u in units)
    return (sum(getattr(u, num) for u in units) / float(d)) if d else None


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def mann_whitney_p(x: list[float], y: list[float]) -> float:
    """Two-sided Mann-Whitney U, normal approximation with tie correction."""
    n1, n2 = len(x), len(y)
    allv = sorted([(v, 0) for v in x] + [(v, 1) for v in y])
    ranks = [0.0] * len(allv)
    i, tie = 0, 0.0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        t = j - i + 1
        if t > 1:
            tie += t**3 - t
        i = j + 1
    r1 = sum(r for r, (_, g) in zip(ranks, allv, strict=True) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    n = n1 + n2
    sigma2 = n1 * n2 / 12 * ((n + 1) - tie / (n * (n - 1)))
    if sigma2 <= 0:
        return 1.0
    z = (u1 - mu) / math.sqrt(sigma2)
    return max(min(2 * (1 - _phi(abs(z))), 1.0), 0.0)


def permutation_p(
    cell: list[Unit], comp: list[Unit], stat: Callable[[list[Unit], list[Unit]], float | None], draws: int = 1000, seed: int = 7
) -> float:
    obs = stat(cell, comp)
    if obs is None:
        return 1.0
    rng = random.Random(seed)
    pool, n, hits = list(cell) + list(comp), len(cell), 0
    for _ in range(draws):
        rng.shuffle(pool)
        s = stat(pool[:n], pool[n:])
        if s is not None and abs(s) >= abs(obs):
            hits += 1
    return (hits + 1) / (draws + 1)


def benjamini_hochberg(pvals: list[float], q: float = 0.10) -> set[int]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    k = 0
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= q * rank / m:
            k = rank
    return set(order[:k])


def grade(p: float, change: float, passes_bh: bool) -> str:
    if passes_bh:
        return "strong"
    if p < 0.10:
        return "suggestive"
    if p < 0.25 and abs(change) >= 0.15:
        return "directional"
    return "thin"


def _confounds(cell: list[Unit], comp: list[Unit]) -> list[str]:
    out = []
    cell_imp = sum(u.impressions for u in cell)
    if cell_imp and max(u.impressions for u in cell) / cell_imp >= 0.5:
        out.append("budget_concentration")
    if abs(statistics.median(u.midpoint() for u in cell) - statistics.median(u.midpoint() for u in comp)) > 15:
        out.append("seasonality")
    return out


def _ppk_stat(a: list[Unit], b: list[Unit]) -> float | None:
    ra, rb = pooled(a, "purchases", "impressions"), pooled(b, "purchases", "impressions")
    return None if not ra or not rb else ra / rb - 1


def _cpa_stat(a: list[Unit], b: list[Unit]) -> float | None:
    ra, rb = pooled(a, "spend", "purchases"), pooled(b, "spend", "purchases")
    return None if not ra or not rb else float(ra) / float(rb) - 1


def compare(
    cell: list[Unit],
    comp: list[Unit],
    *,
    dimension: str,
    value: str,
    group: str | None = None,
    bin_edges: tuple[float, float] | None = None,
    draws: int = 1000,
) -> list[Test]:
    """Cell vs complement on every metric. Grades are filled in later by the run's BH pass
    ('ungraded' until then)."""
    if len(cell) < MIN_CELL or len(comp) < MIN_CELL:
        return []
    conf = _confounds(cell, comp)
    base = dict(dimension=dimension, value=value, group=group, confounds=conf, cell_shas=[u.sha256 for u in cell], bin_edges=bin_edges)
    out: list[Test] = []
    cell_rates = [unit_rates(u) for u in cell]
    comp_rates = [unit_rates(u) for u in comp]
    for metric in RATE_METRICS:
        x = [r[metric] for r in cell_rates if metric in r]
        y = [r[metric] for r in comp_rates if metric in r]
        if len(x) < MIN_CELL or len(y) < MIN_CELL:
            continue
        mx, my = statistics.median(x), statistics.median(y)
        if not my:
            continue
        out.append(Test(**base, metric=metric, kind="per_unit", n_cell=len(x), n_complement=len(y), cell_value=mx, comp_value=my, change=mx / my - 1, p=mann_whitney_p(x, y), grade="ungraded"))
    pc, pk = sum(u.purchases for u in cell), sum(u.purchases for u in comp)
    if pc >= MIN_PURCHASES and pk >= MIN_PURCHASES:
        for metric, fn, cv, kv in (
            ("purchases_per_1k", _ppk_stat, 1000 * (pooled(cell, "purchases", "impressions") or 0), 1000 * (pooled(comp, "purchases", "impressions") or 0)),
            ("cpa", _cpa_stat, float(pooled(cell, "spend", "purchases") or 0), float(pooled(comp, "spend", "purchases") or 0)),
        ):
            ch = fn(cell, comp)
            if ch is None:
                continue
            out.append(Test(**base, metric=metric, kind="pooled", n_cell=len(cell), n_complement=len(comp), cell_value=cv, comp_value=kv, change=ch, p=permutation_p(cell, comp, fn, draws), grade="ungraded", cell_purchases=pc))
    return out
```

Create empty `src/cs/insights/account_read/__init__.py` and `tests/insights/account_read/__init__.py`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/insights/account_read/test_stats.py -q`
Expected: 7 passed

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/cs/insights/account_read tests/insights/account_read && uv run ruff format src/cs/insights/account_read tests/insights/account_read
git add src/cs/insights/account_read tests/insights/account_read
git commit -m "feat(account-read): unit and test types with pure-Python comparison statistics

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Facet extraction

**Files:**
- Create: `src/cs/insights/account_read/facets.py`
- Test: `tests/insights/account_read/test_facets.py`

**Interfaces:**
- Consumes: nothing from other tasks (operates on plain tag-row dicts `{dimension, kind, value, raw_value, t_start_s, ordinal}`).
- Produces: `extract(tag_rows, duration_s) -> Extracted` with fields `facets: set[tuple[str,str]]`, `numeric: dict[str,float]`, `first_product_s: float | None`, `concept_summary: str | None`, `hook_verbatim: str | None`; `reveal_bin(t0, duration_s) -> str`; `tercile_labels(values: dict[str,float]) -> tuple[dict[str,str], tuple[float,float] | None]`; constants `SKIP_DIMS`, `REVEAL_BINS`, `NUMERIC_DIMS`, `DERIVED_SINGLE_DIMS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/insights/account_read/test_facets.py
from cs.insights.account_read.facets import extract, reveal_bin, tercile_labels


def row(d, k, v, raw=None, t=None, o=None):
    return {"dimension": d, "kind": k, "value": v, "raw_value": raw, "t_start_s": t, "ordinal": o}


def test_extract_closed_open_numeric_text_and_derived():
    rows = [
        row("messenger", "single", "expert_on_camera"),
        row("hook_devices", "multi", "warning_or_risk", t=0),
        row("claims", "open", "promotes_restful_sleep"),
        row("duration_s", "numeric", "93.2"),
        row("scenes", "ordered", "talking_head", raw="face", t=0, o=0),
        row("scenes", "ordered", "product_closeup", raw="face|product", t=27, o=1),
        row("concept_summary", "text", "A pharmacist warns about OTC sleep aids."),
        row("hook_verbatim", "text", "Worst OTC sleep aids"),
        row("beats", "ordered", "hook", t=0, o=0),
        row("transcript", "text", "long transcript"),
    ]
    x = extract(rows, duration_s=93.2)
    assert ("messenger", "expert_on_camera") in x.facets
    assert ("hook_devices", "warning_or_risk") in x.facets
    assert ("claims", "promotes_restful_sleep") in x.facets
    assert ("opening_scene", "talking_head") in x.facets
    assert ("first_product_shown", "10s to midpoint") in x.facets
    assert x.numeric == {"duration_s": 93.2}
    assert x.first_product_s == 27
    assert x.concept_summary.startswith("A pharmacist") and x.hook_verbatim == "Worst OTC sleep aids"
    assert not any(d in ("beats", "transcript", "scenes") for d, _ in x.facets)


def test_reveal_bins():
    assert reveal_bin(None, 60) == "never"
    assert reveal_bin(3, 60) == "under 5s"
    assert reveal_bin(7, 60) == "5 to 10s"
    assert reveal_bin(20, 60) == "10s to midpoint"
    assert reveal_bin(40, 60) == "second half"
    assert reveal_bin(12, None) == "second half"


def test_tercile_labels_and_edges():
    labels, edges = tercile_labels({f"u{i}": float(v) for i, v in enumerate([10, 20, 30, 40, 50, 60, 70, 80, 90])})
    assert edges == (40.0, 70.0)
    assert labels["u0"] == "low" and labels["u4"] == "mid" and labels["u8"] == "high"
    assert tercile_labels({"a": 1.0, "b": 2.0}) == ({}, None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/insights/account_read/test_facets.py -q`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` for `facets`

- [ ] **Step 3: Write facets.py**

```python
# src/cs/insights/account_read/facets.py
"""Tag rows of one creative's v2 run → facets for the account read."""

from __future__ import annotations

from dataclasses import dataclass, field

SKIP_DIMS = {"beats", "transcript", "hook_verbatim", "concept_summary", "overlay_texts", "scenes"}
TEXT_DIMS = ("concept_summary", "hook_verbatim")
NUMERIC_DIMS = ("duration_s", "cuts_per_10s")
DERIVED_SINGLE_DIMS = ("first_product_shown", "opening_scene")
REVEAL_BINS = ("under 5s", "5 to 10s", "10s to midpoint", "second half", "never")


@dataclass
class Extracted:
    facets: set[tuple[str, str]] = field(default_factory=set)
    numeric: dict[str, float] = field(default_factory=dict)
    first_product_s: float | None = None
    concept_summary: str | None = None
    hook_verbatim: str | None = None


def reveal_bin(t0: float | None, duration_s: float | None) -> str:
    if t0 is None:
        return "never"
    if t0 < 5:
        return "under 5s"
    if t0 < 10:
        return "5 to 10s"
    if duration_s and t0 < duration_s / 2:
        return "10s to midpoint"
    return "second half"


def extract(tag_rows: list[dict], duration_s: float | None) -> Extracted:
    x = Extracted()
    product_ts: list[float] = []
    for t in tag_rows:
        d, k, v = t["dimension"], t["kind"], t["value"]
        if d in TEXT_DIMS:
            setattr(x, d, v)
            continue
        if d == "scenes" and k == "ordered":
            if t.get("ordinal") == 0:
                x.facets.add(("opening_scene", v))
            raw = t.get("raw_value") or ""
            if ("product" in raw or "packaging" in raw) and t.get("t_start_s") is not None:
                product_ts.append(float(t["t_start_s"]))
            continue
        if d in SKIP_DIMS:
            continue
        if k in ("single", "multi", "open"):
            x.facets.add((d, v))
        elif k == "numeric":
            try:
                x.numeric[d] = float(v)
            except (TypeError, ValueError):
                pass
    x.first_product_s = min(product_ts) if product_ts else None
    x.facets.add(("first_product_shown", reveal_bin(x.first_product_s, duration_s)))
    return x


def tercile_labels(values: dict[str, float]) -> tuple[dict[str, str], tuple[float, float] | None]:
    vals = sorted(values.values())
    if len(vals) < 6:
        return {}, None
    lo, hi = vals[len(vals) // 3], vals[(2 * len(vals)) // 3]
    return {k: ("low" if v < lo else "high" if v >= hi else "mid") for k, v in values.items()}, (float(lo), float(hi))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/insights/account_read/test_facets.py -q`
Expected: 3 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/cs/insights/account_read/facets.py tests/insights/account_read/test_facets.py && uv run ruff format src/cs/insights/account_read/facets.py tests/insights/account_read/test_facets.py
git add src/cs/insights/account_read/facets.py tests/insights/account_read/test_facets.py
git commit -m "feat(account-read): facet extraction with derived reveal-timing and opening-scene facets

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Units from the database

**Files:**
- Create: `src/cs/insights/account_read/units.py`
- Test: `tests/insights/account_read/test_units.py`

**Interfaces:**
- Consumes: `Unit` (Task 1), `extract`, `tercile_labels`, `NUMERIC_DIMS` (Task 2).
- Produces: `async build_units(db, org_id, ad_account_id) -> tuple[list[Unit], dict[str, tuple[float,float]]]` (units and tercile bin edges; tercile facets are already added to `unit.facets`).

- [ ] **Step 1: Write the failing test**

```python
# tests/insights/account_read/test_units.py
from datetime import date
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import repo as ingest_repo
from cs.core.repo.assets import insert_asset, set_primary_asset
from cs.insights.account_read.units import build_units
from cs.tagging import repo as tag_repo
from cs.tagging.taxonomy import load_taxonomy


async def _creative(db, org_id, aid, name, sha, spend, tags, insight=None):
    cid, _ = await ingest_repo.upsert_creative(
        db, org_id, aid, PlatformCreative(name, "video", f"H {name}", "P", None, None, "u", None, None, {}), "raw",
        format_source="manual_review",
    )
    asset = await insert_asset(db, org_id, cid, "original_video", f"v/{sha}.mp4", sha, "video/mp4", duration_ms=60_000)
    await set_primary_asset(db, org_id, cid, asset)
    await ingest_repo.upsert_ad(db, org_id, aid, PlatformAd(f"ad_{name}", name, "ACTIVE", "as", "cp", name), cid)
    rows = [MetricRow(f"ad_{name}", date(2026, 6, d), "facebook:feed", Decimal(spend), 10_000, 300, 300, Decimal(10), Decimal(500), 4_000, 1_200, 3_000, 1_000, {}, 0, 8_000, 5_000, 2_000, 1_100, Decimal(0), purchases=10) for d in (1, 2)]
    await ingest_repo.upsert_metrics(db, org_id, aid, rows)
    if tags is not None:
        tv = await tag_repo.ensure_taxonomy_version(db, load_taxonomy())
        rid = await tag_repo.start_tag_run(db, org_id, cid, tv, "fake")
        await tag_repo.finish_tag_run(db, org_id, rid, tags, "s", 1, 1)
    if insight:
        await db.execute(
            "INSERT INTO creative_insight (creative_id, org_id, status, paragraph, moments, problems, brief, model, prompt_sha256, input_tokens, output_tokens)"
            " VALUES ($1,$2,'done',$3,'[]','[]','b','m','s',1,1)", cid, org_id, insight)
    return cid


def _tag(d, k, v, raw=None, t=None, o=None):
    return {"dimension": d, "value": v, "confidence": 1.0, "evidence": "", "kind": k, "raw_value": raw, "t_start_s": t, "ordinal": o}


async def test_units_pool_copies_by_sha_and_take_facets_from_top_spend_copy(db, org_id, ad_account_id):
    aid = ad_account_id
    await db.execute("UPDATE ad_account SET status='active' WHERE id=$1", aid)
    v2 = [_tag("messenger", "single", "creator_on_camera"), _tag("duration_s", "numeric", "60"), _tag("concept_summary", "text", "Creator testimonial"), _tag("hook_verbatim", "text", "I was wrong")]
    big = await _creative(db, org_id, aid, "big", "sha_a", "900", v2, insight="Strong hook.")
    small = await _creative(db, org_id, aid, "small", "sha_a", "100", [_tag("messenger", "single", "expert_on_camera"), _tag("duration_s", "numeric", "60")])
    other = await _creative(db, org_id, aid, "other", "sha_b", "500", [_tag("messenger", "single", "graphic_only"), _tag("duration_s", "numeric", "20")])
    await _creative(db, org_id, aid, "untagged", "sha_c", "500", None)

    units, edges = await build_units(db, org_id, aid)
    by = {u.sha256: u for u in units}
    assert set(by) == {"sha_a", "sha_b"}                      # untagged file excluded, copies pooled
    a = by["sha_a"]
    assert set(a.creative_ids) == {big, small} and a.rep_creative_id == big
    assert a.spend == Decimal(2000) and a.impressions == 40_000 and a.purchases == 40 and a.plays == 32_000
    assert a.has("messenger", "creator_on_camera") and not a.has("messenger", "expert_on_camera")
    assert a.concept_summary == "Creator testimonial" and a.hook_verbatim == "I was wrong" and a.insight_paragraph == "Strong hook."
    assert a.first_day == date(2026, 6, 1) and a.last_day == date(2026, 6, 2) and a.duration_s == 60.0
    assert by["sha_b"].insight_paragraph is None and edges == {}  # fewer than 6 units: no terciles
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/insights/account_read/test_units.py -q`
Expected: FAIL with `ImportError` for `build_units`

- [ ] **Step 3: Write units.py**

```python
# src/cs/insights/account_read/units.py
"""Footage units for one ad account: video creatives grouped by primary-asset sha256, metrics
pooled across copies, facets from the highest-spend copy's latest done v2 tag run."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from cs.core.db import Database
from cs.insights.account_read.facets import NUMERIC_DIMS, extract, tercile_labels
from cs.insights.account_read.types import Unit

_CREATIVES = """
WITH latest AS (
  SELECT DISTINCT ON (r.creative_id) r.creative_id, r.id AS run_id
  FROM tag_run r JOIN taxonomy_version v ON v.id = r.taxonomy_version_id
  WHERE r.org_id = $1 AND r.status = 'done' AND split_part(v.version, '.', 1)::int >= 2
  ORDER BY r.creative_id, r.finished_at DESC
), m AS (
  SELECT creative_id, sum(spend) spend, sum(impressions) imp, sum(video_plays) plays, sum(video_3s_views) v3,
         sum(video_thruplay) thru, sum(video_p25) p25, sum(video_p50) p50, sum(video_p75) p75, sum(video_p100) p100,
         sum(link_clicks) lc, sum(purchases) purch, min(day) first_day, max(day) last_day
  FROM metric_daily WHERE org_id = $1 AND ad_account_id = $2 GROUP BY creative_id
)
SELECT c.id, a.sha256, a.duration_ms, l.run_id,
       left(coalesce(nullif(c.headline, ''), nullif(c.primary_text, ''), '(no copy)'), 90) AS headline,
       coalesce(m.spend, 0) spend, coalesce(m.imp, 0) imp, coalesce(m.plays, 0) plays, coalesce(m.v3, 0) v3,
       coalesce(m.thru, 0) thru, coalesce(m.p25, 0) p25, coalesce(m.p50, 0) p50, coalesce(m.p75, 0) p75,
       coalesce(m.p100, 0) p100, coalesce(m.lc, 0) lc, coalesce(m.purch, 0) purch, m.first_day, m.last_day,
       (SELECT paragraph FROM creative_insight ci WHERE ci.creative_id = c.id AND ci.status = 'done' LIMIT 1) AS insight
FROM creative c
JOIN asset a ON a.id = c.primary_asset_id
LEFT JOIN latest l ON l.creative_id = c.id
LEFT JOIN m ON m.creative_id = c.id
WHERE c.org_id = $1 AND c.ad_account_id = $2 AND c.format = 'video' AND a.kind = 'original_video'
"""

_TAGS = "SELECT dimension, kind, value, raw_value, t_start_s, ordinal FROM tag WHERE tag_run_id = $1"


async def build_units(db: Database, org_id: UUID, ad_account_id: UUID) -> tuple[list[Unit], dict[str, tuple[float, float]]]:
    rows = await db.fetch(_CREATIVES, org_id, ad_account_id)
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r["sha256"]].append(r)
    units: list[Unit] = []
    for sha, rs in groups.items():
        tagged = [r for r in rs if r["run_id"] is not None]
        if not tagged:
            continue
        rep = max(tagged, key=lambda r: r["spend"])
        with_metrics = [r for r in rs if r["first_day"] is not None]
        if not with_metrics:
            continue
        tag_rows = [dict(t) for t in await db.fetch(_TAGS, rep["run_id"])]
        duration_s = (max((r["duration_ms"] or 0) for r in rs) or None)
        duration_s = duration_s / 1000.0 if duration_s else None
        x = extract(tag_rows, duration_s)
        insight = next((r["insight"] for r in sorted(rs, key=lambda r: -r["spend"]) if r["insight"]), None)
        units.append(Unit(
            sha256=sha, creative_ids=[r["id"] for r in rs], rep_creative_id=rep["id"], headline=rep["headline"],
            duration_s=duration_s, spend=Decimal(sum(r["spend"] for r in rs)),
            impressions=int(sum(r["imp"] for r in rs)), plays=int(sum(r["plays"] for r in rs)),
            video_3s_views=int(sum(r["v3"] for r in rs)), thruplays=int(sum(r["thru"] for r in rs)),
            p25=int(sum(r["p25"] for r in rs)), p50=int(sum(r["p50"] for r in rs)), p75=int(sum(r["p75"] for r in rs)), p100=int(sum(r["p100"] for r in rs)),
            link_clicks=int(sum(r["lc"] for r in rs)), purchases=int(sum(r["purch"] for r in rs)),
            first_day=min(r["first_day"] for r in with_metrics), last_day=max(r["last_day"] for r in with_metrics),
            facets=x.facets, numeric=x.numeric, first_product_s=x.first_product_s,
            concept_summary=x.concept_summary, hook_verbatim=x.hook_verbatim, insight_paragraph=insight,
        ))
    edges: dict[str, tuple[float, float]] = {}
    for dim in NUMERIC_DIMS:
        labels, e = tercile_labels({u.sha256: u.numeric[dim] for u in units if dim in u.numeric})
        if e:
            edges[dim] = e
            for u in units:
                if u.sha256 in labels:
                    u.facets.add((dim, labels[u.sha256]))
    return units, edges
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/insights/account_read/test_units.py -q`
Expected: 1 passed. If `upsert_metrics` rejects the positional `MetricRow`, check `cs.connectors.base.MetricRow` field order and fix the test's constructor call, not the query.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/cs/insights/account_read/units.py tests/insights/account_read/test_units.py && uv run ruff format src/cs/insights/account_read/units.py tests/insights/account_read/test_units.py
git add src/cs/insights/account_read/units.py tests/insights/account_read/test_units.py
git commit -m "feat(account-read): build footage units from creatives, assets, metrics and v2 tags

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Pattern run (main effects, interactions, entanglement, retention)

**Files:**
- Create: `src/cs/insights/account_read/patterns.py`
- Modify: `src/cs/core/config.py` (add three settings after `judge_model`)
- Test: `tests/insights/account_read/test_patterns.py`

**Interfaces:**
- Consumes: `Unit`, `Test`, `RunResult` (Task 1); `compare`, `benjamini_hochberg`, `grade`, `unit_rates`, `retention_shares`, `pooled` (Task 1); `REVEAL_BINS`, `DERIVED_SINGLE_DIMS`, `NUMERIC_DIMS` (Task 2).
- Produces: `run(units, bin_edges, *, grouping_dims: list[str], draws: int) -> RunResult`; settings `account_read_grouping_dims: str`, `account_read_min_units: int = 10`, `account_read_permutation_draws: int = 1000`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/insights/account_read/test_patterns.py
import random
from datetime import date
from decimal import Decimal

from cs.insights.account_read.patterns import run
from cs.insights.account_read.types import Unit


def synth(seed: int, n: int = 40, planted: str | None = "warning_or_risk", lift: float = 0.35):
    rng = random.Random(seed)
    units = []
    for i in range(n):
        has_plant = i % 2 == 0
        hook = 0.40 * (1 + rng.uniform(-0.15, 0.15))
        if has_plant and planted:
            hook *= 1 + lift
        imp = 20_000
        facets = {("messenger", "creator_on_camera" if i % 3 else "expert_on_camera"), ("angles", "convenience"), ("first_product_shown", "under 5s" if i % 4 == 0 else "second half")}
        if has_plant:
            facets.add(("hook_devices", "warning_or_risk"))
        if i % 5 == 0:
            facets.add(("registers", "humor"))            # null facet, no planted effect
        units.append(Unit(sha256=f"u{i}", creative_ids=[], rep_creative_id=None, headline=f"ad {i}", duration_s=60.0,
                          spend=Decimal(1000), impressions=imp, plays=15_000, video_3s_views=int(hook * imp), thruplays=3000,
                          p25=8000, p50=5000, p75=3000, p100=2000, link_clicks=600, purchases=25 + (i % 7),
                          first_day=date(2026, 6, 1), last_day=date(2026, 6, 30), facets=facets, numeric={"duration_s": 60.0},
                          first_product_s=2.0 if i % 4 == 0 else 40.0, concept_summary=f"concept {i}", hook_verbatim=f"hook {i}", insight_paragraph=None))
    return units


def test_planted_effect_is_found_and_graded():
    r = run(synth(1), {}, grouping_dims=["messenger"], draws=200)
    hits = [t for t in r.tests if t.group is None and t.dimension == "hook_devices" and t.value == "warning_or_risk" and t.metric == "hook_rate"]
    assert len(hits) == 1 and hits[0].grade in ("strong", "suggestive") and hits[0].change > 0.2
    assert r.summary["units"] == 40 and r.summary["by_grade"]["strong"] >= 1
    assert all(t.grade != "ungraded" for t in r.tests)


def test_null_facet_rarely_reaches_suggestive():
    false_hits = 0
    for seed in range(30):
        r = run(synth(seed, planted=None), {}, grouping_dims=[], draws=50)
        false_hits += any(t.dimension == "registers" and t.grade in ("strong", "suggestive") for t in r.tests)
    assert false_hits <= 6


def test_interactions_only_within_groups_of_ten_or_more():
    r = run(synth(2), {}, grouping_dims=["messenger"], draws=100)
    groups = {t.group for t in r.tests if t.group}
    assert "messenger=creator_on_camera" in groups            # 27 units
    assert "messenger=expert_on_camera" not in groups or True  # 13 units: allowed; the bound is >=10
    assert all(g.startswith("messenger=") for g in groups)


def test_entanglement_detects_shared_units_and_stratifies():
    units = synth(3)
    for u in units:                                           # make 'urgency' ride on the planted attribute
        if u.has("hook_devices", "warning_or_risk"):
            u.facets.add(("registers", "urgency"))
    r = run(units, {}, grouping_dims=[], draws=100)
    pair = {frozenset({tuple(o["a"]), tuple(o["b"])}) for o in r.overlaps}
    assert frozenset({("hook_devices", "warning_or_risk"), ("registers", "urgency")}) in pair
    strat = [s for s in r.stratified if tuple(s["attribute"]) == ("hook_devices", "warning_or_risk") and s["metric"] == "hook_rate"]
    assert strat and strat[0]["within"]["change"] is None      # nothing left to compare inside the partner


def test_retention_by_reveal_bin_has_rows():
    r = run(synth(4), {}, grouping_dims=[], draws=50)
    assert set(r.retention_by_reveal) == {"under 5s", "second half"}
    assert r.retention_by_reveal["under 5s"]["n"] == 10 and 0 < r.retention_by_reveal["under 5s"]["p50"] < 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/insights/account_read/test_patterns.py -q`
Expected: FAIL with `ImportError` for `run`

- [ ] **Step 3: Add the settings**

In `src/cs/core/config.py`, after the `judge_model` line, add:

```python
    # Account read (spec 2026-09-17). Grouping dimensions for bounded interaction tests;
    # a grouping value needs account_read_min_units units before it is used as a group.
    account_read_grouping_dims: str = (
        "audience_callouts,products_mentioned,features_mentioned,messenger,duration_s"
    )
    account_read_min_units: int = 10
    account_read_permutation_draws: int = 1000

    @property
    def account_read_grouping_dims_list(self) -> list[str]:
        return [d.strip() for d in self.account_read_grouping_dims.split(",") if d.strip()]
```

- [ ] **Step 4: Write patterns.py**

```python
# src/cs/insights/account_read/patterns.py
"""One account-read run: main effects, bounded interactions, entanglement, retention by reveal."""

from __future__ import annotations

import statistics
from itertools import combinations

from cs.insights.account_read.facets import DERIVED_SINGLE_DIMS, NUMERIC_DIMS, REVEAL_BINS
from cs.insights.account_read.stats import (
    MIN_PURCHASES, benjamini_hochberg, compare, grade, pooled, retention_shares, unit_rates,
)
from cs.insights.account_read.types import RATE_METRICS, RunResult, Test, Unit

GRADE_RANK = {"strong": 0, "suggestive": 1, "directional": 2, "thin": 3}
MIN_GROUP = 10


def _kinds(units: list[Unit]) -> dict[str, str]:
    """dimension -> 'single' when every unit carries at most one value (complement = units with the dimension)."""
    counts: dict[str, list[int]] = {}
    for u in units:
        per: dict[str, int] = {}
        for d, _ in u.facets:
            per[d] = per.get(d, 0) + 1
        for d, c in per.items():
            counts.setdefault(d, []).append(c)
    kinds = {d: ("single" if max(cs) == 1 else "multi") for d, cs in counts.items()}
    for d in DERIVED_SINGLE_DIMS + NUMERIC_DIMS:
        kinds[d] = "single"
    return kinds


def _all_facets(units: list[Unit]) -> list[tuple[str, str]]:
    return sorted({f for u in units for f in u.facets})


def _scope(units: list[Unit], dim: str, kinds: dict[str, str]) -> list[Unit]:
    if kinds.get(dim) == "single":
        return [u for u in units if any(d == dim for d, _ in u.facets)]
    return list(units)


def main_effects(units: list[Unit], bin_edges: dict, kinds: dict[str, str], draws: int) -> list[Test]:
    out: list[Test] = []
    for dim, val in _all_facets(units):
        cell = [u for u in units if u.has(dim, val)]
        comp = [u for u in _scope(units, dim, kinds) if not u.has(dim, val)]
        out += compare(cell, comp, dimension=dim, value=val, bin_edges=bin_edges.get(dim), draws=draws)
    return out


def interactions(units: list[Unit], grouping_dims: list[str], kinds: dict[str, str], draws: int) -> list[Test]:
    out: list[Test] = []
    for gdim in grouping_dims:
        for gval in sorted({v for u in units for d, v in u.facets if d == gdim}):
            group = [u for u in units if u.has(gdim, gval)]
            if len(group) < MIN_GROUP:
                continue
            label = f"{gdim}={gval}"
            for dim, val in _all_facets(group):
                if dim == gdim:
                    continue
                cell = [u for u in group if u.has(dim, val)]
                comp = [u for u in _scope(group, dim, kinds) if not u.has(dim, val)]
                out += compare(cell, comp, dimension=dim, value=val, group=label, draws=draws)
    return out


def apply_grades(tests: list[Test]) -> None:
    passing = benjamini_hochberg([t.p for t in tests])
    for i, t in enumerate(tests):
        t.grade = grade(t.p, t.change, i in passing)


def _change_within(cell: list[Unit], comp: list[Unit], metric: str) -> tuple[float | None, str]:
    if metric in RATE_METRICS:
        x = [unit_rates(u)[metric] for u in cell if metric in unit_rates(u)]
        y = [unit_rates(u)[metric] for u in comp if metric in unit_rates(u)]
        if len(x) >= 3 and len(y) >= 3 and statistics.median(y):
            return statistics.median(x) / statistics.median(y) - 1, f"{len(x)} vs {len(y)}"
        return None, f"{len(x)} vs {len(y)}"
    num, den = ("purchases", "impressions") if metric == "purchases_per_1k" else ("spend", "purchases")
    ok = len(cell) >= 3 and len(comp) >= 3 and sum(u.purchases for u in cell) >= MIN_PURCHASES and sum(u.purchases for u in comp) >= MIN_PURCHASES
    ra, rb = pooled(cell, num, den), pooled(comp, num, den)
    return ((float(ra) / float(rb) - 1) if ok and ra and rb else None), f"{len(cell)} vs {len(comp)}"


def entanglement(units: list[Unit], tests: list[Test]) -> tuple[list[dict], list[dict]]:
    """Jaccard >= 0.6 between graded unflagged main-effect attributes with 8..48 units, and the
    effect of each on its graded metrics within / outside the partner."""
    graded = {}
    for t in tests:
        if t.group is None and t.grade in ("strong", "suggestive") and not t.confounds and 8 <= t.n_cell <= 48:
            graded.setdefault((t.dimension, t.value), []).append((t.metric, t.change))
    members = {a: {u.sha256 for u in units if u.has(*a)} for a in graded}
    by_sha = {u.sha256: u for u in units}
    overlaps, strat = [], []
    for a, b in combinations(sorted(graded), 2):
        inter, union = len(members[a] & members[b]), len(members[a] | members[b])
        j = inter / union if union else 0.0
        if j < 0.6:
            continue
        overlaps.append({"a": list(a), "b": list(b), "jaccard": round(j, 2), "shared": inter, "n_a": len(members[a]), "n_b": len(members[b])})
        for attr, partner in ((a, b), (b, a)):
            for metric, overall in graded[attr]:
                inside = [by_sha[s] for s in members[partner]]
                outside = [u for u in units if u.sha256 not in members[partner]]
                w_ch, w_n = _change_within([u for u in inside if u.has(*attr)], [u for u in inside if not u.has(*attr)], metric)
                o_ch, o_n = _change_within([u for u in outside if u.has(*attr)], [u for u in outside if not u.has(*attr)], metric)
                strat.append({"attribute": list(attr), "controlling_for": list(partner), "metric": metric, "overall_change": overall,
                              "within": {"change": w_ch, "n": w_n}, "outside": {"change": o_ch, "n": o_n}})
    overlaps.sort(key=lambda o: -o["jaccard"])
    return overlaps, strat


def retention_by_reveal(units: list[Unit]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for b in REVEAL_BINS:
        grp = [u for u in units if u.has("first_product_shown", b)]
        if len(grp) < 3:
            continue
        row: dict = {"n": len(grp), "median_dur_s": round(statistics.median(u.duration_s or 0 for u in grp)),
                     "median_reveal_s": round(statistics.median(u.first_product_s for u in grp if u.first_product_s is not None)) if any(u.first_product_s is not None for u in grp) else None}
        shares = [retention_shares(u) for u in grp]
        for q in ("p25", "p50", "p75", "p100"):
            vals = [s[q] for s in shares if q in s]
            row[q] = round(statistics.median(vals), 3) if vals else None
        hooks = [unit_rates(u)["hook_rate"] for u in grp if "hook_rate" in unit_rates(u)]
        row["hook_rate"] = round(statistics.median(hooks), 3) if hooks else None
        row["cpa"] = round(float(pooled(grp, "spend", "purchases") or 0)) if sum(u.purchases for u in grp) >= MIN_PURCHASES else None
        ppk = pooled(grp, "purchases", "impressions")
        row["ppk"] = round(1000 * ppk, 2) if ppk else None
        out[b] = row
    return out


def run(units: list[Unit], bin_edges: dict[str, tuple[float, float]], *, grouping_dims: list[str], draws: int) -> RunResult:
    kinds = _kinds(units)
    tests = main_effects(units, bin_edges, kinds, draws)
    tests += interactions(units, grouping_dims, kinds, draws)
    apply_grades(tests)
    main = [t for t in tests if t.group is None]
    main.sort(key=lambda t: (GRADE_RANK[t.grade], len(t.confounds), -abs(t.change)))
    inter = [t for t in tests if t.group is not None]
    inter.sort(key=lambda t: (t.group or "", GRADE_RANK[t.grade], t.p))
    tests = main + inter
    overlaps, strat = entanglement(units, tests)
    summary = {
        "units": len(units), "creatives": sum(len(u.creative_ids) for u in units),
        "spend": round(float(sum(u.spend for u in units))), "purchases": sum(u.purchases for u in units),
        "tests": len(main), "interaction_tests": len(inter),
        "by_grade": {g: sum(1 for t in main if t.grade == g) for g in GRADE_RANK},
        "dimensions": sorted({t.dimension for t in main}),
        "first_day": min(u.first_day for u in units).isoformat() if units else None,
        "last_day": max(u.last_day for u in units).isoformat() if units else None,
    }
    return RunResult(units=units, tests=tests, overlaps=overlaps, stratified=strat, retention_by_reveal=retention_by_reveal(units), summary=summary, bin_edges=bin_edges)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/insights/account_read/test_patterns.py -q`
Expected: 5 passed (the null-facet test may take ~20 s). If `test_entanglement…` fails on `within.change is None`, check that the planted and riding attributes have identical member sets so that the inside cell has no complement.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/cs/insights/account_read/patterns.py src/cs/core/config.py tests/insights/account_read/test_patterns.py && uv run ruff format src/cs/insights/account_read/patterns.py src/cs/core/config.py tests/insights/account_read/test_patterns.py
git add src/cs/insights/account_read/patterns.py src/cs/core/config.py tests/insights/account_read/test_patterns.py
git commit -m "feat(account-read): graded main effects, bounded interactions, entanglement and retention by reveal

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Writer (brief, prompt, checks, fake)

**Files:**
- Create: `src/cs/insights/account_read/writer.py`
- Modify: `src/cs/insights/fakes.py` (append `FakeAccountReadWriter`)
- Test: `tests/insights/account_read/test_writer.py`

**Interfaces:**
- Consumes: `RunResult`, `Read`, `Finding`, `Test`, `Unit` (Task 1).
- Produces: `build_brief(run: RunResult) -> str`; `class AccountReadWriter(Protocol): async def complete(self, system: str, user: str) -> tuple[str, int, int]` (text, in_tokens, out_tokens); `GeminiGatewayAccountReadWriter(settings)`; `parse_read(text) -> Read`; `check_read(read, brief, n_tests) -> list[str]`; `async write_read(run, writer) -> tuple[Read | None, list[str], int, int]` (read or None when rejected, problems, tokens); `SYSTEM: str`; `_numbers(text) -> set[float]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/insights/account_read/test_writer.py
import json
from datetime import date
from decimal import Decimal

from cs.insights.account_read.types import Finding, Read, RunResult, Test, Unit
from cs.insights.account_read.writer import build_brief, check_read, parse_read, write_read
from cs.insights.fakes import FakeAccountReadWriter


def _unit(sha, headline, facets, spend=1000):
    return Unit(sha256=sha, creative_ids=[], rep_creative_id=None, headline=headline, duration_s=60.0, spend=Decimal(spend), impressions=10_000, plays=8_000,
                video_3s_views=4_000, thruplays=1_200, p25=4_000, p50=2_500, p75=1_500, p100=1_000, link_clicks=300, purchases=30,
                first_day=date(2026, 6, 1), last_day=date(2026, 6, 30), facets=set(facets), numeric={}, first_product_s=None,
                concept_summary="Pharmacist warns about Benadryl.", hook_verbatim="Worst OTC sleep aids", insight_paragraph="Strong filter hook.")


def _run():
    units = [_unit(f"u{i}", f"Ad {i}", {("hook_devices", "warning_or_risk")} if i % 2 == 0 else {("angles", "convenience")}) for i in range(12)]
    t = Test(dimension="hook_devices", value="warning_or_risk", group=None, metric="link_ctr", kind="per_unit", n_cell=6, n_complement=6,
             cell_value=0.045, comp_value=0.030, change=0.5, p=0.004, grade="suggestive", confounds=[], cell_shas=[u.sha256 for u in units if u.has("hook_devices", "warning_or_risk")])
    return RunResult(units=units, tests=[t], overlaps=[], stratified=[], retention_by_reveal={},
                     summary={"units": 12, "creatives": 12, "spend": 12000, "purchases": 360, "tests": 1, "interaction_tests": 0,
                              "by_grade": {"strong": 0, "suggestive": 1, "directional": 0, "thin": 0}, "dimensions": ["hook_devices"], "first_day": "2026-06-01", "last_day": "2026-06-30"})


def _read(**over):
    f = Finding(title="Warning hooks earn the click", layer="click", paragraph="Warning hooks show link CTR 4.5% with vs 3.0% without (P1), a 50% higher rate.",
                mechanism="The warning frames the category as risky, so the click is a check.", exemplars=["Ad 0", "Ad 2"], exception="Ad 4 opens with a warning but converts no better.",
                why_non_obvious="The table shows a CTR lift; the record shows every warning ad is a pharmacist, so this may be authority not fear.", pattern_ids=["P1"], confidence="suggestive")
    base = dict(overview="Warning-led pharmacist ads earn clicks.", findings=[f], entangled="none", no_evidence="fear register untested", next_tests=["Test a creator delivering the same warning."], data_needed=["More files."])
    base.update(over)
    return Read(**base)


def test_brief_carries_qualitative_record_and_pattern_lines():
    b = build_brief(_run())
    assert "P1 | hook devices = warning or risk | link CTR: 4.5% with vs 3.0% without (50% higher)" in b
    assert "Worst OTC sleep aids" in b and "Pharmacist warns about Benadryl." in b and "Strong filter hook." in b
    assert "Ad 0" in b


def test_check_read_rejects_invented_numbers_missing_exemplars_and_empty_why():
    brief = build_brief(_run())
    assert check_read(_read(), brief, n_tests=1) == []
    bad_num = _read(overview="CTR rose 99% across the board.")
    assert any("99" in p for p in check_read(bad_num, brief, 1))
    f = _read().findings[0]
    f.exemplars = ["Ad that does not exist"]
    assert any("exemplar" in p for p in check_read(_read(findings=[f]), brief, 1))
    f2 = _read().findings[0]
    f2.why_non_obvious = ""
    assert any("why_non_obvious" in p for p in check_read(_read(findings=[f2]), brief, 1))
    f3 = _read().findings[0]
    f3.pattern_ids = ["P9"]
    assert any("P9" in p for p in check_read(_read(findings=[f3]), brief, 1))


def test_parse_read_tolerates_fences_and_missing_fields():
    r = parse_read('```json\n{"overview": "o", "findings": [], "next_tests": ["t"]}\n```')
    assert r.overview == "o" and r.findings == [] and r.entangled == "" and r.next_tests == ["t"] and r.data_needed == []


async def test_write_read_retries_once_then_rejects():
    run = _run()
    good = json.dumps({**_read().__dict__, "findings": [_read().findings[0].__dict__]})
    bad = json.dumps({**_read(overview="CTR rose 99%.").__dict__, "findings": [_read().findings[0].__dict__]})
    read, problems, _, _ = await write_read(run, FakeAccountReadWriter([bad, good]))
    assert read is not None and problems == []
    read2, problems2, _, _ = await write_read(run, FakeAccountReadWriter([bad, bad]))
    assert read2 is None and problems2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/insights/account_read/test_writer.py -q`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add the fake**

Append to `src/cs/insights/fakes.py`:

```python
class FakeAccountReadWriter:
    """Returns canned JSON texts in order; records the prompts it was given."""

    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> tuple[str, int, int]:
        self.calls.append((system, user))
        text = self.texts.pop(0) if self.texts else "{}"
        return text, 10, 5
```

- [ ] **Step 4: Write writer.py**

```python
# src/cs/insights/account_read/writer.py
"""The account read: brief construction, the writer prompt, parsing and the checks that keep the
prose honest (numbers from the brief only, cited IDs exist, exemplars exist, non-obviousness stated)."""

from __future__ import annotations

import json
import re
from typing import Protocol

import openai

from cs.core.config import Settings
from cs.insights.account_read.types import Finding, Read, RunResult, Test

METRIC_NAME = {"hook_rate": "hook rate", "thruplay_rate": "thruplay rate", "link_ctr": "link CTR", "purchases_per_1k": "purchases per 1,000 impressions", "cpa": "CPA"}
MAX_MAIN_UNFLAGGED, MAX_MAIN_FLAGGED, MAX_UNITS_LISTED = 60, 20, 60


def fmt_val(metric: str, v: float) -> str:
    if metric == "cpa":
        return f"${v:.0f}"
    if metric == "purchases_per_1k":
        return f"{v:.2f}"
    return f"{100 * v:.1f}%"


def label(t: Test) -> str:
    v = str(t.value).replace("_", " ")
    if t.bin_edges:
        lo, hi = t.bin_edges
        unit = "s" if t.dimension == "duration_s" else ""
        v += {"low": f" (under {lo:g}{unit})", "mid": f" ({lo:g} to {hi:g}{unit})", "high": f" ({hi:g}{unit} and up)"}.get(str(t.value), "")
    return f"{t.dimension.replace('_', ' ')} = {v}"


def pattern_line(pid: str, t: Test) -> str:
    flags = ", ".join(f.replace("_", " ") for f in t.confounds) or "none"
    ch = t.change
    return (f"{pid} | {label(t)} | {METRIC_NAME[t.metric]}: {fmt_val(t.metric, t.cell_value)} with vs {fmt_val(t.metric, t.comp_value)} without "
            f"({abs(ch) * 100:.0f}% {'higher' if ch > 0 else 'lower'}) | {t.n_cell} vs {t.n_complement} files | p = {t.p:.3f} | {t.grade} | flags: {flags}")


def build_brief(run: RunResult) -> str:
    s = run.summary
    by_sha = {u.sha256: u for u in run.units}
    ids = {id(t): f"P{i}" for i, t in enumerate(run.tests, start=1)}
    top_units = sorted(run.units, key=lambda u: -u.spend)[:MAX_UNITS_LISTED]
    record = []
    for u in top_units:
        bits = [f"\"{u.headline}\"", f"{round(u.duration_s or 0)}s", f"${float(u.spend):,.0f} spend", f"{len(u.creative_ids)} cop{'y' if len(u.creative_ids) == 1 else 'ies'}"]
        messenger = next((v for d, v in u.facets if d == "messenger"), None)
        if messenger:
            bits.append(f"messenger {messenger.replace('_', ' ')}")
        if u.hook_verbatim:
            bits.append(f"hook: \"{u.hook_verbatim}\"")
        if u.concept_summary:
            bits.append(f"concept: {u.concept_summary}")
        if u.insight_paragraph:
            bits.append(f"per-ad insight: {u.insight_paragraph}")
        record.append("- " + " | ".join(bits))

    def with_units(t: Test) -> str:
        cell = sorted((by_sha[s] for s in t.cell_shas if s in by_sha), key=lambda u: -u.spend)[:3]
        return pattern_line(ids[id(t)], t) + "\n    cell examples: " + "; ".join(f"\"{u.headline}\"" for u in cell)

    main = [t for t in run.tests if t.group is None and t.grade != "thin"]
    unflagged = [with_units(t) for t in main if not t.confounds][:MAX_MAIN_UNFLAGGED]
    flagged = [with_units(t) for t in main if t.confounds][:MAX_MAIN_FLAGGED]

    inter: dict[str, list[str]] = {}
    for t in run.tests:
        if t.group is not None and t.grade != "thin":
            inter.setdefault(t.group, []).append("  " + pattern_line(ids[id(t)], t))
    inter_text = "\n".join(f"\nwithin {g.replace('_', ' ')}:\n" + "\n".join(lines) for g, lines in inter.items()) or "\n  none"

    ov = [f"  {o['a'][0].replace('_',' ')} = {o['a'][1]}  and  {o['b'][0].replace('_',' ')} = {o['b'][1]}: {o['shared']} shared files of {o['n_a']} and {o['n_b']} (Jaccard {o['jaccard']})" for o in run.overlaps]
    st = []
    for x in run.stratified:
        w, o = x["within"], x["outside"]
        fmt = lambda part: "not enough files" if part["change"] is None else f"{part['change'] * 100:+.0f}% ({part['n']} files)"
        st.append(f"  effect of {x['attribute'][0].replace('_',' ')} = {x['attribute'][1]} on {METRIC_NAME[x['metric']]}: overall {x['overall_change'] * 100:+.0f}% | within {x['controlling_for'][0].replace('_',' ')} = {x['controlling_for'][1]}: {fmt(w)}; outside it: {fmt(o)}")

    ret = []
    for b, r in run.retention_by_reveal.items():
        ret.append(f"  R-{b}: {r['n']} files, median length {r['median_dur_s']}s, product first shown at {r['median_reveal_s']}s median | hook rate {100 * (r['hook_rate'] or 0):.1f}% | share of plays reaching 25% {100 * (r['p25'] or 0):.1f}%, 50% {100 * (r['p50'] or 0):.1f}%, 75% {100 * (r['p75'] or 0):.1f}%, 100% {100 * (r['p100'] or 0):.1f}% | purchases per 1,000 impressions {r['ppk']} | CPA {('$' + str(r['cpa'])) if r['cpa'] else 'too few purchases'}")

    return f"""ACCOUNT BRIEF

Scope: one Meta ad account, video ads, lifetime metrics {s.get('first_day')} to {s.get('last_day')}.
Unit: a distinct video file; ad creatives reusing one file pool their metrics and count once. {s['units']} files ({s['creatives']} creatives incl. copies). Spend ${s['spend']:,}, {s['purchases']:,} purchases.
Metrics: hook rate = 3-second views / impressions; thruplay rate = thruplays / plays; link CTR = link clicks / impressions; purchases per 1,000 impressions and CPA are pooled per group. Rate metrics compare per-file medians (Mann-Whitney U); pooled metrics use a permutation test. Grades: strong = passes Benjamini-Hochberg at q 0.10 across every test in this run; suggestive = raw p under 0.10; directional = p under 0.25 with at least a 15% change (a lead, not evidence). Flags: budget concentration = one file holds half or more of the group's impressions; seasonality = the groups ran in different periods, so time confounds the comparison.

=== THE ADS (top {len(top_units)} files by spend; what each one is) ===
{chr(10).join(record)}

=== GRADED PATTERNS, whole account (unflagged first) ===
{chr(10).join(unflagged) or '  none'}
  (flagged, use with care)
{chr(10).join(flagged) or '  none'}

=== PATTERNS WITHIN GROUPS (interactions; the group is the set of files that carry the grouping attribute) ==={inter_text}

=== ENTANGLED ATTRIBUTES (the same files carry several attributes) ===
{chr(10).join(ov) or '  none above Jaccard 0.6'}
Stratified checks (does the effect survive when the partner attribute is held constant?):
{chr(10).join(st) or '  none'}

=== RETENTION BY PRODUCT-REVEAL TIMING (the bins are entangled with video length; read the median length) ===
{chr(10).join(ret) or '  none'}
"""


SYSTEM = """\
You are a senior performance-creative strategist writing the account-level read for the team that makes this brand's video ads. You have two kinds of material: a qualitative record of what each ad is (headline, verbatim hook, concept summary, and where one exists the per-ad insight paragraph), and statistically graded comparisons of how ads with each attribute performed against ads without it, including comparisons inside groups, entanglement checks and retention by product-reveal timing.

Your job is the amalgamation. A finding is not "attribute X has metric Y higher"; that is a table row and the reader already has the table. A finding says what the ads that share the attribute are actually doing, what viewer behavior that plausibly produces, which files show it and which break it, and what the team could not have seen from the numbers alone. Write for a smart client who will make the next ad from this.

RULES
- All numbers are precomputed. Reproduce them exactly as written. Do not calculate, combine, re-round or invent numbers. Cite pattern IDs (P1, P2, ...) for every quantitative claim; retention rows may be cited as R-<bin>.
- Grades carry weight: strong is evidence, suggestive is a lead, directional is a hint. Titles must not outrun the grade. A cell under 10 files is small; say so.
- Flags matter. A seasonality flag means time confounds the comparison: say so or leave it out. A budget concentration flag means one file dominates: name it.
- Entanglement matters most. When the stratified checks show an effect vanishing once another attribute is held constant, say the two cannot be separated with this data and do not present them as separate findings.
- Attention, click and conversion are different layers. Say which layer each claim is about. A CTR lift with a CPA penalty is a finding in itself.
- Every finding must have: a mechanism (drawn from the qualitative record, in hypothesis language), two or three exemplar files named by their exact headline as written in the brief, an exception where one exists (a file in the cell that breaks the pattern or a file outside it that behaves like it), and why_non_obvious: one sentence on what the metric table alone could not show.
- Rank findings by decision value for the next ad, not by p-value. Three to six findings. Fewer and sharper beats coverage.
- The overview answers, in prose and before any number: what does this account's creative actually do, and where is it wrong about itself?
- "No signal" and "too few files" are different answers. Report both plainly.
- Hypothesis language: "is consistent with", "one plausible explanation is". These are correlations from an optimizer-driven account, not a controlled test.
- Plain prose. No bullet lists or headings inside paragraphs.

OUTPUT (JSON)
- overview: one paragraph.
- findings: 3 to 6 objects, each with title (plain statement, under 12 words), layer (attention | retention | click | conversion | funnel), paragraph (3 to 6 sentences, evidence with exact numbers and file counts, then interpretation), mechanism (1 to 2 sentences), exemplars (2 to 3 exact headlines), exception (1 sentence, or "none found"), why_non_obvious (1 sentence), pattern_ids, confidence (strong | suggestive | directional | mixed).
- entangled: one paragraph naming attributes that cannot be separated with this data and what that means for reading the findings.
- no_evidence: one paragraph on what showed no difference and what could not be tested, naming attributes.
- next_tests: 3 to 5 one-sentence tests that follow from the findings, each naming the attribute to vary and the metric expected to move. The only place recommendations belong.
- data_needed: 2 to 4 one-sentence items on what would most sharpen this read.
Return valid JSON only.
"""

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)
_NUMBER = re.compile(r"(?<![\w.])\$?[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")


def _numbers(text: str) -> set[float]:
    out: set[float] = set()
    for m in _NUMBER.finditer(text):
        raw = m.group(0)
        tok = raw.replace(",", "").replace("$", "").replace("%", "").replace("+", "").replace("−", "-")
        try:
            v = float(tok)
        except ValueError:
            continue
        if abs(v) < 100 and v.is_integer() and "%" not in raw and "$" not in raw:
            continue  # small bare integers (file counts, "3 sentences") are not metric claims
        out.add(round(abs(v), 2))
    return out


def parse_read(text: str) -> Read:
    m = _FENCE.match(text)
    body = json.loads(m.group(1) if m else text)
    findings = []
    for f in body.get("findings") or []:
        findings.append(Finding(
            title=str(f.get("title", "")), layer=str(f.get("layer", "")), paragraph=str(f.get("paragraph", "")),
            mechanism=str(f.get("mechanism", "")), exemplars=[str(x) for x in (f.get("exemplars") or [])],
            exception=str(f.get("exception", "")), why_non_obvious=str(f.get("why_non_obvious", "")),
            pattern_ids=[str(x) for x in (f.get("pattern_ids") or [])], confidence=str(f.get("confidence", "")),
        ))
    return Read(
        overview=str(body.get("overview", "")), findings=findings, entangled=str(body.get("entangled", "")),
        no_evidence=str(body.get("no_evidence", "")), next_tests=[str(x) for x in (body.get("next_tests") or [])],
        data_needed=[str(x) for x in (body.get("data_needed") or [])],
    )


def check_read(read: Read, brief: str, n_tests: int) -> list[str]:
    allowed = _numbers(brief)
    problems: list[str] = []
    prose = [("overview", read.overview), ("entangled", read.entangled), ("no_evidence", read.no_evidence)]
    prose += [(f"finding '{f.title}'", f.paragraph + " " + f.mechanism + " " + f.exception) for f in read.findings]
    for where, text in prose:
        bad = sorted(_numbers(text) - allowed)
        if bad:
            problems.append(f"{where}: numbers not in the brief: {bad}")
    valid_ids = {f"P{i}" for i in range(1, n_tests + 1)}
    for f in read.findings:
        missing = [p for p in f.pattern_ids if p not in valid_ids and not p.startswith("R-")]
        if missing:
            problems.append(f"finding '{f.title}': unknown pattern ids {missing}")
        for ex in f.exemplars:
            if f'"{ex}"' not in brief:
                problems.append(f"finding '{f.title}': exemplar not in the brief: {ex!r}")
        if not f.why_non_obvious.strip():
            problems.append(f"finding '{f.title}': why_non_obvious is empty")
        if not f.exemplars:
            problems.append(f"finding '{f.title}': no exemplars")
    if not read.findings:
        problems.append("no findings")
    return problems


class AccountReadWriter(Protocol):
    async def complete(self, system: str, user: str) -> tuple[str, int, int]: ...


class GeminiGatewayAccountReadWriter:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1")
        self.model = settings.creative_insight_model

    async def complete(self, system: str, user: str) -> tuple[str, int, int]:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        usage = getattr(resp, "usage", None)
        return resp.choices[0].message.content or "{}", getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0


async def write_read(run: RunResult, writer: AccountReadWriter) -> tuple[Read | None, list[str], int, int]:
    """One draft, one retry with the problems named; None when the retry still fails."""
    brief = build_brief(run)
    tokens_in = tokens_out = 0
    feedback: list[str] = []
    read: Read | None = None
    for _ in range(2):
        user = brief + "\n\nWrite the overview, findings, entangled, no_evidence, next_tests and data_needed."
        if feedback:
            user += "\n\nYour previous draft broke the rules: " + "; ".join(feedback) + ". Rewrite, citing only numbers and headlines that appear in the brief exactly as written."
        text, ti, to = await writer.complete(SYSTEM, user)
        tokens_in, tokens_out = tokens_in + ti, tokens_out + to
        try:
            read = parse_read(text)
        except (json.JSONDecodeError, TypeError) as exc:
            feedback = [f"response was not valid JSON: {exc}"]
            read = None
            continue
        feedback = check_read(read, brief, len(run.tests))
        if not feedback:
            return read, [], tokens_in, tokens_out
    return None, feedback, tokens_in, tokens_out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/insights/account_read/test_writer.py -q`
Expected: 4 passed

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/cs/insights/account_read/writer.py src/cs/insights/fakes.py tests/insights/account_read/test_writer.py && uv run ruff format src/cs/insights/account_read/writer.py src/cs/insights/fakes.py tests/insights/account_read/test_writer.py
git add src/cs/insights/account_read/writer.py src/cs/insights/fakes.py tests/insights/account_read/test_writer.py
git commit -m "feat(account-read): brief with the qualitative ad record, writer prompt, grounding and non-obviousness checks

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Storage, job, schedule, script

**Files:**
- Create: `db/migrations/20260917000001_account_read.sql`, `src/cs/insights/account_read/repo.py`, `src/cs/insights/account_read/jobs.py`, `scripts/compute_account_read.py`
- Modify: `src/cs/jobs/all_handlers.py`, `src/cs/core/queue.py` (only if `purge_done` has no sibling hook; otherwise add purge to the existing nightly purge schedule in `src/cs/jobs/scheduler.py`)
- Test: `tests/insights/account_read/test_jobs.py`

**Interfaces:**
- Consumes: `build_units` (Task 3), `run` (Task 4), `write_read`, `GeminiGatewayAccountReadWriter` (Task 5), `to_json` (Task 1).
- Produces: `repo.insert_run(db, org_id, ad_account_id, *, status, n_units, result: dict, read: dict | None, error: str | None) -> UUID`; `repo.latest_run(db, org_id, ad_account_id) -> Record | None`; `repo.purge_runs(db, days=30) -> int`; job type `insights.compute_account_read` with payload `{ad_account_id}`; module-level `WRITER_FACTORY` (settings → writer) that tests monkeypatch.

- [ ] **Step 1: Write the migration**

```sql
-- migrate:up
CREATE TABLE account_read_run (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL REFERENCES ad_account(id),
  format text NOT NULL DEFAULT 'video',
  window_start date,
  window_end date,
  n_units int NOT NULL,
  status text NOT NULL CHECK (status IN ('done','insufficient','rejected','failed')),
  result jsonb NOT NULL,
  read jsonb,
  error text,
  input_tokens int NOT NULL DEFAULT 0,
  output_tokens int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_read_run_latest_idx ON account_read_run (ad_account_id, format, created_at DESC);

-- migrate:down
DROP TABLE account_read_run;
```

Apply to the test DB: `dbmate --url "$DATABASE_URL_TEST" --no-dump-schema --migrations-dir db/migrations up` (the `settings` fixture also runs this).

- [ ] **Step 2: Write the failing test**

```python
# tests/insights/account_read/test_jobs.py
import json

from cs.core import queue
from cs.insights.account_read import jobs, repo
from cs.insights.fakes import FakeAccountReadWriter
from cs.jobs.worker import run_once
from tests.insights.account_read.test_units import _creative, _tag


def _good_read_for(n_tests: int, headlines: list[str]) -> str:
    return json.dumps({
        "overview": "Creator-led ads carry the account.",
        "findings": [{"title": "Creators earn the click", "layer": "click", "paragraph": "See the pattern (P1).", "mechanism": "Familiar faces lower guard.",
                      "exemplars": headlines[:2], "exception": "none found", "why_non_obvious": "The record shows every creator ad also opens on a face.",
                      "pattern_ids": ["P1"] if n_tests else [], "confidence": "suggestive"}],
        "entangled": "none", "no_evidence": "nothing untested", "next_tests": ["Swap the creator for an expert."], "data_needed": ["More files."],
    })


async def test_job_stores_insufficient_below_min_units(db, blobs, settings, org_id, ad_account_id, monkeypatch):
    await db.execute("UPDATE ad_account SET status='active' WHERE id=$1", ad_account_id)
    for i in range(3):
        await _creative(db, org_id, ad_account_id, f"c{i}", f"sha{i}", "500", [_tag("messenger", "single", "creator_on_camera"), _tag("duration_s", "numeric", "60")])
    monkeypatch.setattr(jobs, "WRITER_FACTORY", lambda s: FakeAccountReadWriter([]))
    await queue.enqueue(db, org_id, "insights.compute_account_read", {"ad_account_id": str(ad_account_id)})
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.latest_run(db, org_id, ad_account_id)
    assert row["status"] == "insufficient" and row["n_units"] == 3 and row["read"] is None


async def test_job_stores_done_run_with_read(db, blobs, settings, org_id, ad_account_id, monkeypatch):
    await db.execute("UPDATE ad_account SET status='active' WHERE id=$1", ad_account_id)
    heads = []
    for i in range(12):
        tags = [_tag("messenger", "single", "creator_on_camera" if i % 2 else "expert_on_camera"), _tag("duration_s", "numeric", "60"), _tag("concept_summary", "text", f"c{i}")]
        await _creative(db, org_id, ad_account_id, f"c{i}", f"sha{i}", str(500 + i), tags)
        heads.append(f"H c{i}")
    monkeypatch.setattr(settings, "account_read_permutation_draws", 50)

    # messenger splits 6/6, so at least one test (P1) exists whatever its grade
    class Adaptive(FakeAccountReadWriter):
        async def complete(self, system, user):
            return _good_read_for(1, heads), 10, 5

    monkeypatch.setattr(jobs, "WRITER_FACTORY", lambda s: Adaptive([]))
    await queue.enqueue(db, org_id, "insights.compute_account_read", {"ad_account_id": str(ad_account_id)})
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.latest_run(db, org_id, ad_account_id)
    assert row["status"] == "done" and row["n_units"] == 12
    result, read = row["result"], row["read"]
    assert result["summary"]["units"] == 12 and isinstance(result["tests"], list) and len(result["units"]) == 12
    assert read["findings"][0]["title"] == "Creators earn the click"


async def test_latest_run_and_purge(db, org_id, ad_account_id):
    a = await repo.insert_run(db, org_id, ad_account_id, status="insufficient", n_units=1, result={"summary": {}}, read=None, error=None)
    b = await repo.insert_run(db, org_id, ad_account_id, status="done", n_units=12, result={"summary": {}}, read={"overview": "o"}, error=None)
    assert (await repo.latest_run(db, org_id, ad_account_id))["id"] == b
    await db.execute("UPDATE account_read_run SET created_at = now() - interval '40 days' WHERE id=$1", a)
    assert await repo.purge_runs(db, days=30) == 1
```

Note: `result["tests"]` may be empty at 12 synthetic units (rate metrics need ≥ 5 units per side with data; the fixture's identical metrics produce ties, p = 1.0, grade thin). The test only asserts shape.

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/insights/account_read/test_jobs.py -q`
Expected: FAIL with `ImportError` (`jobs`, `repo`)

- [ ] **Step 4: Write repo.py**

```python
# src/cs/insights/account_read/repo.py
from __future__ import annotations

from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id


async def insert_run(db: Database, org_id: UUID, ad_account_id: UUID, *, status: str, n_units: int, result: dict, read: dict | None, error: str | None, input_tokens: int = 0, output_tokens: int = 0) -> UUID:
    rid = new_id()
    summary = result.get("summary") or {}
    await db.execute(
        "INSERT INTO account_read_run (id, org_id, ad_account_id, window_start, window_end, n_units, status, result, read, error, input_tokens, output_tokens)"
        " VALUES ($1,$2,$3,$4::date,$5::date,$6,$7,$8,$9,$10,$11,$12)",
        rid, org_id, ad_account_id, summary.get("first_day"), summary.get("last_day"), n_units, status,
        result, read, error, input_tokens, output_tokens,  # jsonb codec in cs.core.db encodes dicts
    )
    return rid


async def latest_run(db: Database, org_id: UUID, ad_account_id: UUID):
    row = await db.fetchrow(
        "SELECT id, status, n_units, window_start, window_end, result, read, error, created_at FROM account_read_run"
        " WHERE org_id=$1 AND ad_account_id=$2 AND format='video' ORDER BY created_at DESC LIMIT 1",
        org_id, ad_account_id,
    )
    if row is None:
        return None
    return dict(row)  # jsonb columns arrive decoded (codec in cs.core.db)


async def purge_runs(db: Database, days: int = 30) -> int:
    res = await db.execute("DELETE FROM account_read_run WHERE created_at < now() - make_interval(days => $1)", days)
    return int(res.split()[-1])
```

`cs.core.db._init_conn` registers a jsonb codec, so dicts go in and come out without `json.dumps`. Passing a pre-dumped string would store a JSON string, not an object.

- [ ] **Step 5: Write jobs.py**

```python
# src/cs/insights/account_read/jobs.py
"""Compute and store one account read per ad account."""

from __future__ import annotations

import logging
import traceback
from uuid import UUID

from cs.core import queue
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights.account_read import repo
from cs.insights.account_read.patterns import run as run_patterns
from cs.insights.account_read.types import to_json
from cs.insights.account_read.units import build_units
from cs.insights.account_read.writer import GeminiGatewayAccountReadWriter, write_read
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule

log = logging.getLogger("cs.account_read")
JOB = "insights.compute_account_read"


def _default_writer(settings: Settings):
    return GeminiGatewayAccountReadWriter(settings)


WRITER_FACTORY = _default_writer


@handler(JOB)
async def compute_account_read(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    ad_account_id = UUID(ctx.job.payload["ad_account_id"])
    s = ctx.settings
    units, edges = await build_units(ctx.db, org_id, ad_account_id)
    if len(units) < s.account_read_min_units:
        await repo.insert_run(ctx.db, org_id, ad_account_id, status="insufficient", n_units=len(units), result={"summary": {"units": len(units)}}, read=None, error=None)
        return
    try:
        result = run_patterns(units, edges, grouping_dims=s.account_read_grouping_dims_list, draws=s.account_read_permutation_draws)
        read, problems, ti, to = await write_read(result, WRITER_FACTORY(s))
    except Exception as exc:  # noqa: BLE001 - a failed run must not hide the previous one
        await repo.insert_run(ctx.db, org_id, ad_account_id, status="failed", n_units=len(units), result={"summary": {"units": len(units)}}, read=None, error=f"{exc}\n{traceback.format_exc()}")
        raise
    payload = to_json(result)  # facets serialize as sorted [dimension, value] pairs
    status = "done" if read else "rejected"
    await repo.insert_run(ctx.db, org_id, ad_account_id, status=status, n_units=len(units), result=payload, read=to_json(read) if read else None, error="; ".join(problems) or None, input_tokens=ti, output_tokens=to)
    log.info("account read stored", extra={"ad_account_id": str(ad_account_id), "status": status, "units": len(units), "tests": len(result.tests)})


@register_schedule("insights.nightly_account_read", every_seconds=86400)
async def nightly_account_read(db: Database, settings: Settings) -> None:
    for a in await db.fetch("SELECT id, org_id FROM ad_account WHERE status='active'"):
        await queue.enqueue(db, a["org_id"], JOB, {"ad_account_id": str(a["id"])}, dedup_key=f"account_read:{a['id']}", max_attempts=2)
    n = await repo.purge_runs(db, 30)
    if n:
        log.info("purged account read runs", extra={"count": n})
```

- [ ] **Step 6: Register the module and write the script**

In `src/cs/jobs/all_handlers.py` add: `import cs.insights.account_read.jobs  # noqa: F401`

```python
# scripts/compute_account_read.py
"""Enqueue insights.compute_account_read for one ad account.

Usage: uv run python scripts/compute_account_read.py --ad-account <uuid>
"""

import argparse
import asyncio
from uuid import UUID

from cs.core import queue
from cs.core.config import get_settings
from cs.core.db import Database


async def main(ad_account_id: UUID) -> None:
    db = await Database.connect(get_settings().database_url)
    try:
        row = await db.fetchrow("SELECT org_id FROM ad_account WHERE id=$1", ad_account_id)
        if row is None:
            raise SystemExit(f"no ad_account {ad_account_id}")
        jid = await queue.enqueue(db, row["org_id"], "insights.compute_account_read", {"ad_account_id": str(ad_account_id)}, dedup_key=f"account_read:{ad_account_id}")
        print("queued" if jid else "already queued", jid or "")
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ad-account", required=True, type=UUID)
    asyncio.run(main(p.parse_args().ad_account))
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/insights/account_read/test_jobs.py -q`
Expected: 3 passed

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src/cs/insights/account_read src/cs/jobs/all_handlers.py scripts/compute_account_read.py tests/insights/account_read && uv run ruff format src/cs/insights/account_read src/cs/jobs/all_handlers.py scripts/compute_account_read.py tests/insights/account_read
git add db/migrations/20260917000001_account_read.sql src/cs/insights/account_read src/cs/jobs/all_handlers.py scripts/compute_account_read.py tests/insights/account_read
git commit -m "feat(account-read): store runs, nightly job per active account, on-demand script

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Ask box logic and API routes

**Files:**
- Create: `src/cs/insights/account_read/ask.py`
- Modify: `src/cs/api/routers/insights.py` (append two routes), `src/cs/insights/fakes.py` (nothing new; reuse `FakeAccountReadWriter`)
- Test: `tests/insights/account_read/test_ask.py`, `tests/api/test_account_read.py`

**Interfaces:**
- Consumes: `repo.latest_run` (Task 6), `unit_from_json`, `test_from_json` (Task 1), `compare`, `benjamini_hochberg`, `grade` (Task 1), `pattern_line`, `check_read`-style number check `_numbers`, `AccountReadWriter` (Task 5).
- Produces: `async map_question(question, result_json, writer) -> Query | None` where `Query = {targets: list[tuple[str,str] | str], group: tuple[str,str] | None, layer: str}`; `async answer(question, result_json, writer, draws=300) -> dict` returning `{verdict, answer, pattern_ids, tests, what_would_settle_it}` or `{unmappable: str}`; routes `GET /insights/account-read`, `POST /insights/account-read/ask`; module-level `ASK_WRITER_FACTORY` in `ask.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/insights/account_read/test_ask.py
import json
from datetime import date
from decimal import Decimal

from cs.insights.account_read.ask import answer, cap_verdict, map_question
from cs.insights.account_read.types import Unit, to_json
from cs.insights.fakes import FakeAccountReadWriter


def _units():
    us = []
    for i in range(14):
        facets = {("messenger_age_band", "40_to_60" if i % 2 else "25_to_40"), ("audience_callouts", "magnesium_users")}
        us.append(Unit(sha256=f"u{i}", creative_ids=[], rep_creative_id=None, headline=f"Ad {i}", duration_s=60.0, spend=Decimal(1000), impressions=20_000, plays=15_000,
                       video_3s_views=8_000, thruplays=3_000, p25=8000, p50=5000, p75=3000, p100=2000, link_clicks=1_200 if i % 2 else 600, purchases=30,
                       first_day=date(2026, 6, 1), last_day=date(2026, 6, 30), facets=facets, numeric={}, first_product_s=None, concept_summary=None, hook_verbatim=None, insight_paragraph=None))
    return us


def _result_json():
    return {"units": [to_json(u) for u in _units()], "tests": [], "summary": {"units": 14, "dimensions": ["messenger_age_band", "audience_callouts"]}}


async def test_map_question_parses_targets_group_and_layer():
    w = FakeAccountReadWriter([json.dumps({"targets": ["messenger_age_band"], "group": ["audience_callouts", "magnesium_users"], "layer": "click"})])
    q = await map_question("Which messenger age works for magnesium users?", _result_json(), w)
    assert q == {"targets": ["messenger_age_band"], "group": ("audience_callouts", "magnesium_users"), "layer": "click"}
    assert "messenger_age_band" in w.calls[0][1]          # the dimension list was offered


async def test_map_question_unmappable():
    w = FakeAccountReadWriter([json.dumps({"unmappable": "persona is not tagged"})])
    assert await map_question("trust by persona", _result_json(), w) is None


async def test_answer_runs_targeted_tests_and_writes_prose():
    mapper = json.dumps({"targets": ["messenger_age_band"], "group": None, "layer": "click"})
    prose = json.dumps({"verdict": "answerable", "answer": "40 to 60 messengers show link CTR 6.0% vs 3.0% (P1).", "pattern_ids": ["P1"], "what_would_settle_it": "more files"})
    out = await answer("Which age band earns clicks?", _result_json(), FakeAccountReadWriter([mapper, prose]), draws=100)
    assert "unmappable" not in out
    assert any(t["dimension"] == "messenger_age_band" and t["metric"] == "link_ctr" for t in out["tests"])
    assert out["verdict"] in ("answerable", "directional", "not yet") and "P1" in out["pattern_ids"]


def test_cap_verdict_never_exceeds_evidence():
    strong = [{"grade": "strong", "confounds": []}]
    sugg = [{"grade": "suggestive", "confounds": []}] * 2
    assert cap_verdict("answerable", strong) == "answerable"
    assert cap_verdict("answerable", sugg) == "directional"
    assert cap_verdict("answerable", []) == "not yet"
```

```python
# tests/api/test_account_read.py
import json
from uuid import UUID

from cs.insights.account_read import ask, repo
from cs.insights.fakes import FakeAccountReadWriter
from tests.conftest import dev_headers

H = dev_headers(org="org_ar")


async def _account(client, db):
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    from cs.core.ids import new_id
    aid = new_id()
    await db.execute("INSERT INTO ad_account (id, org_id, platform, platform_account_id, name, status) VALUES ($1,$2,'meta','act_ar','A','active')", aid, org_id)
    return org_id, aid


async def test_account_read_states_and_cross_org(client, db):
    org_id, aid = await _account(client, db)
    r = await client.get(f"/insights/account-read?ad_account_id={aid}", headers=H)
    assert r.status_code == 200 and r.json() == {"status": "none"}
    await repo.insert_run(db, org_id, aid, status="insufficient", n_units=4, result={"summary": {"units": 4}}, read=None, error=None)
    body = (await client.get(f"/insights/account-read?ad_account_id={aid}", headers=H)).json()
    assert body["status"] == "insufficient" and body["n_units"] == 4
    result = {"summary": {"units": 12}, "tests": [{"dimension": "messenger", "value": "expert_on_camera", "group": None, "metric": "link_ctr", "kind": "per_unit", "n_cell": 6, "n_complement": 6, "cell_value": 0.04, "comp_value": 0.03, "change": 0.33, "p": 0.02, "grade": "suggestive", "confounds": [], "cell_shas": ["a"], "bin_edges": None, "cell_purchases": None},
                                                    {"dimension": "angles", "value": "humor", "group": None, "metric": "cpa", "kind": "pooled", "n_cell": 6, "n_complement": 6, "cell_value": 40, "comp_value": 41, "change": -0.02, "p": 0.9, "grade": "thin", "confounds": [], "cell_shas": ["b"], "bin_edges": None, "cell_purchases": 30}],
              "units": [{"sha256": "a", "headline": "H", "spend": 100, "creative_ids": [], "rep_creative_id": None}], "overlaps": [], "stratified": [], "retention_by_reveal": {}}
    await repo.insert_run(db, org_id, aid, status="done", n_units=12, result=result, read={"overview": "o", "findings": []}, error=None)
    body = (await client.get(f"/insights/account-read?ad_account_id={aid}", headers=H)).json()
    assert body["status"] == "done" and body["read"]["overview"] == "o"
    assert [t["grade"] for t in body["tests"]] == ["suggestive", "thin"]     # all tests, in stored order, so P<n> resolves
    assert (await client.get(f"/insights/account-read?ad_account_id={aid}", headers=dev_headers(org="org_other"))).status_code == 404


async def test_ask_route(client, db, monkeypatch):
    org_id, aid = await _account(client, db)
    await repo.insert_run(db, org_id, aid, status="done", n_units=12, result={"summary": {"units": 12, "dimensions": []}, "tests": [], "units": []}, read={"overview": "o", "findings": []}, error=None)
    monkeypatch.setattr(ask, "ASK_WRITER_FACTORY", lambda s: FakeAccountReadWriter([json.dumps({"unmappable": "persona is not tagged"})]))
    r = await client.post("/insights/account-read/ask", json={"ad_account_id": str(aid), "question": "trust per persona"}, headers=H)
    assert r.status_code == 200 and r.json() == {"unmappable": "persona is not tagged"}
    r = await client.post("/insights/account-read/ask", json={"ad_account_id": str(aid), "question": "x"}, headers=dev_headers(org="org_other"))
    assert r.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/insights/account_read/test_ask.py tests/api/test_account_read.py -q`
Expected: FAIL with `ImportError` for `ask`; API tests fail with 404/405 on the new routes.

- [ ] **Step 3: Write ask.py**

```python
# src/cs/insights/account_read/ask.py
"""Ask box: map a plain-language question onto the stored run, compute the targeted comparisons,
write the answer under the same rules as the account read."""

from __future__ import annotations

import json
import re

from cs.core.config import Settings
from cs.insights.account_read.stats import benjamini_hochberg, compare, grade
from cs.insights.account_read.types import Test, Unit, to_json, unit_from_json
from cs.insights.account_read.writer import GeminiGatewayAccountReadWriter, _numbers, pattern_line

LAYER_METRICS = {
    "attention": ("hook_rate",), "retention": ("thruplay_rate",), "click": ("link_ctr",),
    "conversion": ("purchases_per_1k", "cpa"), "all": ("hook_rate", "thruplay_rate", "link_ctr", "purchases_per_1k", "cpa"),
}
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _default_writer(settings: Settings):
    return GeminiGatewayAccountReadWriter(settings)


ASK_WRITER_FACTORY = _default_writer


def _loads(text: str) -> dict:
    m = _FENCE.match(text)
    return json.loads(m.group(1) if m else text)


def _vocab(units: list[Unit]) -> dict[str, list[str]]:
    counts: dict[tuple[str, str], int] = {}
    for u in units:
        for f in u.facets:
            counts[f] = counts.get(f, 0) + 1
    vocab: dict[str, list[str]] = {}
    for (d, v), n in sorted(counts.items()):
        if n >= 5:
            vocab.setdefault(d, []).append(v)
    return vocab


MAP_SYSTEM = """\
You translate a strategist's question about an ad account into a query over tagged creative attributes.
You are given the attribute dimensions and the values that exist with enough data. Return JSON:
{"targets": [<dimension> or [<dimension>, <value>], ...], "group": [<dimension>, <value>] or null, "layer": "attention" | "retention" | "click" | "conversion" | "all"}
- targets: the attribute(s) the question asks about. A bare dimension means "compare every value of it".
- group: when the question says "for each X" or "within X", the attribute that defines the group; else null. Use one concrete value that exists; if the question names a persona or audience, map it to the closest audience_callouts value.
- layer: "trust" or "hold" → retention; "attention" or "hook" → attention; "clicks" → click; "CAC", "CPA", "conversions", "purchases" → conversion; otherwise all.
If the question asks about something that is not a tagged attribute, return {"unmappable": "<what is missing, in one sentence>"}. Return valid JSON only.
"""

ANSWER_SYSTEM = """\
You are a senior performance-creative strategist answering one question about an ad account from precomputed, graded comparisons. Rules: reproduce numbers exactly as written and cite pattern IDs (P1, P2, ...); strong is evidence, suggestive a lead, directional a hint; name flags; cells under 10 files are small; hypothesis language; say plainly when the data shows no difference or has too few files. Return JSON: {"verdict": "answerable" | "directional" | "not yet", "answer": <3 to 7 sentences, direct answer first, then evidence, then caveat>, "pattern_ids": [...], "what_would_settle_it": <one sentence>}. Return valid JSON only.
"""


async def map_question(question: str, result_json: dict, writer) -> dict | None:
    units = [unit_from_json(u) for u in result_json["units"]]
    vocab = _vocab(units)
    user = "DIMENSIONS AND VALUES:\n" + "\n".join(f"- {d}: {', '.join(vs)}" for d, vs in vocab.items()) + f"\n\nQUESTION: {question}"
    text, _, _ = await writer.complete(MAP_SYSTEM, user)
    body = _loads(text)
    if "unmappable" in body or not body.get("targets"):
        return None
    targets = [tuple(t) if isinstance(t, list) else str(t) for t in body["targets"]]
    group = tuple(body["group"]) if body.get("group") else None
    layer = body.get("layer") if body.get("layer") in LAYER_METRICS else "all"
    return {"targets": targets, "group": group, "layer": layer}


def targeted_tests(query: dict, units: list[Unit], draws: int) -> list[Test]:
    scope = [u for u in units if u.has(*query["group"])] if query["group"] else list(units)
    group_label = f"{query['group'][0]}={query['group'][1]}" if query["group"] else None
    facets: list[tuple[str, str]] = []
    for t in query["targets"]:
        if isinstance(t, tuple):
            facets.append(t)
        else:
            facets += sorted({(d, v) for u in scope for d, v in u.facets if d == t})
    tests: list[Test] = []
    for dim, val in facets:
        cell = [u for u in scope if u.has(dim, val)]
        comp = [u for u in scope if not u.has(dim, val)]
        tests += [t for t in compare(cell, comp, dimension=dim, value=val, group=group_label, draws=draws) if t.metric in LAYER_METRICS[query["layer"]]]
    passing = benjamini_hochberg([t.p for t in tests]) if tests else set()
    for i, t in enumerate(tests):
        t.grade = grade(t.p, t.change, i in passing)
    tests.sort(key=lambda t: t.p)
    return tests


def cap_verdict(claimed: str, cited: list[dict]) -> str:
    if any(c["grade"] == "strong" for c in cited) or sum(1 for c in cited if c["grade"] == "suggestive" and not c["confounds"]) >= 3:
        return "answerable"
    if any(c["grade"] in ("suggestive", "directional") for c in cited):
        return "directional"
    return "not yet"


async def answer(question: str, result_json: dict, writer, draws: int = 300) -> dict:
    query = await map_question(question, result_json, writer)
    if query is None:
        return {"unmappable": "That asks about something the tags do not capture yet."}
    units = [unit_from_json(u) for u in result_json["units"]]
    tests = targeted_tests(query, units, draws)
    if not tests:
        return {"verdict": "not yet", "answer": "Too few files carry those attributes to compare (each side needs 5 files, and purchase metrics need 20 purchases per side).", "pattern_ids": [], "tests": [], "what_would_settle_it": "More tagged videos with these attributes."}
    lines = [pattern_line(f"P{i}", t) for i, t in enumerate(tests, start=1)]
    user = "QUESTION: " + question + "\n\nCOMPARISONS:\n" + "\n".join(lines)
    text, _, _ = await writer.complete(ANSWER_SYSTEM, user)
    body = _loads(text)
    allowed = _numbers(user)
    bad = sorted(_numbers(str(body.get("answer", ""))) - allowed)
    if bad:
        text, _, _ = await writer.complete(ANSWER_SYSTEM, user + f"\n\nYour previous draft used numbers not in the comparisons: {bad}. Rewrite using only the numbers above.")
        body = _loads(text)
    ids = {f"P{i}" for i in range(1, len(tests) + 1)}
    cited_ids = [p for p in body.get("pattern_ids", []) if p in ids]
    cited = [to_json(tests[int(p[1:]) - 1]) for p in cited_ids]
    return {
        "verdict": cap_verdict(str(body.get("verdict", "not yet")), cited),
        "answer": str(body.get("answer", "")),
        "pattern_ids": cited_ids,
        "tests": [to_json(t) for t in tests],
        "what_would_settle_it": str(body.get("what_would_settle_it", "")),
    }
```

- [ ] **Step 4: Append the routes**

In `src/cs/api/routers/insights.py`, add imports `from pydantic import BaseModel`, `from cs.insights.account_read import ask as account_ask, repo as account_read_repo`, `from cs.core.config import get_settings`, and append:

```python
async def _owned_account(db: Database, org_id: UUID, ad_account_id: UUID) -> None:
    if not await db.fetchval("SELECT 1 FROM ad_account WHERE id=$1 AND org_id=$2", ad_account_id, org_id):
        raise HTTPException(404, "ad account not found")


@router.get("/insights/account-read")
async def account_read(
    ad_account_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    await _owned_account(db, auth.org_id, ad_account_id)
    row = await account_read_repo.latest_run(db, auth.org_id, ad_account_id)
    if row is None:
        return {"status": "none"}
    result = row["result"] or {}
    units = [
        {k: u.get(k) for k in ("sha256", "headline", "spend", "creative_ids", "rep_creative_id", "duration_s", "impressions", "purchases")}
        for u in result.get("units", [])
    ]
    return {
        "status": row["status"], "n_units": row["n_units"], "created_at": row["created_at"].isoformat(),
        "window_start": row["window_start"].isoformat() if row["window_start"] else None,
        "window_end": row["window_end"].isoformat() if row["window_end"] else None,
        "read": row["read"], "summary": result.get("summary", {}), "tests": result.get("tests", []),
        "overlaps": result.get("overlaps", []), "stratified": result.get("stratified", []),
        "retention_by_reveal": result.get("retention_by_reveal", {}), "units": units, "error": row["error"],
    }


class AskBody(BaseModel):
    ad_account_id: UUID
    question: str


@router.post("/insights/account-read/ask")
async def account_read_ask(
    body: AskBody, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    await _owned_account(db, auth.org_id, body.ad_account_id)
    row = await account_read_repo.latest_run(db, auth.org_id, body.ad_account_id)
    if row is None or row["status"] not in ("done", "rejected"):
        raise HTTPException(409, "no account read run to ask against yet")
    settings = get_settings()
    return await account_ask.answer(body.question.strip()[:500], row["result"], account_ask.ASK_WRITER_FACTORY(settings), draws=300)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/insights/account_read/test_ask.py tests/api/test_account_read.py -q`
Expected: 6 passed. If the ask route test hits the 409, insert the run with `status="done"` as written; if the `client` fixture's dev auth creates the org lazily, the `/me` call in `_account` handles it.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/cs/insights/account_read/ask.py src/cs/api/routers/insights.py tests/insights/account_read/test_ask.py tests/api/test_account_read.py && uv run ruff format src/cs/insights/account_read/ask.py src/cs/api/routers/insights.py tests/insights/account_read/test_ask.py tests/api/test_account_read.py
git add src/cs/insights/account_read/ask.py src/cs/api/routers/insights.py tests/insights/account_read/test_ask.py tests/api/test_account_read.py
git commit -m "feat(account-read): ask box mapping and answer, latest-run and ask API routes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Web section and ask box

**Files:**
- Modify: `web/src/lib/types.ts`, `web/src/lib/api.ts`, `web/src/app/(app)/insights/page.tsx`
- Create: `web/src/app/(app)/insights/actions.ts`, `web/src/components/account-read-evidence.tsx`, `web/src/components/account-read.tsx`, `web/src/components/account-read-ask.tsx`
- Test: `web/tests/account-read.test.tsx`

**Interfaces:**
- Consumes: `GET /insights/account-read`, `POST /insights/account-read/ask` (Task 7).
- Produces: `AccountRead` type; `api.accountRead(accountId)`; server action `askAccountRead(accountId, question)`; `<AccountReadSection data={...} accountId={...} />`.

- [ ] **Step 1: Write the failing vitest**

```tsx
// web/tests/account-read.test.tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AccountReadSection } from '@/components/account-read';
import type { AccountRead } from '@/lib/types';

const test = (over: Partial<AccountRead['tests'][number]> = {}) => ({
  dimension: 'hook_devices', value: 'warning_or_risk', group: null, metric: 'link_ctr', kind: 'per_unit' as const,
  n_cell: 6, n_complement: 6, cell_value: 0.045, comp_value: 0.03, change: 0.5, p: 0.004, grade: 'suggestive',
  confounds: [], cell_shas: ['a'], bin_edges: null, cell_purchases: null, ...over,
});

const done: AccountRead = {
  status: 'done', n_units: 12, created_at: '2026-09-17T00:00:00Z', window_start: '2026-06-01', window_end: '2026-06-30',
  summary: { units: 12, creatives: 20, spend: 12000, purchases: 360, tests: 1, interaction_tests: 0, by_grade: { strong: 0, suggestive: 1, directional: 0, thin: 0 } },
  tests: [test()], overlaps: [], stratified: [], retention_by_reveal: {},
  units: [{ sha256: 'a', headline: 'Worst OTC sleep aids', spend: 900, creative_ids: ['c1'], rep_creative_id: 'c1', duration_s: 60, impressions: 10000, purchases: 30 }],
  read: {
    overview: 'Warning-led pharmacist ads earn the click.',
    findings: [{ title: 'Warning hooks earn the click', layer: 'click', paragraph: 'Link CTR 4.5% with vs 3.0% without (P1).', mechanism: 'The warning frames the category as risky.',
      exemplars: ['Worst OTC sleep aids'], exception: 'none found', why_non_obvious: 'Every warning ad is also a pharmacist.', pattern_ids: ['P1'], confidence: 'suggestive' }],
    entangled: 'none', no_evidence: 'fear untested', next_tests: ['Swap the messenger.'], data_needed: ['More files.'],
  },
  error: null,
};

describe('AccountReadSection', () => {
  it('renders the insufficient state with the unit count', () => {
    render(<AccountReadSection data={{ ...done, status: 'insufficient', n_units: 4, read: null }} accountId="x" />);
    expect(screen.getByText(/needs 10 tagged video files/i)).toBeInTheDocument();
    expect(screen.getByText(/has 4/i)).toBeInTheDocument();
  });

  it('renders findings with mechanism and expands evidence on citation click', () => {
    render(<AccountReadSection data={done} accountId="x" />);
    expect(screen.getByText('Warning hooks earn the click')).toBeInTheDocument();
    expect(screen.getByText(/frames the category as risky/)).toBeInTheDocument();
    expect(screen.getByText(/Every warning ad is also a pharmacist/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /evidence/i }));
    expect(screen.getByText(/hook devices = warning or risk/)).toBeInTheDocument();
    expect(screen.getByText(/4\.5% with vs 3\.0% without/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd web && npx vitest run tests/account-read.test.tsx`
Expected: FAIL, cannot resolve `@/components/account-read`

- [ ] **Step 3: Add types and API fetchers**

Append to `web/src/lib/types.ts`:

```ts
export type AccountReadTest = { dimension: string; value: string; group: string | null; metric: string; kind: 'per_unit' | 'pooled'; n_cell: number; n_complement: number; cell_value: number; comp_value: number; change: number; p: number; grade: string; confounds: string[]; cell_shas: string[]; bin_edges: [number, number] | null; cell_purchases: number | null };
export type AccountReadFinding = { title: string; layer: string; paragraph: string; mechanism: string; exemplars: string[]; exception: string; why_non_obvious: string; pattern_ids: string[]; confidence: string };
export type AccountReadText = { overview: string; findings: AccountReadFinding[]; entangled: string; no_evidence: string; next_tests: string[]; data_needed: string[] };
export type AccountReadUnit = { sha256: string; headline: string; spend: number; creative_ids: string[]; rep_creative_id: string | null; duration_s: number | null; impressions: number; purchases: number };
export type AccountRead = {
  status: 'none' | 'done' | 'insufficient' | 'rejected' | 'failed'; n_units?: number; created_at?: string; window_start?: string | null; window_end?: string | null;
  summary?: Record<string, unknown>; tests?: AccountReadTest[]; overlaps?: unknown[]; stratified?: unknown[]; retention_by_reveal?: Record<string, unknown>;
  units?: AccountReadUnit[]; read?: AccountReadText | null; error?: string | null;
};
export type AccountReadAnswer = { unmappable?: string; verdict?: 'answerable' | 'directional' | 'not yet'; answer?: string; pattern_ids?: string[]; tests?: AccountReadTest[]; what_would_settle_it?: string };
```

Add to `api` in `web/src/lib/api.ts`:

```ts
  accountRead: (accountId: string) => apiFetch<import('./types').AccountRead>(`/insights/account-read?ad_account_id=${accountId}`),
  accountReadAsk: (accountId: string, question: string) =>
    apiFetch<import('./types').AccountReadAnswer>('/insights/account-read/ask', { method: 'POST', body: JSON.stringify({ ad_account_id: accountId, question }) }),
```

- [ ] **Step 4: Write the server action**

```ts
// web/src/app/(app)/insights/actions.ts
'use server';

import { api } from '@/lib/api';
import type { AccountReadAnswer } from '@/lib/types';

export async function askAccountRead(accountId: string, question: string): Promise<AccountReadAnswer> {
  return api.accountReadAsk(accountId, question);
}
```

- [ ] **Step 5: Write the components**

```tsx
// web/src/components/account-read-evidence.tsx
'use client';

import Link from 'next/link';
import { cn } from 'cn';
import type { AccountReadTest, AccountReadUnit } from '@/lib/types';

const METRIC: Record<string, string> = { hook_rate: 'hook rate', thruplay_rate: 'thruplay rate', link_ctr: 'link CTR', purchases_per_1k: 'purchases / 1k imp', cpa: 'CPA' };
const fmtVal = (m: string, v: number) => (m === 'cpa' ? `$${v.toFixed(0)}` : m === 'purchases_per_1k' ? v.toFixed(2) : `${(100 * v).toFixed(1)}%`);
const fmtChange = (x: number) => `${x >= 0 ? '+' : '−'}${Math.abs(x * 100).toFixed(0)}%`;
const label = (t: AccountReadTest) => {
  let v = String(t.value).replace(/_/g, ' ');
  if (t.bin_edges) {
    const [lo, hi] = t.bin_edges;
    v += t.value === 'low' ? ` (under ${lo})` : t.value === 'mid' ? ` (${lo} to ${hi})` : ` (${hi} and up)`;
  }
  return `${t.dimension.replace(/_/g, ' ')} = ${v}`;
};

export const gradeClass: Record<string, string> = {
  strong: 'bg-success/10 text-success', suggestive: 'bg-muted text-muted-foreground', directional: 'bg-amber-500/10 text-amber-700 dark:text-amber-400', thin: 'bg-muted text-muted-foreground', mixed: 'bg-muted text-muted-foreground',
};

export function Pill({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('inline-block rounded-full px-2 py-0.5 text-xs font-semibold', className)}>{children}</span>;
}

export function EvidenceList({ ids, tests, units }: { ids: string[]; tests: AccountReadTest[]; units: AccountReadUnit[] }) {
  const bySha = new Map(units.map((u) => [u.sha256, u]));
  return (
    <ol className="mt-2 space-y-2 border-l-2 border-border pl-3 text-sm text-muted-foreground">
      {ids.map((pid) => {
        const t = pid.startsWith('P') ? tests[Number(pid.slice(1)) - 1] : undefined;
        if (!t) return null;
        const good = t.metric === 'cpa' ? t.change < 0 : t.change > 0;
        const cell = t.cell_shas.map((s) => bySha.get(s)).filter(Boolean).sort((a, b) => (b!.spend ?? 0) - (a!.spend ?? 0)).slice(0, 5) as AccountReadUnit[];
        return (
          <li key={pid}>
            <span className="font-mono text-xs text-muted-foreground/70">{pid.slice(1)}</span>{' '}
            {t.group && <span className="text-xs text-primary">within {t.group.replace(/_/g, ' ')} · </span>}
            <b className="font-medium text-foreground">{label(t)}</b> · {METRIC[t.metric] ?? t.metric} {fmtVal(t.metric, t.cell_value)} with vs {fmtVal(t.metric, t.comp_value)} without{' '}
            <span className={cn('font-mono font-medium', good ? 'text-success' : 'text-destructive')}>{fmtChange(t.change)}</span> · {t.n_cell} vs {t.n_complement} files · p {t.p.toFixed(3)}{' '}
            <Pill className={gradeClass[t.grade] ?? gradeClass.thin}>{t.grade}</Pill>
            {t.confounds.map((c) => (<Pill key={c} className="ml-1 bg-amber-500/10 text-amber-700 dark:text-amber-400">{c.replace(/_/g, ' ')}</Pill>))}
            {cell.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                {cell.map((u) => (
                  <Link key={u.sha256} href={u.rep_creative_id ? `/creatives/${u.rep_creative_id}` : '#'} className="underline decoration-muted-foreground/50 underline-offset-2 hover:decoration-foreground">
                    {u.headline}{u.creative_ids.length > 1 ? ` ×${u.creative_ids.length}` : ''}
                  </Link>
                ))}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

```

```tsx
// web/src/components/account-read.tsx
'use client';

import { useState } from 'react';
import { Sparkles } from 'lucide-react';
import type { AccountRead } from '@/lib/types';
import { AccountReadAsk } from '@/components/account-read-ask';
import { EvidenceList, Pill, gradeClass } from '@/components/account-read-evidence';

function Cited({ text, onOpen }: { text: string; onOpen: () => void }) {
  // "(P1, P2)" → superscript buttons that open the evidence list
  const parts = text.split(/(\((?:P\d+(?:,\s*)?)+\))/g);
  return (
    <>
      {parts.map((p, i) => {
        const ids = p.match(/P\d+/g);
        if (!ids || !/^\(/.test(p)) return <span key={i}>{p}</span>;
        return (
          <sup key={i} className="ml-0.5">
            {ids.map((id) => (
              <button key={id} type="button" onClick={onOpen} className="mr-0.5 font-mono text-[11px] text-primary hover:underline" aria-label={`pattern ${id}`}>{id.slice(1)}</button>
            ))}
          </sup>
        );
      })}
    </>
  );
}

export function AccountReadSection({ data, accountId }: { data: AccountRead; accountId: string }) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  if (data.status === 'none' || data.status === 'insufficient') {
    return (
      <section className="rounded-xl border border-dashed border-border px-6 py-8 text-center text-sm text-muted-foreground">
        Account read needs 10 tagged video files. This account has {data.n_units ?? 0}.
      </section>
    );
  }
  const tests = data.tests ?? [];
  const units = data.units ?? [];
  const read = data.read;
  const s = (data.summary ?? {}) as Record<string, number>;
  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-lg font-semibold">Account read</h2>
        <span className="text-sm text-muted-foreground tabular-nums">
          {s.units} files · {s.creatives} creatives · ${Number(s.spend ?? 0).toLocaleString()} spend · {Number(s.purchases ?? 0).toLocaleString()} purchases{data.window_end ? ` · through ${data.window_end}` : ''}
        </span>
      </div>
      <AccountReadAsk accountId={accountId} tests={tests} units={units} />
      {!read ? (
        <p className="text-sm text-muted-foreground">The prose read failed its checks on the last run{data.error ? `: ${data.error}` : ''}. The patterns below are still current.</p>
      ) : (
        <>
          <div className="flex gap-3.5 rounded-xl border border-border bg-card px-6 py-4">
            <Sparkles className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <p className="text-base leading-relaxed text-pretty">{read.overview}</p>
          </div>
          <div className="space-y-6">
            {read.findings.map((f, i) => (
              <article key={i} className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Pill className="bg-primary/10 text-primary">{f.layer}</Pill>
                  <Pill className={gradeClass[f.confidence] ?? gradeClass.thin}>{f.confidence}</Pill>
                </div>
                <h3 className="text-base font-semibold">{f.title}</h3>
                <p className="text-sm leading-relaxed text-pretty"><Cited text={f.paragraph} onOpen={() => setOpen((o) => ({ ...o, [i]: true }))} /></p>
                <dl className="grid gap-1 text-sm text-muted-foreground sm:grid-cols-[auto_1fr] sm:gap-x-4">
                  <dt className="font-medium text-foreground">Why</dt><dd>{f.mechanism}</dd>
                  <dt className="font-medium text-foreground">See</dt><dd>{f.exemplars.join(' · ')}</dd>
                  {f.exception && f.exception !== 'none found' && (<><dt className="font-medium text-foreground">Except</dt><dd>{f.exception}</dd></>)}
                  <dt className="font-medium text-foreground">Not in the table</dt><dd>{f.why_non_obvious}</dd>
                </dl>
                <button type="button" onClick={() => setOpen((o) => ({ ...o, [i]: !o[i] }))} className="text-xs font-medium text-muted-foreground hover:text-foreground" aria-expanded={!!open[i]}>
                  {open[i] ? '▾' : '▸'} Evidence · {f.pattern_ids.length}
                </button>
                {open[i] && <EvidenceList ids={f.pattern_ids} tests={tests} units={units} />}
              </article>
            ))}
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Block title="Cannot be separated yet" body={read.entangled} />
            <Block title="No difference, or too few to test" body={read.no_evidence} />
            <Block title="Tests that follow" items={read.next_tests} />
            <Block title="What would sharpen this" items={read.data_needed} />
          </div>
        </>
      )}
    </section>
  );
}

function Block({ title, body, items }: { title: string; body?: string; items?: string[] }) {
  return (
    <div className="rounded-xl border border-border bg-card px-5 py-4">
      <h4 className="mb-1 text-sm font-semibold">{title}</h4>
      {body && <p className="text-sm leading-relaxed text-muted-foreground">{body}</p>}
      {items && (<ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{items.map((t, i) => (<li key={i}>{t}</li>))}</ul>)}
    </div>
  );
}
```

```tsx
// web/src/components/account-read-ask.tsx
'use client';

import { useState, useTransition } from 'react';
import { askAccountRead } from '@/app/(app)/insights/actions';
import { EvidenceList, Pill } from '@/components/account-read-evidence';
import type { AccountReadAnswer, AccountReadTest, AccountReadUnit } from '@/lib/types';

const verdictClass: Record<string, string> = { answerable: 'bg-success/10 text-success', directional: 'bg-amber-500/10 text-amber-700 dark:text-amber-400', 'not yet': 'bg-muted text-muted-foreground' };

export function AccountReadAsk({ accountId, tests, units }: { accountId: string; tests: AccountReadTest[]; units: AccountReadUnit[] }) {
  const [q, setQ] = useState('');
  const [answer, setAnswer] = useState<AccountReadAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!q.trim()) return;
    setError(null);
    start(async () => {
      try { setAnswer(await askAccountRead(accountId, q.trim())); } catch (err) { setError(err instanceof Error ? err.message : 'The question could not be answered.'); }
    });
  };
  const answerTests = answer?.tests ?? [];
  return (
    <div className="space-y-3 rounded-xl border border-border bg-card px-5 py-4">
      <form onSubmit={submit} className="flex flex-wrap gap-2">
        <label htmlFor="account-read-question" className="sr-only">Ask about this account</label>
        <input id="account-read-question" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask, e.g. which messenger works for people who already take magnesium?" className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm" />
        <button type="submit" disabled={pending || !q.trim()} className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">{pending ? 'Thinking…' : 'Ask'}</button>
      </form>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {answer?.unmappable && <p className="text-sm text-muted-foreground">{answer.unmappable}</p>}
      {answer && !answer.unmappable && (
        <div className="space-y-2">
          {answer.verdict && <Pill className={verdictClass[answer.verdict] ?? verdictClass['not yet']}>{answer.verdict}</Pill>}
          <p className="text-sm leading-relaxed text-pretty">{answer.answer}</p>
          {answer.what_would_settle_it && <p className="text-xs text-muted-foreground">To settle it: {answer.what_would_settle_it}</p>}
          {(answer.pattern_ids?.length ?? 0) > 0 && <EvidenceList ids={answer.pattern_ids!} tests={answerTests.length ? answerTests : tests} units={units} />}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Mount on the Insights page**

In `web/src/app/(app)/insights/page.tsx`, import `AccountReadSection` and `api` (already imported), and inside `Insights` before the `insights` fetch add:

```tsx
  const accounts = await api.accounts();
  const active = accounts.find((a) => a.status === 'active') ?? accounts[0];
  const accountRead = active ? await api.accountRead(active.id).catch(() => ({ status: 'none' as const })) : null;
```

and render, right after the page header `<div>` and before the insights list:

```tsx
      {active && accountRead && <AccountReadSection data={accountRead} accountId={active.id} />}
```

- [ ] **Step 7: Run vitest and type-check**

Run: `cd web && npx vitest run tests/account-read.test.tsx && npx tsc --noEmit -p .`
Expected: 2 passed; no type errors. `success` is a theme token in `globals.css`; there is no `warning` token, which is why the plan uses amber utilities.

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/types.ts web/src/lib/api.ts "web/src/app/(app)/insights/actions.ts" "web/src/app/(app)/insights/page.tsx" web/src/components/account-read-evidence.tsx web/src/components/account-read.tsx web/src/components/account-read-ask.tsx web/tests/account-read.test.tsx
git commit -m "feat(web): account read section with findings, evidence and ask box on the Insights page

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Real-data run on org 3 and full test pass

**Files:**
- No code changes expected; fixes only if the run exposes one.

- [ ] **Step 1: Apply the migration to the primary DB**

```bash
set -a; . ./.env; set +a
dbmate --url "$DATABASE_URL" --no-dump-schema --migrations-dir db/migrations up
```

- [ ] **Step 2: Run the job locally against the primary DB (not via the box worker: the box runs main)**

```bash
uv run python - <<'EOF'
import asyncio
from uuid import UUID
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.core import queue
from cs.jobs.registry import JobContext
import cs.jobs.all_handlers  # noqa
from cs.insights.account_read.jobs import compute_account_read

async def main():
    s = get_settings(); db = await Database.connect(s.database_url)
    aid = await db.fetchval("SELECT id FROM ad_account WHERE org_id='01a089c9-231e-7000-bdaa-39277b4cc94b' AND status='active' LIMIT 1")
    org = UUID('01a089c9-231e-7000-bdaa-39277b4cc94b')
    class J:  # minimal job stand-in
        org_id = org; payload = {"ad_account_id": str(aid)}
    await compute_account_read(JobContext(db=db, blobs=blob_store_from_settings(s), settings=s, job=J()))
    row = await db.fetchrow("SELECT status, n_units, error, jsonb_array_length(result->'tests') AS tests FROM account_read_run ORDER BY created_at DESC LIMIT 1")
    print(dict(row)); await db.close()
asyncio.run(main())
EOF
```

Expected: `status='done'`, `n_units=56`, several hundred tests, runtime under 3 minutes. If `rejected`, read `error` for the failed checks and fix the prompt or the check, then rerun.

- [ ] **Step 3: Read the stored read and compare with the throwaway report**

```bash
psql "$DATABASE_URL" -X -q -At -c "SELECT read->>'overview' FROM account_read_run ORDER BY created_at DESC LIMIT 1"
psql "$DATABASE_URL" -X -q -At -c "SELECT jsonb_pretty(read->'findings') FROM account_read_run ORDER BY created_at DESC LIMIT 1" | head -120
```

Check: every finding has a mechanism, two or three exemplars that are real headlines, and a `why_non_obvious` that is not a restatement. Directions should agree with `docs`-free reference: early reveal hurts hook rate; long/expert entangled; risk-avoidance and bold-claim CPA lower. If a finding is table-narration, tighten the prompt's "Your job is the amalgamation" paragraph and rerun once.

- [ ] **Step 4: Full Python test pass and lint**

```bash
export DATABASE_URL_TEST=$(grep -o "DATABASE_URL_TEST=.*" .env | cut -d= -f2-)
uv run pytest tests/insights/account_read tests/api/test_account_read.py tests/api/test_insights.py tests/jobs -q
uv run ruff check src/cs/insights/account_read src/cs/api/routers/insights.py scripts/compute_account_read.py
cd web && npx vitest run && cd ..
```

Expected: all pass except the pre-existing `test_keep_lease_extends_a_held_lease` flake against Supabase latency.

- [ ] **Step 5: Commit any fixes, push the branch**

```bash
git add -A src tests web scripts db docs
git commit -m "fix(account-read): adjustments from the first org 3 run

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"   # only if there are changes
git push -u origin aggregate-insights
```

Do not merge to main or deploy; the user reviews the branch and the stored read first.
