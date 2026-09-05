from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import anthropic

from cs.core.config import Settings
from cs.generation.brand import BrandKit

CTA_TYPES = ["SHOP_NOW", "LEARN_MORE", "SIGN_UP", "GET_OFFER", "DOWNLOAD", "BOOK_NOW", "SUBSCRIBE"]

WRITE_SCHEMA = {"type": "object", "properties": {"sets": {"type": "array", "items": {
    "type": "object", "properties": {"headline": {"type": "string"}, "primary_text": {"type": "string"},
                                     "cta_type": {"type": "string", "enum": CTA_TYPES}},
    "required": ["headline", "primary_text", "cta_type"], "additionalProperties": False}}},
    "required": ["sets"], "additionalProperties": False}
CHECK_SCHEMA = {"type": "object", "properties": {"approved": {"type": "array", "items": {"type": "boolean"}}},
                "required": ["approved"], "additionalProperties": False}

WRITE_SYSTEM = ("You write paid social ad copy for Meta. Headlines under 40 characters, primary text under 125 characters, "
                "one clear idea each, no emojis unless the voice rules ask for them, no claims you cannot support.")
CHECK_SYSTEM = ("You are a brand compliance reviewer. Approve a copy set only if it follows the voice rules and contains "
                "none of the prohibited claims, in spirit as well as wording.")


@dataclass
class CopySet:
    headline: str
    primary_text: str
    cta_type: str


def contains_prohibited(text: str, prohibited: list[str]) -> bool:
    low = text.lower()
    return any(p.lower() in low for p in prohibited if p.strip())


class CopyWriter(Protocol):
    async def write(self, brief: str, kit: BrandKit, n: int) -> list[CopySet]: ...


class ClaudeCopyWriter:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
        self.model = settings.claude_model_copy

    async def _call(self, system: str, user: str, schema: dict) -> dict:
        resp = await self.client.beta.messages.create(
            model=self.model, max_tokens=4000, system=system,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}})
        if resp.stop_reason == "refusal":
            return {}
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        return json.loads(text) if text else {}

    async def write(self, brief: str, kit: BrandKit, n: int) -> list[CopySet]:
        user = (f"Brief:\n{brief}\n\nVoice rules:\n{kit.voice_rules}\n\nProhibited claims (never use or imply): "
                f"{', '.join(kit.prohibited_claims) or 'none'}\n\nWrite {n} distinct copy sets.")
        data = await self._call(WRITE_SYSTEM, user, WRITE_SCHEMA)
        sets = [CopySet(s["headline"], s["primary_text"], s["cta_type"]) for s in data.get("sets", [])]
        sets = [s for s in sets if not contains_prohibited(f"{s.headline} {s.primary_text}", kit.prohibited_claims)]
        if not sets:
            return []
        check_user = json.dumps({"voice_rules": kit.voice_rules, "prohibited_claims": kit.prohibited_claims,
                                 "sets": [s.__dict__ for s in sets]})
        verdict = await self._call(CHECK_SYSTEM, check_user, CHECK_SCHEMA)
        approved = verdict.get("approved", [True] * len(sets))
        return [s for s, ok in zip(sets, approved, strict=False) if ok]
