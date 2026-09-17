"""Text embeddings through the AI Gateway's OpenAI-compatible endpoint, plus a
deterministic fake for tests and a cosine helper."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

import openai

from cs.core.config import Settings

BATCH = 100


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class GatewayEmbedder:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            chunk = texts[start : start + BATCH]
            resp = await self.client.embeddings.create(
                model=self.model, input=chunk, dimensions=self.dimensions
            )
            out.extend([list(d.embedding) for d in resp.data])
        return out


class FakeEmbedder:
    """Deterministic vectors of the configured width from character trigram hashes:
    identical text embeds identically and texts sharing trigrams are closer than
    unrelated ones. No network."""

    def __init__(self, seed: int = 0, dims: int = 1536):
        self.seed = seed
        self.dims = dims

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            v = [0.0] * self.dims
            padded = f"  {text.lower()}  "
            for i in range(len(padded) - 2):
                h = hashlib.blake2b(
                    f"{self.seed}:{padded[i : i + 3]}".encode(), digest_size=3
                ).digest()
                v[int.from_bytes(h[:2], "big") % self.dims] += 1.0 if h[2] % 2 else -1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v])
        return vectors


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
