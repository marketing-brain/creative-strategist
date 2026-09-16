import shutil
import subprocess

import pytest

from cs.tagging import video as video_mod
from cs.tagging.video import (
    VideoStats,
    _display_size,
    aspect_label,
    cuts_per_10s,
    probe_duration_ms,
    probe_video,
)

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


def test_probe_video_degrades_when_the_cut_probe_times_out(two_scene_video, monkeypatch):
    """A pathological file must not hang the single worker: the cut probe is bounded and
    a timeout leaves cut_count None, exactly like any other cut-probe failure."""
    real_run, seen = subprocess.run, []

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "ffmpeg":
            seen.append((cmd, kwargs))
            raise subprocess.TimeoutExpired("ffmpeg", 180)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(video_mod.subprocess, "run", fake_run)
    s = probe_video(two_scene_video)
    assert s.cut_count is None
    assert 3.8 <= s.duration_s <= 4.2 and (s.width, s.height) == (320, 240)
    cmd, kwargs = seen[0]
    assert "-nostdin" in cmd
    assert kwargs["timeout"] == 180 and kwargs["stdin"] is subprocess.DEVNULL


def test_probe_duration_reports_an_ffprobe_timeout_as_a_failure(sample_video, monkeypatch):
    def fake_run(cmd, *args, **kwargs):
        assert kwargs["timeout"] == 60 and kwargs["stdin"] is subprocess.DEVNULL
        raise subprocess.TimeoutExpired("ffprobe", 60)

    monkeypatch.setattr(video_mod.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="ffprobe failed: timeout"):
        probe_duration_ms(sample_video)


@pytest.fixture(scope="module")
def rotated_video(tmp_path_factory) -> bytes:
    """320x240 coded, carrying a 90-degree display matrix: it displays as 240x320, the
    shape a phone-shot portrait clip arrives in."""
    d = tmp_path_factory.mktemp("v")
    flat, rotated = d / "flat.mp4", d / "rot.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(flat),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-display_rotation",
            "90",
            "-i",
            str(flat),
            "-c",
            "copy",
            str(rotated),
        ],
        check=True,
    )
    return rotated.read_bytes()


def test_probe_video_reports_the_displayed_size_of_a_rotated_clip(rotated_video):
    s = probe_video(rotated_video)
    assert (s.width, s.height) == (240, 320)
    assert aspect_label(s.width, s.height) == "other"  # 3:4, as displayed


@pytest.mark.parametrize(
    "rotation, expected",
    [(None, (320, 240)), (0, (320, 240)), (90, (240, 320)), (-90, (240, 320)), (180, (320, 240))],
)
def test_display_size_swaps_only_on_a_quarter_turn(rotation, expected):
    assert _display_size(320, 240, rotation) == expected


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
