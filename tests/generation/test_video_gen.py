import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cs.generation.video import NodeVideoGenerator

SCRIPT = Path(__file__).resolve().parents[2] / "tools/video-gen/index.mjs"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_cli_dry_run_parses_args():
    out = subprocess.run(
        [
            "node",
            str(SCRIPT),
            "--dry-run",
            "--model",
            "m/x",
            "--image-url",
            "https://h/a.jpg",
            "--prompt",
            "p",
            "--duration",
            "5",
            "--aspect",
            "9:16",
            "--out",
            "/tmp/x.mp4",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert json.loads(out) == {
        "model": "m/x",
        "imageUrl": "https://h/a.jpg",
        "prompt": "p",
        "duration": 5,
        "aspectRatio": "9:16",
        "out": "/tmp/x.mp4",
    }


async def test_node_generator_runs_script(settings, tmp_path):
    fake = tmp_path / "fake.mjs"
    fake.write_text(
        "import fs from 'node:fs'; const i = process.argv.indexOf('--out'); "
        "fs.writeFileSync(process.argv[i+1], Buffer.from('MP4!'));"
    )
    gen = NodeVideoGenerator(settings, script=fake)
    assert await gen.generate("https://h/a.jpg", "p", 5, "9:16") == b"MP4!"
