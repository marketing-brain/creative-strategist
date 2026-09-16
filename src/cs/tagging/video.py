from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass

FFPROBE_TIMEOUT_S = 60
FFMPEG_TIMEOUT_S = 180


def _run(cmd: list[str]) -> bytes:
    """ffprobe, bounded and detached from stdin: on the single worker a file that makes
    ffprobe block would otherwise hold the whole tagging queue."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=FFPROBE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{cmd[0]} failed: timeout") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {proc.stderr.decode(errors='ignore')[-500:]}")
    return proc.stdout


def probe_duration_ms(video: bytes) -> int:
    """Video length from the file itself. Meta reports length for videos in the ad account's
    own library (ad_video_history.length, used at download); this covers the rest."""
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(video)
        f.flush()
        out = _run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", f.name]
        )
    return int(float(json.loads(out)["format"]["duration"]) * 1000)


SCENE_THRESHOLD = 0.3
_ASPECTS = {"9:16": 9 / 16, "4:5": 4 / 5, "1:1": 1.0, "16:9": 16 / 9}
_ASPECT_TOLERANCE = 0.03


@dataclass(frozen=True)
class VideoStats:
    duration_s: float
    width: int
    height: int
    cut_count: int | None  # None when the cut probe failed


def probe_video(video: bytes) -> VideoStats:
    """Duration, frame size and scene-cut count from the file itself (spec §5). ffprobe
    failure raises; a failed cut probe leaves cut_count None so the run still completes."""
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(video)
        f.flush()
        out = _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:stream_side_data=rotation:format=duration",
                "-of",
                "json",
                f.name,
            ]
        )
        info = json.loads(out)
        stream = (info.get("streams") or [{}])[0]
        duration_s = float(info["format"]["duration"])
        try:
            cut_count = _count_scene_cuts(f.name)
        except (RuntimeError, subprocess.TimeoutExpired):
            cut_count = None
    rotation = next(
        (
            float(sd["rotation"])
            for sd in stream.get("side_data_list") or []
            if sd.get("rotation") is not None
        ),
        None,
    )
    width, height = _display_size(
        int(stream.get("width", 0)), int(stream.get("height", 0)), rotation
    )
    return VideoStats(duration_s, width, height, cut_count)


def _display_size(width: int, height: int, rotation: float | None) -> tuple[int, int]:
    """ffprobe's stream width/height are the coded dimensions. A phone clip carrying a
    quarter-turn display matrix (1920x1080 coded, rotation -90) is shown as 1080x1920,
    which is the shape aspect_ratio has to reason about."""
    if rotation is not None and abs(rotation) % 180 == 90:
        return height, width
    return width, height


def _count_scene_cuts(path: str) -> int:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "info",
            "-i",
            path,
            "-an",
            "-vf",
            f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=FFMPEG_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode(errors='ignore')[-500:]}")
    # showinfo prints one "[Parsed_showinfo_1 @ ...] n:   0 pts: ..." line per selected frame.
    return len(
        re.findall(r"Parsed_showinfo\S* @ [^\]]+\] n:\s*\d+", proc.stderr.decode(errors="ignore"))
    )


def cuts_per_10s(stats: VideoStats) -> float | None:
    if stats.cut_count is None or stats.duration_s <= 0:
        return None
    return round(stats.cut_count / stats.duration_s * 10, 1)


def aspect_label(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "other"
    r = width / height
    best = min(_ASPECTS.items(), key=lambda kv: abs(r - kv[1]) / kv[1])
    return best[0] if abs(r - best[1]) / best[1] <= _ASPECT_TOLERANCE else "other"
