"""Replace a video's opening with an edited version and put it back without breaking the ad.

What code does around the one paid call (all measured, nothing assumed):
1. Find the first real shot change after the hook (ffmpeg scene detection). The edit window
   runs one second past it, so the join can land on a cut instead of mid-shot.
2. After the edit comes back, match every edited frame to the original frame it shows. On
   2026-09-26 Seedance returned v26's 4 s playing about 11% fast; laid over the real voice
   that drifts ~0.3 s out of lip sync. The speed is fitted from the matches, the edit is
   stretched back, and the point where it stops matching is recorded.
3. Join the edited picture (up to the shot change, or the last in-sync frame if the cut comes
   later) to the untouched original, under the original sound for the whole ad.

Frame matching compares a small grey crop of the middle of the frame, where the person and
product usually are; the background is what the edit changes, so it is left out.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

FPS = 24
MIN_WINDOW, MAX_WINDOW = 4.0, 12.0
FALLBACK_WINDOW = 5.0
SYNC_TOLERANCE_S = 1.5 / FPS
# WaveSpeed Seedance 2.5 video-edit: per second of input plus output, minimum per run.
RATE = {"480p": 0.11, "720p": 0.22, "1080p": 0.55}
MIN_CHARGE = 0.99
_W, _H = 30, 53


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=kw.pop("text", True), check=kw.pop("check", True), **kw
    )


def duration(path: Path) -> float:
    return float(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ]
        ).stdout
    )


def size(path: Path) -> tuple[int, int]:
    w, h = (
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0",
                str(path),
            ]
        )
        .stdout.strip()
        .split(",")[:2]
    )
    return int(w), int(h)


def shot_changes(path: Path, upto: float = 15.0, threshold: float = 8.0) -> list[float]:
    err = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-t",
            str(upto),
            "-i",
            str(path),
            "-vf",
            f"scdet=threshold={threshold}",
            "-f",
            "null",
            "-",
        ],
        check=False,
    ).stderr
    return [float(t) for t in re.findall(r"lavfi\.scd\.time: ([0-9.]+)", err)]


def plan_window(cuts: list[float], total: float) -> tuple[float, float | None]:
    """(seconds to send, the shot change to join at or None)."""
    cut = next((c for c in cuts if 3.0 <= c <= MAX_WINDOW - 1.0), None)
    window = max(MIN_WINDOW, cut + 1.0) if cut else FALLBACK_WINDOW
    return round(min(window, total, MAX_WINDOW), 2), cut


def estimate_cost(window: float, resolution: str) -> float:
    return round(max(MIN_CHARGE, 2 * window * RATE[resolution]), 2)


def cut_clip(src: Path, out: Path, seconds: float) -> None:
    run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-t",
            f"{seconds:.3f}",
            "-c:v",
            "libx264",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )


def _frames(path: Path) -> list[bytes]:
    raw = run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"scale={_W}:{_H},format=gray",
            "-r",
            str(FPS),
            "-f",
            "rawvideo",
            "-",
        ],
        text=False,
    ).stdout
    n = len(raw) // (_W * _H)
    crop = [(y, x) for y in range(8, 28) for x in range(8, 22)]
    return [bytes(raw[k * _W * _H + y * _W + x] for y, x in crop) for k in range(n)]


def matches(original: Path, edited: Path) -> list[tuple[float, float]]:
    """For each edited frame, the time of the original frame it looks most like."""
    o, e = _frames(original), _frames(edited)
    out = []
    for i, fe in enumerate(e):
        best = min(
            range(len(o)), key=lambda j: sum(abs(a - b) for a, b in zip(fe, o[j], strict=False))
        )
        out.append((i / FPS, best / FPS))
    return out


def fit_speed(pairs: list[tuple[float, float]]) -> float:
    """Slope through the origin of original time against edited time, on the steady middle
    (the first and last half second are where models wander)."""
    pts = [(e, o) for e, o in pairs if 0.5 <= e <= (pairs[-1][0] - 0.5) and o > 0]
    if len(pts) < 5:
        return 1.0
    for _ in range(2):  # drop points far off the line and refit
        k = sum(e * o for e, o in pts) / sum(e * e for e, _ in pts)
        pts = [(e, o) for e, o in pts if abs(o - k * e) <= 0.25] or pts
    return round(sum(e * o for e, o in pts) / sum(e * e for e, _ in pts), 4)


def retime(edited: Path, out: Path, speed: float, width: int, height: int, seconds: float) -> None:
    run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(edited),
            "-vf",
            f"setpts=PTS*{speed},fps={FPS},scale={width}:{height}:flags=lanczos",
            "-t",
            f"{seconds:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )


def in_sync_until(pairs: list[tuple[float, float]]) -> float:
    """Last time before the matches start to drift for good (three misses in a row)."""
    last, misses = 0.0, 0
    for e, o in pairs:
        if abs(o - e) <= SYNC_TOLERANCE_S * 2:
            last, misses = e, 0
        else:
            misses += 1
            if misses >= 3:
                break
    return round(last, 2)


def compose(original: Path, retimed: Path, swap_at: float, out: Path) -> None:
    """Edited picture up to `swap_at`, then the untouched original; original sound throughout."""
    fps = 30
    run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(retimed),
            "-i",
            str(original),
            "-filter_complex",
            f"[0:v]trim=0:{swap_at},setpts=PTS-STARTPTS,fps={fps},format=yuv420p[a];"
            f"[1:v]trim=start={swap_at},setpts=PTS-STARTPTS,fps={fps},format=yuv420p[b];[a][b]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-crf",
            "19",
            "-preset",
            "medium",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(out),
        ],
        timeout=900,
    )


def side_by_side(original: Path, result: Path, seconds: float, out: Path) -> None:
    w, h = size(original)
    half_w = 540 if w < h else 640
    half_h = int(round(half_w * h / w / 2) * 2)
    run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(original),
            "-i",
            str(result),
            "-filter_complex",
            f"[0:v]trim=0:{seconds},setpts=PTS-STARTPTS,scale={half_w}:{half_h},fps=30[l];"
            f"[1:v]trim=0:{seconds},setpts=PTS-STARTPTS,scale={half_w}:{half_h},fps=30[r];[l][r]hstack[v]",
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-t",
            f"{seconds:.2f}",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out),
        ],
        timeout=600,
    )


def finish(original_bytes: bytes, edited_bytes: bytes, window: float, cut: float | None) -> dict:
    """Everything after the paid call: fit, stretch, choose the join, render. Free."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        orig, clip, edit = t / "orig.mp4", t / "clip.mp4", t / "edit.mp4"
        orig.write_bytes(original_bytes)
        edit.write_bytes(edited_bytes)
        cut_clip(orig, clip, window)
        speed = fit_speed(matches(clip, edit))
        w, h = size(orig)
        retimed = t / "retimed.mp4"
        retime(edit, retimed, speed, w, h, window)
        sync = in_sync_until(matches(clip, retimed))
        notes = [
            f"The edit ran {abs(speed - 1):.0%} {'fast' if speed > 1 else 'slow'}; stretched back to the original's timing."
            if abs(speed - 1) >= 0.01
            else "The edit kept the original's timing."
        ]
        if cut and cut <= sync + 0.05:
            swap = cut
            notes.append(
                f"Joined at the shot change at {cut:.1f}s, where the original cuts anyway."
            )
        else:
            swap = max(1.0, sync)
            notes.append(
                f"In sync to {sync:.1f}s; joined there, mid-shot, so the background changes at the join."
                + (
                    f" The first shot change is at {cut:.1f}s."
                    if cut
                    else " No shot change in the first seconds."
                )
            )
        result, compare = t / "result.mp4", t / "compare.mp4"
        compose(orig, retimed, swap, result)
        side_by_side(orig, result, min(duration(orig), swap + 2.5), compare)
        return {
            "speed": speed,
            "in_sync_until_s": sync,
            "swap_at_s": swap,
            "notes": notes,
            "result": result.read_bytes(),
            "compare": compare.read_bytes(),
        }
