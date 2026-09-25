"""Creates the second experiment: what does the creative page's diagnosis say when it is
turned on one ad set? Idempotent: re-running refreshes the ad sets and rules and leaves step
progress alone.

Usage:
    uv run python scripts/seed_adset_read_experiment.py --org <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.ids import new_id

SLUG = "adset-reads"
SOURCE_SLUG = "adset-families"  # experiment 1, whose ad sets this one reads
TITLE = "What can one ad set tell you?"
QUESTION = (
    "Ads in one ad set share the audience, budget and dates, so what separates them is the "
    "creative. What should you do with each one?"
)
METHOD = (
    "The same five-tier diagnosis as the creative page (attention, retention, conversion, "
    "fatigue, context), run twice per ad set: pooled against the account, and ad by ad against "
    "its siblings. Statistics only; no model call decides anything."
)

MIN_CLICKS = 100  # the creative page's floor for any claim about purchases
MIN_ADS = 3

STEPS = (
    (
        "pick",
        "Pick the ad sets",
        "Six ad sets of different kinds, for a first look: a pooled scaling ad set, a single "
        "creator, a messenger type, a concept test, a static batch and a creator test. Swap any "
        "of them, or ask for all of the ad sets that pass the rules below.",
    ),
    (
        "against_account",
        "Read each ad set against the account",
        "Pool every ad in the ad set and run the five tiers against the rest of the account, "
        "same format. Does this ad set, as a whole, stop people, hold them and sell?",
    ),
    (
        "inside",
        "Rank the ads inside each ad set",
        "Each ad against the other ads in its own ad set: a leaderboard, the funnel step that "
        "separates the best from the rest, and the retention curves side by side for videos.",
    ),
    (
        "patterns",
        "Find patterns across ad sets",
        "Each ad set is its own test. For every tag, count how often files with it beat files "
        "without it inside the same ad set, and correct for the number of tags tested.",
    ),
    (
        "words",
        "Put each read into words",
        "Optional. A short write-up per ad set, written only from the numbers above, with no "
        "number of its own. A few cents.",
    ),
)


async def run(org: UUID) -> None:
    db = await Database.connect(get_settings().database_url)
    stats = await db.fetch(
        """
        WITH per_ad AS (
          SELECT a.adset_name, a.id, sum(m.impressions) AS imps, sum(m.link_clicks) AS clicks
          FROM ad a JOIN metric_daily m ON m.ad_id = a.id
          JOIN footage_creative fc ON fc.creative_id = a.creative_id
          JOIN footage f ON f.id = fc.footage_id
          WHERE a.org_id = $1 AND (f.format = 'image'
                 OR EXISTS (SELECT 1 FROM footage_attribute x WHERE x.footage_id = f.id))
          GROUP BY a.adset_name, a.id
        )
        SELECT adset_name, count(*) AS ads,
               count(*) FILTER (WHERE clicks >= $2) AS readable
        FROM per_ad GROUP BY adset_name
        """,
        org,
        MIN_CLICKS,
    )
    by_name = {r["adset_name"]: r for r in stats}
    # The buyer's pick: the same ad sets as experiment 1, taken from its file list so the two
    # can never drift apart.
    picked = [
        r["label"]
        for r in await db.fetch(
            "SELECT DISTINCT i.label FROM experiment_item i JOIN experiment e ON e.id = i.experiment_id"
            " WHERE e.org_id = $1 AND e.slug = $2 ORDER BY i.label",
            org,
            SOURCE_SLUG,
        )
    ]
    thin = [n for n in picked if by_name.get(n) is None or by_name[n]["readable"] < MIN_ADS]
    rows = await db.fetch(
        """
        SELECT DISTINCT a.adset_name, fc.footage_id
        FROM ad a JOIN footage_creative fc ON fc.creative_id = a.creative_id
        JOIN footage f ON f.id = fc.footage_id
        WHERE a.org_id = $1 AND a.adset_name = ANY($2::text[])
          AND (f.format = 'image'
                 OR EXISTS (SELECT 1 FROM footage_attribute x WHERE x.footage_id = f.id))
        """,
        org,
        picked,
    )
    rules = [
        {
            "title": "The same ad sets as experiment 1",
            "rule": f"Every ad set in \"Can the model rediscover your ad-set concepts?\": "
            f"{len(picked)} TESTING ad sets.",
            "decided_by": "you",
            "why": "Both experiments then look at the same ads, one by grouping them and one by "
            "reading their numbers.",
            "effect": f"{len(picked)} ad sets.",
            "dropped": [],
        },
        {
            "title": "Only files the model can describe",
            "rule": "Every image, and every video that has been tagged. Untagged videos are "
            "ignored, here and in each ad set's pooled numbers.",
            "decided_by": "you",
            "why": "The same files as experiment 1, so the two experiments stay comparable.",
            "effect": "An ad set's read covers only these files, so its totals can be lower than "
            "in Ads Manager.",
            "dropped": [],
        },
        {
            "title": f"Ads under {MIN_CLICKS} link clicks are marked, not dropped",
            "rule": f"An ad with fewer than {MIN_CLICKS} link clicks is still listed, but its "
            "purchase numbers are shown as too thin to compare, as on the creative page.",
            "decided_by": "claude",
            "why": "Below that, one lucky sale decides the ranking.",
            "effect": f"{len(thin)} of the {len(picked)} ad sets have fewer than {MIN_ADS} ads "
            "past that floor, so their purchase ranking will be mostly empty.",
            "dropped": [],
        },
        {
            "title": "Each ad over its whole life in the ad set",
            "rule": "An ad's numbers are every day it delivered in this ad set, not a fixed "
            "30-day window.",
            "decided_by": "claude",
            "why": "Ads in one test launch together, so their lives mostly overlap. A trailing "
            "window would cut ads that finished their test weeks ago.",
            "effect": "Applies to every ad set.",
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
                exp,
                org,
                SLUG,
                TITLE,
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
        await tx.execute(
            "UPDATE experiment SET title=$2, question=$3, method=$4, rules=$5 WHERE id=$1",
            exp,
            TITLE,
            QUESTION,
            METHOD,
            rules,
        )
        await tx.execute("DELETE FROM experiment_item WHERE experiment_id=$1", exp)
        await tx.executemany(
            "INSERT INTO experiment_item (experiment_id, footage_id, label) VALUES ($1, $2, $3)",
            [(exp, r["footage_id"], r["adset_name"]) for r in rows],
        )
        await tx.execute(
            "UPDATE experiment_step SET status='done', progress_done=$2, progress_total=$2,"
            " waiting_on=NULL, detail=$3, result_note=$4, finished_at=COALESCE(finished_at, now())"
            " WHERE experiment_id=$1 AND key='pick'",
            exp,
            len(picked),
            "The same TESTING ad sets as experiment 1, at your request.",
            f"{len(picked)} ad sets, {len({r['footage_id'] for r in rows})} files.",
        )
    print(f"experiment {exp}: {len(picked)} ad sets, {len({r['footage_id'] for r in rows})} files")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    asyncio.run(run(p.parse_args().org))


if __name__ == "__main__":
    main()
