import io
import shutil

import pytest
from PIL import Image

from cs.generation.compose import fit_to_aspect, overlay_logo, video_with_end_card
from cs.generation.fakes import solid_png, tiny_mp4


def _size(b: bytes):
    return Image.open(io.BytesIO(b)).size


def test_fit_to_aspect_sizes():
    src = solid_png(2000, 1000)
    assert _size(fit_to_aspect(src, "1:1")) == (1080, 1080)
    assert _size(fit_to_aspect(src, "4:5")) == (1080, 1350)
    assert _size(fit_to_aspect(src, "9:16")) == (1080, 1920)


def test_overlay_logo_changes_corner():
    base = fit_to_aspect(solid_png(1080, 1080, (0, 0, 0)), "1:1")
    logo = solid_png(200, 200, (255, 255, 255))
    out = Image.open(io.BytesIO(overlay_logo(base, logo)))
    assert out.getpixel((1060, 1060))[0] > 200 and out.getpixel((20, 20))[0] < 30


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_with_end_card_extends_duration():
    from cs.tagging.video import probe_duration_ms

    out = video_with_end_card(tiny_mp4(2), "Sleep deeper", solid_png(200, 200), "#112233", "9:16")
    assert probe_duration_ms(out) >= 3300
