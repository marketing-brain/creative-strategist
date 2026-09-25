"""Steps 3 and 4 of an experiment: group its files blind on one description answer, name the
groups, then score them against the labels. Progress is written to the experiment's page.

Usage:
    uv run python scripts/discover_groups.py --org <uuid> --experiment adset-families
    uv run python scripts/discover_groups.py --org <uuid> --experiment adset-families --k 12

Embeddings are stored, so a re-run with another --k pays only for naming the new groups.
"""

from __future__ import annotations

import argparse
import asyncio
import math
from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

import openai

from cs.api.routers.experiments import score
from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.embeddings import GatewayEmbedder
from cs.core.ids import new_id
from cs.experiments import describe as d
from cs.experiments import discover as disc

FIELD = "format"
PRICE_IN, PRICE_OUT = 0.30, 2.50  # Gemini 2.5 Flash list, for the running estimate only


async def step(db: Database, exp: UUID, key: str, **cols) -> None:
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(cols, 3))
    await db.execute(
        f"UPDATE experiment_step SET {sets} WHERE experiment_id = $1 AND key = $2",
        exp,
        key,
        *cols.values(),
    )


async def run(org: UUID, slug: str, k: int | None) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, slug)
    if exp is None:
        raise SystemExit(f"no experiment {slug!r}")
    rows = await db.fetch(
        """
        SELECT DISTINCT i.footage_id, fd.answers->>$2 AS answer
        FROM experiment_item i
        JOIN footage_description fd ON fd.footage_id = i.footage_id AND fd.prompt_version = $3
        WHERE i.experiment_id = $1 ORDER BY i.footage_id
        """,
        exp,
        FIELD,
        d.PROMPT_VERSION,
    )
    texts = {r["footage_id"]: r["answer"] for r in rows if r["answer"]}
    ids = list(texts)
    await step(
        db, exp, "discover", status="running", waiting_on=None, started_at=datetime.now(UTC),
        progress_done=0, progress_total=3, result_note="Embedding the format answers.",
    )

    embedder = GatewayEmbedder(settings)
    fresh = await disc.embed_missing(db, embedder, org, texts, d.PROMPT_VERSION, FIELD)
    vecs = await disc.stored_vectors(db, ids, d.PROMPT_VERSION, FIELD, embedder.model)
    print(f"embedded {fresh} new, {len(vecs)} total")
    await step(db, exp, "discover", progress_done=1, result_note="Grouping the answers.")

    g = disc.group([vecs[f] for f in ids], k)
    print("silhouette by k:", {c: round(s, 3) for c, s in g.scores.items()}, "chosen:", g.k)
    await step(db, exp, "discover", progress_done=2, result_note=f"Naming {g.k} groups.")

    members: dict[int, list[UUID]] = defaultdict(list)
    for f, lab in zip(ids, g.labels, strict=True):
        members[lab].append(f)
    # Biggest group first on the page.
    order = sorted(members, key=lambda lab: -len(members[lab]))
    run_id = f"{datetime.now(UTC):%Y%m%d-%H%M}-k{g.k}"
    client = openai.AsyncOpenAI(
        api_key=settings.ai_gateway_api_key,
        base_url=f"{settings.ai_gateway_base_url}/v1",
        max_retries=0,
    )
    tin = tout = 0
    for ordinal, lab in enumerate(order):
        fs = members[lab]
        # The most typical answers: closest to the group's centre.
        dims = len(vecs[fs[0]])
        centre = [sum(vecs[f][i] for f in fs) / len(fs) for i in range(dims)]
        norm = math.sqrt(sum(x * x for x in centre)) or 1.0
        close = sorted(fs, key=lambda f: -sum(a * b for a, b in zip(vecs[f], centre)) / norm)
        named, i_, o_ = await disc.name_group(
            db, client, settings.judge_model, org, run_id, ordinal,
            [texts[f] for f in close[: disc.NAME_SAMPLES]],
        )
        tin, tout = tin + i_, tout + o_
        gid = new_id()
        await db.execute(
            "INSERT INTO experiment_group (id, experiment_id, run, ordinal, name, definition)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            gid, exp, run_id, ordinal, named["name"], named["definition"],
        )
        await db.executemany(
            "INSERT INTO experiment_member (group_id, footage_id) VALUES ($1, $2)",
            [(gid, f) for f in fs],
        )
        print(f"  {ordinal + 1}/{g.k} {named['name']} ({len(fs)} files)", flush=True)
    cost = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
    sil = g.scores.get(g.k)
    await step(
        db, exp, "discover", status="done", progress_done=3, finished_at=datetime.now(UTC),
        cost_usd=round(cost, 4),
        result_note=f"{len(ids)} files in {g.k} groups. The count was picked by how cleanly the "
        f"answers separate (silhouette {sil:.2f}, best of {min(g.scores)} to {max(g.scores)} "
        "groups), never by the ad set names.",
    )

    # Step 4: score against the labels, one pair per (file, ad set).
    group_of = {f: order.index(lab) for f, lab in zip(ids, g.labels, strict=True)}
    labels = await db.fetch(
        "SELECT footage_id, label FROM experiment_item WHERE experiment_id=$1", exp
    )
    pairs = [(r["label"], group_of[r["footage_id"]]) for r in labels if r["footage_id"] in group_of]
    s = score(pairs)
    n_labels = len({lab for lab, _ in pairs})
    await step(
        db, exp, "score", status="done", started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC), progress_done=len(pairs), progress_total=len(pairs),
        result_note=f"Groups hold one ad set: {s['purity']:.0%}. Ad sets stay together: "
        f"{s['completeness']:.0%}. Agreement beyond chance: {s['ari']:.2f}. "
        f"{n_labels} ad sets against {g.k} groups, so several ad sets must share a group; "
        "“stay together” is the fairer of the two here.",
    )
    print(s)
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--experiment", required=True)
    p.add_argument("--k", type=int, default=None, help="force the number of groups")
    a = p.parse_args()
    asyncio.run(run(a.org, a.experiment, a.k))


if __name__ == "__main__":
    main()
