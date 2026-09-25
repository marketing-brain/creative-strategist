"""Experiment 5: cluster every tagged video by who carries it, whether it is animated and how
long it is, read straight off the tagger's own tags (cs.experiments.tag_clusters). No model
call; re-running recomputes everything from the current tags.

Usage:
    uv run python scripts/seed_tag_cluster_experiment.py --org <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.ids import new_id
from cs.experiments import tag_clusters as tc

SLUG = "tag-clusters"
TITLE = "Clusters from what the tagger already saw"
QUESTION = (
    "Every tagged video clustered by who carries it (talking head, expert, interview, text "
    "only…) and when the product first appears, with all AI-made videos as one cluster. Do "
    "these clusters match how you think about the ads?"
)
METHOD = (
    "No model call. When each video was tagged, the tagger watched it and recorded who is on "
    "screen, whether anyone speaks, the cast and the production style. Fixed rules turn those "
    "facts into a cluster; hover a video to see the facts behind its cluster."
)
STEPS = (
    ("videos", "Take every tagged video", "Videos with tags; images come later."),
    ("cluster", "Cluster from the tags", "AI in one cluster; everything else by who carries it × when the product appears. Free."),
    ("review", "Check the clusters", "Scroll the clusters below. Tell me which look wrong."),
)
FACT_DIMS = ("messenger", "narration", "cast", "production", "native_format_cues")


async def run(org: UUID) -> None:
    db = await Database.connect(get_settings().database_url)
    videos = await db.fetch(
        """
        SELECT f.id, f.duration_s,
               (SELECT array_agg(DISTINCT n) FROM (
                  SELECT a.name AS n FROM ad a JOIN footage_creative fc ON fc.creative_id = a.creative_id
                   WHERE fc.footage_id = f.id
                  UNION SELECT a.adset_name FROM ad a JOIN footage_creative fc ON fc.creative_id = a.creative_id
                   WHERE fc.footage_id = f.id) x) AS names,
               (SELECT array_agg(DISTINCT a.adset_name) FROM ad a
                  JOIN footage_creative fc ON fc.creative_id = a.creative_id
                 WHERE fc.footage_id = f.id) AS adsets
        FROM footage f
        WHERE f.org_id = $1 AND f.format = 'video' AND f.rep_creative_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM footage_attribute a WHERE a.footage_id = f.id)
        """,
        org,
    )
    tags: dict[UUID, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for r in await db.fetch(
        "SELECT footage_id, dimension, value FROM footage_attribute WHERE footage_id = ANY($1::uuid[])",
        [v["id"] for v in videos],
    ):
        tags[r["footage_id"]][r["dimension"]].append(r["value"])

    scenes = {
        r["footage_id"]: r["first_product"]
        for r in await db.fetch(
            "SELECT footage_id, min(t_start_s) FILTER (WHERE shows_product) AS first_product"
            " FROM scene WHERE footage_id = ANY($1::uuid[]) GROUP BY footage_id",
            [v["id"] for v in videos],
        )
    }
    labels = []
    for v in videos:
        t = tags[v["id"]]
        facts = "; ".join(f"{d.replace('_', ' ')}: {', '.join(t[d])}" for d in FACT_DIMS if t.get(d))
        fp = scenes.get(v["id"])
        got = tc.labels(
            t, [n for n in (v["names"] or []) if n], [n for n in (v["adsets"] or []) if n], float(v["duration_s"]) if v["duration_s"] is not None else None,
            float(fp) if fp is not None else None, v["id"] in scenes,
        )
        reveal_fact = f"product first on screen at {fp:.0f}s" if fp is not None else "product never on screen" if v["id"] in scenes else "no scene list"
        for axis, (_, label) in got.items():
            labels.append((v["id"], axis, label, f"{facts}; {reveal_fact}"))

    rules = [
        {"title": "Videos only", "rule": "Every tagged video; images come later.", "decided_by": "you",
         "why": "Start where the tags are richest.", "effect": f"{len(videos)} videos.", "dropped": []},
        {"title": "Who carries it", "decided_by": "claude",
         "rule": "First match wins: chat screenshots; interview = 'interview' in the ad or ad set name, or "
         "'podcast' as a word in an ad set name; reacting to another video = the tagger's duet/stitch cue; expert on camera; "
         "creator or founder on camera = talking head; actor = acted; off-camera or voiceover; "
         "nobody speaking = text only. No model calls.",
         "why": "Two people on screen caught reaction videos, experts and voiceovers as interviews "
         "(9 of 16 wrong). Reaction videos the tagger didn't mark as stitches stay talking heads.",
         "effect": "Open to change.", "dropped": []},
        {"title": "All AI in one cluster", "decided_by": "you",
         "rule": "AI = motion-graphics production, or 'AI', 'Animation' or 'Claymation' in the ad's "
         "name or in any ad set it ran in. Every AI video is one cluster, whoever carries it.",
         "why": "AI-generated people read as real people in the tags; the ad set names catch them "
         "(every such series sits in an 'AI …' ad set).", "effect": "Open to change.", "dropped": []},
        {"title": "Product reveal, not length", "decided_by": "you",
         "rule": "Early reveal: the first scene showing the product starts within 10 seconds; late "
         "reveal: after 10 seconds. Length is no longer part of the cluster.",
         "why": "Length mostly repeated who carries the ad (short = text only); reveal timing split "
         "talking heads and experts cleanly ($44–45 vs $59 per purchase, first 7 days).",
         "effect": "Open to change.", "dropped": []},
        {"title": "Small clusters become Other", "decided_by": "you",
         "rule": f"A cluster with fewer than {tc.MIN_CLUSTER} videos across the whole experiment joins "
         "one 'Other' group. Other is a mixed bag, not a kind of ad: it gets no card, no read, and is "
         "left out of like-with-like comparisons.",
         "why": "One- and two-video clusters were noise on every view (cleanup, 2026-09-25).",
         "effect": "Open to change.", "dropped": []},
        {"title": "Fixed window: last 30 days, $200+", "decided_by": "you",
         "rule": "The cluster list, the cards across clusters and each cluster's ad cards use the last 30 "
         "days of delivery (ending on the account's newest day) for videos with $200+ lifetime spend. "
         "The two written reads use each video's lifetime, as the Insights tab does; each section is "
         "labelled with its window.",
         "why": "One set of numbers per section, no toggles (cleanup, 2026-09-25).",
         "effect": "Open to change.", "dropped": []},
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
        await tx.execute("UPDATE experiment SET title=$2, question=$3, method=$4, rules=$5 WHERE id=$1",
                         exp, TITLE, QUESTION, METHOD, rules)
        for key, title, detail in STEPS:
            await tx.execute("UPDATE experiment_step SET title=$3, detail=$4 WHERE experiment_id=$1 AND key=$2",
                             exp, key, title, detail)
        await tx.execute("DELETE FROM experiment_item WHERE experiment_id=$1", exp)
        await tx.executemany(
            "INSERT INTO experiment_item (experiment_id, footage_id, label) VALUES ($1, $2, 'video')",
            [(exp, v["id"]) for v in videos],
        )
        await tx.execute("DELETE FROM experiment_label WHERE experiment_id=$1", exp)
        await tx.executemany(
            "INSERT INTO experiment_label (experiment_id, footage_id, axis, value, reason) VALUES ($1,$2,$3,$4,$5)",
            [(exp, *row) for row in labels],
        )
        n_clusters = len(set(tc.cluster_map({
            v["id"]: {l[1]: l[2] for l in labels if l[0] == v["id"]} for v in videos
        }).values()))
        for key, note in (("videos", f"{len(videos)} tagged videos."),
                          ("cluster", f"{len(videos)} videos in {n_clusters} clusters.")):
            await tx.execute(
                "UPDATE experiment_step SET status='done', progress_done=$3, progress_total=$3,"
                " finished_at=COALESCE(finished_at, now()), result_note=$4 WHERE experiment_id=$1 AND key=$2",
                exp, key, len(videos), note,
            )
        # Reviewed with the user through 2026-09-25; a re-run doesn't reopen it.
        await tx.execute(
            "UPDATE experiment_step SET status='done', waiting_on=NULL, finished_at=COALESCE(finished_at, now()),"
            " result_note=$2 WHERE experiment_id=$1 AND key='review'",
            exp, "Reviewed with you: interview rule fixed, AI as one cluster, reveal instead of length, small clusters as Other.",
        )
    print(f"experiment {exp}: {len(videos)} videos")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    asyncio.run(run(p.parse_args().org))


if __name__ == "__main__":
    main()
