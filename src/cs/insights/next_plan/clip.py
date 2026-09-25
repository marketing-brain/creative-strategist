"""Cut the seconds a card is about out of the file, so the model is shown those and no more.

This is the one decision that decides whether step 3 is affordable. Gemini charges video by
duration: the top card's file runs 111 seconds, and its card is about the first three. Sending
the whole file to ask what happens at 0:02 costs roughly 35x what sending the opening costs,
and buys nothing -- the model would have to find the moment we already measured.

ffmpeg is on the box (deploy/ec2/setup.sh installs a static build) and is already a dependency
of the tagging path, so this adds no new infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

log = logging.getLogger("cs.next_plan.clip")

MAX_CLIP_S = 8.0  # a recut card is about an opening; past this it is a different card
TIMEOUT_S = 60


class ClipUnavailable(RuntimeError):
    """ffmpeg is missing or the cut failed. The caller falls back to no description rather
    than to sending the whole file -- an unclipped video is the cost this module exists to
    avoid, and a card without a description is still a usable card."""


def available() -> bool:
    return shutil.which("ffmpeg") is not None


async def cut(data: bytes, start_s: float, end_s: float, suffix: str = ".mp4") -> bytes:
    """The segment [start_s, end_s), re-encoded small: the model reads frames and audio, not
    pixels, so 480p at 24fps carries everything the description needs."""
    if not available():
        raise ClipUnavailable("ffmpeg not on PATH")
    span = min(max(end_s - start_s, 0.5), MAX_CLIP_S)
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / f"in{suffix}", Path(tmp) / f"out{suffix}"
        src.write_bytes(data)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_s:.2f}",
            "-t",
            f"{span:.2f}",
            "-i",
            str(src),
            "-vf",
            "scale=-2:480",
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "30",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-movflags",
            "+faststart",
            str(dst),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
        except TimeoutError as exc:
            proc.kill()
            raise ClipUnavailable("ffmpeg timed out") from exc
        if proc.returncode != 0 or not dst.exists():
            raise ClipUnavailable(f"ffmpeg failed: {err.decode()[:200]}")
        out = dst.read_bytes()
    log.info("clipped", extra={"in_bytes": len(data), "out_bytes": len(out), "span_s": span})
    return out
