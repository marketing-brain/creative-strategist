"""Clusters against each other, with the Insights tab's own statistics. No model calls.

Each video becomes the unit the account read grades (cs.insights.account_read.types.Unit),
built from its delivery over the chosen days. A comparison is one side's videos against the
other's, run through the same `compare` the Insights tab uses: hook, hold and link CTR as
per-video medians (Mann-Whitney), purchases per 1,000 impressions and cost per purchase pooled
(permutation test). Every test the page shows at once goes through one Benjamini-Hochberg pass
and gets the same grade words: strong, suggestive, directional, thin.

Each cluster is compared with all the others at once, and the results that hold up become a
few decision cards, the same shape as the cards inside one cluster: a plain title, the numbers
that prove it, and one line on which step of the funnel the cost gap sits in.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from uuid import UUID

from cs.core.db import Database
from cs.insights.account_read.patterns import apply_grades
from cs.insights.account_read.stats import MIN_CELL, compare, unit_rates
from cs.insights.account_read.types import LOWER_IS_BETTER, METRICS, Test, Unit

METRIC_LABEL = {
    "hook_rate": "Stop in the first 3 seconds",
    "thruplay_rate": "Watch to ThruPlay",
    "link_ctr": "Link click-through",
    "purchases_per_1k": "Purchases per 1,000 impressions",
    "cpa": "Cost per purchase",
}
GOOD_GRADES = ("strong", "suggestive")


async def units(
    db: Database, org_id: UUID, file_ids: list[UUID], span: tuple[date, date] | None
) -> dict[UUID, Unit]:
    """One Unit per video from its delivery in `span` (or lifetime when None)."""
    rows = await db.fetch(
        """
        SELECT fc.footage_id AS id, sum(m.spend) AS spend, sum(m.impressions) AS impressions,
               sum(m.video_plays) AS plays, sum(m.video_3s_views) AS v3, sum(m.video_thruplay) AS thru,
               sum(m.video_p25) AS p25, sum(m.video_p50) AS p50, sum(m.video_p75) AS p75,
               sum(m.video_p100) AS p100, sum(m.link_clicks) AS clicks, sum(m.purchases) AS purchases,
               min(m.day) AS first_day, max(m.day) AS last_day
        FROM metric_daily m JOIN footage_creative fc ON fc.creative_id = m.creative_id
        WHERE m.org_id = $1 AND fc.footage_id = ANY($2::uuid[])
          AND ($3::date IS NULL OR m.day BETWEEN $3 AND $4)
        GROUP BY fc.footage_id
        """,
        org_id,
        file_ids,
        span[0] if span else None,
        span[1] if span else None,
    )
    n = lambda x: int(x or 0)  # noqa: E731
    return {
        r["id"]: Unit(
            sha256=str(r["id"]),
            creative_ids=[],
            rep_creative_id=None,
            headline="",
            duration_s=None,
            spend=Decimal(r["spend"] or 0),
            impressions=n(r["impressions"]),
            plays=n(r["plays"]),
            video_3s_views=n(r["v3"]),
            thruplays=n(r["thru"]),
            p25=n(r["p25"]),
            p50=n(r["p50"]),
            p75=n(r["p75"]),
            p100=n(r["p100"]),
            link_clicks=n(r["clicks"]),
            purchases=n(r["purchases"]),
            first_day=r["first_day"],
            last_day=r["last_day"],
            facets=set(),
            numeric={},
            first_product_s=None,
            concept_summary=None,
            hook_verbatim=None,
        )
        for r in rows
        if r["impressions"]
    }


def _side(
    groups: dict[str, list[UUID]], keys: Iterable[str], by_id: dict[UUID, Unit]
) -> list[Unit]:
    return [by_id[f] for k in keys for f in groups.get(k, []) if f in by_id]


def _row(t: Test) -> dict:
    better = t.change < 0 if t.metric in LOWER_IS_BETTER else t.change > 0
    return {
        "metric": t.metric,
        "label": METRIC_LABEL[t.metric],
        "mine": t.cell_value,
        "theirs": t.comp_value,
        "change": t.change,
        "better": better,
        "p": t.p,
        "grade": t.grade,
        "n_mine": t.n_cell,
        "n_theirs": t.n_complement,
        "kind": t.kind,
        "confounds": t.confounds,
    }


def _tests(a: list[Unit], b: list[Unit], name: str) -> list[Test]:
    return compare(a, b, dimension="cluster", value=name)


def _pooled(side: list[Unit]) -> dict:
    """A side's own numbers, every impression and dollar pooled."""
    spend = float(sum(u.spend for u in side))
    imps = sum(u.impressions for u in side)
    plays = sum(u.plays for u in side)
    purchases = sum(u.purchases for u in side)
    return {
        "videos": len(side),
        "spend": spend,
        "purchases": purchases,
        "cpa": spend / purchases if purchases else None,
        "hook_rate": sum(u.video_3s_views for u in side) / imps if imps else None,
        "thruplay_rate": sum(u.thruplays for u in side) / plays if plays else None,
        "link_ctr": sum(u.link_clicks for u in side) / imps if imps else None,
    }


def typical(side: list[Unit]) -> dict:
    """The numbers the grades are made on: stop, ThruPlay and link CTR of the typical (median)
    video, among videos with enough delivery to have a rate; cost per purchase pooled."""
    rates = [unit_rates(u) for u in side]
    out: dict = {"cpa": _pooled(side)["cpa"]}
    for m in ("hook_rate", "thruplay_rate", "link_ctr"):
        vals = [r[m] for r in rates if m in r]
        out[m] = statistics.median(vals) if vals else None
    return out


def one_vs_rest(
    groups: dict[str, list[UUID]], names: dict[str, str], by_id: dict[UUID, Unit],
    account: dict[UUID, Unit] | None = None,
) -> list[dict]:
    """Every cluster against the rest: the other clusters, or, given `account` (every video in
    the account over the same days), the rest of the account. One BH pass over every test."""
    runs = []
    for k in groups:
        mine = _side(groups, [k], by_id)
        if account is not None:
            ids = set(groups[k])
            rest = [u for f, u in account.items() if f not in ids]
        else:
            rest = _side(groups, [o for o in groups if o != k], by_id)
        runs.append((k, mine, rest, _tests(mine, rest, k)))
    apply_grades([t for *_, ts in runs for t in ts])
    out = []
    for k, mine, rest, ts in runs:
        by_metric = {t.metric: _row(t) for t in ts}
        out.append({
            "key": k, "name": names.get(k, k), "totals": _pooled(mine), "rest": _pooled(rest),
            "metrics": [by_metric.get(m) for m in METRICS],
            "too_small": len(mine) < MIN_CELL, "typical": typical(mine), "_mine": mine, "_rest": rest,
        })
    return out


HOLDS = ("strong", "suggestive")
STEPS = ("hook_rate", "thruplay_rate", "link_ctr")


def _sums(side: list[Unit]) -> dict:
    """A side's pooled counts in the shape cs.experiments.why.decompose reads."""
    return {
        "spend": float(sum(u.spend for u in side)),
        "impressions": sum(u.impressions for u in side),
        "video_3s_views": sum(u.video_3s_views for u in side),
        "link_clicks": sum(u.link_clicks for u in side),
        "conversions": sum(u.purchases for u in side),
        "conversion_value": 0,
    }


# The four numbers every card shows, in this order: the ones the decisions are made on.
CARD_METRICS = ("cpa", "hook_rate", "thruplay_rate", "link_ctr")


def cards(results: list[dict], account: dict) -> dict:
    """The one-vs-rest results that hold up, as one card per cluster. A cluster with nothing
    that holds gets no card; it is named in `level` instead.

    Kinds, by what holds (strong or suggestive):
      scale             cost per purchase clearly lower
      attention_not_sale cost clearly higher while an attention step is clearly better
      cut               cost clearly higher
      weak_steps        cost not clearly different, one or more steps clearly worse
      strong_steps      cost not clearly different, one or more steps clearly better
    """
    from cs.experiments import why as why_mod

    out, level = [], []
    for c in results:
        if c["too_small"]:
            continue
        ms = {m["metric"]: m for m in c["metrics"] if m}
        held = {k: m for k, m in ms.items() if m["grade"] in HOLDS}
        cpa = ms.get("cpa")
        up_good = [ms[k] for k in STEPS if k in held and held[k]["better"]]
        up_bad = [ms[k] for k in STEPS if k in held and not held[k]["better"]]
        pct = lambda x: f"{round(abs(x) * 100)}%"  # noqa: E731
        name = c["name"]
        if "cpa" in held and cpa["better"]:
            kind, title = "scale", f"{name}: sales {pct(cpa['change'])} cheaper than the rest of the account"
        elif "cpa" in held and up_good:
            kind = "attention_not_sale"
            title = f"{name} wins attention but pays {pct(cpa['change'])} more per sale than the account"
        elif "cpa" in held:
            kind, title = "cut", f"{name} pays {pct(cpa['change'])} more per sale than the account"
        elif up_bad:
            kind = "weak_steps"
            what = {"hook_rate": "stops fewer people", "thruplay_rate": "holds fewer viewers",
                    "link_ctr": "gets fewer clicks"}
            title = f"{name} {_and([what[m['metric']] for m in up_bad])}"
            if cpa and cpa["change"] <= 0:
                title += ", yet sells no dearer"
        elif up_good:
            kind = "strong_steps"
            what = {"hook_rate": "stops more people", "thruplay_rate": "holds more viewers",
                    "link_ctr": "gets more clicks"}
            title = f"{name} {_and([what[m['metric']] for m in up_good])}"
            if cpa and cpa["change"] >= 0:
                title += ", but sells no cheaper"
        else:
            level.append(name)
            continue
        # Every card shows the same four numbers, cluster beside the whole account over the same
        # days, measured the way they are graded (typical video for rates, pooled for cost).
        stats = []
        for m in CARD_METRICS:
            t = ms.get(m)
            mine, theirs = c["typical"].get(m), account.get(m)
            # Colour only when the test holds and the numbers shown point the same way (the
            # test is against the rest of the account; the number shown is the whole account).
            shown_better = None
            if mine is not None and theirs is not None and mine != theirs:
                shown_better = (mine < theirs) if m in LOWER_IS_BETTER else (mine > theirs)
            holds = bool(t and t["grade"] in HOLDS and shown_better == t["better"])
            stats.append({
                "metric": m, "label": METRIC_LABEL[m], "mine": mine, "account": theirs,
                "grade": t["grade"] if t else None, "better": shown_better, "holds": holds,
            })
        why = None
        if "cpa" in held:
            dec = why_mod.decompose(_sums(c["_mine"]), _sums(c["_rest"]), "video", "account")
            why = _why(dec) if dec else None
        out.append({"key": c["key"], "kind": kind, "title": title, "stats": stats, "why": why,
                    "totals": c["totals"]})
    for c in results:
        c.pop("_mine", None)
        c.pop("_rest", None)
    order = {"scale": 0, "attention_not_sale": 1, "cut": 2, "weak_steps": 3, "strong_steps": 4}
    out.sort(key=lambda x: (order[x["kind"]], -x["totals"]["spend"]))
    return {"cards": out, "level": level,
            "too_small": [c["name"] for c in results if c["too_small"]]}


def _and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def _why(dec: dict) -> str | None:
    """Which step of cost per purchase the gap sits in, largest first, in one line."""
    from cs.experiments.why import MIN_PUSH, STEP_TEXT

    named = [s for s in dec["steps"] if s["push"] is not None and abs(s["push"]) >= MIN_PUSH]
    named.sort(key=lambda s: -abs(s["push"]))
    if not named:
        return "No single step of the funnel carries the gap; it is spread across all of them."
    words = [
        f"{STEP_TEXT[s['key']][1] if s['push'] < 0 else STEP_TEXT[s['key']][2]}"
        f" ({_fmt(s)} vs {_fmt(s, True)})"
        for s in named[:3]
    ]
    return _and(words).capitalize() + "."


def _fmt(s: dict, theirs: bool = False) -> str:
    v = s["theirs"] if theirs else s["mine"]
    return f"${v:.0f}" if s["key"] == "cpm" else f"{v * 100:.1f}%"
