from __future__ import annotations

import asyncio
import json
from typing import Protocol
from uuid import UUID

import openai

from cs.core.config import Settings
from cs.core.db import Database
from cs.core.ids import new_id
from cs.tagging.prompts import SYSTEM
from cs.tagging.tagger import build_content


class BatchTagger(Protocol):
    async def submit(self, requests: list[dict]) -> str: ...
    async def poll(self, batch_id: str) -> tuple[bool, list[dict]]: ...


class GeminiGatewayBatchTagger:
    """AI Gateway has no async batch endpoint for Gemini, so submit() runs every
    request concurrently right away (bounded, so a big backfill doesn't fire
    hundreds of requests at once) and persists the results to
    tag_batch_provider_result under a fresh id; poll() -- a separate job, on a
    separate instance -- reads them back by that id."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        org_id: UUID,
        client: openai.AsyncOpenAI | None = None,
        concurrency: int = 8,
    ):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.tagging_model
        self.db = db
        self.org_id = org_id
        self._sem = asyncio.Semaphore(concurrency)

    async def _tag_one(self, r: dict) -> dict:
        try:
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": build_content(r["images"], r["user_text"])},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "tags", "schema": r["schema"], "strict": True},
                },
            )
            choice = resp.choices[0]
            if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
                return {
                    "custom_id": r["custom_id"],
                    "ok": False,
                    "tags": [],
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "error": "refusal",
                }
            text = choice.message.content or ""
            usage = resp.usage
            return {
                "custom_id": r["custom_id"],
                "ok": True,
                "tags": json.loads(text).get("tags", []) if text else [],
                "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "error": None,
            }
        except Exception as exc:
            return {
                "custom_id": r["custom_id"],
                "ok": False,
                "tags": [],
                "input_tokens": 0,
                "output_tokens": 0,
                "error": str(exc),
            }

    async def submit(self, requests: list[dict]) -> str:
        async def bounded(r: dict) -> dict:
            async with self._sem:
                return await self._tag_one(r)

        results = await asyncio.gather(*(bounded(r) for r in requests))
        result_id = new_id()
        await self.db.execute(
            "INSERT INTO tag_batch_provider_result (id, org_id, results) VALUES ($1,$2,$3)",
            result_id,
            self.org_id,
            list(results),
        )
        return str(result_id)

    async def poll(self, batch_id: str) -> tuple[bool, list[dict]]:
        row = await self.db.fetchrow(
            "SELECT results FROM tag_batch_provider_result WHERE id=$1", UUID(batch_id)
        )
        if row is None:
            return False, []
        return True, row["results"]
