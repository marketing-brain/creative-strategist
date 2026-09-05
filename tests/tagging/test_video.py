import shutil
import subprocess

import httpx
import pytest
import respx

from cs.tagging.video import GatewayTranscriber, extract_audio, extract_keyframes, probe_duration_ms

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
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(p),
        ],
        check=True,
    )
    return p.read_bytes()


def test_keyframes_include_fixed_timestamps(sample_video):
    frames = extract_keyframes(sample_video)
    ts = [round(f.timestamp, 1) for f in frames]
    assert ts[:3] == [0.0, 1.0, 3.0] and len(frames) <= 24
    assert frames[0].jpeg[:3] == b"\xff\xd8\xff" and frames[0].width == 320


def test_audio_and_duration(sample_video):
    assert probe_duration_ms(sample_video) in range(4900, 5200)
    audio = extract_audio(sample_video)
    assert audio and audio[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3")


@respx.mock
async def test_gateway_transcriber(settings):
    settings.ai_gateway_api_key = "k"
    route = respx.post("https://ai-gateway.vercel.sh/v4/ai/transcription-model").mock(
        return_value=httpx.Response(
            200, json={"text": "hi there", "segments": [], "language": "en"}
        )
    )
    async with httpx.AsyncClient() as http:
        text = await GatewayTranscriber(settings, http).transcribe(b"abc", "audio/mpeg")
    assert text == "hi there"
    req = route.calls[0].request
    assert (
        req.headers["ai-model-id"] == "openai/whisper-1"
        and req.headers["authorization"] == "Bearer k"
    )
