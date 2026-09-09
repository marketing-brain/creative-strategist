from __future__ import annotations

import json
from datetime import date
from typing import Protocol
from uuid import UUID

import openai

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
            JOIN metric_daily m ON m.creative_id = c.id AND m.org_id = $1
                AND m.day BETWEEN $4 AND $5
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


class Copilot(Protocol):
    async def answer(self, history: list[dict], tools: CopilotTools) -> str: ...


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "metrics_by_creative",
            "description": (
                "Daily spend, impressions, clicks, conversions, ctr, cpa, roas for one creative."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "creative_id": {"type": "string", "description": "The creative UUID."},
                    "since": {"type": "string", "description": "ISO date, inclusive."},
                    "until": {"type": "string", "description": "ISO date, inclusive."},
                },
                "required": ["creative_id", "since", "until"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tags_for_creative",
            "description": (
                "Latest structured tags (dimension, value, confidence, evidence) for one creative."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "creative_id": {"type": "string", "description": "The creative UUID."}
                },
                "required": ["creative_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_creatives_by_dimension",
            "description": "Creatives carrying a tag value, ranked by a metric over a date window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "description": "Tag dimension name, e.g. hook_type.",
                    },
                    "value": {"type": "string", "description": "Tag value, e.g. question."},
                    "metric": {"type": "string", "description": "One of ctr, cpa, roas."},
                    "since": {"type": "string", "description": "ISO date, inclusive."},
                    "until": {"type": "string", "description": "ISO date, inclusive."},
                    "limit": {"type": "integer", "description": "Max rows."},
                },
                "required": ["dimension", "value", "metric", "since", "until"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insights",
            "description": "Stored insights with explanations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "pattern or fatigue."},
                    "limit": {"type": "integer", "description": "Max rows."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fatigue_alerts",
            "description": "Creatives whose CTR or CPA decayed versus their first 14 days.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "Max rows."}},
                "required": [],
            },
        },
    },
]

MAX_TOOL_ITERATIONS = 10


class GeminiGatewayCopilot:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.copilot_model

    async def answer(self, history: list[dict], tools: CopilotTools) -> str:
        system = f"{SYSTEM} Today's date is {date.today().isoformat()}."
        messages: list[dict] = [{"role": "system", "content": system}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history]

        for i in range(MAX_TOOL_ITERATIONS):
            last_iteration = i == MAX_TOOL_ITERATIONS - 1
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=None if last_iteration else TOOL_SPECS,
            )
            choice = resp.choices[0]
            if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
                return "I can't help with that request."
            if not choice.message.tool_calls:
                return (choice.message.content or "").strip() or "I could not produce an answer."

            messages.append(
                {
                    "role": "assistant",
                    "content": choice.message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in choice.message.tool_calls
                    ],
                }
            )
            for tc in choice.message.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                method = getattr(tools, tc.function.name, None)
                try:
                    result = await method(**args) if method else {"error": "unknown tool"}
                except Exception as exc:
                    result = {"error": str(exc)}
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)}
                )
        return "I could not produce an answer."


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
