"""Why one ad did what it did against the other ads in its ad set. No model calls.

A. Where the gap comes from. Cost per purchase is a product of steps:

       CPA = (cost per 1,000 impressions / 1,000) / (stop rate x click-after-stop x buy-after-click)

   (an image has no stop step: click-through takes both middle places). So the ratio of an
   ad's CPA to its siblings' is the product of each step's ratio, and each step's log-ratio is
   its exact share of the gap. A step is "clear" when its 95% range on the ad's side leaves out
   the siblings' number; otherwise the swing may be noise and is shown as such.

B. What in the ad sits at the step that matters, from facts already held: the opening (first
   words, text and scenes in the first 3 seconds) for the stop step; the call to action, proof
   and watch-through for the click step; what the ad promises (offer, price, claims) for the buy
   step, since every ad in an ad set sends people to the same page; and who Meta showed it to
   for the cost step. Always beside the siblings' version of the same thing.

The step analysis says where; the evidence says what was there. It is still one ad against a
handful, and Meta gives winners more budget and better audiences, so the page shows each ad's
share of spend and days live beside it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any
from uuid import UUID

from cs.core.db import Database

Z = 1.96
MIN_PUSH = 0.10  # a step moving the gap by under 10% is not worth naming
OPENING_S = 3.0


def _f(x: Any) -> float:
    return float(x or 0)


def _wilson(k: float, n: float) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + Z * Z / n
    mid = (p + Z * Z / (2 * n)) / den
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


# key, label, what "better" means in words, what "worse" means
STEP_TEXT = {
    "cpm": ("Cost to reach 1,000 people", "Meta delivers it more cheaply", "it costs more to deliver"),
    "stop": ("Stop in the first 3 seconds", "more people stop on it", "fewer people stop in the first 3 seconds"),
    "click": ("Click, of those who stop", "more of those who stop go on to click", "fewer of those who stop click"),
    "ctr": ("Click, of those who see it", "more of those who see it click", "fewer of those who see it click"),
    "buy": ("Buy, of those who click", "more of those who click go on to buy", "fewer of those who click buy"),
}


def decompose(me: dict, rest: dict, fmt: str, noun: str = "ad set") -> dict | None:
    """Each step's value on both sides and how much it pushed cost per purchase. Returns None
    when either side has no delivery to compare."""
    imps, rimps = _f(me["impressions"]), _f(rest["impressions"])
    if imps <= 0 or rimps <= 0 or _f(me["spend"]) <= 0 or _f(rest["spend"]) <= 0:
        return None
    steps = []

    def add(key: str, mine: float | None, theirs: float | None, k: float, n: float, cost_like: bool):
        if not mine or not theirs:
            steps.append({"key": key, "mine": mine, "theirs": theirs, "push": None, "clear": False})
            return
        # For a rate, a higher value lowers cost; for CPM, a higher value raises it.
        log = math.log(mine / theirs) * (1 if cost_like else -1)
        rng = None if cost_like else _wilson(k, n)
        clear = n >= 1000 if cost_like else bool(rng and (rng[0] > theirs or rng[1] < theirs))
        steps.append(
            {
                "key": key,
                "mine": mine,
                "theirs": theirs,
                "low": rng[0] if rng else None,
                "high": rng[1] if rng else None,
                "push": math.exp(log) - 1,
                "log": log,
                "clear": clear,
            }
        )

    views, rviews = _f(me["video_3s_views"]), _f(rest["video_3s_views"])
    clicks, rclicks = _f(me["link_clicks"]), _f(rest["link_clicks"])
    conv, rconv = _f(me["conversions"]), _f(rest["conversions"])
    add("cpm", _f(me["spend"]) / imps * 1000, _f(rest["spend"]) / rimps * 1000, 0, imps, True)
    if fmt == "video" and views and rviews:
        add("stop", views / imps, rviews / rimps, views, imps, False)
        add("click", clicks / views if views else None, rclicks / rviews if rviews else None, clicks, views, False)
    else:
        add("ctr", clicks / imps, rclicks / rimps, clicks, imps, False)
    add("buy", conv / clicks if clicks else None, rconv / rclicks if rclicks else None, conv, clicks, False)

    cpa = _f(me["spend"]) / conv if conv else None
    rcpa = _f(rest["spend"]) / rconv if rconv else None
    aov = _f(me["conversion_value"]) / conv if conv else None
    raov = _f(rest["conversion_value"]) / rconv if rconv else None
    for s in steps:
        s["label"] = STEP_TEXT[s["key"]][0]
        s.pop("log", None)
    return {
        "cpa": cpa,
        "siblings_cpa": rcpa,
        "cpa_gap": cpa / rcpa - 1 if cpa and rcpa else None,
        "aov": aov,
        "siblings_aov": raov,
        "steps": steps,
        "summary": summary(steps, cpa, rcpa, noun),
    }


def summary(steps: list[dict], cpa: float | None, rcpa: float | None, noun: str = "ad set") -> str:
    """One sentence from the numbers: what the cost gap is and which steps drive it."""
    named = [s for s in steps if s["push"] is not None and s["clear"] and abs(s["push"]) >= MIN_PUSH]
    named.sort(key=lambda s: -abs(s["push"]))
    pct = lambda x: f"{'+' if x >= 0 else '−'}{round(abs(x) * 100)}%"  # noqa: E731
    reasons = [
        f"{STEP_TEXT[s['key']][1] if s['push'] < 0 else STEP_TEXT[s['key']][2]} ({pct(s['push'])})"
        for s in named
    ]
    if cpa and rcpa:
        gap = cpa / rcpa - 1
        head = (
            f"About level with the rest of the {noun} on cost per purchase ({pct(gap)})"
            if abs(gap) < MIN_PUSH
            else f"{'Cheaper' if gap < 0 else 'Dearer'} per purchase than the rest of the {noun} ({pct(gap)})"
        )
    else:
        head = "Too few purchases to compare cost"
    if not reasons:
        return f"{head}. No single step differs clearly enough to name."
    if len(reasons) == 1:
        return f"{head}: {reasons[0]}."
    return f"{head}: {', '.join(reasons[:-1])}, and {reasons[-1]}."


def main_step(steps: list[dict]) -> str | None:
    named = [s for s in steps if s["push"] is not None and s["clear"] and abs(s["push"]) >= MIN_PUSH]
    return max(named, key=lambda s: abs(s["push"]))["key"] if named else None


async def evidence(
    db: Database, org_id: UUID, label: str | None, file_ids: list[UUID]
) -> dict[UUID, dict[str, Any]]:
    """Facts per file for every step, read once for the whole ad set."""
    out: dict[UUID, dict[str, Any]] = {f: {"opening": {}, "tags": {}, "audience": {}} for f in file_ids}
    runs = {
        r["footage_id"]: r["run"]
        for r in await db.fetch(
            "SELECT DISTINCT ON (footage_id) footage_id, source_tag_run_id AS run FROM footage_attribute"
            " WHERE footage_id = ANY($1::uuid[]) AND source_tag_run_id IS NOT NULL",
            file_ids,
        )
    }
    if runs:
        by_run = {v: k for k, v in runs.items()}
        for r in await db.fetch(
            "SELECT tag_run_id, dimension, value, t_start_s FROM tag WHERE tag_run_id = ANY($1::uuid[])"
            " AND dimension IN ('hook_verbatim', 'overlay_texts') ORDER BY t_start_s NULLS FIRST, ordinal",
            list(by_run),
        ):
            o = out[by_run[r["tag_run_id"]]]["opening"]
            if r["dimension"] == "hook_verbatim":
                o["words"] = r["value"]
            elif (r["t_start_s"] or 0) < OPENING_S:
                o.setdefault("text", []).append(r["value"])
    for r in await db.fetch(
        "SELECT footage_id, label, raw, shows_face, shows_product FROM scene"
        " WHERE footage_id = ANY($1::uuid[]) AND t_start_s < $2 ORDER BY footage_id, ordinal",
        file_ids,
        OPENING_S,
    ):
        o = out[r["footage_id"]]["opening"]
        o.setdefault("scenes", []).append(r["raw"] or r["label"])
        o["face"] = o.get("face", False) or r["shows_face"]
        o["product"] = o.get("product", False) or r["shows_product"]
    for r in await db.fetch(
        "SELECT footage_id, dimension, value FROM footage_attribute WHERE footage_id = ANY($1::uuid[])",
        file_ids,
    ):
        out[r["footage_id"]]["tags"].setdefault(r["dimension"], []).append(r["value"])
    # Who Meta showed it to, in this ad set only: impressions and spend by age and gender.
    for r in await db.fetch(
        """
        SELECT fc.footage_id, m.age, m.gender, sum(m.impressions) AS imps, sum(m.spend) AS spend
        FROM metric_audience m JOIN ad a ON a.id = m.ad_id
        JOIN footage_creative fc ON fc.creative_id = a.creative_id
        WHERE m.org_id = $1 AND ($2::text IS NULL OR a.adset_name = $2) AND fc.footage_id = ANY($3::uuid[])
        GROUP BY fc.footage_id, m.age, m.gender
        """,
        org_id,
        label,
        file_ids,
    ):
        out[r["footage_id"]]["audience"][f"{r['gender']} {r['age']}"] = (_f(r["imps"]), _f(r["spend"]))
    return out


# Which tags speak to each step. Offer and price tags measured unreliable (agreement 0.28 and
# 0.50), so the page marks them as the tagger's guess.
STEP_TAGS = {
    "click": ("cta_modes", "proof_devices", "angles", "cta_style", "proof_type", "hook_type"),
    "ctr": ("cta_style", "proof_type", "hook_type", "layout", "offer_type", "message_angle"),
    "buy": ("offer_elements", "price_mentions", "offer_type", "angles", "message_angle"),
}
UNRELIABLE = {"offer_elements", "price_mentions", "hook_devices", "products_mentioned"}


def audience_shift(mine: dict, theirs: list[dict], top: int = 3) -> list[dict]:
    """The segments whose share of impressions differs most from the siblings', with what an
    impression cost in each on this ad."""
    tot = sum(v[0] for v in mine.values())
    pool: dict[str, float] = defaultdict(float)
    for t in theirs:
        for k, v in t.items():
            pool[k] += v[0]
    ptot = sum(pool.values())
    if not tot or not ptot:
        return []
    rows = []
    for k in set(mine) | set(pool):
        share = mine.get(k, (0, 0))[0] / tot
        pshare = pool.get(k, 0) / ptot
        imps, spend = mine.get(k, (0, 0))
        rows.append(
            {"segment": k, "share": share, "siblings_share": pshare,
             "cpm": spend / imps * 1000 if imps else None}
        )
    rows.sort(key=lambda r: -abs(r["share"] - r["siblings_share"]))
    return rows[:top]
