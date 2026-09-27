"""Experiment 5: the reviewed insights for one cluster, computed from its videos' own numbers and
stored beside the model's read (experiment_result `profile_read:<cluster>`, key `reviewed`).

Each insight is a claim a person checked against the data: the numbers are computed here, the
tests are rank correlations with permutation p-values and a two-proportion z, and the visual
facts were verified on frames. Written 2026-09-25 for Talking head · Late reveal.

Usage:
    uv run python scripts/review_profile_read.py --org <uuid> --cluster "Talking head · Late reveal (>10s)"
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
from collections import defaultdict
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments import cluster_why as cw

SLUG = "tag-clusters"


def _ranks(xs):
    o = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0] * len(xs)
    for i, j in enumerate(o):
        r[j] = i
    return r


def spearman(x, y) -> float:
    rx, ry, n = _ranks(x), _ranks(y), len(x)
    return 1 - 6 * sum((a - b) ** 2 for a, b in zip(rx, ry)) / (n * (n * n - 1))


def perm_p(x, y, draws=20000, seed=7) -> float:
    rnd = random.Random(seed)
    r0, y, hits = abs(spearman(x, y)), list(y), 0
    for _ in range(draws):
        rnd.shuffle(y)
        hits += abs(spearman(x, y)) >= r0
    return (hits + 1) / (draws + 1)


def pool(vs):
    s = defaultdict(float)
    for v in vs:
        for k, x in v.sums.items():
            s[k] += x
    return s


async def run(org: UUID, cluster: str) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
    b = await cw.build(db, org, exp, cluster, open_=True)
    fam = {v.code: v.code for v in b.videos}
    for g in b.twin_groups:
        for v in g:
            fam[v.code] = g[0].code
    ranked = [v for v in b.videos if v.side != "unrankable"]
    by = b.by_code
    click = lambda S: S["link_clicks"] / S["video_3s_views"]  # noqa: E731
    buy = lambda S: S["conversions"] / S["link_clicks"]  # noqa: E731
    cpa = lambda S: S["spend"] / S["conversions"]  # noqa: E731
    stop = lambda S: S["video_3s_views"] / S["impressions"]  # noqa: E731


    # 1. The trade-off between clicking and buying.
    x, y = [click(v.sums) for v in ranked], [buy(v.sums) for v in ranked]
    r_v, p_v = spearman(x, y), perm_p(x, y)
    scripts = defaultdict(list)
    for v in ranked:
        scripts[fam[v.code]].append(v)
    xs = [click(pool(vs)) for vs in scripts.values()]
    ys = [buy(pool(vs)) for vs in scripts.values()]
    r_s, p_s = spearman(xs, ys), perm_p(xs, ys)
    hi = sorted(ranked, key=lambda v: -click(v.sums))
    c = [cpa(v.sums) for v in ranked]
    r_stop = spearman([stop(v.sums) for v in ranked], c)
    r_click = spearman(x, c)
    r_buy = spearman(y, c)

    # 2. The podcast-reaction script against the rest.
    pod_fam = next(g for g in b.twin_groups if len(g) > 2)
    pod = [v for v in ranked if fam[v.code] == pod_fam[0].code]
    rest = [v for v in ranked if fam[v.code] != pod_fam[0].code]
    P, R = pool(pod), pool(rest)
    p_all = (P["conversions"] + R["conversions"]) / (P["link_clicks"] + R["link_clicks"])
    z = (buy(P) - buy(R)) / (p_all * (1 - p_all) * (1 / P["link_clicks"] + 1 / R["link_clicks"])) ** 0.5

    # 3. The same script, different results.
    twin = next(g for g in b.twin_groups if len(g) == 2)
    tw = sorted(twin, key=lambda v: cpa(v.sums))
    pod_cpas = sorted(pod, key=lambda v: cpa(v.sums))

    # 4. The cluster's cheapest video.
    top = min(ranked, key=lambda v: cpa(v.sums))
    rest_top = pool([v for v in ranked if v is not top])

    reviewed = [
        {
            "title": "Attention and buying pull in opposite directions",
            "body": (
                f"The videos that get the most of their viewers to click convert the fewest of those clicks into purchases. "
                f"Across the {len(ranked)} ranked videos, click rate and buy rate move against each other "
                f"(rank correlation {r_v:+.2f}, p = {p_v:.3f}); counting each script once, {r_s:+.2f} across {len(scripts)} scripts "
                f"(p = {p_s:.3f}). The two effects largely cancel: neither the stop rate, the click rate nor the buy rate on its own "
                f"tracks cost per purchase (rank correlations {r_stop:+.2f}, {r_click:+.2f} and {r_buy:+.2f}). A video that wins "
                "clicks is not, by that alone, a better video here."),
            "numbers": [
                {"label": "Click vs buy, videos", "value": f"{r_v:+.2f} (p {p_v:.3f})"},
                {"label": "Click vs buy, scripts", "value": f"{r_s:+.2f} (p {p_s:.3f})"},
                {"label": "Highest click rate", "value": f"{hi[0].code} {click(hi[0].sums):.1%}, buys {buy(hi[0].sums):.1%}"},
                {"label": "Lowest click rate", "value": f"{hi[-1].code} {click(hi[-1].sums):.1%}, buys {buy(hi[-1].sums):.1%}"},
            ],
            "videos": [hi[0].code, hi[1].code, hi[-2].code],
            "basis": "Measured: rank correlation over every ranked video, permutation test.",
            "strength": "holds",
            "p": round(p_v, 4),
        },
        {
            "title": "The podcast-reaction script buys attention, not buyers",
            "body": (
                f"All {len(pod)} edits of the podcast-reaction script (a creator reacting to clips of Dana White and Gary Brecka) "
                f"stop more people ({stop(P):.0%} vs {stop(R):.0%}) and get more of them to click ({click(P):.1%} vs {click(R):.1%}), "
                f"but only {buy(P):.1%} of those clicks buy, against {buy(R):.1%} for the rest of the cluster "
                f"({P['conversions']:.0f} of {P['link_clicks']:,.0f} vs {R['conversions']:.0f} of {R['link_clicks']:,.0f}; z = {z:.1f}). "
                "Every edit buys below the rest. One plausible reading: a hook about what doctors won't tell you pulls in curious "
                "viewers, and the product arrives late in a long video, so many who click were not shopping for it. "
                f"It ends at about the same cost per purchase (${cpa(P):,.0f} vs ${cpa(R):,.0f})."),
            "numbers": [
                {"label": "Stop", "value": f"{stop(P):.0%} vs {stop(R):.0%}"},
                {"label": "Click, of those who stop", "value": f"{click(P):.1%} vs {click(R):.1%}"},
                {"label": "Buy, of those who click", "value": f"{buy(P):.1%} vs {buy(R):.1%}"},
                {"label": "Cost per purchase", "value": f"${cpa(P):,.0f} vs ${cpa(R):,.0f}"},
            ],
            "videos": [v.code for v in pod_cpas[:3]],
            "basis": f"Measured: pooled over {len(pod)} edits and {P['link_clicks']:,.0f} clicks; two-proportion z. The reading is a hypothesis.",
            "strength": "holds",
            "p": round(math.erfc(abs(z) / math.sqrt(2)), 6),
        },
        {
            "title": "The same script lands among both the best and the worst",
            "body": (
                f"{tw[0].code} and {tw[1].code} are the same video (the 'I've been taking the wrong one' confession, "
                f"checked frame by frame) and cost ${cpa(tw[0].sums):,.0f} and ${cpa(tw[1].sums):,.0f} per purchase. The podcast-reaction "
                f"edits range from ${cpa(pod_cpas[0].sums):,.0f} to ${cpa(pod_cpas[-1].sums):,.0f}. Two of the three scripts behind this "
                "cluster's best results also produced some of its worst. Gaps of this size between single videos are within what "
                "delivery and chance produce on their own, so judge a script on all its edits together, not on its best or worst cut."),
            "numbers": [
                {"label": "Identical content", "value": f"${cpa(tw[0].sums):,.0f} vs ${cpa(tw[1].sums):,.0f}"},
                {"label": "Podcast-reaction edits", "value": f"${cpa(pod_cpas[0].sums):,.0f} to ${cpa(pod_cpas[-1].sums):,.0f}"},
                {"label": "Script totals", "value": f"${cpa(pool(twin)):,.0f} and ${cpa(P):,.0f}"},
            ],
            "videos": [tw[0].code, tw[1].code, pod_cpas[-1].code],
            "basis": "Measured, and the twins' content verified on frames at five points.",
            "strength": "holds",
            "p": None,
        },
        {
            "title": "The cluster's cheapest video is a delivery win, not a creative lesson",
            "body": (
                f"{top.code} has the lowest cost per purchase (${cpa(top.sums):,.0f}) while stopping the fewest people "
                f"({stop(top.sums):.0%} vs {stop(rest_top):.0%} for the rest). Its impressions cost ${top.sums['spend'] / top.sums['impressions'] * 1000:,.0f} "
                f"per thousand against ${rest_top['spend'] / rest_top['impressions'] * 1000:,.0f}, most of its spend ran in a Spanish-language "
                "campaign, and the creator spends the video holding another brand's capsule bottle; Root Labs appears only on the end card."),
            "numbers": [
                {"label": "Cost per purchase", "value": f"${cpa(top.sums):,.0f}"},
                {"label": "CPM", "value": f"${top.sums['spend'] / top.sums['impressions'] * 1000:,.0f} vs ${rest_top['spend'] / rest_top['impressions'] * 1000:,.0f}"},
                {"label": "Stop", "value": f"{stop(top.sums):.0%} vs {stop(rest_top):.0%}"},
            ],
            "videos": [top.code],
            "basis": "Measured, and the other brand's bottle verified on frames.",
            "strength": "context",
            "p": None,
        },
    ]
    key = f"profile_read:{cluster}"
    payload = await db.fetchval("SELECT payload FROM experiment_result WHERE experiment_id=$1 AND key=$2", exp, key)
    payload["reviewed"] = reviewed
    # Errors found by checking the model's read against the data; shown beside it.
    payload["model_read_errors"] = [
        f"It gives the noise floor as $60. The identical edits cost ${cpa(tw[0].sums):,.0f} and ${cpa(tw[1].sums):,.0f}, a gap of "
        f"${cpa(tw[1].sums) - cpa(tw[0].sums):,.0f}.",
        "It calls two findings supported that rest on two scripts each. One of them leans on a video whose product is "
        "another brand's bottle and on a script that also has an edit among the worst.",
    ]
    await db.execute("UPDATE experiment_result SET payload=$3 WHERE experiment_id=$1 AND key=$2", exp, key, payload)
    for r in reviewed:
        print(f"\n## {r['title']}\n{r['body']}\n  " + " | ".join(f"{n['label']}: {n['value']}" for n in r["numbers"]) + f"\n  videos {r['videos']}")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--cluster", required=True)
    a = p.parse_args()
    asyncio.run(run(a.org, a.cluster))


if __name__ == "__main__":
    main()
