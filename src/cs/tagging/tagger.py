from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Protocol

import anthropic

from cs.core.config import Settings


@dataclass
class TagResult:
    tags: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    refused: bool = False


class Tagger(Protocol):
    async def tag(
        self, images: list[tuple[bytes, str]], user_text: str, schema: dict
    ) -> TagResult: ...


def build_content(images: list[tuple[bytes, str]], user_text: str) -> list[dict]:
    content: list[dict] = []
    for data, mime in images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.standard_b64encode(data).decode(),
                },
            }
        )
    content.append({"type": "text", "text": user_text})
    return content


class ClaudeTagger:
    def __init__(
        self,
        settings: Settings,
        client: anthropic.AsyncAnthropic | None = None,
        model: str | None = None,
    ):
        self.client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
        self.model = model or settings.claude_model_tagging

    async def tag(self, images: list[tuple[bytes, str]], user_text: str, schema: dict) -> TagResult:
        from cs.tagging.prompts import SYSTEM

        resp = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            messages=[{"role": "user", "content": build_content(images, user_text)}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        usage = getattr(resp, "usage", None)
        result = TagResult(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            model=resp.model,
        )
        if resp.stop_reason == "refusal":
            result.refused = True
            return result
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        result.tags = json.loads(text).get("tags", []) if text else []
        return result
