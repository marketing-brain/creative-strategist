from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from cs.core.config import Settings
from cs.tagging.images import prepare_image

FIXED_TIMESTAMPS = (0.0, 1.0, 3.0)


@dataclass
class Keyframe:
    timestamp: float
    jpeg: bytes
    width: int
    height: int


def _run(cmd: list[str], input_bytes: bytes | None = None) -> bytes:
    proc = subprocess.run(cmd, input=input_bytes, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr.decode(errors='ignore')[-500:]}")
    return proc.stdout


def probe_duration_ms(video: bytes) -> int:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(video)
        f.flush()
        out = _run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", f.name]
        )
    return int(float(json.loads(out)["format"]["duration"]) * 1000)


def _scene_timestamps(path: str, threshold: float) -> list[float]:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-i",
            path,
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    out: list[float] = []
    for line in proc.stderr.splitlines():
        if "showinfo" in line and "pts_time:" in line:
            out.append(float(line.split("pts_time:")[1].split()[0]))
    return out


def _frame_at(path: str, ts: float) -> bytes:
    return _run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-ss",
            f"{ts:.3f}",
            "-i",
            path,
            "-frames:v",
            "1",
            "-f",
            "image2",
            "-vcodec",
            "png",
            "-",
        ]
    )


def extract_keyframes(
    video: bytes, max_frames: int = 24, scene_threshold: float = 0.3
) -> list[Keyframe]:
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "in.mp4")
        Path(path).write_bytes(video)
        duration = probe_duration_ms(video) / 1000
        stamps = [t for t in FIXED_TIMESTAMPS if t < duration]
        for t in _scene_timestamps(path, scene_threshold):
            if all(abs(t - s) > 0.4 for s in stamps):
                stamps.append(t)
        stamps = sorted(stamps)
        if len(stamps) > max_frames:
            fixed = [t for t in stamps if t in FIXED_TIMESTAMPS]
            rest = [t for t in stamps if t not in FIXED_TIMESTAMPS]
            step = max(1, len(rest) // (max_frames - len(fixed)))
            stamps = sorted(fixed + rest[::step][: max_frames - len(fixed)])
        frames: list[Keyframe] = []
        for t in stamps:
            png = _frame_at(path, t)
            jpeg, w, h = prepare_image(png)
            frames.append(Keyframe(t, jpeg, w, h))
        return frames


def extract_audio(video: bytes) -> bytes | None:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(video)
        f.flush()
        probe = _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                f.name,
            ]
        )
        if not json.loads(probe).get("streams"):
            return None
        return _run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-i",
                f.name,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-b:a",
                "48k",
                "-f",
                "mp3",
                "-",
            ]
        )


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, media_type: str) -> str: ...


class GatewayTranscriber:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.settings = settings
        self.http = http

    async def transcribe(self, audio: bytes, media_type: str) -> str:
        r = await self.http.post(
            f"{self.settings.ai_gateway_base_url}/v4/ai/transcription-model",
            headers={
                "Authorization": f"Bearer {self.settings.ai_gateway_api_key}",
                "ai-model-id": self.settings.transcription_model,
                "Content-Type": "application/json",
            },
            json={"audio": base64.b64encode(audio).decode(), "mediaType": media_type},
            timeout=300,
        )
        r.raise_for_status()
        return r.json().get("text", "")
