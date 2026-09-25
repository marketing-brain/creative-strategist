"""Creates the first experiment: does blind discovery rediscover the concepts in the TESTING ad
sets? Idempotent: re-running refreshes the file list and leaves step progress alone.

Usage:
    uv run python scripts/seed_family_experiment.py --org <uuid>                # all files
    uv run python scripts/seed_family_experiment.py --org <uuid> --videos-only  # a copy, videos only
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.ids import new_id

SLUG = "adset-families"

# Ad sets smaller than this can't show whether files that belong together end up together.
MIN_FILES = 3

QUESTION = (
    "Your TESTING ad sets already group ads the way you launched them. If blind discovery puts "
    "the same files together without seeing the ad set names, its groups can be trusted on the "
    "ads you never labeled."
)
METHOD = (
    "Format axis first: many ad set names say how an ad is made. Unit is the file. The ad set "
    "names stay hidden until the scoring step. A file in several ad sets counts once for each."
)

STEPS = (
    (
        "dataset",
        "Pick the labeled files",
        "Every tagged video and every image in a TESTING ad set, labeled with that ad set exactly "
        "as you named it. A file that ran in several TESTING ad sets carries each of them. The "
        "selection rules below say exactly what was left out and why.",
    ),
    (
        "describe",
        "Describe each file",
        "The model answers six questions about each file in its own words, with no labels and no "
        "ad set names. Only the format answer is used here. Files already described are reused.",
    ),
    (
        "discover",
        "Group them blind",
        "Group the format answers by similarity and have the model name each group. The ad set "
        "names are hidden from this step.",
    ),
    (
        "score",
        "Compare with your ad sets",
        "Line the groups up against your ad sets. Does each group hold one ad set, does each ad "
        "set land in one group, and which files cross over?",
    ),
)


async def run(org: UUID, videos_only: bool = False) -> None:
    slug = f"{SLUG}-video" if videos_only else SLUG
    title = (
        "Can the model rediscover your video ad-set concepts?"
        if videos_only
        else "Can the model rediscover your ad-set concepts?"
    )
    db = await Database.connect(get_settings().database_url)
    all_sets = await db.fetch(
        "SELECT DISTINCT adset_name FROM ad WHERE org_id=$1 AND adset_name IS NOT NULL", org
    )
    rows = await db.fetch(
        """
        SELECT fc.footage_id, a.adset_name, min(a.first_seen_at) AS first_seen, f.format,
               (f.format = 'image'
                OR EXISTS (SELECT 1 FROM footage_attribute x WHERE x.footage_id = f.id)) AS usable
        FROM ad a JOIN footage_creative fc ON fc.creative_id = a.creative_id
        JOIN footage f ON f.id = fc.footage_id
        WHERE a.org_id = $1 AND a.adset_name LIKE 'TESTING%'
        GROUP BY fc.footage_id, a.adset_name, f.format, f.id
        """,
        org,
    )
    testing = {r["adset_name"] for r in rows}
    all_testing = {r["adset_name"] for r in all_sets if r["adset_name"].startswith("TESTING")}
    not_testing = sorted(r["adset_name"] for r in all_sets if not r["adset_name"].startswith("TESTING"))
    usable = [r for r in rows if r["usable"]]
    with_usable = {r["adset_name"] for r in usable}
    image_only = set()
    if videos_only:
        videos = [r for r in usable if r["format"] == "video"]
        image_only = with_usable - {r["adset_name"] for r in videos}
        usable = videos

    # Every TESTING ad set a file ran in is one of its labels, as the ad sets were set up.
    runs: dict[UUID, set[str]] = defaultdict(set)
    for r in usable:
        runs[r["footage_id"]].add(r["adset_name"])
    sizes = Counter(n for labels in runs.values() for n in labels)
    kept = {n for n, c in sizes.items() if c >= MIN_FILES}
    items = [(fid, n) for fid, labels in runs.items() for n in sorted(labels) if n in kept]
    small = sorted((n for n in sizes if n not in kept), key=lambda n: (-sizes[n], n))
    files = {fid for fid, _ in items}

    # What the page shows under "Selection rules": each rule as applied, who decided it, and
    # what it removed, measured here rather than written by hand.
    rules = [
        {
            "title": "Only TESTING ad sets",
            "rule": "The ad set's name starts with TESTING.",
            "decided_by": "claude",
            "why": "Scaling, retargeting and remarketing ad sets pool winners from many tests, so "
            "their name says little about what the ads in them are. They could be added back.",
            "effect": f"{len(all_sets)} ad sets in the account. {len(not_testing)} are not TESTING "
            "and are left out.",
            "dropped": not_testing,
        },
        {
            "title": "Only files the model can describe",
            "rule": "Every image, and every video that has been tagged. Untagged videos are "
            "ignored.",
            "decided_by": "you",
            "why": "An untagged video has no transcript or scene list to describe from.",
            "effect": f"{len(all_testing)} TESTING ad sets. {len(all_testing - testing)} hold only "
            "carousels or ads whose image or video we never got, and "
            f"{len(testing - with_usable)} hold only untagged videos. Both are left out.",
            "dropped": sorted((all_testing - testing) | (testing - with_usable)),
        },
        *(
            [
                {
                    "title": "Videos only",
                    "rule": "Images are left out for now.",
                    "decided_by": "you",
                    "why": "The image groups in the first experiment overlapped; videos are "
                    "tested on their own.",
                    "effect": f"{len(image_only)} ad sets hold only images and are left out.",
                    "dropped": sorted(image_only),
                }
            ]
            if videos_only
            else []
        ),
        {
            "title": f"At least {MIN_FILES} files per ad set",
            "rule": f"An ad set needs {MIN_FILES} or more files after the rules above.",
            "decided_by": "claude",
            "why": "An ad set with one file can't show whether its files end up together. Two "
            "files can, so this could be lowered to 2.",
            "effect": f"{len(small)} ad sets are left out. {len(kept)} ad sets remain, holding "
            f"{len(files)} files. {sum(1 for f in files if len(runs[f] & kept) > 1)} of those "
            "files ran in more than one of them and count once in each.",
            "dropped": [f"{n} ({sizes[n]} file{'s' if sizes[n] > 1 else ''})" for n in small],
        },
    ]

    async with db.transaction() as tx:
        exp = await tx.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, slug)
        if exp is None:
            exp = new_id()
            await tx.execute(
                "INSERT INTO experiment (id, org_id, slug, title, question, method)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                exp,
                org,
                slug,
                title,
                QUESTION,
                METHOD,
            )
            for i, (key, title, detail) in enumerate(STEPS):
                await tx.execute(
                    "INSERT INTO experiment_step (experiment_id, ordinal, key, title, detail)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    exp,
                    i,
                    key,
                    title,
                    detail,
                )
        for i, (key, title, detail) in enumerate(STEPS):
            await tx.execute(
                "UPDATE experiment_step SET title=$3, detail=$4 WHERE experiment_id=$1 AND key=$2",
                exp,
                key,
                title,
                detail,
            )
        await tx.execute(
            "UPDATE experiment SET question=$2, method=$3, rules=$4 WHERE id=$1",
            exp,
            QUESTION,
            METHOD,
            rules,
        )
        await tx.execute("DELETE FROM experiment_item WHERE experiment_id=$1", exp)
        await tx.executemany(
            "INSERT INTO experiment_item (experiment_id, footage_id, label, label_source)"
            " VALUES ($1, $2, $3, $4)",
            [(exp, fid, lab, " · ".join(sorted(runs[fid] - {lab}))) for fid, lab in items],
        )
        await tx.execute(
            "UPDATE experiment_step SET status='done', progress_done=$2, progress_total=$2,"
            " finished_at=COALESCE(finished_at, now()),"
            " result_note=$3 WHERE experiment_id=$1 AND key='dataset'",
            exp,
            len(files),
            f"{len(files)} files in {len(kept)} ad sets. "
            f"{sum(1 for f in files if len(runs[f] & kept) > 1)} of them are in more than one.",
        )
    print(f"experiment {exp}: {len(files)} files, {len(items)} file-ad set pairs, {len(kept)} ad sets")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--videos-only", action="store_true", help="seed the video-only copy")
    a = p.parse_args()
    asyncio.run(run(a.org, a.videos_only))


if __name__ == "__main__":
    main()
