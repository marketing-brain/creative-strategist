"""Creates the v26 hook-edit experiment (Seedance 2.5 background edit of the first seconds) and
uploads the videos it made. Idempotent: an existing experiment is left alone.

Usage:
    uv run python scripts/seed_v26_hook_edit_experiment.py --org <uuid> --dir <folder with the mp4s>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.ids import new_id

SLUG = "v26-hook-edit"
PARENT = UUID("01a0ca3a-790d-7069-85d5-96bf97d6a5af")
TITLE = "Can a generated background fix a weak hook?"
QUESTION = (
    "v26 (“3AM Cortisol Mechanism”, the doctor) is the account's biggest spender and converts below the "
    "account's cost per purchase, but its hook rate is 21% under the account's. If Seedance 2.5 can change "
    "only the setting of the first seconds and leave her, her words and the on-screen text alone, a new "
    "opening costs a dollar or two instead of a reshoot."
)
METHOD = (
    "Seedance 2.5 video edit (WaveSpeed) on the first 4 seconds, 480p, sound off. The edited picture is laid "
    "back over her original sound, the rest of the ad is untouched. Judged first by eye, then on the share "
    "of viewers still watching at 3s against the original."
)
RULES = [
    {"title": "Parent: v26", "rule": "The video with the most last-30-day spend among converting videos with a weak hook.",
     "why": "$41,378 spend, $52 per purchase (account $55), hook rate 37.9% against the account's 47.8%.",
     "effect": "One video.", "dropped": [], "decided_by": "you"},
    {"title": "Change only the setting", "rule": "Prompt keeps her face, mouth, hands, the capsule, the gummy and all on-screen text; only the background changes (dark bedroom, clock at 3:17).",
     "why": "Ties to her own line about waking at 3:17; editing a real person's face or words needs consent.",
     "effect": "Open to change.", "dropped": [], "decided_by": "claude"},
    {"title": "Keep her original sound", "rule": "The edit's picture replaces the original's; her voice runs untouched underneath.",
     "why": "No lip-sync risk from generated audio, and the rest of the ad is unchanged.", "effect": "Open to change.",
     "dropped": [], "decided_by": "claude"},
    {"title": "Paid calls run once", "rule": "One edit run, logged before it is sent, never re-submitted.",
     "why": "The paid-call rule for the whole app.", "effect": "$0.99, covered by WaveSpeed's free signup credit.",
     "dropped": [], "decided_by": "claude"},
]
STEPS = [
    ("pick", "Pick the video", "Converting videos with a hook rate at least 20% under the account's, ranked by spend.",
     "done", None, 0, "v26: $41,378 spend in 30 days, $52 per purchase, hook rate 37.9% (account 47.8%)."),
    ("edit", "Edit the first 4 seconds", "Seedance 2.5 video edit on WaveSpeed, 480p, sound off.",
     "done", None, 0.99, "Background changed to a dark bedroom with a clock at 3:17. Her face, hands, the capsule, the gummy, the title and the captions came through intact. The edit played about 11% fast."),
    ("swap", "Swap it into the ad", "Stretch the edit back to her timing, lay it over her original sound, join the untouched rest.",
     "done", None, 0, "In sync within one frame to 3.6s; swapped at 3.6s. The background jumps back to daylight there, mid-shot (her first real cut is at 4.2s). 480p opening upscaled, so a little softer."),
    ("launch-version", "Make the launch version", "0–5s at 720p so the join lands on the 4.2s cut. About $2.20 on WaveSpeed.",
     "waiting", "your go after watching the test", 0, None),
    ("launch", "Launch against the original", "Both in one new TESTING ad set, same day, equal budget.",
     "pending", None, 0, None),
    ("read", "Read the hook", "Share still watching at 3s after about 5,000 impressions each; the bar is v26's 37.9%.",
     "pending", None, 0, None),
]
MEDIA = [
    ("v26_compare_first6s.mp4", "Original and edit, side by side",
     "First 6 seconds with her sound. Left: the original. Right: the edited opening, swapped at 3.6s."),
    ("v26_H_bedroom317_TEST.mp4", "The full ad with the new opening",
     "111s. Edited 0:00–0:03.6, untouched after. Test quality: the opening is 480p upscaled."),
]


async def run(org: UUID, folder: Path) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    blobs = blob_store_from_settings(s)
    if await db.fetchval("SELECT 1 FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG):
        print("already exists")
        return
    exp = new_id()
    videos = []
    for fname, title, note in MEDIA:
        path = f"{org}/experiments/{exp}/{fname}"
        await blobs.put(path, (folder / fname).read_bytes(), "video/mp4")
        videos.append({"title": title, "note": note, "path": path})
    async with db.transaction() as tx:
        await tx.execute(
            "INSERT INTO experiment (id, org_id, slug, title, question, method, rules) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            exp, org, SLUG, TITLE, QUESTION, METHOD, RULES,
        )
        for i, (key, title, detail, status, waiting_on, cost, note) in enumerate(STEPS, 1):
            await tx.execute(
                "INSERT INTO experiment_step (experiment_id, ordinal, key, title, detail, status, waiting_on, result_note,"
                " progress_done, progress_total, cost_usd, finished_at)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9, $10, CASE WHEN $6 = 'done' THEN now() END)",
                exp, i, key, title, detail, status, waiting_on, note, 1 if status == "done" else 0, cost,
            )
        await tx.execute(
            "INSERT INTO experiment_item (experiment_id, footage_id, label, label_source) VALUES ($1, $2, 'parent', 'you')",
            exp, PARENT,
        )
        await tx.execute(
            "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1, 'media', $2)", exp, {"videos": videos}
        )
    print(json.dumps({"experiment_id": str(exp)}))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--dir", type=Path, required=True)
    a = p.parse_args()
    asyncio.run(run(a.org, a.dir))


if __name__ == "__main__":
    main()
