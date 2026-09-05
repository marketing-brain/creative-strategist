# Phase 3: Insights and Copilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn tags plus metrics into deterministic pattern insights and fatigue alerts, have Claude explain each one and suggest a next test, assemble a weekly brief, and expose a Copilot chat that answers questions with read-only tools.

**Architecture:** `insights/patterns.py` and `insights/fatigue.py` are pure SQL-plus-arithmetic and produce `InsightDraft` values. `insights/explain.py` (an `Explainer` Protocol, Claude behind it) writes the prose. `insights/repo.py` persists append-only `insight` and `brief` rows. Scheduled jobs run nightly and hourly. `insights/copilot.py` wraps repo queries as tools for the Claude tool runner.

**Tech Stack:** SQL over `metric_daily` ⋈ latest tags, `anthropic` SDK (`client.beta.messages.tool_runner` with `@beta_async_tool`), FastAPI.

**Spec:** `docs/superpowers/specs/2026-09-04-creative-strategist-design.md` (section 7)

## Global Constraints

- Pattern thresholds (defaults, overridable per call): `min_creatives=5`, `min_spend=1000`, `window_days=30`, `lift_threshold=0.15`, tag `confidence >= 0.5`.
- Fatigue: creatives with spend in the trailing 7 days ≥ `spend_floor=100`; compare trailing 7-day CTR/CPA to the creative's first 14 days; flag when CTR drops more than 30% or CPA rises more than 30%.
- Metrics considered: `ctr = clicks/impressions`, `cpa = spend/conversions`, `roas = conversion_value/spend`. Lift is signed so positive is always "better".
- Insights are append-only; a duplicate `(kind, dimension, value, metric, window, creative_id)` is skipped, never updated.
- Copilot tools are read-only repo calls; no model-generated SQL.
- Models from `settings.claude_model_insights` and `settings.claude_model_copilot` (default `claude-opus-5`).
- All earlier constraints apply.
- Phase 2 final-review follow-ups this phase must close: `taxonomy.validate()` deduplicating repeated dimensions and `finish_tag_run` idempotency (both Task 0, ahead of the pattern-aggregation SQL that groups `tag` rows by `(dimension, value)`), and the missing `asset(creative_id, kind, seq)` unique constraint (added in Task 1's migration). Carousel-tagged-via-first-card-only is a documented, deliberate limitation carried forward, not fixed this phase.

---

## File structure

```
src/cs/tagging/taxonomy.py         # modified: validate() dedupes repeated dimensions (Task 0)
src/cs/tagging/repo.py             # modified: finish_tag_run idempotent tag insert (Task 0)
db/migrations/20260904000006_insights.sql   # insight, brief, copilot_conversation, copilot_message, view creative_latest_tag, asset unique index
src/cs/insights/__init__.py
src/cs/insights/types.py          # InsightDraft dataclass
src/cs/insights/patterns.py       # compute_pattern_insights()
src/cs/insights/fatigue.py        # compute_fatigue()
src/cs/insights/explain.py        # Explainer Protocol, ClaudeExplainer
src/cs/insights/repo.py           # insert_insight (skip dup), list_insights, list_fatigue, insert_brief, list_briefs
src/cs/insights/brief.py          # render_brief(insights) -> markdown
src/cs/insights/copilot.py        # CopilotTools, Copilot Protocol, ClaudeCopilot, conversation persistence
src/cs/insights/fakes.py          # FakeExplainer, FakeCopilot
src/cs/insights/jobs.py           # insights.compute_patterns, insights.compute_fatigue, insights.weekly_brief + schedules
src/cs/api/routers/insights.py    # /insights, /fatigue, /briefs
src/cs/api/routers/copilot.py     # /copilot/conversations, /copilot/conversations/{id}/messages
tests/insights/...
```

---

### Task 0: Tagging hardening required before pattern aggregation (Phase 2 follow-ups)

**Why this task exists:** the Phase 2 final review flagged that `taxonomy.validate()` does not deduplicate repeated dimensions, and that Task 2's pattern-aggregation SQL (grouping `tag` rows by `(dimension, value)` through `creative_latest_tag`) would double-count or split a creative's contribution if the model ever emits the same dimension twice in one `tag_run`. The same review flagged `finish_tag_run`'s tag inserts as non-idempotent under job retry. Both are cheap to close now, before Task 2 depends on clean per-dimension tag data.

**Files:**
- Modify: `src/cs/tagging/taxonomy.py`, `src/cs/tagging/repo.py`
- Test: `tests/tagging/test_taxonomy.py`, `tests/tagging/test_repo.py` (new)

**Interfaces:**
- `Taxonomy.validate(tags, fmt)`: after the existing per-tag coercion/drop logic, deduplicate by `dimension`, keeping only the highest-`confidence` entry for each dimension (first occurrence wins on an exact tie). Output order otherwise unchanged (first-seen order of the kept dimensions).
- `finish_tag_run(db, org_id, run_id, tags, prompt_sha, input_tokens, output_tokens)`: inside its existing transaction, `DELETE FROM tag WHERE tag_run_id=$1` before inserting the new rows, so a job that crashes mid-insert and is retried (reclaimed) does not double-insert `tag` rows for the same run.

- [ ] **Step 1: Write failing tests**

Add to `tests/tagging/test_taxonomy.py`:

```python
def test_validate_dedupes_repeated_dimension_keeping_highest_confidence():
    t = load_taxonomy()
    out = t.validate(
        [
            {"dimension": "hook_type", "value": "question", "confidence": 0.4, "evidence": "e1"},
            {"dimension": "tone", "value": "warm", "confidence": 0.8, "evidence": "e2"},
            {"dimension": "hook_type", "value": "statistic", "confidence": 0.9, "evidence": "e3"},
        ],
        "image",
    )
    assert [(o["dimension"], o["value"], o["confidence"]) for o in out] == [
        ("hook_type", "statistic", 0.9),
        ("tone", "warm", 0.8),
    ]
```

`tests/tagging/test_repo.py` (new file):

```python
from cs.connectors.base import PlatformCreative
from cs.core.crypto import encrypt_str
from cs.ingest import repo as ingest_repo
from cs.tagging import repo
from cs.tagging.taxonomy import load_taxonomy


async def test_finish_tag_run_is_idempotent_on_retry(db, org_id, settings):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    cid, _ = await ingest_repo.upsert_creative(
        db, org_id, acct, PlatformCreative("c", "image", "H", "P", None, None, "u", None, None, {}), "raw"
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    tags = [{"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "e"}]
    await repo.finish_tag_run(db, org_id, rid, tags, "sha", 1, 1)
    await repo.finish_tag_run(db, org_id, rid, tags, "sha", 1, 1)  # simulates a reclaimed/retried job
    rows = await db.fetch("SELECT dimension, value FROM tag WHERE tag_run_id=$1", rid)
    assert len(rows) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/tagging/test_taxonomy.py tests/tagging/test_repo.py -v`
Expected: FAIL (dedup test and idempotency test)

- [ ] **Step 3: Implement**

In `src/cs/tagging/taxonomy.py`, change `Taxonomy.validate` to dedupe after building `out`:

```python
    def validate(self, tags: list[dict], fmt: str) -> list[dict]:
        by_name = {d.name: d for d in self.for_format(fmt)}
        out: list[dict] = []
        for t in tags:
            d = by_name.get(t.get("dimension"))
            if d is None:
                continue
            value = str(t.get("value", "")).strip()
            if d.values is not None and value not in d.values:
                value = "other" if "other" in d.values else d.values[0]
            conf = float(t.get("confidence", 0.0))
            out.append(
                {
                    "dimension": d.name,
                    "value": value,
                    "confidence": max(0.0, min(1.0, conf)),
                    "evidence": str(t.get("evidence", ""))[:500],
                }
            )
        best: dict[str, dict] = {}
        for tag in out:
            existing = best.get(tag["dimension"])
            if existing is None or tag["confidence"] > existing["confidence"]:
                best[tag["dimension"]] = tag
        return [best[name] for name in dict.fromkeys(t["dimension"] for t in out)]
```

In `src/cs/tagging/repo.py`, add the delete as the first statement inside `finish_tag_run`'s transaction:

```python
    async with db.transaction() as tx:
        await tx.execute("DELETE FROM tag WHERE tag_run_id=$1", run_id)
        for t in tags:
            await tx.execute(
                "INSERT INTO tag (id, org_id, tag_run_id, creative_id, dimension, value, "
                "confidence, evidence) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                new_id(),
                org_id,
                run_id,
                creative_id,
                t["dimension"],
                t["value"],
                t["confidence"],
                t["evidence"],
            )
```

(the rest of `finish_tag_run` — the `tag_run` status update — is unchanged.)

- [ ] **Step 4: Run to verify it passes, then commit**

Run: `uv run pytest tests/tagging -v`
Expected: PASS

```bash
git add src/cs/tagging/taxonomy.py src/cs/tagging/repo.py tests/tagging/test_taxonomy.py tests/tagging/test_repo.py
git commit -m "fix(tagging): dedupe repeated tag dimensions and make finish_tag_run idempotent"
```

---

### Task 1: Migration, view, and InsightDraft

**Files:**
- Create: `db/migrations/20260904000006_insights.sql`, `src/cs/insights/__init__.py`, `src/cs/insights/types.py`, `src/cs/insights/repo.py`
- Test: `tests/insights/test_repo.py`

**Interfaces:**
- View `creative_latest_tag(org_id, creative_id, dimension, value, confidence)`.
- Tables: `insight`, `brief`, `copilot_conversation(id, org_id, user_id, title, created_at)`, `copilot_message(id, org_id, conversation_id, role, content JSONB, created_at)`.
- Unique index `asset(creative_id, kind, seq)`, closing the Phase 2 final-review follow-up (this is a defense-in-depth constraint on top of the existing check-then-insert/delete-then-insert application logic, which already keeps this invariant for sequential retries).
- `@dataclass InsightDraft(kind: str, dimension: str, value: str, metric: str, window_start: date, window_end: date, sample_creatives: int, sample_spend: Decimal, baseline: float, observed: float, lift: float, creative_id: UUID | None = None, ad_account_id: UUID | None = None)`
- repo: `insert_insight(db, org_id, draft, explanation: str, suggested_test: str) -> UUID | None`; `list_insights(db, org_id, kind: str | None, ad_account_id=None, limit=50) -> list[Record]`; `unexplained_insights` is not needed (explanation is written at insert). `insert_brief(db, org_id, week_start, content_md, insight_ids) -> UUID`; `list_briefs(db, org_id, limit=12)`.

- [ ] **Step 1: Migration**

`db/migrations/20260904000006_insights.sql`:

```sql
-- migrate:up
CREATE VIEW creative_latest_tag AS
SELECT t.org_id, t.creative_id, t.dimension, t.value, t.confidence
FROM tag t
JOIN (
  SELECT DISTINCT ON (creative_id) id, creative_id
  FROM tag_run WHERE status = 'done'
  ORDER BY creative_id, finished_at DESC
) r ON r.id = t.tag_run_id;

CREATE TABLE insight (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  ad_account_id UUID REFERENCES ad_account(id),
  kind TEXT NOT NULL CHECK (kind IN ('pattern', 'fatigue')),
  dimension TEXT NOT NULL,
  value TEXT NOT NULL,
  metric TEXT NOT NULL CHECK (metric IN ('ctr', 'cpa', 'roas')),
  creative_id UUID REFERENCES creative(id),
  window_start DATE NOT NULL,
  window_end DATE NOT NULL,
  sample_creatives INT NOT NULL,
  sample_spend NUMERIC(14,4) NOT NULL,
  baseline DOUBLE PRECISION NOT NULL,
  observed DOUBLE PRECISION NOT NULL,
  lift DOUBLE PRECISION NOT NULL,
  explanation TEXT NOT NULL,
  suggested_test TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX insight_dedup_idx ON insight
  (org_id, kind, dimension, value, metric, window_start, window_end, COALESCE(creative_id, '00000000-0000-0000-0000-000000000000'::uuid));
CREATE INDEX insight_org_kind_idx ON insight (org_id, kind, created_at DESC);

CREATE TABLE brief (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  week_start DATE NOT NULL,
  content_md TEXT NOT NULL,
  insight_ids UUID[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, week_start)
);

CREATE TABLE copilot_conversation (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  user_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT 'New conversation',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE copilot_message (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  conversation_id UUID NOT NULL REFERENCES copilot_conversation(id),
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX copilot_message_conv_idx ON copilot_message (conversation_id, created_at);

CREATE UNIQUE INDEX asset_creative_kind_seq_uidx ON asset (creative_id, kind, seq);

-- migrate:down
DROP INDEX asset_creative_kind_seq_uidx;
DROP TABLE copilot_message;
DROP TABLE copilot_conversation;
DROP TABLE brief;
DROP TABLE insight;
DROP VIEW creative_latest_tag;
```

Note: if any pre-existing dev data violates this constraint (a duplicate `(creative_id, kind, seq)` from before the application-level check-then-insert logic was added), the migration will fail on `migrate:up`; if that happens, deduplicate the offending rows (keep the newest by `created_at`) before re-running, and record what was found in the ledger.

- [ ] **Step 2: Write failing test**

`tests/insights/test_repo.py`:

```python
from datetime import date
from decimal import Decimal

from cs.insights import repo
from cs.insights.types import InsightDraft


def _draft(**kw):
    base = dict(kind="pattern", dimension="hook_type", value="question", metric="ctr",
                window_start=date(2026, 8, 1), window_end=date(2026, 8, 31), sample_creatives=6,
                sample_spend=Decimal("1500"), baseline=0.01, observed=0.013, lift=0.3)
    base.update(kw)
    return InsightDraft(**base)


async def test_insert_skips_duplicates_and_lists(db, org_id):
    a = await repo.insert_insight(db, org_id, _draft(), "because", "test X")
    b = await repo.insert_insight(db, org_id, _draft(), "because", "test X")
    assert a is not None and b is None
    rows = await repo.list_insights(db, org_id, "pattern")
    assert len(rows) == 1 and rows[0]["explanation"] == "because"
    assert await repo.list_insights(db, org_id, "fatigue") == []


async def test_briefs(db, org_id):
    bid = await repo.insert_brief(db, org_id, date(2026, 8, 31), "# Brief", [])
    assert bid is not None
    assert (await repo.list_briefs(db, org_id))[0]["content_md"] == "# Brief"
```

- [ ] **Step 3: Run to verify it fails**

Run: `make migrate-test && uv run pytest tests/insights/test_repo.py -v`
Expected: FAIL

- [ ] **Step 4: Implement**

`src/cs/insights/__init__.py`: empty.

`src/cs/insights/types.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass
class InsightDraft:
    kind: str
    dimension: str
    value: str
    metric: str
    window_start: date
    window_end: date
    sample_creatives: int
    sample_spend: Decimal
    baseline: float
    observed: float
    lift: float
    creative_id: UUID | None = None
    ad_account_id: UUID | None = None
```

`src/cs/insights/repo.py`:

```python
from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id
from cs.insights.types import InsightDraft


async def insert_insight(db: Database, org_id: UUID, d: InsightDraft, explanation: str, suggested_test: str) -> UUID | None:
    row = await db.fetchrow(
        """
        INSERT INTO insight (id, org_id, ad_account_id, kind, dimension, value, metric, creative_id, window_start, window_end,
                             sample_creatives, sample_spend, baseline, observed, lift, explanation, suggested_test)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
        ON CONFLICT DO NOTHING RETURNING id
        """,
        new_id(), org_id, d.ad_account_id, d.kind, d.dimension, d.value, d.metric, d.creative_id, d.window_start,
        d.window_end, d.sample_creatives, d.sample_spend, d.baseline, d.observed, d.lift, explanation, suggested_test)
    return row["id"] if row else None


async def list_insights(db: Database, org_id: UUID, kind: str | None = None, ad_account_id: UUID | None = None,
                        limit: int = 50) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT i.*, c.headline AS creative_headline
        FROM insight i LEFT JOIN creative c ON c.id = i.creative_id
        WHERE i.org_id = $1 AND ($2::text IS NULL OR i.kind = $2) AND ($3::uuid IS NULL OR i.ad_account_id = $3)
        ORDER BY i.created_at DESC, abs(i.lift) DESC LIMIT $4
        """, org_id, kind, ad_account_id, limit)


async def insert_brief(db: Database, org_id: UUID, week_start: date, content_md: str, insight_ids: list[UUID]) -> UUID | None:
    row = await db.fetchrow(
        "INSERT INTO brief (id, org_id, week_start, content_md, insight_ids) VALUES ($1,$2,$3,$4,$5) "
        "ON CONFLICT (org_id, week_start) DO NOTHING RETURNING id", new_id(), org_id, week_start, content_md, insight_ids)
    return row["id"] if row else None


async def list_briefs(db: Database, org_id: UUID, limit: int = 12) -> list[asyncpg.Record]:
    return await db.fetch("SELECT * FROM brief WHERE org_id=$1 ORDER BY week_start DESC LIMIT $2", org_id, limit)
```

- [ ] **Step 5: Run to verify it passes, then commit**

Run: `uv run pytest tests/insights/test_repo.py -v`
Expected: PASS

```bash
git add db/migrations/20260904000006_insights.sql src/cs/insights tests/insights/test_repo.py
git commit -m "feat(insights): schema, latest-tag view, insight and brief repository"
```

---

### Task 2: Pattern insights

**Files:**
- Create: `src/cs/insights/patterns.py`
- Test: `tests/insights/test_patterns.py`, `tests/insights/seed.py` (shared seeding helper)

**Interfaces:**
- `async compute_pattern_insights(db, org_id, ad_account_id, window_start, window_end, *, min_creatives=5, min_spend=Decimal("1000"), lift_threshold=0.15, min_confidence=0.5) -> list[InsightDraft]`
- `metrics_from_sums(spend, impressions, clicks, conversions, conversion_value) -> dict[str, float | None]` (pure).
- `signed_lift(metric, observed, baseline) -> float | None` (pure; for `cpa` lower is better).
- `tests/insights/seed.py`: `async seed_account(db, org_id, settings) -> UUID`; `async seed_creative(db, org_id, account_id, name, fmt, tags: dict[str, str], daily: list[tuple[date, Decimal, int, int, Decimal, Decimal]]) -> UUID` where daily tuples are `(day, spend, impressions, clicks, conversions, conversion_value)`.

- [ ] **Step 1: Write the seed helper and failing tests**

`tests/insights/seed.py`:

```python
from datetime import date
from decimal import Decimal
from uuid import UUID

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core.crypto import encrypt_str
from cs.ingest import repo as ingest_repo
from cs.tagging import repo as tag_repo
from cs.tagging.taxonomy import load_taxonomy


async def seed_account(db, org_id, settings) -> UUID:
    aid = await ingest_repo.upsert_ad_account(db, org_id, "1", "Main", "USD", encrypt_str(settings, "t"), None)
    await ingest_repo.set_ad_account_status(db, org_id, aid, "active")
    return aid


async def seed_creative(db, org_id, account_id, name, fmt, tags: dict[str, str],
                        daily: list[tuple[date, Decimal, int, int, Decimal, Decimal]]) -> UUID:
    cid, _ = await ingest_repo.upsert_creative(db, org_id, account_id,
        PlatformCreative(name, fmt, f"H {name}", "P", None, None, "u", None, None, {}), "raw")
    await ingest_repo.upsert_ad(db, org_id, account_id, PlatformAd(f"ad_{name}", name, "ACTIVE", "as", "cp", name), cid)
    tv = await tag_repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await tag_repo.start_tag_run(db, org_id, cid, tv, "fake")
    await tag_repo.finish_tag_run(db, org_id, rid,
        [{"dimension": k, "value": v, "confidence": 0.9, "evidence": ""} for k, v in tags.items()], "s", 1, 1)
    rows = [MetricRow(f"ad_{name}", d, "facebook:feed", sp, imp, clk, clk, conv, val, 0, 0, 0, 0, {})
            for d, sp, imp, clk, conv, val in daily]
    await ingest_repo.upsert_metrics(db, org_id, account_id, rows)
    return cid
```

`tests/insights/test_patterns.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from cs.insights.patterns import compute_pattern_insights, metrics_from_sums, signed_lift
from tests.insights.seed import seed_account, seed_creative


def test_metrics_and_lift():
    m = metrics_from_sums(Decimal("100"), 10000, 200, Decimal("10"), Decimal("500"))
    assert m == {"ctr": 0.02, "cpa": 10.0, "roas": 5.0}
    assert metrics_from_sums(Decimal("0"), 0, 0, Decimal("0"), Decimal("0")) == {"ctr": None, "cpa": None, "roas": None}
    assert signed_lift("ctr", 0.02, 0.01) == 1.0
    assert signed_lift("cpa", 5.0, 10.0) == 0.5
    assert signed_lift("cpa", None, 10.0) is None


async def test_pattern_surfaces_high_ctr_hook(db, org_id, settings):
    acct = await seed_account(db, org_id, settings)
    start = date(2026, 8, 1)
    days = [start + timedelta(days=i) for i in range(10)]
    for i in range(6):  # question hooks: ctr 3%
        await seed_creative(db, org_id, acct, f"q{i}", "image", {"hook_type": "question"},
                            [(d, Decimal("30"), 1000, 30, Decimal("1"), Decimal("50")) for d in days])
    for i in range(6):  # statistic hooks: ctr 1%
        await seed_creative(db, org_id, acct, f"s{i}", "image", {"hook_type": "statistic"},
                            [(d, Decimal("30"), 1000, 10, Decimal("1"), Decimal("50")) for d in days])
    drafts = await compute_pattern_insights(db, org_id, acct, start, start + timedelta(days=30))
    by = {(d.dimension, d.value, d.metric): d for d in drafts}
    q = by[("hook_type", "question", "ctr")]
    assert q.sample_creatives == 6 and q.sample_spend == Decimal("1800")
    assert round(q.observed, 3) == 0.03 and round(q.baseline, 3) == 0.02 and round(q.lift, 2) == 0.5
    s = by[("hook_type", "statistic", "ctr")]
    assert s.lift < 0
    assert ("hook_type", "question", "cpa") not in by  # same cpa, no lift


async def test_pattern_respects_thresholds(db, org_id, settings):
    acct = await seed_account(db, org_id, settings)
    start = date(2026, 8, 1)
    for i in range(3):
        await seed_creative(db, org_id, acct, f"q{i}", "image", {"hook_type": "question"},
                            [(start, Decimal("10"), 100, 5, Decimal("0"), Decimal("0"))])
    assert await compute_pattern_insights(db, org_id, acct, start, start + timedelta(days=30)) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/insights/test_patterns.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

`src/cs/insights/patterns.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from cs.core.db import Database
from cs.insights.types import InsightDraft

METRICS = ("ctr", "cpa", "roas")


def metrics_from_sums(spend: Decimal, impressions: int, clicks: int, conversions: Decimal, conversion_value: Decimal) -> dict:
    spend_f, conv_f, val_f = float(spend), float(conversions), float(conversion_value)
    return {
        "ctr": round(clicks / impressions, 6) if impressions else None,
        "cpa": round(spend_f / conv_f, 4) if conv_f else None,
        "roas": round(val_f / spend_f, 4) if spend_f else None,
    }


def signed_lift(metric: str, observed: float | None, baseline: float | None) -> float | None:
    if observed is None or baseline is None or baseline == 0:
        return None
    raw = (observed - baseline) / baseline
    return round(-raw if metric == "cpa" else raw, 6)


async def compute_pattern_insights(
    db: Database, org_id: UUID, ad_account_id: UUID, window_start: date, window_end: date, *,
    min_creatives: int = 5, min_spend: Decimal = Decimal("1000"), lift_threshold: float = 0.15,
    min_confidence: float = 0.5,
) -> list[InsightDraft]:
    rows = await db.fetch(
        """
        WITH m AS (
          SELECT creative_id, sum(spend) AS spend, sum(impressions) AS imp, sum(clicks) AS clicks,
                 sum(conversions) AS conv, sum(conversion_value) AS val
          FROM metric_daily WHERE org_id=$1 AND ad_account_id=$2 AND day BETWEEN $3 AND $4
          GROUP BY creative_id
        ), base AS (
          SELECT sum(spend) AS spend, sum(imp) AS imp, sum(clicks) AS clicks, sum(conv) AS conv, sum(val) AS val FROM m
        ), seg AS (
          SELECT lt.dimension, lt.value, count(*) AS n, sum(m.spend) AS spend, sum(m.imp) AS imp,
                 sum(m.clicks) AS clicks, sum(m.conv) AS conv, sum(m.val) AS val
          FROM m JOIN creative_latest_tag lt ON lt.creative_id = m.creative_id AND lt.org_id = $1
          WHERE lt.confidence >= $7
          GROUP BY lt.dimension, lt.value
        )
        SELECT seg.dimension, seg.value, seg.n, seg.spend, seg.imp, seg.clicks, seg.conv, seg.val,
               base.spend AS b_spend, base.imp AS b_imp, base.clicks AS b_clicks, base.conv AS b_conv, base.val AS b_val
        FROM seg, base
        WHERE seg.n >= $5 AND seg.spend >= $6
        """, org_id, ad_account_id, window_start, window_end, min_creatives, min_spend, min_confidence)
    out: list[InsightDraft] = []
    for r in rows:
        seg = metrics_from_sums(r["spend"], int(r["imp"]), int(r["clicks"]), r["conv"], r["val"])
        base = metrics_from_sums(r["b_spend"], int(r["b_imp"]), int(r["b_clicks"]), r["b_conv"], r["b_val"])
        for metric in METRICS:
            lift = signed_lift(metric, seg[metric], base[metric])
            if lift is None or abs(lift) < lift_threshold:
                continue
            out.append(InsightDraft(
                kind="pattern", dimension=r["dimension"], value=r["value"], metric=metric,
                window_start=window_start, window_end=window_end, sample_creatives=int(r["n"]),
                sample_spend=Decimal(r["spend"]), baseline=base[metric], observed=seg[metric], lift=lift,
                ad_account_id=ad_account_id))
    out.sort(key=lambda d: -abs(d.lift))
    return out
```

- [ ] **Step 4: Run to verify they pass, then commit**

Run: `uv run pytest tests/insights/test_patterns.py -v`
Expected: PASS

```bash
git add src/cs/insights/patterns.py tests/insights
git commit -m "feat(insights): spend-weighted pattern insights with thresholds"
```

---

### Task 3: Fatigue detection

**Files:**
- Create: `src/cs/insights/fatigue.py`
- Test: `tests/insights/test_fatigue.py`

**Interfaces:**
- `async compute_fatigue(db, org_id, ad_account_id, today: date, *, spend_floor=Decimal("100"), drop_threshold=0.30, first_days=14, trailing_days=7) -> list[InsightDraft]` — one draft per (creative, metric) that decayed; `dimension="creative"`, `value=str(creative_id)`, `creative_id` set.

- [ ] **Step 1: Write failing test**

`tests/insights/test_fatigue.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from cs.insights.fatigue import compute_fatigue
from tests.insights.seed import seed_account, seed_creative


async def test_fatigue_flags_ctr_decay(db, org_id, settings):
    acct = await seed_account(db, org_id, settings)
    today = date(2026, 9, 4)
    start = today - timedelta(days=40)
    good = [(start + timedelta(days=i), Decimal("20"), 1000, 30, Decimal("2"), Decimal("100")) for i in range(14)]
    bad = [(today - timedelta(days=i), Decimal("20"), 1000, 15, Decimal("2"), Decimal("100")) for i in range(7)]
    fatigued = await seed_creative(db, org_id, acct, "f", "image", {"hook_type": "question"}, good + bad)
    steady = [(start + timedelta(days=i), Decimal("20"), 1000, 30, Decimal("2"), Decimal("100")) for i in range(40)]
    await seed_creative(db, org_id, acct, "ok", "image", {"hook_type": "question"}, steady)
    drafts = await compute_fatigue(db, org_id, acct, today)
    assert [(d.creative_id, d.metric) for d in drafts] == [(fatigued, "ctr")]
    d = drafts[0]
    assert round(d.baseline, 3) == 0.03 and round(d.observed, 3) == 0.015 and round(d.lift, 2) == -0.5
    assert d.window_start == today - timedelta(days=6) and d.window_end == today


async def test_fatigue_ignores_low_spend(db, org_id, settings):
    acct = await seed_account(db, org_id, settings)
    today = date(2026, 9, 4)
    rows = [(today - timedelta(days=30 - i), Decimal("1"), 100, 5, Decimal("0"), Decimal("0")) for i in range(31)]
    await seed_creative(db, org_id, acct, "tiny", "image", {}, rows)
    assert await compute_fatigue(db, org_id, acct, today) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/insights/test_fatigue.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

`src/cs/insights/fatigue.py`:

```python
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from cs.core.db import Database
from cs.insights.patterns import metrics_from_sums, signed_lift
from cs.insights.types import InsightDraft


async def compute_fatigue(
    db: Database, org_id: UUID, ad_account_id: UUID, today: date, *,
    spend_floor: Decimal = Decimal("100"), drop_threshold: float = 0.30, first_days: int = 14, trailing_days: int = 7,
) -> list[InsightDraft]:
    trailing_start = today - timedelta(days=trailing_days - 1)
    rows = await db.fetch(
        """
        SELECT creative_id, day, sum(spend) AS spend, sum(impressions) AS imp, sum(clicks) AS clicks,
               sum(conversions) AS conv, sum(conversion_value) AS val
        FROM metric_daily WHERE org_id=$1 AND ad_account_id=$2 AND day <= $3
          AND creative_id IN (SELECT creative_id FROM metric_daily WHERE org_id=$1 AND ad_account_id=$2
                              AND day BETWEEN $4 AND $3 GROUP BY creative_id HAVING sum(spend) >= $5)
        GROUP BY creative_id, day ORDER BY creative_id, day
        """, org_id, ad_account_id, today, trailing_start, spend_floor)
    by_creative: dict[UUID, list] = defaultdict(list)
    for r in rows:
        by_creative[r["creative_id"]].append(r)
    out: list[InsightDraft] = []
    for cid, days in by_creative.items():
        first_day = days[0]["day"]
        first_end = first_day + timedelta(days=first_days - 1)
        if first_end >= trailing_start:
            continue  # too young to compare
        first = [d for d in days if d["day"] <= first_end]
        trailing = [d for d in days if d["day"] >= trailing_start]

        def sums(chunk):
            return metrics_from_sums(sum(d["spend"] for d in chunk), sum(int(d["imp"]) for d in chunk),
                                     sum(int(d["clicks"]) for d in chunk), sum(d["conv"] for d in chunk),
                                     sum(d["val"] for d in chunk))

        base, now = sums(first), sums(trailing)
        for metric in ("ctr", "cpa"):
            lift = signed_lift(metric, now[metric], base[metric])
            if lift is not None and lift <= -drop_threshold:
                out.append(InsightDraft(
                    kind="fatigue", dimension="creative", value=str(cid), metric=metric,
                    window_start=trailing_start, window_end=today, sample_creatives=1,
                    sample_spend=Decimal(sum(d["spend"] for d in trailing)), baseline=base[metric],
                    observed=now[metric], lift=lift, creative_id=cid, ad_account_id=ad_account_id))
    out.sort(key=lambda d: d.lift)
    return out
```

- [ ] **Step 4: Run to verify it passes, then commit**

Run: `uv run pytest tests/insights/test_fatigue.py -v`
Expected: PASS

```bash
git add src/cs/insights/fatigue.py tests/insights/test_fatigue.py
git commit -m "feat(insights): creative fatigue detection"
```

---

### Task 4: Explainer and scheduled insight jobs

**Files:**
- Create: `src/cs/insights/explain.py`, `src/cs/insights/fakes.py`, `src/cs/insights/jobs.py`
- Modify: `src/cs/jobs/all_handlers.py`
- Test: `tests/insights/test_jobs.py`

**Interfaces:**
- `Explainer` Protocol: `async explain(draft: InsightDraft, context: dict) -> tuple[str, str]` (explanation, suggested_test). `context` carries `dimension_description`, `creative_headline` (fatigue), `account_name`.
- `ClaudeExplainer(settings, client=None)` → `client.beta.messages.create` with structured output schema `{"explanation": str, "suggested_test": str}` and refusal fallback; on refusal returns a deterministic template explanation.
- `FakeExplainer` returns `("fake explanation", "fake test")` and records drafts.
- Jobs: `insights.compute_patterns` payload `{"ad_account_id"}` (window = last 30 days ending yesterday); `insights.compute_fatigue` payload `{"ad_account_id"}`; schedules `insights.nightly_patterns` (86400s) and `insights.hourly_fatigue` (3600s) enqueue per active account with dedup keys `patterns:{id}` / `fatigue:{id}`. Seam `EXPLAINER_FACTORY(settings) -> Explainer`.

- [ ] **Step 1: Write failing tests**

`tests/insights/test_jobs.py`:

```python
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from cs.core import queue
from cs.insights import jobs as insight_jobs
from cs.insights.explain import ClaudeExplainer
from cs.insights.fakes import FakeExplainer
from cs.insights.types import InsightDraft
from cs.jobs.worker import run_once
from tests.insights.seed import seed_account, seed_creative


async def test_compute_patterns_job_persists_with_explanations(db, blobs, settings, org_id, monkeypatch):
    fake = FakeExplainer()
    monkeypatch.setattr(insight_jobs, "EXPLAINER_FACTORY", lambda s: fake)
    acct = await seed_account(db, org_id, settings)
    today = date.today()
    days = [today - timedelta(days=i + 1) for i in range(10)]
    for i in range(6):
        await seed_creative(db, org_id, acct, f"q{i}", "image", {"hook_type": "question"},
                            [(d, Decimal("30"), 1000, 30, Decimal("1"), Decimal("50")) for d in days])
    for i in range(6):
        await seed_creative(db, org_id, acct, f"s{i}", "image", {"hook_type": "statistic"},
                            [(d, Decimal("30"), 1000, 10, Decimal("1"), Decimal("50")) for d in days])
    await queue.enqueue(db, org_id, "insights.compute_patterns", {"ad_account_id": str(acct)})
    await run_once(db, blobs, settings, "w")
    n = await db.fetchval("SELECT count(*) FROM insight WHERE kind='pattern'")
    assert n >= 2 and len(fake.seen) == n
    assert await db.fetchval("SELECT explanation FROM insight LIMIT 1") == "fake explanation"
    # second run same window: no duplicates, no new explain calls
    await queue.enqueue(db, org_id, "insights.compute_patterns", {"ad_account_id": str(acct)})
    await run_once(db, blobs, settings, "w")
    assert await db.fetchval("SELECT count(*) FROM insight") == n and len(fake.seen) == n


async def test_schedules_enqueue_per_active_account(db, settings, org_id):
    await seed_account(db, org_id, settings)
    await insight_jobs.nightly_patterns(db, settings)
    await insight_jobs.hourly_fatigue(db, settings)
    types = sorted(r["job_type"] for r in await db.fetch("SELECT job_type FROM job"))
    assert types == ["insights.compute_fatigue", "insights.compute_patterns"]


async def test_claude_explainer_parses_structured_output(settings):
    class M:
        async def create(self, **kw):
            return SimpleNamespace(stop_reason="end_turn", model="m",
                                   content=[SimpleNamespace(type="text", text='{"explanation": "E", "suggested_test": "T"}')])
    client = SimpleNamespace(beta=SimpleNamespace(messages=M()))
    d = InsightDraft("pattern", "hook_type", "question", "ctr", date(2026, 8, 1), date(2026, 8, 31), 6, Decimal("1"), 0.02, 0.03, 0.5)
    assert await ClaudeExplainer(settings, client=client).explain(d, {"dimension_description": "x", "account_name": "A"}) == ("E", "T")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/insights/test_jobs.py -v`
Expected: FAIL

- [ ] **Step 3: Implement explainer and fakes**

`src/cs/insights/explain.py`:

```python
from __future__ import annotations

import json
from typing import Protocol

import anthropic

from cs.core.config import Settings
from cs.insights.types import InsightDraft

SYSTEM = ("You are a performance creative strategist writing for a growth team. Explain one data finding in plain "
          "language in two or three sentences, name the likely reason, and propose one concrete next creative test. "
          "Do not invent numbers beyond those given.")

SCHEMA = {"type": "object", "properties": {"explanation": {"type": "string"}, "suggested_test": {"type": "string"}},
          "required": ["explanation", "suggested_test"], "additionalProperties": False}


def _template(d: InsightDraft) -> tuple[str, str]:
    direction = "better" if d.lift > 0 else "worse"
    pct = f"{abs(d.lift) * 100:.0f}%"
    if d.kind == "fatigue":
        return (f"This creative's {d.metric.upper()} is {pct} worse over the last 7 days than in its first 14 days.",
                "Refresh the hook and first frame while keeping the winning offer and message.")
    return (f"Creatives tagged {d.dimension}={d.value} performed {pct} {direction} on {d.metric.upper()} than the account "
            f"average across {d.sample_creatives} creatives and ${d.sample_spend:.0f} of spend.",
            f"Launch two new variants that lean into {d.dimension}={d.value} and compare against the current best.")


def _user(d: InsightDraft, ctx: dict) -> str:
    return json.dumps({
        "kind": d.kind, "dimension": d.dimension, "dimension_description": ctx.get("dimension_description"),
        "value": d.value, "metric": d.metric, "baseline": d.baseline, "observed": d.observed, "lift": d.lift,
        "sample_creatives": d.sample_creatives, "sample_spend": float(d.sample_spend),
        "window": [d.window_start.isoformat(), d.window_end.isoformat()],
        "creative_headline": ctx.get("creative_headline"), "account_name": ctx.get("account_name"),
    })


class Explainer(Protocol):
    async def explain(self, draft: InsightDraft, context: dict) -> tuple[str, str]: ...


class ClaudeExplainer:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.model = settings.claude_model_insights

    async def explain(self, draft: InsightDraft, context: dict) -> tuple[str, str]:
        resp = await self.client.beta.messages.create(
            model=self.model, max_tokens=2000, system=SYSTEM,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content": _user(draft, context)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"},
        )
        if resp.stop_reason == "refusal":
            return _template(draft)
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        data = json.loads(text) if text else {}
        return data.get("explanation") or _template(draft)[0], data.get("suggested_test") or _template(draft)[1]
```

`src/cs/insights/fakes.py`:

```python
from __future__ import annotations

from cs.insights.types import InsightDraft


class FakeExplainer:
    def __init__(self):
        self.seen: list[InsightDraft] = []

    async def explain(self, draft: InsightDraft, context: dict) -> tuple[str, str]:
        self.seen.append(draft)
        return "fake explanation", "fake test"


class FakeCopilot:
    def __init__(self, reply: str = "fake answer"):
        self.reply = reply
        self.calls: list[list[dict]] = []

    async def answer(self, history: list[dict], tools) -> str:
        self.calls.append(history)
        rows = await tools.insights(kind="pattern", limit=3)
        return f"{self.reply} ({len(rows)} insights)"
```

- [ ] **Step 4: Implement jobs**

`src/cs/insights/jobs.py`:

```python
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from cs.core import queue
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights import repo
from cs.insights.explain import ClaudeExplainer, Explainer
from cs.insights.fatigue import compute_fatigue
from cs.insights.patterns import compute_pattern_insights
from cs.insights.types import InsightDraft
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule
from cs.tagging.taxonomy import load_taxonomy

WINDOW_DAYS = 30


def default_explainer_factory(settings: Settings) -> Explainer:
    return ClaudeExplainer(settings)


EXPLAINER_FACTORY = default_explainer_factory


async def _persist(ctx: JobContext, drafts: list[InsightDraft], account_name: str) -> int:
    explainer = EXPLAINER_FACTORY(ctx.settings)
    dims = {d.name: d.description for d in load_taxonomy().dimensions}
    n = 0
    for d in drafts:
        exists = await ctx.db.fetchval(
            "SELECT 1 FROM insight WHERE org_id=$1 AND kind=$2 AND dimension=$3 AND value=$4 AND metric=$5 "
            "AND window_start=$6 AND window_end=$7 AND creative_id IS NOT DISTINCT FROM $8",
            ctx.job.org_id, d.kind, d.dimension, d.value, d.metric, d.window_start, d.window_end, d.creative_id)
        if exists:
            continue
        context = {"dimension_description": dims.get(d.dimension), "account_name": account_name}
        if d.creative_id:
            context["creative_headline"] = await ctx.db.fetchval("SELECT headline FROM creative WHERE id=$1", d.creative_id)
        explanation, test = await explainer.explain(d, context)
        if await repo.insert_insight(ctx.db, ctx.job.org_id, d, explanation, test):
            n += 1
    return n


async def _account(ctx: JobContext):
    return await ctx.db.fetchrow("SELECT id, name FROM ad_account WHERE org_id=$1 AND id=$2",
                                 ctx.job.org_id, UUID(ctx.job.payload["ad_account_id"]))


@handler("insights.compute_patterns")
async def compute_patterns(ctx: JobContext) -> None:
    acct = await _account(ctx)
    end = date.today() - timedelta(days=1)
    drafts = await compute_pattern_insights(ctx.db, ctx.job.org_id, acct["id"], end - timedelta(days=WINDOW_DAYS - 1), end)
    await _persist(ctx, drafts, acct["name"])


@handler("insights.compute_fatigue")
async def compute_fatigue_job(ctx: JobContext) -> None:
    acct = await _account(ctx)
    drafts = await compute_fatigue(ctx.db, ctx.job.org_id, acct["id"], date.today())
    await _persist(ctx, drafts, acct["name"])


async def _enqueue_for_active(db: Database, job_type: str, prefix: str) -> None:
    for a in await db.fetch("SELECT id, org_id FROM ad_account WHERE status='active'"):
        await queue.enqueue(db, a["org_id"], job_type, {"ad_account_id": str(a["id"])}, dedup_key=f"{prefix}:{a['id']}")


@register_schedule("insights.nightly_patterns", every_seconds=86400)
async def nightly_patterns(db: Database, settings: Settings) -> None:
    await _enqueue_for_active(db, "insights.compute_patterns", "patterns")


@register_schedule("insights.hourly_fatigue", every_seconds=3600)
async def hourly_fatigue(db: Database, settings: Settings) -> None:
    await _enqueue_for_active(db, "insights.compute_fatigue", "fatigue")
```

Add `import cs.insights.jobs  # noqa: F401` to `src/cs/jobs/all_handlers.py`.

- [ ] **Step 5: Run to verify they pass, then commit**

Run: `uv run pytest tests/insights -v`
Expected: PASS

```bash
git add src/cs/insights src/cs/jobs/all_handlers.py tests/insights/test_jobs.py
git commit -m "feat(insights): Claude explanations and nightly/hourly insight jobs"
```

---

### Task 5: Weekly brief and insights API

**Files:**
- Create: `src/cs/insights/brief.py`, `src/cs/api/routers/insights.py`
- Modify: `src/cs/insights/jobs.py` (weekly brief job + schedule), `src/cs/api/app.py`
- Test: `tests/insights/test_brief.py`, `tests/api/test_insights.py`

**Interfaces:**
- `render_brief(week_start: date, patterns: list[Record], fatigue: list[Record]) -> str` (markdown).
- Job `insights.weekly_brief` payload `{}` → builds from insights created in the last 7 days, inserts `brief` for the Monday of the current week; schedule `insights.weekly_brief` every 86400s enqueues only on Mondays (`date.today().weekday() == 0`) with `dedup_key=f"brief:{org}:{week_start}"`.
- Routes: `GET /insights?kind&account_id&limit`, `GET /fatigue` (alias `kind=fatigue` with creative headline), `GET /briefs`, `GET /briefs/{id}`.

- [ ] **Step 1: Write failing tests**

`tests/insights/test_brief.py`:

```python
from datetime import date
from decimal import Decimal

from cs.insights import repo
from cs.insights.brief import render_brief
from cs.insights.types import InsightDraft


async def test_render_brief_lists_patterns_and_fatigue(db, org_id):
    d = InsightDraft("pattern", "hook_type", "question", "ctr", date(2026, 8, 1), date(2026, 8, 31), 6, Decimal("1500"), 0.02, 0.03, 0.5)
    await repo.insert_insight(db, org_id, d, "Questions win", "Test 2 more")
    patterns = await repo.list_insights(db, org_id, "pattern")
    md = render_brief(date(2026, 8, 31), patterns, [])
    assert md.startswith("# Weekly creative brief") and "hook_type = question" in md and "+50%" in md and "Test 2 more" in md
    assert "No fatigue alerts" in md
```

`tests/api/test_insights.py`:

```python
from datetime import date
from decimal import Decimal

from cs.insights import repo
from cs.insights.types import InsightDraft
from tests.conftest import dev_headers

H = dev_headers(org="org_ins")


async def test_insights_and_briefs_routes(client, db):
    from uuid import UUID
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    d = InsightDraft("pattern", "hook_type", "question", "ctr", date(2026, 8, 1), date(2026, 8, 31), 6, Decimal("1500"), 0.02, 0.03, 0.5)
    await repo.insert_insight(db, org_id, d, "E", "T")
    await repo.insert_brief(db, org_id, date(2026, 8, 31), "# B", [])
    r = await client.get("/insights?kind=pattern", headers=H)
    assert r.status_code == 200 and r.json()[0]["lift"] == 0.5 and r.json()[0]["explanation"] == "E"
    assert (await client.get("/fatigue", headers=H)).json() == []
    briefs = (await client.get("/briefs", headers=H)).json()
    assert briefs[0]["content_md"] == "# B"
    assert (await client.get(f"/briefs/{briefs[0]['id']}", headers=H)).status_code == 200
    assert (await client.get("/insights", headers=dev_headers(org="org_x"))).json() == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/insights/test_brief.py tests/api/test_insights.py -v`
Expected: FAIL

- [ ] **Step 3: Implement brief and job**

`src/cs/insights/brief.py`:

```python
from __future__ import annotations

from datetime import date


def _pct(lift: float) -> str:
    return f"{'+' if lift >= 0 else '-'}{abs(lift) * 100:.0f}%"


def render_brief(week_start: date, patterns, fatigue) -> str:
    lines = [f"# Weekly creative brief — week of {week_start.isoformat()}", ""]
    lines += ["## What is working", ""]
    if not patterns:
        lines.append("No new pattern insights this week.")
    for p in patterns:
        lines.append(f"- **{p['dimension']} = {p['value']}** — {p['metric'].upper()} {_pct(p['lift'])} vs account "
                     f"({p['sample_creatives']} creatives, ${float(p['sample_spend']):.0f} spend)")
        lines.append(f"  - {p['explanation']}")
        lines.append(f"  - Next test: {p['suggested_test']}")
    lines += ["", "## Fatigue alerts", ""]
    if not fatigue:
        lines.append("No fatigue alerts.")
    for f in fatigue:
        lines.append(f"- {f.get('creative_headline') or f['creative_id']} — {f['metric'].upper()} {_pct(f['lift'])} "
                     f"vs its first 14 days. {f['suggested_test']}")
    return "\n".join(lines) + "\n"
```

Append to `src/cs/insights/jobs.py`:

```python
from cs.insights.brief import render_brief  # noqa: E402


@handler("insights.weekly_brief")
async def weekly_brief(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    rows = await ctx.db.fetch(
        "SELECT i.*, c.headline AS creative_headline FROM insight i LEFT JOIN creative c ON c.id = i.creative_id "
        "WHERE i.org_id=$1 AND i.created_at >= now() - interval '7 days' ORDER BY abs(i.lift) DESC", org_id)
    patterns = [dict(r) for r in rows if r["kind"] == "pattern"][:10]
    fatigue = [dict(r) for r in rows if r["kind"] == "fatigue"][:10]
    await repo.insert_brief(ctx.db, org_id, week_start, render_brief(week_start, patterns, fatigue),
                            [r["id"] for r in rows])


@register_schedule("insights.weekly_brief", every_seconds=86400)
async def weekly_brief_schedule(db: Database, settings: Settings) -> None:
    today = date.today()
    if today.weekday() != 0:
        return
    week_start = today.isoformat()
    for o in await db.fetch("SELECT id FROM org"):
        await queue.enqueue(db, o["id"], "insights.weekly_brief", {}, dedup_key=f"brief:{o['id']}:{week_start}")
```

- [ ] **Step 4: Implement router**

`src/cs/api/routers/insights.py`:

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_db
from cs.core.db import Database
from cs.insights import repo

router = APIRouter()


def _ser(r) -> dict:
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, UUID):
            d[k] = str(v)
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif hasattr(v, "as_integer_ratio") and not isinstance(v, (int, float)):
            d[k] = float(v)
    return d


@router.get("/insights")
async def insights(kind: str | None = None, account_id: UUID | None = None, limit: int = 50,
                   auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    return [_ser(r) for r in await repo.list_insights(db, auth.org_id, kind, account_id, min(limit, 200))]


@router.get("/fatigue")
async def fatigue(limit: int = 50, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    return [_ser(r) for r in await repo.list_insights(db, auth.org_id, "fatigue", None, min(limit, 200))]


@router.get("/briefs")
async def briefs(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    return [_ser(r) for r in await repo.list_briefs(db, auth.org_id)]


@router.get("/briefs/{brief_id}")
async def brief(brief_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    r = await db.fetchrow("SELECT * FROM brief WHERE org_id=$1 AND id=$2", auth.org_id, brief_id)
    if not r:
        raise HTTPException(404)
    return _ser(r)
```

Include the router in `src/cs/api/app.py`.

- [ ] **Step 5: Run to verify they pass, then commit**

Run: `uv run pytest tests/insights tests/api/test_insights.py -v`
Expected: PASS

```bash
git add src/cs/insights src/cs/api tests/insights/test_brief.py tests/api/test_insights.py
git commit -m "feat(insights): weekly brief job and insights/briefs API"
```

---

### Task 6: Copilot

**Files:**
- Create: `src/cs/insights/copilot.py`, `src/cs/api/routers/copilot.py`
- Modify: `src/cs/insights/fakes.py` (FakeCopilot already added in Task 4), `src/cs/api/app.py`
- Test: `tests/insights/test_copilot.py`, `tests/api/test_copilot.py`

**Interfaces:**
- `CopilotTools(db, org_id)` with async methods returning JSON-serializable lists/dicts:
  - `metrics_by_creative(creative_id: str, since: str, until: str)`
  - `tags_for_creative(creative_id: str)`
  - `top_creatives_by_dimension(dimension: str, value: str, metric: str, since: str, until: str, limit: int = 10)`
  - `insights(kind: str = "pattern", limit: int = 10)`
  - `fatigue_alerts(limit: int = 10)`
  - `similar_creatives(creative_id: str, k: int = 5)`
- `Copilot` Protocol: `async answer(history: list[dict], tools: CopilotTools) -> str` where history is `[{"role": "user"|"assistant", "content": str}]`.
- `ClaudeCopilot(settings, client=None)` — wraps each `CopilotTools` method in `@beta_async_tool` closures, runs `client.beta.messages.tool_runner(...)`, returns the final text (or a refusal notice).
- Conversation persistence in `copilot.py`: `create_conversation(db, org_id, user_id, title) -> UUID`, `append_message(db, org_id, conversation_id, role, content: str) -> UUID`, `history(db, org_id, conversation_id) -> list[dict]`, `list_conversations(db, org_id, user_id)`.
- Routes: `POST /copilot/conversations` `{title?}` → `{id}`; `GET /copilot/conversations`; `GET /copilot/conversations/{id}` → messages; `POST /copilot/conversations/{id}/messages` `{content}` → `{"reply": str}`.
- Seam: `cs.api.routers.copilot.COPILOT_FACTORY(settings) -> Copilot`.

- [ ] **Step 1: Write failing tests**

`tests/insights/test_copilot.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from cs.insights.copilot import CopilotTools, append_message, create_conversation, history
from tests.insights.seed import seed_account, seed_creative


async def test_tools_return_serializable_data(db, org_id, settings):
    acct = await seed_account(db, org_id, settings)
    today = date(2026, 9, 1)
    cid = await seed_creative(db, org_id, acct, "q1", "image", {"hook_type": "question"},
                              [(today, Decimal("10"), 1000, 20, Decimal("1"), Decimal("40"))])
    tools = CopilotTools(db, org_id)
    m = await tools.metrics_by_creative(str(cid), "2026-08-01", "2026-09-30")
    assert m[0]["day"] == "2026-09-01" and m[0]["ctr"] == 0.02
    assert (await tools.tags_for_creative(str(cid)))[0]["dimension"] == "hook_type"
    top = await tools.top_creatives_by_dimension("hook_type", "question", "ctr", "2026-08-01", "2026-09-30")
    assert top[0]["creative_id"] == str(cid) and top[0]["ctr"] == 0.02
    assert await tools.insights() == [] and await tools.fatigue_alerts() == []
    assert await tools.similar_creatives(str(cid)) == []


async def test_conversation_persistence(db, org_id):
    conv = await create_conversation(db, org_id, "u1", "t")
    await append_message(db, org_id, conv, "user", "hi")
    await append_message(db, org_id, conv, "assistant", "hello")
    assert await history(db, org_id, conv) == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
```

`tests/api/test_copilot.py`:

```python
from cs.api.routers import copilot as copilot_router
from cs.insights.fakes import FakeCopilot
from tests.conftest import dev_headers

H = dev_headers(org="org_cp", user="u1")


async def test_copilot_roundtrip(client, db, monkeypatch):
    monkeypatch.setattr(copilot_router, "COPILOT_FACTORY", lambda s: FakeCopilot())
    conv = (await client.post("/copilot/conversations", headers=H, json={"title": "Q"})).json()["id"]
    r = await client.post(f"/copilot/conversations/{conv}/messages", headers=H, json={"content": "what works?"})
    assert r.status_code == 200 and r.json()["reply"] == "fake answer (0 insights)"
    msgs = (await client.get(f"/copilot/conversations/{conv}", headers=H)).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert (await client.get("/copilot/conversations", headers=dev_headers(org="org_cp", user="u2"))).json() == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/insights/test_copilot.py tests/api/test_copilot.py -v`
Expected: FAIL

- [ ] **Step 3: Implement copilot**

`src/cs/insights/copilot.py`:

```python
from __future__ import annotations

import json
from datetime import date
from typing import Protocol
from uuid import UUID

import anthropic
from anthropic import beta_async_tool

from cs.core.config import Settings
from cs.core.db import Database
from cs.core.ids import new_id
from cs.ingest import repo as ingest_repo
from cs.insights import repo as insight_repo
from cs.insights.patterns import metrics_from_sums
from cs.tagging import repo as tag_repo

SYSTEM = ("You are the Copilot for a paid-social creative team. Answer questions about this ad account using the tools. "
          "Always call a tool before stating numbers. Be concise, cite the creative ids you used, and say when data is "
          "insufficient. Dates are ISO strings. Metrics: ctr, cpa, roas.")


def _jsonable(v):
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, date):
        return v.isoformat()
    if hasattr(v, "as_integer_ratio") and not isinstance(v, (int, float)):
        return float(v)
    return v


def _rows(rows) -> list[dict]:
    return [{k: _jsonable(v) for k, v in dict(r).items()} for r in rows]


class CopilotTools:
    def __init__(self, db: Database, org_id: UUID):
        self.db = db
        self.org_id = org_id

    async def metrics_by_creative(self, creative_id: str, since: str, until: str) -> list[dict]:
        rows = await ingest_repo.creative_metrics(self.db, self.org_id, UUID(creative_id), date.fromisoformat(since), date.fromisoformat(until))
        out = []
        for r in rows:
            m = metrics_from_sums(r["spend"], int(r["impressions"]), int(r["clicks"]), r["conversions"], r["conversion_value"])
            out.append({"day": r["day"].isoformat(), "spend": float(r["spend"]), "impressions": int(r["impressions"]),
                        "clicks": int(r["clicks"]), "conversions": float(r["conversions"]), **m})
        return out

    async def tags_for_creative(self, creative_id: str) -> list[dict]:
        return _rows(await tag_repo.latest_tags(self.db, self.org_id, UUID(creative_id)))

    async def top_creatives_by_dimension(self, dimension: str, value: str, metric: str, since: str, until: str, limit: int = 10) -> list[dict]:
        rows = await self.db.fetch(
            """
            SELECT c.id AS creative_id, c.headline, c.format, sum(m.spend) AS spend, sum(m.impressions) AS imp,
                   sum(m.clicks) AS clicks, sum(m.conversions) AS conv, sum(m.conversion_value) AS val
            FROM creative_latest_tag lt JOIN creative c ON c.id = lt.creative_id
            JOIN metric_daily m ON m.creative_id = c.id AND m.day BETWEEN $4 AND $5
            WHERE lt.org_id = $1 AND lt.dimension = $2 AND lt.value = $3
            GROUP BY c.id, c.headline, c.format
            """, self.org_id, dimension, value, date.fromisoformat(since), date.fromisoformat(until))
        out = []
        for r in rows:
            m = metrics_from_sums(r["spend"], int(r["imp"]), int(r["clicks"]), r["conv"], r["val"])
            out.append({"creative_id": str(r["creative_id"]), "headline": r["headline"], "format": r["format"],
                        "spend": float(r["spend"]), **m})
        key = (lambda d: d[metric] if d[metric] is not None else float("inf")) if metric == "cpa" else \
              (lambda d: -(d[metric] or 0))
        return sorted(out, key=key)[: min(limit, 50)]

    async def insights(self, kind: str = "pattern", limit: int = 10) -> list[dict]:
        return _rows(await insight_repo.list_insights(self.db, self.org_id, kind, None, min(limit, 50)))

    async def fatigue_alerts(self, limit: int = 10) -> list[dict]:
        return _rows(await insight_repo.list_insights(self.db, self.org_id, "fatigue", None, min(limit, 50)))

    async def similar_creatives(self, creative_id: str, k: int = 5) -> list[dict]:
        return _rows(await tag_repo.similar_creatives(self.db, self.org_id, UUID(creative_id), min(k, 20)))


class Copilot(Protocol):
    async def answer(self, history: list[dict], tools: CopilotTools) -> str: ...


class ClaudeCopilot:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.model = settings.claude_model_copilot

    def _tools(self, t: CopilotTools) -> list:
        @beta_async_tool
        async def metrics_by_creative(creative_id: str, since: str, until: str) -> str:
            """Daily spend, impressions, clicks, conversions, ctr, cpa, roas for one creative.

            Args:
                creative_id: The creative UUID.
                since: ISO date, inclusive.
                until: ISO date, inclusive.
            """
            return json.dumps(await t.metrics_by_creative(creative_id, since, until))

        @beta_async_tool
        async def tags_for_creative(creative_id: str) -> str:
            """Latest structured tags (dimension, value, confidence, evidence) for one creative.

            Args:
                creative_id: The creative UUID.
            """
            return json.dumps(await t.tags_for_creative(creative_id))

        @beta_async_tool
        async def top_creatives_by_dimension(dimension: str, value: str, metric: str, since: str, until: str, limit: int = 10) -> str:
            """Creatives carrying a tag value, ranked by a metric over a date window.

            Args:
                dimension: Tag dimension name, e.g. hook_type.
                value: Tag value, e.g. question.
                metric: One of ctr, cpa, roas.
                since: ISO date, inclusive.
                until: ISO date, inclusive.
                limit: Max rows.
            """
            return json.dumps(await t.top_creatives_by_dimension(dimension, value, metric, since, until, limit))

        @beta_async_tool
        async def insights(kind: str = "pattern", limit: int = 10) -> str:
            """Stored insights with explanations.

            Args:
                kind: pattern or fatigue.
                limit: Max rows.
            """
            return json.dumps(await t.insights(kind, limit))

        @beta_async_tool
        async def fatigue_alerts(limit: int = 10) -> str:
            """Creatives whose CTR or CPA decayed versus their first 14 days.

            Args:
                limit: Max rows.
            """
            return json.dumps(await t.fatigue_alerts(limit))

        @beta_async_tool
        async def similar_creatives(creative_id: str, k: int = 5) -> str:
            """Nearest creatives by tag embedding.

            Args:
                creative_id: The creative UUID.
                k: Number of neighbours.
            """
            return json.dumps(await t.similar_creatives(creative_id, k))

        return [metrics_by_creative, tags_for_creative, top_creatives_by_dimension, insights, fatigue_alerts, similar_creatives]

    async def answer(self, history: list[dict], tools: CopilotTools) -> str:
        runner = self.client.beta.messages.tool_runner(
            model=self.model, max_tokens=8000, system=SYSTEM, tools=self._tools(tools),
            messages=[{"role": m["role"], "content": m["content"]} for m in history],
        )
        last = None
        async for message in runner:
            last = message
        if last is None:
            return "I could not produce an answer."
        if last.stop_reason == "refusal":
            return "I can't help with that request."
        return "".join(b.text for b in last.content if getattr(b, "type", "") == "text").strip()


async def create_conversation(db: Database, org_id: UUID, user_id: str, title: str) -> UUID:
    cid = new_id()
    await db.execute("INSERT INTO copilot_conversation (id, org_id, user_id, title) VALUES ($1,$2,$3,$4)", cid, org_id, user_id, title)
    return cid


async def append_message(db: Database, org_id: UUID, conversation_id: UUID, role: str, content: str) -> UUID:
    mid = new_id()
    await db.execute("INSERT INTO copilot_message (id, org_id, conversation_id, role, content) VALUES ($1,$2,$3,$4,$5)",
                     mid, org_id, conversation_id, role, {"text": content})
    return mid


async def history(db: Database, org_id: UUID, conversation_id: UUID) -> list[dict]:
    rows = await db.fetch("SELECT role, content FROM copilot_message WHERE org_id=$1 AND conversation_id=$2 ORDER BY created_at, id",
                          org_id, conversation_id)
    return [{"role": r["role"], "content": r["content"]["text"]} for r in rows]


async def list_conversations(db: Database, org_id: UUID, user_id: str) -> list[dict]:
    rows = await db.fetch("SELECT id, title, created_at FROM copilot_conversation WHERE org_id=$1 AND user_id=$2 ORDER BY created_at DESC",
                          org_id, user_id)
    return [{"id": str(r["id"]), "title": r["title"], "created_at": r["created_at"].isoformat()} for r in rows]
```

- [ ] **Step 4: Implement router**

`src/cs/api/routers/copilot.py`:

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_db, get_settings_dep
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights import copilot

router = APIRouter(prefix="/copilot")


def default_copilot_factory(settings: Settings) -> copilot.Copilot:
    return copilot.ClaudeCopilot(settings)


COPILOT_FACTORY = default_copilot_factory


class NewConversation(BaseModel):
    title: str = "New conversation"


class NewMessage(BaseModel):
    content: str


@router.post("/conversations")
async def create(body: NewConversation, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    return {"id": str(await copilot.create_conversation(db, auth.org_id, auth.user_id, body.title))}


@router.get("/conversations")
async def list_convs(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    return await copilot.list_conversations(db, auth.org_id, auth.user_id)


async def _owned(db: Database, auth: AuthContext, conversation_id: UUID) -> None:
    ok = await db.fetchval("SELECT 1 FROM copilot_conversation WHERE id=$1 AND org_id=$2 AND user_id=$3",
                           conversation_id, auth.org_id, auth.user_id)
    if not ok:
        raise HTTPException(404)


@router.get("/conversations/{conversation_id}")
async def messages(conversation_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    await _owned(db, auth, conversation_id)
    return await copilot.history(db, auth.org_id, conversation_id)


@router.post("/conversations/{conversation_id}/messages")
async def send(conversation_id: UUID, body: NewMessage, auth: AuthContext = Depends(current_auth),
               db: Database = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> dict:
    await _owned(db, auth, conversation_id)
    await copilot.append_message(db, auth.org_id, conversation_id, "user", body.content)
    hist = await copilot.history(db, auth.org_id, conversation_id)
    reply = await COPILOT_FACTORY(settings).answer(hist, copilot.CopilotTools(db, auth.org_id))
    await copilot.append_message(db, auth.org_id, conversation_id, "assistant", reply)
    return {"reply": reply}
```

Include the router in `src/cs/api/app.py`.

- [ ] **Step 5: Run the suite, then commit**

Run: `make lint && make test`
Expected: PASS

```bash
git add src/cs/insights/copilot.py src/cs/api tests/insights/test_copilot.py tests/api/test_copilot.py
git commit -m "feat(copilot): tool-backed Claude copilot with persisted conversations"
```

---

## Self-review

- **Spec coverage:** pattern insights with thresholds (N=5, S=$1000, 30 days) → Task 2; fatigue (7d vs first 14d, 30%) → Task 3; LLM explanation + suggested test → Task 4; weekly brief Monday job → Task 5; Copilot with the six named read-only tools, no model SQL, history per user → Task 6.
- **Deviation:** the spec's frontend section mentions streaming Copilot replies. This phase returns the complete reply per request; Phase 5 shows a pending state. Streaming is a follow-up once the loop is closed.
- **Type consistency:** `InsightDraft` field order matches every positional construction in tests. `metrics_from_sums` / `signed_lift` are shared by patterns, fatigue, and copilot. `list_insights(db, org_id, kind, ad_account_id, limit)` positional order is consistent across jobs, brief, copilot, and routers. Seams: `EXPLAINER_FACTORY(settings)`, `COPILOT_FACTORY(settings)`.
