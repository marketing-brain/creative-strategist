from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Protocol

import openai

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
        self,
        images: list[tuple[bytes, str]],
        user_text: str,
        schema: dict,
        video: tuple[bytes, str] | None = None,
    ) -> TagResult: ...


def build_content(
    images: list[tuple[bytes, str]], user_text: str, video: tuple[bytes, str] | None = None
) -> list[dict]:
    """OpenAI-compatible chat-completions content blocks (media first, text last) -- the
    shape AI Gateway expects regardless of which provider/model is behind it. `video`
    uses the generic `file` block (not `image_url`) -- confirmed against a real AI
    Gateway call routed to google/gemini-2.5-flash: the model processes both the video's
    frames and its audio track from this one block, no separate transcription needed."""
    content: list[dict] = []
    for data, mime in images:
        b64 = base64.standard_b64encode(data).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    if video is not None:
        data, mime = video
        b64 = base64.standard_b64encode(data).decode()
        content.append({"type": "file", "file": {"data": b64, "media_type": mime}})
    content.append({"type": "text", "text": user_text})
    return content


class GeminiGatewayTagger:
    def __init__(
        self,
        settings: Settings,
        client: openai.AsyncOpenAI | None = None,
        model: str | None = None,
    ):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = model or settings.tagging_model
        self.video_model = settings.video_tagging_model

    async def tag(
        self,
        images: list[tuple[bytes, str]],
        user_text: str,
        schema: dict,
        video: tuple[bytes, str] | None = None,
    ) -> TagResult:
        from cs.tagging.prompts import SYSTEM

        resp = await self.client.chat.completions.create(
            model=self.video_model if video is not None else self.model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_content(images, user_text, video)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "tags", "schema": schema, "strict": True},
            },
        )
        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        result = TagResult(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=resp.model,
        )
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            result.refused = True
            return result
        text = choice.message.content or ""
        result.tags = json.loads(text).get("tags", []) if text else []
        return result
