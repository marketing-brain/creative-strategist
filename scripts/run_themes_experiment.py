"""Experiment 4: sort every tagged video into the buyer's themes, one call per video.

Usage:
    uv run python scripts/run_themes_experiment.py --org <uuid>            # create/refresh + assign
    uv run python scripts/run_themes_experiment.py --org <uuid> --seed-only

Creates the experiment if needed (all tagged videos), then assigns any video without labels.
Progress and cost go to the experiment's page; calls go one at a time and are never retried.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from uuid import UUID

import openai

from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.ids import new_id
from cs.experiments import describe, themes

SLUG = "themes-simple"
TITLE = "Your themes, simple version"
QUESTION = (
    "Every tagged video sorted into your format and content themes, with its length and funnel "
    "stage, in one pass. Do the groups look right?"
)
METHOD = (
    "One model call per video picks one format, one content theme and a funnel stage, each "
    "with a one-line reason, from what we already hold about the video. Duration comes from "
    "the file. No answer key, no repeat runs: you check the groups by eye, and a theme that "
    "sorts badly gets a sharper definition and a re-run."
)
STEPS = (
    ("videos", "Take every tagged video", "All videos with a transcript and scene list. Images come later."),
    ("assign", "Sort each video into your themes", "One call per video, about $0.30 in all."),
    ("review", "Check the groups", "Scroll each theme below. Tell me which ones look wrong."),
)
PRICE_IN, PRICE_OUT = 0.30, 2.50


async def seed(db: Database, org: UUID) -> tuple[UUID, list]:
    videos = await db.fetch(
        """
        SELECT f.id, f.format, f.duration_s, f.rep_creative_id FROM footage f
        WHERE f.org_id = $1 AND f.format = 'video' AND f.rep_creative_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM footage_attribute a WHERE a.footage_id = f.id)
        ORDER BY f.id
        """,
        org,
    )
    rules = [
        {"title": "Videos only", "rule": "Every tagged video; images come later.", "decided_by": "you",
         "why": "Start where the evidence is richest.", "effect": f"{len(videos)} videos.", "dropped": []},
        {"title": "Your themes, one pass", "decided_by": "you",
         "rule": "Format: " + ", ".join(v for k, v in themes.LABELS["format"].items() if k != "other")
         + ". Content: " + ", ".join(v for k, v in themes.LABELS["content"].items() if k != "other")
         + ". Each allows Other. Funnel stage on the five awareness levels.",
         "why": "The simple version: one call per video, checked by eye.", "effect": "Definitions as agreed.",
         "dropped": []},
    ]
    async with db.transaction() as tx:
        exp = await tx.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
        if exp is None:
            exp = new_id()
            await tx.execute(
                "INSERT INTO experiment (id, org_id, slug, title, question, method) VALUES ($1,$2,$3,$4,$5,$6)",
                exp, org, SLUG, TITLE, QUESTION, METHOD,
            )
            for i, (key, title, detail) in enumerate(STEPS):
                await tx.execute(
                    "INSERT INTO experiment_step (experiment_id, ordinal, key, title, detail) VALUES ($1,$2,$3,$4,$5)",
                    exp, i, key, title, detail,
                )
        await tx.execute("UPDATE experiment SET rules=$2 WHERE id=$1", exp, rules)
        await tx.execute("DELETE FROM experiment_item WHERE experiment_id=$1", exp)
        await tx.executemany(
            "INSERT INTO experiment_item (experiment_id, footage_id, label) VALUES ($1, $2, 'video')",
            [(exp, v["id"]) for v in videos],
        )
        await tx.execute(
            "UPDATE experiment_step SET status='done', progress_done=$2, progress_total=$2,"
            " finished_at=COALESCE(finished_at, now()), result_note=$3 WHERE experiment_id=$1 AND key='videos'",
            exp, len(videos), f"{len(videos)} tagged videos.",
        )
    return exp, videos


async def step(db: Database, exp: UUID, key: str, **cols) -> None:
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(cols, 3))
    await db.execute(f"UPDATE experiment_step SET {sets} WHERE experiment_id=$1 AND key=$2", exp, key, *cols.values())


async def run(org: UUID, seed_only: bool) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    exp, videos = await seed(db, org)
    print(f"experiment {exp}: {len(videos)} videos")
    if seed_only:
        await db.close()
        return
    have = {r["footage_id"] for r in await db.fetch(
        "SELECT DISTINCT footage_id FROM experiment_label WHERE experiment_id=$1 AND axis='format'", exp)}
    todo = [v for v in videos if v["id"] not in have]
    client = openai.AsyncOpenAI(api_key=s.ai_gateway_api_key, base_url=f"{s.ai_gateway_base_url}/v1", max_retries=0)
    await step(db, exp, "assign", status="running", started_at=datetime.now(UTC), waiting_on=None,
               progress_total=len(videos), progress_done=len(have))
    tin = tout = done = 0
    failed = []
    for v in todo:
        try:
            ev = await describe.video_evidence(db, v)
            u = await themes.assign(db, client, s.judge_model, org, exp, ev)
            tin += u["input_tokens"]
            tout += u["output_tokens"]
            done += 1
        except Exception as exc:  # recorded in the ledger; never retried here
            failed.append((v["id"], str(exc)[:160]))
        cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
        await step(db, exp, "assign", progress_done=len(have) + done, cost_usd=round(cost, 4))
        print(f"  {len(have) + done}/{len(videos)} failed={len(failed)} ~${cost:.3f}", flush=True)
    note = f"{len(have) + done} of {len(videos)} videos sorted." + (f" {len(failed)} failed, not retried." if failed else "")
    await step(db, exp, "assign", status="done" if not failed else "failed", finished_at=datetime.now(UTC), result_note=note)
    if not failed:
        await step(db, exp, "review", status="waiting",
                   waiting_on="Scroll the groups below and tell me which themes look wrong.")
    for fid, err in failed:
        print("FAILED", fid, err)
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--seed-only", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.org, a.seed_only))


if __name__ == "__main__":
    main()
