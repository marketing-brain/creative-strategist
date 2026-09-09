from __future__ import annotations

import json
from typing import Protocol

import openai

from cs.core.config import Settings
from cs.insights.types import InsightDraft

SYSTEM = (
    "You are a performance creative strategist writing for a growth team. Explain one data "
    "finding in plain language in two or three sentences, name the likely reason, and propose "
    "one concrete next creative test. Do not invent numbers beyond those given."
)

SCHEMA = {
    "type": "object",
    "properties": {"explanation": {"type": "string"}, "suggested_test": {"type": "string"}},
    "required": ["explanation", "suggested_test"],
    "additionalProperties": False,
}


def _template(d: InsightDraft) -> tuple[str, str]:
    direction = "better" if d.lift > 0 else "worse"
    pct = f"{abs(d.lift) * 100:.0f}%"
    if d.kind == "fatigue":
        return (
            f"This creative's {d.metric.upper()} is {pct} worse over the last 7 days than in "
            "its first 14 days.",
            "Refresh the hook and first frame while keeping the winning offer and message.",
        )
    return (
        f"Creatives tagged {d.dimension}={d.value} performed {pct} {direction} on "
        f"{d.metric.upper()} than the account average across {d.sample_creatives} creatives "
        f"and ${d.sample_spend:.0f} of spend.",
        f"Launch two new variants that lean into {d.dimension}={d.value} and compare against "
        "the current best.",
    )


def _user(d: InsightDraft, ctx: dict) -> str:
    return json.dumps(
        {
            "kind": d.kind,
            "dimension": d.dimension,
            "dimension_description": ctx.get("dimension_description"),
            "value": d.value,
            "metric": d.metric,
            "baseline": d.baseline,
            "observed": d.observed,
            "lift": d.lift,
            "sample_creatives": d.sample_creatives,
            "sample_spend": float(d.sample_spend),
            "window": [d.window_start.isoformat(), d.window_end.isoformat()],
            "creative_headline": ctx.get("creative_headline"),
            "account_name": ctx.get("account_name"),
        }
    )


class Explainer(Protocol):
    async def explain(self, draft: InsightDraft, context: dict) -> tuple[str, str]: ...


class GeminiGatewayExplainer:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.insights_model

    async def explain(self, draft: InsightDraft, context: dict) -> tuple[str, str]:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": _user(draft, context)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "explanation", "schema": SCHEMA, "strict": True},
            },
        )
        choice = resp.choices[0]
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            return _template(draft)
        text = choice.message.content or ""
        data = json.loads(text) if text else {}
        return data.get("explanation") or _template(draft)[0], data.get(
            "suggested_test"
        ) or _template(draft)[1]
