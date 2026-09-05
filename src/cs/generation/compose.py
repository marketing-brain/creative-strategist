from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ASPECTS = {"1:1": (1080, 1080), "4:5": (1080, 1350), "9:16": (1080, 1920)}


def fit_to_aspect(image: bytes, aspect: str) -> bytes:
    tw, th = ASPECTS[aspect]
    im = Image.open(io.BytesIO(image)).convert("RGB")
    w, h = im.size
    target = tw / th
    if w / h > target:
        nw = int(h * target); left = (w - nw) // 2; im = im.crop((left, 0, left + nw, h))
    else:
        nh = int(w / target); top = (h - nh) // 2; im = im.crop((0, top, w, top + nh))
    im = im.resize((tw, th), Image.LANCZOS)
    out = io.BytesIO(); im.save(out, format="JPEG", quality=90)
    return out.getvalue()


def overlay_logo(image: bytes, logo_png: bytes, position: str = "bottom_right", scale: float = 0.18, margin: float = 0.04) -> bytes:
    base = Image.open(io.BytesIO(image)).convert("RGBA")
    logo = Image.open(io.BytesIO(logo_png)).convert("RGBA")
    lw = int(base.width * scale)
    logo = logo.resize((lw, int(logo.height * lw / logo.width)), Image.LANCZOS)
    m = int(base.width * margin)
    x = base.width - logo.width - m if "right" in position else m
    y = base.height - logo.height - m if "bottom" in position else m
    base.alpha_composite(logo, (x, y))
    out = io.BytesIO(); base.convert("RGB").save(out, format="JPEG", quality=90)
    return out.getvalue()


def _hex_to_ffmpeg(color: str) -> str:
    return "0x" + color.lstrip("#")


def video_with_end_card(video: bytes, caption: str, logo_png: bytes | None, end_card_bg: str, aspect: str,
                        end_seconds: float = 1.5) -> bytes:
    tw, th = ASPECTS[aspect]
    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        (dd / "in.mp4").write_bytes(video)
        safe = caption.replace("'", "").replace(":", "\\:")
        vf = (f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},"
              f"drawtext=text='{safe}':fontcolor=white:fontsize={th // 22}:box=1:boxcolor=black@0.5:boxborderw=16:"
              f"x=(w-text_w)/2:y=h-text_h-{th // 12}")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(dd / "in.mp4"), "-vf", vf, "-an",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", str(dd / "main.mp4")], check=True)
        card_inputs = ["-f", "lavfi", "-i", f"color=c={_hex_to_ffmpeg(end_card_bg)}:s={tw}x{th}:d={end_seconds}:r=30"]
        if logo_png:
            (dd / "logo.png").write_bytes(logo_png)
            lw = tw // 3
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *card_inputs, "-i", str(dd / "logo.png"),
                            "-filter_complex", f"[1:v]scale={lw}:-1[l];[0:v][l]overlay=(W-w)/2:(H-h)/2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(end_seconds), str(dd / "card.mp4")], check=True)
        else:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *card_inputs, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-t", str(end_seconds), str(dd / "card.mp4")], check=True)
        (dd / "list.txt").write_text("file 'main.mp4'\nfile 'card.mp4'\n")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(dd / "list.txt"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dd / "out.mp4")], check=True)
        return (dd / "out.mp4").read_bytes()
