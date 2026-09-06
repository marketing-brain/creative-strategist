from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Protocol

from cs.core.config import Settings

DEFAULT_SCRIPT = Path(__file__).resolve().parents[3] / "tools" / "video-gen" / "index.mjs"


class VideoGenerator(Protocol):
    async def generate(
        self, still_url: str, prompt: str, duration_s: int, aspect: str
    ) -> bytes: ...


class NodeVideoGenerator:
    def __init__(self, settings: Settings, script: Path = DEFAULT_SCRIPT, timeout_s: int = 900):
        self.settings = settings
        self.script = script
        self.timeout_s = timeout_s

    async def generate(self, still_url: str, prompt: str, duration_s: int, aspect: str) -> bytes:
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.mp4"
            env = {**os.environ, "AI_GATEWAY_API_KEY": self.settings.ai_gateway_api_key}
            proc = await asyncio.create_subprocess_exec(
                "node",
                str(self.script),
                "--model",
                self.settings.video_model,
                "--image-url",
                still_url,
                "--prompt",
                prompt,
                "--duration",
                str(duration_s),
                "--aspect",
                aspect,
                "--out",
                str(out),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
            except TimeoutError:
                proc.kill()
                raise RuntimeError("video generation timed out") from None
            if proc.returncode != 0:
                raise RuntimeError(f"video-gen failed: {err.decode(errors='ignore')[-500:]}")
            return out.read_bytes()
