"""Experiment 5: the numbers behind a cluster's insights, for a person to review. Free: no model
calls. Needs every video's blind profile (scripts/profile_video.py).

What it prints, all over the account's last 30 days:
- every video: cost per purchase, CPM, stop / click / buy rates, where it ran, its script family,
  and the trusted profile features (cs.experiments.profile_read.features);
- the noise floor: how far edits of one script differ;
- how each funnel step tracks cost per purchase, and click against buy (the trade-off found in
  Talking head · Late reveal);
- cost per purchase by where videos mostly ran;
- every feature value tested on the step it could move. The main test is at script level (each
  script's pooled rate, a permutation test on the difference between scripts with and without
  the value), so a script with many edits cannot carry a result; the pooled two-proportion z is
  shown beside it but ignores video-to-video variation;
- every script with several edits against the rest of the cluster.

Candidates are marked when the script-level test clears p < 0.1 with at least 3 scripts on each
side. Marked is not proven: a person reads the videos before anything is written up.

Usage:
    uv run python scripts/analyze_cluster.py --org <uuid> --cluster "Talking head · Early reveal (≤10s)"
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import random
import statistics as st
from collections import defaultdict
from uuid import UUID

from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments import cluster_why as cw
from cs.experiments import profile_read as pr

SLUG = "tag-clusters"
STEPS = {
    "stop": ("video_3s_views", "impressions"),
    "click": ("link_clicks", "video_3s_views"),
    "buy": ("conversions", "link_clicks"),
}


def rate(s: dict, step: str) -> float | None:
    num, den = STEPS[step]
    return s[num] / s[den] if s[den] else None


def pool(vs) -> dict:
    out = defaultdict(float)
    for v in vs:
        for k, x in v.sums.items():
            out[k] += x
    return out


def _ranks(xs):
    o = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0] * len(xs)
    for i, j in enumerate(o):
        r[j] = i
    return r


def spearman(x, y) -> float:
    rx, ry, n = _ranks(x), _ranks(y), len(x)
    return 1 - 6 * sum((a - b) ** 2 for a, b in zip(rx, ry)) / (n * (n * n - 1)) if n > 2 else 0.0


def perm_corr(x, y, draws=20000, seed=7) -> float:
    rnd, r0, y, hits = random.Random(seed), abs(spearman(x, y)), list(y), 0
    for _ in range(draws):
        rnd.shuffle(y)
        hits += abs(spearman(x, y)) >= r0 - 1e-12
    return (hits + 1) / (draws + 1)


def perm_diff(a: list[float], b: list[float]) -> float:
    """Two-sided permutation p for a difference in means; exact when small."""
    allv, na = a + b, len(a)
    d0 = abs(st.mean(a) - st.mean(b))
    combos = list(itertools.combinations(range(len(allv)), na))
    if len(combos) > 50000:
        rnd = random.Random(7)
        combos = [tuple(rnd.sample(range(len(allv)), na)) for _ in range(20000)]
    hits = 0
    for c in combos:
        s = set(c)
        ga = [allv[i] for i in s]
        gb = [allv[i] for i in range(len(allv)) if i not in s]
        hits += abs(st.mean(ga) - st.mean(gb)) >= d0 - 1e-12
    return hits / len(combos)


def z2(s1: dict, s2: dict, step: str) -> float | None:
    num, den = STEPS[step]
    n1, n2 = s1[den], s2[den]
    if not n1 or not n2:
        return None
    p1, p2 = s1[num] / n1, s2[num] / n2
    p = (s1[num] + s2[num]) / (n1 + n2)
    se = (p * (1 - p) * (1 / n1 + 1 / n2)) ** 0.5
    return (p1 - p2) / se if se else None


class _Combined:
    """Several clusters read as one: videos recoded with a cluster prefix, twins per cluster."""

    def __init__(self, briefs):
        self.videos, self.twin_groups = [], []
        self.span = briefs[0].span
        for i, b in enumerate(briefs):
            tag = "ABCDEFGH"[i]
            for v in b.videos:
                v.code = f"{tag}{v.code}"
                self.videos.append(v)
            self.twin_groups += b.twin_groups


async def run(org: UUID, clusters: list[str]) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    blobs = blob_store_from_settings(s)
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
    briefs = [await cw.build(db, org, exp, c, open_=True) for c in clusters]
    b = briefs[0] if len(briefs) == 1 else _Combined(briefs)
    cluster = " + ".join(clusters)
    fam = {v.code: v.code for v in b.videos}
    for g in b.twin_groups:
        for v in g:
            fam[v.code] = g[0].code
    rows, profiles = {}, {}
    for v in b.videos:
        p = await db.fetchval("SELECT payload FROM experiment_result WHERE experiment_id=$1 AND key=$2", exp, f"profile:{v.id}")
        p3 = await db.fetchval("SELECT payload FROM experiment_result WHERE experiment_id=$1 AND key=$2", exp, f"profile3:{v.id}")
        profiles[v.code] = p
        rows[v.code] = pr.features(p, await pr.measure(db, blobs, exp, v), p3)
    ranked = [v for v in b.videos if v.side != "unrankable"]
    cpa = lambda S: S["spend"] / S["conversions"] if S["conversions"] else None  # noqa: E731
    cpm = lambda S: S["spend"] / S["impressions"] * 1000  # noqa: E731

    print(f"== {cluster}: {len(b.videos)} videos, {len(ranked)} ranked, window {b.span[0]}..{b.span[1]}")
    print(f"   missing profiles: {[c for c, p in profiles.items() if not p] or 'none'}")
    print("\n-- videos (ranked first)")
    for v in b.videos:
        S, w = v.sums, sum(v.where.values()) or 1
        c = cpa(S)
        r = rows[v.code]
        proofs = ",".join(k[6:] for k, x in r.items() if k.startswith("proof:") and x)
        concept = ((profiles[v.code] or {}).get("creative_profile", {}).get("concept_and_message", {}).get("description") or "")[:110]
        print(f" {v.code} {v.side:10} cpa {'$%.0f' % c if c else '  -  ':>5} cpm ${cpm(S):>4.0f} stop {rate(S, 'stop'):.1%} "
              f"click {rate(S, 'click') or 0:.1%} buy {rate(S, 'buy') or 0:.1%} spend ${S['spend']:>6,.0f} clicks {S['link_clicks']:>5.0f} "
              f"fam {fam[v.code]} where {','.join(f'{k[:4]}{x / w:.0%}' for k, x in v.where.items())}")
        print(f"      hook {r['hook_type']} | aware {r['awareness_level']} | speaker {r['main_speaker']} | captions {r['running_captions']} "
              f"| pace {r['pace']} | product {r['product_first_shown']} | {r['length']} | proof {proofs}")
        print(f"      {v.name}\n      {concept}")

    print("\n-- noise floor (edits of one script)")
    for g in b.twin_groups:
        print("   " + " vs ".join(f"{v.code} {'$%.0f' % cpa(v.sums) if cpa(v.sums) else 'no purchases'}" for v in g))

    print("\n-- funnel steps against cost per purchase, and against each other (ranked videos)")
    c = [cpa(v.sums) for v in ranked]
    for step in STEPS:
        x = [rate(v.sums, step) for v in ranked]
        print(f"   {step} vs CPA: {spearman(x, c):+.2f} (p {perm_corr(x, c):.3f})")
    scripts = defaultdict(list)
    for v in ranked:
        scripts[fam[v.code]].append(v)
    for a_, b_ in (("stop", "click"), ("click", "buy"), ("stop", "buy")):
        x, y = [rate(v.sums, a_) for v in ranked], [rate(v.sums, b_) for v in ranked]
        xs = [rate(pool(vs), a_) for vs in scripts.values()]
        ys = [rate(pool(vs), b_) for vs in scripts.values()]
        print(f"   {a_} vs {b_}: videos {spearman(x, y):+.2f} (p {perm_corr(x, y):.3f}, n {len(x)}); "
              f"scripts {spearman(xs, ys):+.2f} (p {perm_corr(xs, ys):.3f}, n {len(xs)})")

    print("\n-- where videos mostly ran")
    by = defaultdict(list)
    for v in ranked:
        by[max(v.where, key=v.where.get) if v.where else "?"].append(v)
    for k, vs in by.items():
        P = pool(vs)
        print(f"   {k}: {len(vs)} videos, CPA ${cpa(P):.0f}, CPM ${cpm(P):.0f}, stop {rate(P, 'stop'):.1%}, "
              f"click {rate(P, 'click'):.1%}, buy {rate(P, 'buy'):.1%}")

    print("\n-- features on each step (script level: mean of script rates with vs without, permutation p; pooled z)")
    for feat in sorted({k for r in rows.values() for k in r}):
        for val in sorted({str(rows[v.code][feat]) for v in ranked if rows[v.code].get(feat) is not None}):
            has = [v for v in ranked if rows[v.code].get(feat) is not None and str(rows[v.code][feat]) == val]
            no = [v for v in ranked if rows[v.code].get(feat) is not None and str(rows[v.code][feat]) != val]
            sh, sn = defaultdict(list), defaultdict(list)
            for v in has:
                sh[fam[v.code]].append(v)
            for v in no:
                sn[fam[v.code]].append(v)
            if len(sh) < 2 or len(sn) < 2:
                continue
            parts, mark = [], ""
            for step in STEPS:
                a = [rate(pool(vs), step) for vs in sh.values()]
                bb = [rate(pool(vs), step) for vs in sn.values()]
                a, bb = [x for x in a if x is not None], [x for x in bb if x is not None]
                if len(a) < 2 or len(bb) < 2:
                    continue
                p = perm_diff(a, bb)
                z = z2(pool(has), pool(no), step)
                parts.append(f"{step} {st.mean(a):.1%} vs {st.mean(bb):.1%} p{p:.3f} z{z:+.1f}")
                if p < 0.1 and len(a) >= 3 and len(bb) >= 3:
                    mark = "  <== candidate"
            print(f"   {feat}={val} ({len(sh)}s/{len(sn)}s): " + " | ".join(parts) + mark)

    print("\n-- scripts with several edits against the rest")
    for g in b.twin_groups:
        g_ranked = [v for v in g if v.side != "unrankable"]
        if len(g_ranked) < 2:
            continue
        rest = [v for v in ranked if fam[v.code] != fam[g[0].code]]
        P, R = pool(g_ranked), pool(rest)
        line = []
        for step in STEPS:
            each = [rate(v.sums, step) for v in g_ranked]
            below = sum(x < rate(R, step) for x in each if x is not None)
            line.append(f"{step} {rate(P, step):.1%} vs {rate(R, step):.1%} z{z2(P, R, step):+.1f} ({below}/{len(each)} edits below)")
        print(f"   {'+'.join(v.code for v in g_ranked)}: " + " | ".join(line) + f" | CPA ${cpa(P):.0f} vs ${cpa(R):.0f}")
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--cluster", required=True, action="append", help="repeat to read several clusters as one")
    p.add_argument("--features-only", action="store_true", help="skip the per-video listing")
    a = p.parse_args()
    asyncio.run(run(a.org, a.cluster))


if __name__ == "__main__":
    main()
