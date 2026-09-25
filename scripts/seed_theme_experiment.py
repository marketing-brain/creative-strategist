"""Creates the themes experiment: can the buyer's own themes (format, content, tone, plus
duration and funnel stage) be assigned from the information already held? A copy of the
video-only ad set experiment's files, with assignment against the buyer's themes instead of
blind discovery. Idempotent: re-running refreshes the file list, steps' text and rules.

Usage:
    uv run python scripts/seed_theme_experiment.py --org <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.ids import new_id

SLUG = "themes-video"
SOURCE_SLUG = "adset-families-video"
TITLE = "Can we cluster by your themes from what we already have?"
QUESTION = (
    "Every video gets your format, content and tone themes, its length and its funnel stage. "
    "Which of your themes can be read from what we already hold, and which need the video "
    "watched again?"
)
METHOD = (
    "Assign, don't discover: the model sorts each video into your themes (or 'none of "
    "these'), three times, majority wins. Your own labels on about 30 videos are the answer "
    "key. Only themes the existing information can't see get the video watched again."
)

STEPS = (
    ("themes", "Agree the theme list",
     "A one-line definition for each of your themes, per axis, plus 'none of these'. You edit "
     "it before anything is assigned."),
    ("answer_key", "Label about 30 videos yourself",
     "Pick the format, content and tone for ~30 videos on this page. This is the answer key "
     "the model is scored against. About 10 minutes."),
    ("duration", "Duration", "Read from each file. Free."),
    ("assign", "Assign from existing information",
     "Format, content, tone and funnel stage for every video, from its transcript, on-screen "
     "text, scenes, tags and copy. Three runs each, majority wins. About $1."),
    ("score", "Score against your labels",
     "Per theme: how often the model matches you, and how often its three runs agree. This "
     "answers the question: which themes existing information can handle."),
    ("rewatch", "Watch again where needed",
     "Only for themes that failed the score, only for their candidate videos: the model looks "
     "at the frames. About $0.03 a video."),
    ("leftovers", "Propose themes from the leftovers",
     "Videos that fit none of your themes are grouped and the model proposes new ones. You "
     "accept or reject each."),
    ("view", "Group and compare",
     "Group by any one or two axes, filter by the rest, with decision cards inside each "
     "cluster."),
)


async def run(org: UUID) -> None:
    db = await Database.connect(get_settings().database_url)
    src = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SOURCE_SLUG)
    if src is None:
        raise SystemExit(f"no experiment {SOURCE_SLUG!r}")
    items = await db.fetch(
        "SELECT footage_id, label, label_source FROM experiment_item WHERE experiment_id=$1", src
    )
    files = {r["footage_id"] for r in items}
    rules = [
        {
            "title": "The same videos as the video-only ad set experiment",
            "rule": f"Its {len(files)} tagged videos from TESTING ad sets, each still carrying its "
            "ad set name.",
            "decided_by": "you",
            "why": "A copy of that experiment, so the two can be compared directly. Widening to "
            "all tagged videos comes once the method works.",
            "effect": f"{len(files)} videos.",
            "dropped": [],
        },
        {
            "title": "Your themes are the starting list",
            "rule": "Format: Cryptic Text, Interviews, Animated, Mashups, 3P Video Reference, "
            "Body close-ups, Kitchen videos. Content: Medical Advice, Competitor Diss, "
            "Conundrum, Living the problem. Tone: Rage/frustration, High octane/Rant. Each axis "
            "also allows 'none of these'.",
            "decided_by": "you",
            "why": "You already know the groups you want; the model assigns to them rather than "
            "inventing its own.",
            "effect": "Definitions are drafted in step 1 for you to edit.",
            "dropped": [],
        },
    ]
    async with db.transaction() as tx:
        exp = await tx.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
        if exp is None:
            exp = new_id()
            await tx.execute(
                "INSERT INTO experiment (id, org_id, slug, title, question, method)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                exp, org, SLUG, TITLE, QUESTION, METHOD,
            )
            for i, (key, title, detail) in enumerate(STEPS):
                await tx.execute(
                    "INSERT INTO experiment_step (experiment_id, ordinal, key, title, detail)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    exp, i, key, title, detail,
                )
        await tx.execute(
            "UPDATE experiment SET title=$2, question=$3, method=$4, rules=$5 WHERE id=$1",
            exp, TITLE, QUESTION, METHOD, rules,
        )
        await tx.execute("DELETE FROM experiment_item WHERE experiment_id=$1", exp)
        await tx.executemany(
            "INSERT INTO experiment_item (experiment_id, footage_id, label, label_source)"
            " VALUES ($1, $2, $3, $4)",
            [(exp, r["footage_id"], r["label"], r["label_source"]) for r in items],
        )
        await tx.execute(
            "UPDATE experiment_step SET status='waiting', waiting_on=$2"
            " WHERE experiment_id=$1 AND key='themes' AND status='pending'",
            exp,
            "Your edits to the draft theme definitions. I'll post the draft next.",
        )
    print(f"experiment {exp}: {len(files)} videos")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    asyncio.run(run(p.parse_args().org))


if __name__ == "__main__":
    main()
