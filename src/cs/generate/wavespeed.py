"""WaveSpeed's Seedance 2.5 video-edit endpoint: submit once, then check status (free)."""

from __future__ import annotations

import asyncio
import time

import httpx

API = "https://api.wavespeed.ai/api/v3"
EDIT_MODEL = "bytedance/seedance-2.5/video-edit"


async def submit(token: str, video_url: str, prompt: str, resolution: str) -> str:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{API}/{EDIT_MODEL}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "video": video_url,
                "prompt": prompt,
                "resolution": resolution,
                "generate_audio": False,
            },
        )
        r.raise_for_status()
        return r.json()["data"]["id"]


async def wait(token: str, prediction_id: str, timeout_s: int = 1500) -> bytes:
    """The edited video's bytes. Raises on failure or when it takes longer than `timeout_s`."""
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=120) as c:
        while time.monotonic() - t0 < timeout_s:
            await asyncio.sleep(8)
            r = await c.get(
                f"{API}/predictions/{prediction_id}/result",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code != 200:
                continue
            d = r.json()["data"]
            if d["status"] == "completed":
                return (await c.get(d["outputs"][0])).content
            if d["status"] == "failed":
                raise RuntimeError(f"WaveSpeed: {d.get('error') or 'failed'}")
    raise TimeoutError(f"still running after {timeout_s // 60} min (prediction {prediction_id})")
