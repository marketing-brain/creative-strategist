"""Part 4 of the ad set read: patterns across ad sets, each ad set its own controlled test.

For a tag and a metric, every ad set holding files with the tag and files without it is one
comparison: the two sides pooled on that ad set's delivery only, so they share its audience,
budget and dates. Across ad sets the question is how often the side with the tag won, which a
sign test answers without assuming anything about the size of the gaps; Benjamini-Hochberg
then accounts for the many tag x metric pairs tested at once.

Ad sets holding exactly the same files count once (Meta duplicates of one test are not two
tests). Hold is left out: a ThruPlay is 15 s or the whole video, so it mostly measures length.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from uuid import UUID

from cs.core.db import Database
from cs.experiments.adset_read import CLICK_FLOOR, IMPRESSION_FLOOR, SKIP_DIMS

MIN_ADSETS = 5  # comparisons a tag x metric needs before it is reported at all
Q_CONSISTENT = 0.10  # BH q under which a pattern reads as consistent
P_LEANING = 0.10  # uncorrected p under which it reads as leaning

# metric -> (numerator, denominator, higher is better, floor column, floor)
METRICS = {
    "hook_rate": ("views", "imps", True, "imps", IMPRESSION_FLOOR),
    "link_ctr": ("clicks", "imps", True, "imps", IMPRESSION_FLOOR),
    "cpa": ("spend", "conv", False, "clicks", CLICK_FLOOR),
}
METRIC_LABEL = {"hook_rate": "Hook rate", "link_ctr": "Link CTR", "cpa": "Cost per purchase"}


def sign_test(wins: int, n: int) -> float:
    """Two-sided exact binomial test against a coin flip."""
    if n == 0:
        return 1.0
    k = min(wins, n - wins)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def bh(ps: list[float]) -> list[float]:
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    q = [0.0] * m
    prev = 1.0
    for rank, i in reversed(list(enumerate(order, 1))):
        prev = min(prev, ps[i] * m / rank)
        q[i] = prev
    return q


async def patterns(db: Database, org_id: UUID, experiment_id: UUID) -> dict:
    rows = await db.fetch(
        """
        SELECT a.adset_name AS label, fc.footage_id, f.format,
               sum(m.impressions) AS imps, sum(m.video_3s_views) AS views,
               sum(m.link_clicks) AS clicks, sum(m.spend) AS spend,
               sum(m.conversions) AS conv
        FROM experiment_item i
        JOIN footage f ON f.id = i.footage_id
        JOIN footage_creative fc ON fc.footage_id = f.id
        JOIN ad a ON a.creative_id = fc.creative_id AND a.adset_name = i.label
        JOIN metric_daily m ON m.ad_id = a.id
        WHERE i.experiment_id = $1 AND a.org_id = $2
        GROUP BY a.adset_name, fc.footage_id, f.format
        """,
        experiment_id,
        org_id,
    )
    ids = list({r["footage_id"] for r in rows})
    attrs: dict[UUID, set[tuple[str, str]]] = defaultdict(set)
    for r in await db.fetch(
        "SELECT footage_id, dimension, value FROM footage_attribute WHERE footage_id = ANY($1::uuid[])",
        ids,
    ):
        if r["dimension"] not in SKIP_DIMS:
            attrs[r["footage_id"]].add((r["dimension"], r["value"]))

    # (format, ad set) -> {file: sums}; drop ad sets whose file set duplicates one already kept.
    tests: dict[tuple[str, str], dict[UUID, dict]] = defaultdict(dict)
    for r in rows:
        tests[(r["format"], r["label"])][r["footage_id"]] = {
            k: float(r[k] or 0) for k in ("imps", "views", "clicks", "spend", "conv")
        }
    seen: dict[tuple[str, frozenset], str] = {}
    duplicates = []
    for (fmt, label), files in sorted(tests.items(), key=lambda kv: -sum(f["spend"] for f in kv[1].values())):
        key = (fmt, frozenset(files))
        if key in seen:
            duplicates.append({"label": label, "same_as": seen[key]})
        else:
            seen[key] = label
    kept = {(fmt, label): tests[(fmt, label)] for (fmt, _), label in seen.items()}

    found = []
    for fmt in ("video", "image"):
        sets = {label: files for (f, label), files in kept.items() if f == fmt}
        tags = sorted({a for files in sets.values() for fid in files for a in attrs.get(fid, ())})
        for tag in tags:
            for metric, (num, den, higher, floor_col, floor) in METRICS.items():
                if fmt == "image" and metric == "hook_rate":
                    continue
                comps = []
                for label, files in sets.items():
                    tagged = [fid for fid in files if fid in attrs]  # untagged files say nothing
                    has = [files[f] for f in tagged if tag in attrs[f]]
                    lacks = [files[f] for f in tagged if tag not in attrs[f]]
                    if not has or not lacks:
                        continue
                    h = {k: sum(x[k] for x in has) for k in ("imps", "views", "clicks", "spend", "conv")}
                    lck = {k: sum(x[k] for x in lacks) for k in ("imps", "views", "clicks", "spend", "conv")}
                    if h[floor_col] < floor or lck[floor_col] < floor or not h[den] or not lck[den]:
                        continue
                    rh, rl = h[num] / h[den], lck[num] / lck[den]
                    if rl == 0 or rh == 0:
                        continue
                    ratio = rh / rl
                    won = ratio > 1 if higher else ratio < 1
                    comps.append(
                        {"label": label, "has": len(has), "lacks": len(lacks), "with": rh,
                         "without": rl, "gap": ratio - 1, "won": won}
                    )
                if len(comps) < MIN_ADSETS:
                    continue
                wins = sum(c["won"] for c in comps)
                found.append(
                    {
                        "format": fmt,
                        "dimension": tag[0],
                        "value": tag[1],
                        "metric": metric,
                        "metric_label": METRIC_LABEL[metric],
                        "higher_is_better": higher,
                        "adsets": len(comps),
                        "wins": wins,
                        "median_gap": statistics.median(c["gap"] for c in comps),
                        "p": sign_test(wins, len(comps)),
                        "comparisons": sorted(comps, key=lambda c: -c["has"] - c["lacks"]),
                    }
                )
    for f, q in zip(found, bh([f["p"] for f in found]), strict=True):
        f["q"] = q
        f["grade"] = "consistent" if q < Q_CONSISTENT else "leaning" if f["p"] < P_LEANING else "mixed"
    found.sort(key=lambda f: (f["q"], f["p"], -f["adsets"]))
    return {
        "tested": len(found),
        "adsets_used": {fmt: sum(1 for (f, _) in kept if f == fmt) for fmt in ("video", "image")},
        "duplicates": duplicates,
        "patterns": found,
    }
