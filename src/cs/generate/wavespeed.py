"""WaveSpeed's Seedance 2.5 video-edit endpoint: submit once, then check status (free)."""

from __future__ import annotations

import asyncio
import time

import httpx

API = "https://api.wavespeed.ai/api/v3"
EDIT_MODEL = "bytedance/seedance-2.5/video-edit"


def edit_prompt(change: str, video: str = "@video1") -> str:
    """ByteDance's edit structure (Seedance 2.5 manual, via WaveSpeed's model page): name the one
    master video, say the only thing to process, then everything to keep. Setting and lighting
    swaps are what edit models do well; changing actions or words breaks lip sync. A prompt that
    already starts "Edit @video1" (written in full by hand) is sent as it is."""
    change = change.strip().rstrip(".")
    if change.lower().startswith(("edit @video1", "edit [video 1]")):
        return change
    return (
        f"Edit {video}. {video} is the sole editing master. Process only the background and setting behind the "
        f"person, for the whole clip: {change}. Keep exactly as in {video}: the person's face, identity, hair, "
        "clothing and expression, head and mouth movements and lip sync, hands and hand movements, the product and "
        "its label, the framing, camera movement, and the timing and pacing of every action. Keep all on-screen text "
        "unchanged and reading left to right. No new text, no subtitles, no cuts, no slow motion. Everything else "
        "unchanged."
    )


async def submit(token: str, video_url: str, prompt: str, resolution: str) -> str:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{API}/{EDIT_MODEL}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "video": video_url,
                "prompt": edit_prompt(prompt),
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
