"""Seedance 2.5 video editing through AI Gateway: the opening clip is [Video 1], the prompt says the
one thing to change and everything to keep (ByteDance's edit structure; see cs.generate.wavespeed
for the wording's sources). The start carries an idempotency key, so a repeated start returns the
same job instead of billing twice; status checks are free."""

from __future__ import annotations

import asyncio
import base64
import time

import httpx

MODEL = "bytedance/seedance-2.5"
# Vertical sizes by label; output follows the input's orientation.
SIZES = {"480p": (480, 854), "720p": (720, 1280), "1080p": (1080, 1920)}


def _headers(settings) -> dict:
    return {
        "Authorization": f"Bearer {settings.ai_gateway_api_key}",
        "ai-gateway-protocol-version": "0.0.1",
        "ai-video-model-specification-version": "4",
        "ai-model-id": MODEL,
        "Content-Type": "application/json",
    }


def size(resolution: str, width: int, height: int) -> tuple[str, str]:
    """(resolution, aspect ratio) matching the input's orientation."""
    w, h = SIZES[resolution]
    if width > height:
        return f"{h}x{w}", "16:9"
    if width == height:
        return f"{w}x{w}", "1:1"
    return f"{w}x{h}", "9:16"


async def submit(settings, video_url: str, prompt: str, resolution: str, width: int, height: int, key: str) -> dict:
    res, ratio = size(resolution, width, height)
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{settings.ai_gateway_base_url}/v4/ai/video-model/start",
            headers={**_headers(settings), "idempotency-key": key},
            json={
                "prompt": prompt,
                "n": 1,
                "aspectRatio": ratio,
                "resolution": res,
                # The original sound is laid back over the edit, so the model makes none.
                "generateAudio": False,
                "inputReferences": [{"type": "url", "url": video_url, "mediaType": "video/mp4"}],
            },
        )
    if r.status_code >= 400:
        raise RuntimeError(f"the video model refused it ({r.status_code}): {r.text[:300]}")
    return r.json()["operation"]


async def wait(settings, operation: dict, timeout_s: int = 1500) -> bytes:
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=120) as c:
        while time.monotonic() - t0 < timeout_s:
            await asyncio.sleep(10)
            st = await c.post(
                f"{settings.ai_gateway_base_url}/v4/ai/video-model/status",
                headers=_headers(settings),
                json={"operation": operation},
            )
            if st.status_code != 200:
                continue
            j = st.json()
            if j["status"] == "completed":
                v = j["videos"][0]
                return (await c.get(v["url"])).content if v["type"] == "url" else base64.b64decode(v["data"])
            if j["status"] == "error":
                raise RuntimeError(f"the video model failed: {str(j.get('error'))[:300]}")
    raise RuntimeError("still editing after 25 minutes")
