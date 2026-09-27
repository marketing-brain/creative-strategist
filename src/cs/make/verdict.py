"""Is this a good video, and which part of it is weak? From its last 30 days against a
baseline (its cluster's videos, and the whole account's), step by step: stopping the scroll (3-second views per impression), clicking (link
clicks per 3-second view) and buying (purchases per link click), plus cost per purchase.

The steps map onto parts of the video an editor can change: the opening stops people, the
middle makes the case that earns the click, and the close (offer, end card, what follows the
click) decides the purchase. A part is strong or weak when its step is at least 10% better or
worse than the baseline's. Every number here is measured; the sentence is assembled from them.

The cluster is the like-for-like baseline and leads: videos with the same carrier and reveal
share an opening style, so "weak opening" against the account can just mean "this kind of
video opens that way". The account is shown beside it. Both leave the video itself out.
"""

from __future__ import annotations

from uuid import UUID

from cs.core.db import Database

MIN_IMPRESSIONS = 5000
MIN_CLICKS = 100
MARGIN = 0.10
PART_OF = {"stop": "opening", "click": "middle", "buy": "close"}
PART_LABEL = {"opening": "opening", "middle": "middle", "close": "close"}


def _rates(r) -> dict[str, float | None]:
    imp, v3, clicks, buys, spend = (float(r[k] or 0) for k in ("impressions", "views_3s", "link_clicks", "purchases", "spend"))
    return {
        "stop": v3 / imp if imp else None,
        "click": clicks / v3 if v3 else None,
        "buy": buys / clicks if clicks else None,
        "cpa": spend / buys if buys else None,
        "impressions": imp,
        "clicks": clicks,
        "purchases": buys,
        "spend": spend,
        "_v3": v3,
        "videos": int(r["videos"]) if "videos" in r.keys() else 1,
    }


async def account_rates(db: Database, org_id: UUID, ad_account_id: UUID) -> dict:
    return _rates(await _sum(db, org_id, ad_account_id, None))


async def group_rates(db: Database, org_id: UUID, ad_account_id: UUID, footage_ids: list[UUID]) -> dict:
    return _rates(await _sum(db, org_id, ad_account_id, footage_ids))


async def _sum(db: Database, org_id: UUID, ad_account_id: UUID, footage_ids: list[UUID] | None):
    return await db.fetchrow(
        """
        SELECT sum(w.impressions) impressions, sum(w.views_3s) views_3s, sum(w.link_clicks) link_clicks,
               sum(w.purchases) purchases, sum(w.spend) spend, count(*) videos
        FROM footage f JOIN footage_metric_window w ON w.footage_id = f.id AND w.window_kind = 'last_30d'
        WHERE f.org_id = $1 AND f.ad_account_id = $2 AND f.format = 'video'
          AND ($3::uuid[] IS NULL OR f.id = ANY($3::uuid[]))
        """,
        org_id,
        ad_account_id,
        footage_ids,
    )


def without(base: dict, v: dict | None) -> dict:
    """The baseline with this video's own numbers taken out, so it is not compared with itself."""
    if not v:
        return base
    raw = {k: max(0.0, base[k] - v[k]) for k in ("impressions", "clicks", "purchases", "spend")}
    v3 = max(0.0, base["_v3"] - v["_v3"])
    out = _rates({"impressions": raw["impressions"], "views_3s": v3, "link_clicks": raw["clicks"], "purchases": raw["purchases"], "spend": raw["spend"]})
    out["videos"] = max(0, base.get("videos", 0) - 1)
    return out


async def video_rates(db: Database, footage_id: UUID) -> dict | None:
    r = await db.fetchrow(
        "SELECT impressions, views_3s, link_clicks, purchases, spend FROM footage_metric_window"
        " WHERE footage_id=$1 AND window_kind='last_30d'",
        footage_id,
    )
    return _rates(r) if r else None


def verdict(v: dict | None, acct: dict, worn: bool = False) -> dict:
    """The verdict for one video against one baseline (`acct`): overall, per step, and the part
    to work on."""
    if not v or v["impressions"] < MIN_IMPRESSIONS:
        return {"kind": "unknown", "headline": "Not enough delivery in the last 30 days to judge", "steps": [], "weak": None, "strong": []}
    steps = []
    for key, floor in (("stop", MIN_IMPRESSIONS), ("click", MIN_IMPRESSIONS), ("buy", MIN_CLICKS)):
        mine, base = v[key], acct[key]
        enough = (v["impressions"] if key != "buy" else v["clicks"]) >= floor
        ratio = mine / base if (mine is not None and base) else None
        state = None
        if enough and ratio is not None:
            state = "strong" if ratio >= 1 + MARGIN else "weak" if ratio <= 1 - MARGIN else "even"
        steps.append({"step": key, "part": PART_OF[key], "value": mine, "base": base, "ratio": ratio, "state": state})
    weak = min((s for s in steps if s["state"] == "weak"), key=lambda s: s["ratio"], default=None)
    strong = [s["part"] for s in steps if s["state"] == "strong"]
    cpa, acpa = v["cpa"], acct["cpa"]
    overall = (
        "good" if cpa and acpa and cpa <= acpa * (1 - MARGIN / 2)
        else "weak" if cpa and acpa and cpa >= acpa * (1 + MARGIN)
        else "even" if cpa and acpa
        else "unknown"
    )
    noun = {"good": "Good video", "even": "Average video", "weak": "Weak video", "unknown": "Video"}[overall]
    tail = []
    if strong:
        tail.append(f"strong {' and '.join(PART_LABEL[p] for p in strong)}")
    if weak:
        tail.append(f"weak {PART_LABEL[weak['part']]}")
    headline = noun + (", " + ", ".join(tail) if tail else "")
    if worn:
        headline += ", wearing out"
    if weak:
        advice = f"Work on the {PART_LABEL[weak['part']]}" + (f"; keep the {' and '.join(PART_LABEL[p] for p in strong)}." if strong else ".")
    elif strong:
        advice = f"No step is weak. Keep the {' and '.join(PART_LABEL[p] for p in strong)}; test new versions to refresh it."
    else:
        advice = "No step stands out against the account."
    return {
        "kind": overall,
        "headline": headline,
        "advice": advice,
        "cpa": cpa,
        "base_cpa": acpa,
        "steps": steps,
        "weak": weak["part"] if weak else None,
        "strong": strong,
    }
