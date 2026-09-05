from __future__ import annotations

from typing import Protocol

import httpx

from cs.core.config import Settings


def tag_text(copy: dict, tags) -> str:
    pairs = sorted(f"{t['dimension']}={t['value']}" for t in tags)
    parts = ["; ".join(pairs)]
    if copy.get("headline"):
        parts.append(f"headline: {copy['headline']}")
    if copy.get("primary_text"):
        parts.append(f"text: {str(copy['primary_text'])[:500]}")
    return "\n".join(parts)


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class GatewayEmbedder:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http

    async def embed(self, texts: list[str]) -> list[list[float]]:
        r = await self.http.post(
            f"{self.settings.ai_gateway_base_url}/v1/embeddings",
            headers={"Authorization": f"Bearer {self.settings.ai_gateway_api_key}"},
            json={"model": self.settings.embedding_model, "input": texts},
            timeout=60,
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]
