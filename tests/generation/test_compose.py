import io
import shutil
import subprocess

import pytest
from PIL import Image

from cs.generation.compose import fit_to_aspect, overlay_logo, video_with_end_card
from cs.generation.fakes import solid_png, tiny_mp4


def _size(b: bytes):
    return Image.open(io.BytesIO(b)).size


def _ffmpeg_has_drawtext() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True)
    return "drawtext" in out.stdout


def test_fit_to_aspect_sizes():
    src = solid_png(2000, 1000)
    assert _size(fit_to_aspect(src, "1:1")) == (1080, 1080)
    assert _size(fit_to_aspect(src, "4:5")) == (1080, 1350)
    assert _size(fit_to_aspect(src, "9:16")) == (1080, 1920)


def test_overlay_logo_changes_corner():
    base = fit_to_aspect(solid_png(1080, 1080, (0, 0, 0)), "1:1")
    logo = solid_png(200, 200, (255, 255, 255))
    out = Image.open(io.BytesIO(overlay_logo(base, logo)))
    assert out.getpixel((950, 950))[0] > 200 and out.getpixel((20, 20))[0] < 30


@pytest.mark.skipif(not _ffmpeg_has_drawtext(), reason="ffmpeg built without drawtext (libfreetype) support")
def test_video_with_end_card_extends_duration():
    from cs.tagging.video import probe_duration_ms

    out = video_with_end_card(tiny_mp4(2), "Sleep deeper", solid_png(200, 200), "#112233", "9:16")
    assert probe_duration_ms(out) >= 3300


@pytest.mark.skipif(not _ffmpeg_has_drawtext(), reason="ffmpeg built without drawtext (libfreetype) support")
def test_video_with_end_card_caption_with_comma():
    """Regression test: captions with commas must be escaped to avoid breaking ffmpeg filtergraph."""
    out = video_with_end_card(tiny_mp4(2), "Sleep deeper, wake happier", solid_png(200, 200), "#112233", "9:16")
    assert out[:4] == b"\x00\x00\x00" or out[:8] == b"\x00\x00\x00\x18ftyp"  # MP4 file header
