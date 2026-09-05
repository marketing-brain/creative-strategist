from __future__ import annotations

import json
from typing import Protocol

import anthropic

from cs.core.config import Settings
from cs.tagging.prompts import SYSTEM
from cs.tagging.tagger import build_content


class BatchTagger(Protocol):
    async def submit(self, requests: list[dict]) -> str: ...
    async def poll(self, batch_id: str) -> tuple[bool, list[dict]]: ...


class ClaudeBatchTagger:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
        self.model = settings.claude_model_tagging

    async def submit(self, requests: list[dict]) -> str:
        payload = [
            {
                "custom_id": r["custom_id"],
                "params": {
                    "model": self.model,
                    "max_tokens": 16000,
                    "system": SYSTEM,
                    "messages": [
                        {"role": "user", "content": build_content(r["images"], r["user_text"])}
                    ],
                    "output_config": {"format": {"type": "json_schema", "schema": r["schema"]}},
                },
            }
            for r in requests
        ]
        batch = await self.client.messages.batches.create(requests=payload)
        return batch.id

    async def poll(self, batch_id: str) -> tuple[bool, list[dict]]:
        batch = await self.client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            return False, []
        out: list[dict] = []
        async for item in await self.client.messages.batches.results(batch_id):
            res = item.result
            if res.type == "succeeded" and res.message.stop_reason != "refusal":
                text = next(
                    (b.text for b in res.message.content if getattr(b, "type", "") == "text"),
                    "{}",
                )
                out.append(
                    {
                        "custom_id": item.custom_id,
                        "ok": True,
                        "tags": json.loads(text).get("tags", []),
                        "input_tokens": res.message.usage.input_tokens,
                        "output_tokens": res.message.usage.output_tokens,
                        "error": None,
                    }
                )
            else:
                err = (
                    "refusal"
                    if res.type == "succeeded"
                    else f"{res.type}: {getattr(res, 'error', '')}"
                )
                out.append(
                    {
                        "custom_id": item.custom_id,
                        "ok": False,
                        "tags": [],
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "error": err,
                    }
                )
        return True, out
