"""One ad set, read three ways, from its own delivery only. No model calls.

1. The ad set against the account: its files pooled and run through the creative page's
   five-tier diagnosis (cs.insights.diagnosis.diagnose, unchanged), against every other file
   of the same format in the account over the same days.
2. The files inside it, ranked: each against the rest of the ad set, which shares its audience,
   budget and dates, so a gap is about the creative. Every rate carries a 95% range, and a gap
   is only called when the range clears the siblings' number.
3. What the best file does differently: the tags it has that most siblings lack, and the
   reverse, plus each video's watch-through steps in this ad set.

Only the experiment's files count (tagged videos and images): untagged videos are left out
of the ad set's own numbers, a rule the buyer set. Numbers are every day the file delivered
in this ad set.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from cs.core.db import Database
from cs.core.repo.creatives import _SUMS_SQL
from cs.experiments import adset_insights
from cs.experiments import why as why_mod
from cs.insights.diagnosis import FATIGUE_MIN_DAYS, Sums, diagnose

IMPRESSION_FLOOR = 1000  # the creative page's floor for attention rates
CLICK_FLOOR = 100  # and for anything about purchases
VIEW_FLOOR = 300  # 3-second views a hold rate needs
Z = 1.96
# A gap is called only when it is both real (the range clears the siblings' number) and big
# enough to matter: the creative page's own bar for colouring a gap. On a file with 50,000
# impressions the range is so narrow that a 2% difference would otherwise count.
GAP_BAR = 0.15

# Tags too unreliable or too specific to say what a file "does": measured agreement under 0.8
# (cs.insights.account_read.reliability) or a list of mentions rather than a trait.
SKIP_DIMS = {
    "duration_s", "cuts_per_10s", "aspect_ratio", "spoken_language", "subtitle_language",
    "price_mentions", "features_mentioned", "products_mentioned", "claims", "offer_elements",
    "hook_devices", "opening_shot", "product_reveal", "message_summary", "hook_summary",
}


def wilson(k: float, n: float) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + Z * Z / n
    mid = (p + Z * Z / (2 * n)) / den
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def count_range(n: float) -> tuple[float, float]:
    """95% range on a count (purchases), Wilson-style on a Poisson mean."""
    half = Z * math.sqrt(n + Z * Z / 4)
    mid = n + Z * Z / 2
    return max(0.0, mid - half), mid + half


def _f(x: Any) -> float:
    return float(x or 0)


def rates(s: dict[str, Any], fmt: str = "video") -> dict[str, Any]:
    """Each rate with its range and whether it clears the floor to be read at all. An image
    has no 3-second views, so its hook and hold are never readable."""
    imps, views, clicks = _f(s["impressions"]), _f(s["video_3s_views"]), _f(s["link_clicks"])
    spend, conv, value = _f(s["spend"]), _f(s["conversions"]), _f(s["conversion_value"])
    thru = _f(s["video_thruplay"])

    def rate(k: float, n: float, floor_ok: bool) -> dict[str, Any]:
        r = wilson(k, n)
        return {
            "value": k / n if n else None,
            "low": r[0] if r else None,
            "high": r[1] if r else None,
            "readable": floor_ok and n > 0,
        }

    lo_c, hi_c = count_range(conv)
    return {
        "spend": spend,
        "impressions": int(imps),
        "link_clicks": int(clicks),
        "purchases": int(_f(s["purchases"])),
        "hook_rate": rate(views, imps, fmt == "video" and imps >= IMPRESSION_FLOOR),
        "hold_rate": rate(thru, views, fmt == "video" and views >= VIEW_FLOOR),
        "link_ctr": rate(clicks, imps, imps >= IMPRESSION_FLOOR),
        # Cost per purchase: the range comes from the purchase count, so few sales -> wide.
        "cpa": {
            "value": spend / conv if conv else None,
            "low": spend / hi_c if hi_c else None,
            "high": spend / lo_c if lo_c > 0 else None,
            "readable": clicks >= CLICK_FLOOR and conv > 0,
        },
        "roas": {"value": value / spend if spend else None, "readable": clicks >= CLICK_FLOOR},
    }


def verdict(mine: dict, theirs: dict, higher_is_better: bool) -> str | None:
    """'better' / 'worse' only when this file's range clears the siblings' pooled value and
    the gap is at least GAP_BAR; 'level' otherwise; None when either side is too thin."""
    if not (mine.get("readable") and theirs.get("readable")):
        return None
    t, v = theirs.get("value"), mine.get("value")
    lo, hi = mine.get("low"), mine.get("high")
    if t is None or v is None or lo is None or hi is None or t == 0:
        return None
    if abs(v / t - 1) < GAP_BAR:
        return "level"
    if lo > t:
        return "better" if higher_is_better else "worse"
    if hi < t:
        return "worse" if higher_is_better else "better"
    return "level"


SUM_KEYS = (
    "impressions", "reach", "link_clicks", "spend", "video_3s_views", "video_plays",
    "video_p25", "video_p50", "video_p75", "video_p95", "video_p100", "video_thruplay",
    "video_watch_seconds", "landing_page_views", "content_views", "add_to_carts", "checkouts",
    "purchases", "post_reactions", "comments", "saves", "conversions", "conversion_value",
)


def _minus(a: dict, b: dict) -> dict:
    return {k: _f(a.get(k)) - _f(b.get(k)) for k in SUM_KEYS}


def _plus(rows: list[dict]) -> dict:
    return {k: sum(_f(r.get(k)) for r in rows) for k in SUM_KEYS}


async def adset_labels(db: Database, experiment_id: UUID) -> list[dict]:
    rows = await db.fetch(
        "SELECT label, count(DISTINCT footage_id) AS files FROM experiment_item"
        " WHERE experiment_id=$1 GROUP BY label ORDER BY label",
        experiment_id,
    )
    return [{"label": r["label"], "files": int(r["files"])} for r in rows]


async def read(
    db: Database,
    org_id: UUID,
    experiment_id: UUID,
    label: str | None,
    file_ids: list[UUID] | None = None,
    span: tuple[date, date] | None = None,
    noun: str = "ad set",
) -> dict | None:
    """One ad set (`label`), or any set of files read the same way (`file_ids`, a cluster).

    For an ad set, only the delivery of the ads in that ad set counts. For a set of files, all
    of each file's delivery counts. `span` (first day, last day) limits either to those days,
    e.g. the account's last 30 days. `noun` names the group on the page ("ad set", "cluster")."""
    if file_ids is None:
        files = await db.fetch(
            """
            SELECT f.id, f.format, f.duration_s, f.ad_account_id, f.rep_creative_id,
                   (SELECT ad.name FROM ad WHERE ad.creative_id = f.rep_creative_id
                     ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS name
            FROM experiment_item i JOIN footage f ON f.id = i.footage_id
            WHERE i.experiment_id = $1 AND i.label = $2
            """,
            experiment_id,
            label,
        )
    else:
        files = await db.fetch(
            """
            SELECT f.id, f.format, f.duration_s, f.ad_account_id, f.rep_creative_id,
                   (SELECT ad.name FROM ad WHERE ad.creative_id = f.rep_creative_id
                     ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS name
            FROM footage f WHERE f.org_id = $1 AND f.id = ANY($2::uuid[])
            """,
            org_id,
            file_ids,
        )
    if not files:
        return None
    ids = [f["id"] for f in files]
    # Per file, per day: an ad set's own ads, or every ad of the file within its first days.
    daily = await db.fetch(
        f"""
        SELECT fc.footage_id, m.day, {_SUMS_SQL}
        FROM ad a
        JOIN footage_creative fc ON fc.creative_id = a.creative_id
        JOIN footage f ON f.id = fc.footage_id
        JOIN metric_daily m ON m.ad_id = a.id
        WHERE a.org_id = $1 AND fc.footage_id = ANY($3::uuid[])
          AND ($2::text IS NULL OR a.adset_name = $2)
          AND ($4::date IS NULL OR m.day BETWEEN $4 AND $5)
        GROUP BY fc.footage_id, m.day
        """,
        org_id,
        label if file_ids is None else None,
        ids,
        span[0] if span else None,
        span[1] if span else None,
    )
    creatives = {
        r["footage_id"]: r["ids"]
        for r in await db.fetch(
            "SELECT footage_id, array_agg(creative_id) AS ids FROM footage_creative"
            " WHERE footage_id = ANY($1::uuid[]) GROUP BY footage_id",
            ids,
        )
    }
    attrs: dict[UUID, set[tuple[str, str]]] = defaultdict(set)
    for r in await db.fetch(
        "SELECT footage_id, dimension, value FROM footage_attribute WHERE footage_id = ANY($1::uuid[])",
        ids,
    ):
        if r["dimension"] not in SKIP_DIMS:
            attrs[r["footage_id"]].add((r["dimension"], r["value"]))
    answers = {
        r["footage_id"]: r["answers"]
        for r in await db.fetch(
            "SELECT footage_id, answers FROM footage_description WHERE footage_id = ANY($1::uuid[])"
            " AND prompt_version = 'describe-v1'",
            ids,
        )
    }

    by_file: dict[UUID, list[dict]] = defaultdict(list)
    for r in daily:
        by_file[r["footage_id"]].append(dict(r))

    out_formats = []
    for fmt in ("video", "image"):
        fs = [f for f in files if f["format"] == fmt]
        if not fs:
            continue
        days = [r["day"] for f in fs for r in by_file[f["id"]]]
        totals = {f["id"]: _plus(by_file[f["id"]]) for f in fs}
        pooled = _plus(list(totals.values()))

        # ---- 1. the ad set against the account
        diagnosis = None
        since = until = None
        if days:
            since, until = min(days), max(days)
            exclude = [c for f in fs for c in creatives.get(f["id"], [])]
            base = await db.fetchrow(  # always one row: an aggregate with no GROUP BY
                f"SELECT {_SUMS_SQL}, "
                "count(DISTINCT COALESCE(a.sha256, m.creative_id::text)) AS creatives "
                "FROM metric_daily m JOIN creative c ON c.id = m.creative_id "
                "LEFT JOIN asset a ON a.id = c.primary_asset_id "
                "WHERE m.org_id=$1 AND m.ad_account_id=$2 AND c.format=$3 "
                "AND m.creative_id <> ALL($4::uuid[]) AND m.day BETWEEN $5 AND $6",
                org_id,
                fs[0]["ad_account_id"],
                fmt,
                exclude,
                since,
                until,
            )
            days_live = (until - since).days + 1

            def window(a: date, b: date) -> Sums:
                return Sums.from_record(
                    _plus([r for f in fs for r in by_file[f["id"]] if a <= r["day"] <= b])
                )

            prior14 = trailing7 = None
            if days_live >= FATIGUE_MIN_DAYS:
                prior14 = window(until - timedelta(days=20), until - timedelta(days=7))
                trailing7 = window(until - timedelta(days=6), until)
            diagnosis = diagnose(
                fmt=fmt,
                window=Sums.from_record(pooled),
                baseline=Sums.from_record(base or {}),
                baseline_creatives=int((base or {}).get("creatives") or 0),
                prior14=prior14,
                trailing7=trailing7,
                days_live=days_live,
                # Videos of mixed lengths have no single length bucket, so retention is read
                # file by file in part 2 instead.
                duration_ms=None,
                window_start=since,
                window_end=until,
            ).to_dict()

        # ---- 2. the files inside, each against the rest of the ad set and against the account
        # The account side, per file: every other file of the same format in the account over
        # the days this file ran here. Built from two day-by-day queries (the whole account,
        # and these files everywhere) so it costs the same for 3 files or 30.
        acct_day: dict[date, dict] = {}
        file_day: dict[UUID, dict[date, dict]] = defaultdict(dict)
        if days:
            for r in await db.fetch(
                f"SELECT m.day, {_SUMS_SQL} FROM metric_daily m JOIN creative c ON c.id = m.creative_id"
                " WHERE m.org_id=$1 AND m.ad_account_id=$2 AND c.format=$3"
                " AND m.day BETWEEN $4 AND $5 GROUP BY m.day",
                org_id, fs[0]["ad_account_id"], fmt, since, until,
            ):
                acct_day[r["day"]] = dict(r)
            for r in await db.fetch(
                f"SELECT fc.footage_id, m.day, {_SUMS_SQL} FROM metric_daily m"
                " JOIN footage_creative fc ON fc.creative_id = m.creative_id"
                " WHERE m.org_id=$1 AND fc.footage_id = ANY($2::uuid[]) AND m.day BETWEEN $3 AND $4"
                " GROUP BY fc.footage_id, m.day",
                org_id, [f["id"] for f in fs], since, until,
            ):
                file_day[r["footage_id"]][r["day"]] = dict(r)

        def account_rest(fid: UUID) -> dict | None:
            mine_days = [r["day"] for r in by_file[fid]]
            if not mine_days:
                return None
            a, b = min(mine_days), max(mine_days)
            everyone = _plus([v for d_, v in acct_day.items() if a <= d_ <= b])
            itself = _plus([v for d_, v in file_day[fid].items() if a <= d_ <= b])
            return _minus(everyone, itself)

        rows = []
        acct_sums: dict[UUID, dict | None] = {}
        for f in fs:
            mine = totals[f["id"]]
            rest = _minus(pooled, mine)
            mr, rr = rates(mine, fmt), rates(rest, fmt)
            acct = account_rest(f["id"])
            acct_sums[f["id"]] = acct
            ar = rates(acct, fmt) if acct else None
            plays = _f(mine["video_plays"])
            steps = None
            if fmt == "video" and plays > 0:
                steps = [
                    {"label": lab, "share": _f(mine[k]) / plays}
                    for lab, k in (
                        ("25%", "video_p25"),
                        ("50%", "video_p50"),
                        ("75%", "video_p75"),
                        ("95%", "video_p95"),
                        ("100%", "video_p100"),
                    )
                ]
            rows.append(
                {
                    "id": str(f["id"]),
                    "creative_id": str(f["rep_creative_id"]) if f["rep_creative_id"] else None,
                    "name": f["name"],
                    "duration_s": float(f["duration_s"]) if f["duration_s"] is not None else None,
                    "days": len(by_file[f["id"]]),
                    "totals": mr,
                    "siblings": rr,
                    "vs_siblings": {
                        "hook_rate": verdict(mr["hook_rate"], rr["hook_rate"], True),
                        "hold_rate": verdict(mr["hold_rate"], rr["hold_rate"], True),
                        "link_ctr": verdict(mr["link_ctr"], rr["link_ctr"], True),
                        "cpa": verdict(mr["cpa"], rr["cpa"], False),
                    },
                    "account": ar,
                    "vs_account": {
                        "hook_rate": verdict(mr["hook_rate"], ar["hook_rate"], True) if ar else None,
                        "hold_rate": verdict(mr["hold_rate"], ar["hold_rate"], True) if ar else None,
                        "link_ctr": verdict(mr["link_ctr"], ar["link_ctr"], True) if ar else None,
                        "cpa": verdict(mr["cpa"], ar["cpa"], False) if ar else None,
                    },
                    "retention": steps,
                    "_attrs": attrs.get(f["id"], set()),
                    "_answers": answers.get(f["id"]),
                }
            )
        rows.sort(key=lambda r: -r["totals"]["spend"])

        # ---- why each file did what it did, against the rest of the ad set
        ev = await why_mod.evidence(db, org_id, label if file_ids is None else None, [f["id"] for f in fs])
        spend_total = _f(pooled["spend"])
        stop_rate = {
            r["id"]: r["totals"]["hook_rate"]["value"] if r["totals"]["hook_rate"]["readable"] else None
            for r in rows
        }
        for r in rows:
            fid = UUID(r["id"])
            me = totals[fid]
            others = [UUID(x["id"]) for x in rows if x["id"] != r["id"]]
            dec = why_mod.decompose(me, _minus(pooled, me), fmt, noun)
            # The same step breakdown against the account, so every number can be read both
            # ways: against ads like it, and against everything else that ran on those days.
            acct = acct_sums.get(fid)
            dec_acct = why_mod.decompose(me, acct, fmt) if acct else None
            e = ev.get(fid, {})
            tags = e.get("tags", {})
            step_tags = {}
            for step, dims in why_mod.STEP_TAGS.items():
                items = []
                for d_ in dims:
                    for v in tags.get(d_, []):
                        n = sum(1 for o in others if v in ev.get(o, {}).get("tags", {}).get(d_, []))
                        items.append({"dimension": d_, "value": v, "siblings_with": n,
                                      "unreliable": d_ in why_mod.UNRELIABLE})
                step_tags[step] = items
            r["why"] = {
                "decomposition": dec,
                "account_steps": dec_acct["steps"] if dec_acct else [],
                "main_step": why_mod.main_step(dec["steps"]) if dec else None,
                "spend_share": _f(me["spend"]) / spend_total if spend_total else None,
                "siblings": len(others),
                "opening": e.get("opening") or None,
                "sibling_openings": sorted(
                    [
                        {"id": str(o), "name": next((x["name"] for x in rows if x["id"] == str(o)), None),
                         "stop_rate": stop_rate.get(str(o)), **(ev.get(o, {}).get("opening") or {})}
                        for o in others
                    ],
                    key=lambda x: -(x["stop_rate"] or -1),
                ),
                "step_tags": step_tags,
                "sibling_retention": [
                    {"label": lab, "share": sum(x["retention"][i]["share"] for x in rows if x["id"] != r["id"] and x["retention"]) / n}
                    for i, lab in enumerate(("25%", "50%", "75%", "95%", "100%"))
                    if (n := sum(1 for x in rows if x["id"] != r["id"] and x["retention"]))
                ] or None,
                "audience": why_mod.audience_shift(
                    e.get("audience", {}), [ev.get(o, {}).get("audience", {}) for o in others]
                ),
                "described": {k: (answers.get(fid) or {}).get(k) for k in ("hook", "angle", "funnel_stage")}
                if answers.get(fid)
                else None,
            }

        # ---- 3. the best file, and what it does differently
        best = None
        conv = [r for r in rows if r["totals"]["cpa"]["readable"]]
        seen = [r for r in rows if r["totals"]["hook_rate"]["readable"]]
        if len(conv) >= 2:
            top = min(conv, key=lambda r: r["totals"]["cpa"]["value"])
            basis = "lowest cost per purchase among files with 100+ link clicks"
        elif len(seen) >= 2:
            top = max(seen, key=lambda r: r["totals"]["hook_rate"]["value"])
            basis = "highest hook rate (too few purchases here to rank on cost)"
        else:
            top, basis = None, None
        if top is not None:
            others = [r for r in rows if r is not top and r["_attrs"]]
            n = len(others)
            share = defaultdict(int)
            for r in others:
                for a in r["_attrs"]:
                    share[a] += 1
            has = sorted(a for a in top["_attrs"] if n and share[a] / n <= 1 / 3)
            lacks = sorted(a for a, c in share.items() if n and c / n >= 2 / 3 and a not in top["_attrs"])
            best = {
                "id": top["id"],
                "basis": basis,
                "compared_with": n,
                "has": [{"dimension": d, "value": v, "others": share[(d, v)]} for d, v in has],
                "lacks": [{"dimension": d, "value": v, "others": share[(d, v)]} for d, v in lacks],
                "described": {
                    k: (top["_answers"] or {}).get(k) for k in ("format", "hook", "angle")
                }
                if top["_answers"]
                else None,
            }
        for r in rows:
            r.pop("_attrs")
            r.pop("_answers")
        fread = {
            "format": fmt,
            "rows": rows,
            "diagnosis": diagnosis,
        }
        money = lambda x: f"${x:,.0f}"  # noqa: E731  (org 3 reports in USD)
        out_formats.append(
            {
                "cards": adset_insights.cards(fread, money, noun),
                "headline": adset_insights.headline(fread, money, noun),
                "format": fmt,
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
                "files": len(fs),
                "delivered": sum(1 for r in rows if r["totals"]["impressions"] > 0),
                "pooled": rates(pooled, fmt),
                "diagnosis": diagnosis,
                "rows": rows,
                "best": best,
            }
        )
    return {"label": label, "noun": noun, "formats": out_formats}
