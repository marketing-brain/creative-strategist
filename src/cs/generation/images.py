from __future__ import annotations

import base64
import io
from typing import Protocol

import openai

from cs.core.config import Settings


class ImageGenerator(Protocol):
    async def generate(
        self, prompt: str, reference_images: list[bytes], n: int = 1
    ) -> list[bytes]: ...


class GatewayImageGenerator:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.image_model

    async def generate(self, prompt: str, reference_images: list[bytes], n: int = 1) -> list[bytes]:
        if reference_images:
            files = []
            for i, data in enumerate(reference_images[:4]):
                f = io.BytesIO(data)
                f.name = f"ref{i}.png"
                files.append(f)
            result = await self.client.images.edit(
                model=self.model, image=files, prompt=prompt, n=n, response_format="b64_json"
            )
        else:
            result = await self.client.images.generate(
                model=self.model, prompt=prompt, n=n, response_format="b64_json"
            )
        return [base64.b64decode(d.b64_json) for d in result.data if getattr(d, "b64_json", None)]
