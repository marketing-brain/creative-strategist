from __future__ import annotations

import json
import subprocess
import tempfile


def _run(cmd: list[str]) -> bytes:
    proc = subprocess.run(cmd, capture_output=True)
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
