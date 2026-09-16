import shutil
import subprocess

import pytest

from cs.tagging.video import probe_duration_ms

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory) -> bytes:
    p = tmp_path_factory.mktemp("v") / "s.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(p),
        ],
        check=True,
    )
    return p.read_bytes()


def test_probe_duration(sample_video):
    # The fallback for videos Meta's library does not report a length for.
    assert probe_duration_ms(sample_video) in range(4900, 5200)


def test_probe_duration_rejects_a_non_video():
    with pytest.raises(RuntimeError, match="ffprobe failed"):
        probe_duration_ms(b"not a video")
