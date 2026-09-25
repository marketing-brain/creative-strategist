"""The Insights tab's read inside one cluster, with the bar lowered to suggestive.

Inside a cluster (10-40 videos) nothing reaches the Insights tab's bar, so here an idea is any
result that is suggestive or better, carries no confound flag and kept its grade on the second
shuffle (the Insights tab asks for strong). Everything else is the Insights tab's own code: its
statistics (cs.insights.account_read.patterns.run on the cluster's videos), its brief with the
IDEAS section rebuilt from these ideas, its writer, gates, retries and page slice. The writer's
confidence gate already caps each finding at its weakest cited grade, so titles read as leads.

A lower bar lets chance through, so a cluster is written up only when it has more suggestive
results than chance would plausibly give: at p < 0.10, about one clean test in ten comes out
suggestive by luck, and the cluster must beat that (one-sided binomial, p < 0.05). Decided
2026-09-25 after a dry run: only Text only cleared it (6 of 15 clean tests, ~2 by chance).
"""

from __future__ import annotations

import math

from cs.insights.account_read.patterns import SAME_FILES, _jaccard
from cs.insights.account_read.types import RunResult, Test
from cs.insights.account_read.writer import SYSTEM, build_brief, pattern_line

MIN_VIDEOS = 10  # the Insights tab's own minimum for a read
CHANCE_RATE = 0.10  # share of tests suggestive or better by luck alone (p < 0.10)
BEYOND_CHANCE_P = 0.05
MAX_IDEAS = 12  # the Insights tab's MAX_FINDINGS


def clean(tests: list[Test]) -> list[Test]:
    """Main-effect tests with no confound flag and a grade that held on the second shuffle."""
    return [t for t in tests if t.group is None and not t.confounds and not t.grade_unstable]


def loose_ideas(tests: list[Test]) -> list[list[Test]]:
    """Suggestive-or-better clean results, grouped by the files they describe (the Insights
    tab's rule: 60% shared files is one idea). Smallest p first."""
    out: list[list[Test]] = []
    for t in sorted((t for t in clean(tests) if t.grade in ("strong", "suggestive")), key=lambda t: t.p):
        for g in out:
            if _jaccard(t.cell_shas, g[0].cell_shas) >= SAME_FILES:
                g.append(t)
                break
        else:
            out.append([t])
    return out[:MAX_IDEAS]


def beyond_chance(tests: list[Test]) -> dict:
    """How many clean tests came out suggestive or better, how many chance would give, and the
    one-sided binomial p of seeing at least that many."""
    c = clean(tests)
    n, k = len(c), sum(t.grade in ("strong", "suggestive") for t in c)
    p = sum(math.comb(n, i) * CHANCE_RATE**i * (1 - CHANCE_RATE) ** (n - i) for i in range(k, n + 1)) if n else 1.0
    return {"clean": n, "suggestive": k, "chance": round(n * CHANCE_RATE, 1), "p": p, "beyond": n > 0 and p < BEYOND_CHANCE_P}


def system(cluster: str, stats: dict) -> str:
    return (
        SYSTEM
        + f"""
CONTEXT FOR THIS READ
This read covers one cluster of the account's videos: {cluster} (who carries the ad and when
the product first appears). Every comparison is between videos of this cluster only, so a result
is about a choice within this kind of ad. The bar is lower than the account read's: THE IDEAS are
results that are suggestive or better, unflagged and stable, not only strong ones, so every
finding is a lead, never evidence. Of {stats["clean"]} clean comparisons in this cluster,
{stats["suggestive"]} came out suggestive or better, where chance alone would give about
{stats["chance"]}. The overview says that plainly, then what the leads suggest for the next ad
of this kind.
"""
    )


def brief(run: RunResult, cluster: str, ideas: list[list[Test]]) -> str:
    """The Insights tab's brief for the cluster's run, with THE IDEAS rebuilt from `ideas`."""
    text = build_brief(run)
    ids = {id(t): f"P{i}" for i, t in enumerate(run.tests, start=1)}
    by_sha = {u.sha256: u for u in run.units}
    blocks = []
    for n, group in enumerate(ideas, start=1):
        lines = [f"  {pattern_line(ids[id(t)], t)}" for t in group]
        files = sorted((by_sha[x] for x in group[0].cell_shas if x in by_sha), key=lambda u: -u.spend)
        examples = "; ".join(f'"{u.headline}"' for u in files[:3])
        blocks.append(
            f"\nIDEA {n} ({len(group)} result{'s' if len(group) > 1 else ''}, {group[0].n_cell} files):\n"
            + "\n".join(lines)
            + f"\n    files: {examples}"
        )
    start, end = text.index("=== THE IDEAS"), text.index("=== GRADED PATTERNS")
    return (
        text[:start]
        + f"=== THE IDEAS: leads within {cluster} ({len(blocks)}) ===\n"
        "Each is one idea: suggestive-or-better results over the same files, unflagged and stable.\n"
        "Write one finding per idea, in order.\n"
        + "\n".join(blocks)
        + "\n\n"
        + text[end:]
    )
