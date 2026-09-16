import shutil
import subprocess

import pytest

from cs.tagging.video import VideoStats, aspect_label, cuts_per_10s, probe_duration_ms, probe_video

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


@pytest.fixture(scope="module")
def two_scene_video(tmp_path_factory) -> bytes:
    """Two seconds of solid blue then two seconds of solid red: exactly one scene cut."""
    p = tmp_path_factory.mktemp("v") / "two.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=320x240:rate=10:duration=2",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=320x240:rate=10:duration=2",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(p),
        ],
        check=True,
    )
    return p.read_bytes()


def test_probe_video_reports_duration_size_and_cuts(two_scene_video):
    s = probe_video(two_scene_video)
    assert 3.8 <= s.duration_s <= 4.2
    assert (s.width, s.height) == (320, 240)
    assert s.cut_count == 1
    assert 2.4 <= cuts_per_10s(s) <= 2.6  # 1 cut over ~4s


def test_probe_video_single_scene_has_no_cuts(sample_video):
    assert probe_video(sample_video).cut_count == 0


def test_probe_video_rejects_a_non_video():
    with pytest.raises(RuntimeError, match="ffprobe failed"):
        probe_video(b"not a video")


def test_cuts_per_10s_is_none_without_a_cut_count():
    assert cuts_per_10s(VideoStats(4.0, 320, 240, None)) is None


@pytest.mark.parametrize(
    "w, h, label",
    [
        (1080, 1920, "9:16"),
        (1080, 1350, "4:5"),
        (1080, 1080, "1:1"),
        (1920, 1080, "16:9"),
        (1080, 1900, "9:16"),  # 1.6% off 9:16
        (320, 240, "other"),  # 4:3
        (1080, 1440, "other"),  # 3:4, 6.7% off 4:5
    ],
)
def test_aspect_label(w, h, label):
    assert aspect_label(w, h) == label
