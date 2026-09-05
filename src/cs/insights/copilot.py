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

SYSTEM = (
    "You are the Copilot for a paid-social creative team. Answer questions about this ad "
    "account using the tools. Always call a tool before stating numbers. Be concise, cite "
    "the creative ids you used, and say when data is insufficient. Dates are ISO strings. "
    "Metrics: ctr, cpa, roas."
)


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
        rows = await ingest_repo.creative_metrics(
            self.db,
            self.org_id,
            UUID(creative_id),
            date.fromisoformat(since),
            date.fromisoformat(until),
        )
        out = []
        for r in rows:
            m = metrics_from_sums(
                r["spend"],
                int(r["impressions"]),
                int(r["clicks"]),
                r["conversions"],
                r["conversion_value"],
            )
            out.append(
                {
                    "day": r["day"].isoformat(),
                    "spend": float(r["spend"]),
                    "impressions": int(r["impressions"]),
                    "clicks": int(r["clicks"]),
                    "conversions": float(r["conversions"]),
                    **m,
                }
            )
        return out

    async def tags_for_creative(self, creative_id: str) -> list[dict]:
        return _rows(await tag_repo.latest_tags(self.db, self.org_id, UUID(creative_id)))

    async def top_creatives_by_dimension(
        self, dimension: str, value: str, metric: str, since: str, until: str, limit: int = 10
    ) -> list[dict]:
        rows = await self.db.fetch(
            """
            SELECT c.id AS creative_id, c.headline, c.format, sum(m.spend) AS spend,
                   sum(m.impressions) AS imp, sum(m.clicks) AS clicks, sum(m.conversions) AS conv,
                   sum(m.conversion_value) AS val
            FROM creative_latest_tag lt JOIN creative c ON c.id = lt.creative_id
            JOIN metric_daily m ON m.creative_id = c.id AND m.day BETWEEN $4 AND $5
            WHERE lt.org_id = $1 AND lt.dimension = $2 AND lt.value = $3
            GROUP BY c.id, c.headline, c.format
            """,
            self.org_id,
            dimension,
            value,
            date.fromisoformat(since),
            date.fromisoformat(until),
        )
        out = []
        for r in rows:
            m = metrics_from_sums(r["spend"], int(r["imp"]), int(r["clicks"]), r["conv"], r["val"])
            out.append(
                {
                    "creative_id": str(r["creative_id"]),
                    "headline": r["headline"],
                    "format": r["format"],
                    "spend": float(r["spend"]),
                    **m,
                }
            )
        key = (
            (lambda d: d[metric] if d[metric] is not None else float("inf"))
            if metric == "cpa"
            else (lambda d: -(d[metric] or 0))
        )
        return sorted(out, key=key)[: min(limit, 50)]

    async def insights(self, kind: str = "pattern", limit: int = 10) -> list[dict]:
        return _rows(
            await insight_repo.list_insights(self.db, self.org_id, kind, None, min(limit, 50))
        )

    async def fatigue_alerts(self, limit: int = 10) -> list[dict]:
        return _rows(
            await insight_repo.list_insights(self.db, self.org_id, "fatigue", None, min(limit, 50))
        )

    async def similar_creatives(self, creative_id: str, k: int = 5) -> list[dict]:
        return _rows(
            await tag_repo.similar_creatives(self.db, self.org_id, UUID(creative_id), min(k, 20))
        )


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
        async def top_creatives_by_dimension(
            dimension: str, value: str, metric: str, since: str, until: str, limit: int = 10
        ) -> str:
            """Creatives carrying a tag value, ranked by a metric over a date window.

            Args:
                dimension: Tag dimension name, e.g. hook_type.
                value: Tag value, e.g. question.
                metric: One of ctr, cpa, roas.
                since: ISO date, inclusive.
                until: ISO date, inclusive.
                limit: Max rows.
            """
            return json.dumps(
                await t.top_creatives_by_dimension(dimension, value, metric, since, until, limit)
            )

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

        return [
            metrics_by_creative,
            tags_for_creative,
            top_creatives_by_dimension,
            insights,
            fatigue_alerts,
            similar_creatives,
        ]

    async def answer(self, history: list[dict], tools: CopilotTools) -> str:
        runner = self.client.beta.messages.tool_runner(
            model=self.model,
            max_tokens=8000,
            system=SYSTEM,
            tools=self._tools(tools),
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
    await db.execute(
        "INSERT INTO copilot_conversation (id, org_id, user_id, title) VALUES ($1,$2,$3,$4)",
        cid,
        org_id,
        user_id,
        title,
    )
    return cid


async def append_message(
    db: Database, org_id: UUID, conversation_id: UUID, role: str, content: str
) -> UUID:
    mid = new_id()
    await db.execute(
        "INSERT INTO copilot_message (id, org_id, conversation_id, role, content) "
        "VALUES ($1,$2,$3,$4,$5)",
        mid,
        org_id,
        conversation_id,
        role,
        {"text": content},
    )
    return mid


async def history(db: Database, org_id: UUID, conversation_id: UUID) -> list[dict]:
    rows = await db.fetch(
        "SELECT role, content FROM copilot_message WHERE org_id=$1 AND conversation_id=$2 "
        "ORDER BY created_at, id",
        org_id,
        conversation_id,
    )
    return [{"role": r["role"], "content": r["content"]["text"]} for r in rows]


async def list_conversations(db: Database, org_id: UUID, user_id: str) -> list[dict]:
    rows = await db.fetch(
        "SELECT id, title, created_at FROM copilot_conversation WHERE org_id=$1 AND user_id=$2 "
        "ORDER BY created_at DESC",
        org_id,
        user_id,
    )
    return [
        {"id": str(r["id"]), "title": r["title"], "created_at": r["created_at"].isoformat()}
        for r in rows
    ]
