"""Step 1-2 of clustering: describe files on every model axis (cs.experiments.describe).

Usage:
    uv run python scripts/describe_footage.py --org <uuid> --show 1        # print one file's evidence, no call
    uv run python scripts/describe_footage.py --org <uuid> --pilot         # 15 videos + 5 images, spread by spend
    uv run python scripts/describe_footage.py --org <uuid> --all           # every tagged video and every image
    uv run python scripts/describe_footage.py --org <uuid> --experiment adset-families
                                                                          # an experiment's files, progress on its page

Only tagged videos are in scope: an untagged video has no transcript or scenes to describe.
A file already described under the current prompt version is skipped, so a run never pays
twice. Calls go one at a time and are never retried; failures are listed at the end.
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

import openai

from cs.experiments import describe as d
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database

# Gemini 2.5 Flash list price per million tokens, for the running estimate only.
PRICE_IN, PRICE_OUT = 0.30, 2.50


async def in_scope(db: Database, org: UUID) -> list:
    """Tagged videos and images with a file, highest lifetime spend first."""
    return await db.fetch(
        """
        SELECT f.id, f.format, f.duration_s, f.rep_creative_id, COALESCE(w.spend, 0) AS spend
        FROM footage f
        LEFT JOIN footage_metric_window w ON w.footage_id = f.id AND w.window_kind = 'lifetime'
        WHERE f.org_id = $1 AND f.rep_creative_id IS NOT NULL
          AND (f.format = 'image'
               OR EXISTS (SELECT 1 FROM footage_attribute a WHERE a.footage_id = f.id))
        ORDER BY spend DESC, f.id
        """,
        org,
    )


def spread(rows: list, n: int) -> list:
    """n rows evenly spaced through a spend-ranked list: the pilot should see the long tail,
    not just the few concepts the top spenders share."""
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


async def experiment_rows(db: Database, org: UUID, slug: str) -> tuple[UUID, list]:
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, slug)
    if exp is None:
        raise SystemExit(f"no experiment {slug!r}")
    rows = await db.fetch(
        """
        SELECT DISTINCT f.id, f.format, f.duration_s, f.rep_creative_id
        FROM experiment_item i JOIN footage f ON f.id = i.footage_id
        WHERE i.experiment_id = $1 ORDER BY f.id
        """,
        exp,
    )
    return exp, rows


async def step_update(db: Database, exp: UUID | None, **cols) -> None:
    """Write the describe step's state to the experiment, so its page shows the run live."""
    if exp is None:
        return
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(cols, 2))
    await db.execute(
        f"UPDATE experiment_step SET {sets} WHERE experiment_id = $1 AND key = 'describe'",
        exp,
        *cols.values(),
    )


async def run(org: UUID, pilot: bool, show: int, experiment: str | None = None) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    blobs = blob_store_from_settings(settings)
    rows = await in_scope(db, org)
    videos = [r for r in rows if r["format"] == "video"]
    images = [r for r in rows if r["format"] == "image"]
    todo = spread(videos, 15) + spread(images, 5) if pilot or show else videos + images
    exp = None
    if experiment:
        exp, todo = await experiment_rows(db, org, experiment)
    print(f"in scope: {len(videos)} videos, {len(images)} images; this run: {len(todo)} files")

    async def evidence(f):
        return await (d.video_evidence(db, f) if f["format"] == "video" else d.image_evidence(db, blobs, f))

    if show:
        for f in todo[:show]:
            ev = await evidence(f)
            print(f"\n=== {f['id']} ({f['format']}) ===\n{d.user_text(ev)}")
        await db.close()
        return

    client = openai.AsyncOpenAI(
        api_key=settings.ai_gateway_api_key,
        base_url=f"{settings.ai_gateway_base_url}/v1",
        max_retries=0,
    )
    model = settings.judge_model
    tin = tout = done = skipped = 0
    failed: list[tuple[UUID, str]] = []
    from datetime import UTC, datetime

    await step_update(
        db, exp, status="running", waiting_on=None, started_at=datetime.now(UTC), progress_total=len(todo)
    )
    for i, f in enumerate(todo, 1):
        if await d.existing(db, f["id"]) is not None:
            skipped += 1
            await step_update(db, exp, progress_done=done + skipped)
            continue
        try:
            ev = await evidence(f)
            await d.describe(db, client, model, org, ev)
            r = await db.fetchrow(
                "SELECT input_tokens, output_tokens FROM footage_description"
                " WHERE footage_id=$1 AND prompt_version=$2",
                f["id"],
                d.PROMPT_VERSION,
            )
            tin += r["input_tokens"] or 0
            tout += r["output_tokens"] or 0
            done += 1
        except Exception as exc:  # recorded in model_call; listed, never retried
            failed.append((f["id"], str(exc)[:160]))
        cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
        await step_update(db, exp, progress_done=done + skipped, cost_usd=round(cost, 4))
        print(f"  {i}/{len(todo)} done={done} skipped={skipped} failed={len(failed)} ~${cost:.3f}", flush=True)
    for fid, err in failed:
        print(f"FAILED {fid}: {err}")
    note = f"{done} described now, {skipped} reused from earlier runs."
    if failed:
        note += f" {len(failed)} failed and were not retried."
    await step_update(
        db,
        exp,
        status="failed" if failed and not done else "done",
        finished_at=datetime.now(UTC),
        result_note=note,
    )
    if exp is not None and not failed:
        await db.execute(
            "UPDATE experiment_step SET status='waiting', waiting_on=$2"
            " WHERE experiment_id=$1 AND key='discover' AND status='pending'",
            exp,
            "Your go to group the files. Costs a few cents: one embedding call and one naming call per group.",
        )
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--pilot", action="store_true")
    g.add_argument("--all", action="store_true")
    g.add_argument("--show", type=int, metavar="N", help="print N files' evidence, no call")
    g.add_argument("--experiment", metavar="SLUG", help="describe an experiment's files")
    args = p.parse_args()
    asyncio.run(run(args.org, args.pilot, args.show or 0, args.experiment))


if __name__ == "__main__":
    main()
