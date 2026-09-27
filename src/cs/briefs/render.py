"""Render a brief variant: cut the source file at the edit's times, join the pieces with a 20 ms
audio fade at each join (no click), draw any title or offer text, and hold the last frame if
asked. Free: ffmpeg on the worker, no model call.

Text is drawn with Pillow into a PNG and overlaid, because not every ffmpeg build has drawtext.
It is placed inside the picture area (black bars found by cropdetect are left alone), so a
vertical video stored inside a square frame gets its text on the video, not on the bars.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)
FADE = 0.02


def _font(size: int):
    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


def probe(path: Path) -> tuple[int, int, float]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    d = json.loads(out)
    s = d["streams"][0]
    return int(s["width"]), int(s["height"]), float(d["format"]["duration"])


def picture_box(path: Path, at: float) -> tuple[int, int, int, int]:
    """(x, y, w, h) of the area with picture in it, ignoring black bars."""
    err = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-t",
            "2",
            "-vf",
            "cropdetect=24:2:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    ).stderr
    m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", err)
    if not m:
        w, h, _ = probe(path)
        return 0, 0, w, h
    w, h, x, y = map(int, m[-1])
    return x, y, w, h


def _wrap(text: str, font, width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def text_png(path: Path, text: str, box_w: int) -> tuple[int, int]:
    """A rounded white label with dark bold text, sized to the picture's width."""
    size = max(14, box_w // 13)
    font = _font(size)
    pad = size // 2
    probe_img = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lines = _wrap(text, font, int(box_w * 0.9) - 2 * pad, probe_img)
    lh = int(size * 1.25)
    w = int(box_w * 0.94)
    h = 2 * pad + lh * len(lines)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(size / 1.5), fill=(255, 255, 255, 238))
    for i, line in enumerate(lines):
        d.text(
            ((w - d.textlength(line, font=font)) / 2, pad + i * lh),
            line,
            font=font,
            fill=(20, 20, 20, 255),
        )
    im.save(path)
    return w, h


def render(source: bytes, edit: dict, offer: str) -> tuple[bytes, dict]:
    """The MP4 for one variant, and its width, height and length."""
    segs = [(float(a), float(b)) for a, b in edit["segments"] if float(b) - float(a) > 2 * FADE]
    if not segs:
        raise ValueError("the edit keeps nothing")
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        src = t / "src.mp4"
        src.write_bytes(source)
        _, _, dur = probe(src)
        segs = [(max(0.0, a), min(dur, b)) for a, b in segs]
        bx, by, bw, bh = picture_box(src, min(5.0, dur / 2))
        args = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src)]
        overlays = []
        for k, o in enumerate(edit.get("overlays") or []):
            text = o["text"].replace("{offer}", offer or "[Current offer]").strip()
            if not text:
                continue
            png = t / f"o{k}.png"
            w, _ = text_png(png, text, bw)
            args += ["-loop", "1", "-i", str(png)]
            overlays.append(
                (
                    len(overlays) + 1,
                    bx + (bw - w) // 2,
                    by + max(8, bh // 14),
                    float(o["start"]),
                    float(o["end"]),
                )
            )
        fc = []
        for i, (a, b) in enumerate(segs):
            fc.append(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS[v{i}]")
            fc.append(
                f"[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS,afade=t=in:d={FADE},"
                f"afade=t=out:st={b - a - FADE:.3f}:d={FADE}[a{i}]"
            )
        fc.append(
            "".join(f"[v{i}][a{i}]" for i in range(len(segs)))
            + f"concat=n={len(segs)}:v=1:a=1[vc][ac]"
        )
        v, a = "vc", "ac"
        hold = float(edit.get("hold_end") or 0)
        if hold > 0:
            fc += [
                f"[vc]tpad=stop_mode=clone:stop_duration={hold}[vh]",
                f"[ac]apad=pad_dur={hold}[ah]",
            ]
            v, a = "vh", "ah"
        for n, x, y, t0, t1 in overlays:
            fc.append(f"[{v}][{n}:v]overlay={x}:{y}:shortest=1:enable='between(t,{t0},{t1})'[o{n}]")
            v = f"o{n}"
        out = t / "out.mp4"
        args += [
            "-filter_complex",
            ";".join(fc),
            "-map",
            f"[{v}]",
            "-map",
            f"[{a}]",
            "-c:v",
            "libx264",
            "-crf",
            "19",
            "-preset",
            "medium",
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
        subprocess.run(args, check=True, timeout=600, capture_output=True)
        w, h, d = probe(out)
        return out.read_bytes(), {"width": w, "height": h, "duration_s": round(d, 2)}
