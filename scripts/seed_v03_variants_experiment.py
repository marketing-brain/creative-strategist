"""Creates the V03 variants experiment: do single-change edits of a winning video beat it on the
step each edit targets? Idempotent: re-running leaves an existing experiment alone.

Usage:
    uv run python scripts/seed_v03_variants_experiment.py --org <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.ids import new_id

SLUG = "v03-variants"
PARENT = UUID("01a0ca3a-790d-7028-b295-d73bf6ac5913")
BRIEF = "01a0db1c-bb27-7000-aa49-ba6207fa59d8"

TITLE = "Can edits of a winner beat it?"
QUESTION = (
    "V03 (“Not a sleep aid”) had the lowest first-week cost per purchase in Talking head · Early "
    "reveal and has since worn out. If edits of the same file, each changing one thing, beat it "
    "on the step that change targets, the winner becomes the next parent."
)
METHOD = (
    "Five edits of the original file (three new openings, one cut, one offer end card) plus the "
    "parent as control, launched together in one TESTING ad set. Openings are judged on the share "
    "still watching at 3s; only the survivors are judged on cost per purchase."
)

RULES = [
    {"title": "Parent: V03", "rule": "MagAshwa_v207_bearthevoice3_…_NotASleepAid_byManini.",
     "why": "Cheapest first-week cost per purchase in its cluster with a real purchase count.",
     "effect": "$26 per purchase, ROAS 1.78, 52 purchases in its first 7 days; $44 over the last 30.",
     "dropped": [], "decided_by": "you"},
    {"title": "Rank on the first 7 days, retargeting left out", "rule": "First-week cost per purchase, at least 10 purchases, retargeting campaigns excluded.",
     "why": "Lifetime totals mix creative quality with how long Meta kept spending; retargeting buys from people already about to buy.",
     "effect": "Open to change.", "dropped": [], "decided_by": "claude"},
    {"title": "One change per variant", "rule": "Each edit changes one part of the parent and keeps the rest.",
     "why": "So a result says which change made the difference.", "effect": "Open to change.", "dropped": [], "decided_by": "claude"},
    {"title": "No splice with the chiropractor ad", "rule": "V07's opening is not joined to V03's body.",
     "why": "V07's opening holds fewer viewers at 3s (27% vs 39%) and is a different person.", "effect": "Dropped one planned variant.",
     "dropped": [], "decided_by": "claude"},
    {"title": "Edits only from existing footage", "rule": "Cuts, reordering, on-screen text and an end card. New lines are shot by a person; no cloning of the creator's face or voice.",
     "why": "Needs no new shoot, costs nothing to render; cloning needs the creator's consent.", "effect": "Open to change.", "dropped": [], "decided_by": "claude"},
    {"title": "Judge each edit on the step it acts on", "rule": "Openings: share still watching at 3s after about 5,000 impressions each. Cut and offer: click and purchase rate. Cost per purchase only for survivors.",
     "why": "A 50% cost-per-purchase gap needs about 100 purchases per ad to show; the 3s share shows within days.",
     "effect": "Open to change.", "dropped": [], "decided_by": "claude"},
]

STEPS = [
    ("parent", "Pick the parent",
     "Rank videos on first-7-day cost per purchase with retargeting left out; you chose V03.",
     "done", None, "V03: $26 per purchase in its first 7 days (52 purchases), $44 over the last 30."),
    ("brief", "Write the brief",
     "Slot map from the transcript, audio pauses and watch curve; five edits, two pickup lines, a new-creator script.",
     "done", None, f"Brief: /briefs/{BRIEF}. Viewers leave fastest at 0:15–0:20 (the science line); the end card has no offer."),
    ("previews", "Render previews",
     "Render H1, H2, H3, R1 and R2 from the stored 360×360 copy with ffmpeg, cutting only at pauses in the audio. Free, no model call.",
     "waiting", "your go to render the five previews", None),
    ("original", "Get the original file",
     "The stored copy is too low-resolution to launch. Get the original from the editor (Manini) or the TikTok it was cut from.",
     "pending", None, None),
    ("renders", "Render launch versions",
     "The same cuts on the original file, with the current offer on R2's end card once you confirm it.",
     "pending", None, None),
    ("launch", "Launch",
     "You launch the five edits plus the parent as a fresh upload in one new TESTING ad set, same day, equal budget per ad.",
     "pending", None, None),
    ("read-openings", "Read the openings",
     "After about 5,000 impressions each, compare H1–H3 with the parent on the share still watching at 3s. The bar is the parent's 39%.",
     "pending", None, None),
    ("read-purchases", "Read cost per purchase",
     "The best opening, R1 and R2 against the parent, once each has enough purchases to tell a real gap from noise.",
     "pending", None, None),
]


async def run(org: UUID) -> None:
    db = await Database.connect(get_settings().database_url)
    if await db.fetchval("SELECT 1 FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG):
        print("already exists")
        return
    exp = new_id()
    async with db.transaction() as tx:
        await tx.execute(
            "INSERT INTO experiment (id, org_id, slug, title, question, method, rules) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            exp, org, SLUG, TITLE, QUESTION, METHOD, RULES,
        )
        for i, (key, title, detail, status, waiting_on, note) in enumerate(STEPS, 1):
            await tx.execute(
                "INSERT INTO experiment_step (experiment_id, ordinal, key, title, detail, status, waiting_on, result_note,"
                " progress_done, progress_total, cost_usd, finished_at)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9, 0, CASE WHEN $6 = 'done' THEN now() END)",
                exp, i, key, title, detail, status, waiting_on, note, 1 if status == "done" else 0,
            )
        await tx.execute(
            "INSERT INTO experiment_item (experiment_id, footage_id, label, label_source) VALUES ($1, $2, 'parent', 'you')",
            exp, PARENT,
        )
    print(json.dumps({"experiment_id": str(exp), "slug": SLUG}))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--org", type=UUID, required=True)
    asyncio.run(run(p.parse_args().org))


if __name__ == "__main__":
    main()
