from __future__ import annotations

import io
import shutil
import subprocess
import tempfile

from PIL import Image

from cs.generation.copy import CopySet


def solid_png(w: int = 1024, h: int = 1024, color=(40, 90, 200)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, format="PNG")
    return b.getvalue()


def tiny_mp4(seconds: int = 2) -> bytes:
    if shutil.which("ffmpeg") is None:
        return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=blue:s=320x568:d={seconds}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", f.name], check=True)
        return open(f.name, "rb").read()


class FakeCopyWriter:
    def __init__(self, sets: list[CopySet] | None = None):
        self.sets = sets if sets is not None else [CopySet("Sleep deeper", "Tired of waking up tired?", "SHOP_NOW"),
                                                   CopySet("Rest easy", "Wind down in minutes.", "LEARN_MORE")]
        self.briefs: list[str] = []

    async def write(self, brief, kit, n) -> list[CopySet]:
        self.briefs.append(brief)
        return self.sets[:n]


class FakeImageGenerator:
    def __init__(self):
        self.calls: list[dict] = []

    async def generate(self, prompt: str, reference_images: list[bytes], n: int = 1) -> list[bytes]:
        self.calls.append({"prompt": prompt, "refs": len(reference_images), "n": n})
        return [solid_png() for _ in range(n)]


class FakeVideoGenerator:
    def __init__(self):
        self.calls: list[dict] = []

    async def generate(self, still_url: str, prompt: str, duration_s: int, aspect: str) -> bytes:
        self.calls.append({"still_url": still_url, "prompt": prompt, "duration_s": duration_s, "aspect": aspect})
        return tiny_mp4()
