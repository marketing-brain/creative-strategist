"""Reading a launched test back, so What works learns from it.

A test (experiment of kind 'test', made at launch in Make) lists its versions with the ad name each
was to run under, the finding it tests and the measure it is judged on. Each version is matched to
its Meta ads by that name (Fivetran ad names); the original is the parent video's ads in the same
ad sets. Delivery since launch is summed per side, and the version's rate is compared with the
original's on its measure with a two-sided two-proportion z-test:

- share still watching at 3s: 3-second views / impressions (needs HOOK_IMPRESSIONS a side);
- click rate: link clicks / 3-second views (needs MIN_CLICKS clicks in all);
- purchase rate or cost per purchase: purchases / link clicks (needs PURCHASES_PER_AD a side).

Verdicts: won or lost (p < 0.05, the version better or worse), no difference (enough delivery, no
clear gap), waiting (not enough yet), not found (no ad with that name). A finding's tests are shown
on What works next to it; a lead that wins is marked as confirmed by a test, one that loses as
failed. The playbook's own tiers are never rewritten by a single test.
"""

from __future__ import annotations

import math
from datetime import date
from uuid import UUID

from cs.core.db import Database
from cs.make.batch import HOOK_IMPRESSIONS, PURCHASES_PER_AD

MIN_CLICKS = 100
ALPHA = 0.05


def measure(read_on: str) -> str:
    t = (read_on or "").lower()
    if "3s" in t or "watching" in t:
        return "stop"
    if "purchase" in t or "buy" in t or "cost" in t:
        return "buy"
    return "click"


def _ratio(sums: dict, m: str) -> tuple[float, float]:
    """(successes, trials) for the measure."""
    if m == "stop":
        return sums["v3"], sums["imp"]
    if m == "click":
        return sums["clicks"], sums["v3"]
    return sums["buys"], sums["clicks"]


def _enough(m: str, a: dict, b: dict) -> bool:
    if m == "stop":
        return a["imp"] >= HOOK_IMPRESSIONS and b["imp"] >= HOOK_IMPRESSIONS
    if m == "click":
        return a["clicks"] + b["clicks"] >= MIN_CLICKS
    return a["buys"] >= PURCHASES_PER_AD and b["buys"] >= PURCHASES_PER_AD


def z_test(x1: float, n1: float, x2: float, n2: float) -> float | None:
    """Two-sided p-value for p1 == p2."""
    if n1 <= 0 or n2 <= 0:
        return None
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (x1 / n1 - x2 / n2) / se
    return math.erfc(abs(z) / math.sqrt(2))


async def _sums(db: Database, org_id: UUID, ad_ids: list[UUID], since: date) -> dict:
    r = await db.fetchrow(
        "SELECT coalesce(sum(impressions),0) imp, coalesce(sum(video_3s_views),0) v3, coalesce(sum(link_clicks),0) clicks,"
        " coalesce(sum(purchases),0) buys, coalesce(sum(spend),0) spend FROM metric_daily"
        " WHERE org_id=$1 AND ad_id = ANY($2::uuid[]) AND day >= $3",
        org_id,
        ad_ids,
        since,
    )
    return {k: float(r[k]) for k in ("imp", "v3", "clicks", "buys", "spend")}


async def read(db: Database, org_id: UUID, experiment_id: UUID) -> dict:
    e = await db.fetchrow("SELECT id, rules, created_at FROM experiment WHERE id=$1 AND org_id=$2 AND kind='test'", experiment_id, org_id)
    if e is None:
        return {"arms": []}
    launched = await db.fetchval(
        "SELECT coalesce(finished_at, $2) FROM experiment_step WHERE experiment_id=$1 AND key='launch'", experiment_id, e["created_at"]
    )
    since = (launched or e["created_at"]).date()
    parents = [r["footage_id"] for r in await db.fetch("SELECT footage_id FROM experiment_item WHERE experiment_id=$1", experiment_id)]
    arms = []
    adsets: set[UUID] = set()
    for rule in e["rules"] or []:
        name = (rule.get("ad_name") or "").strip()
        if not name:
            continue
        ads = await db.fetch(
            "SELECT id, adset_id FROM ad WHERE org_id=$1 AND (lower(name) = lower($2) OR lower(name) LIKE lower($2) || '%')",
            org_id,
            name,
        )
        adsets |= {a["adset_id"] for a in ads if a["adset_id"]}
        arms.append({"rule": rule, "ad_ids": [a["id"] for a in ads]})
    control_ids = [
        r["id"]
        for r in await db.fetch(
            """
            SELECT DISTINCT ad.id FROM ad JOIN footage_creative fc ON fc.creative_id = ad.creative_id
            WHERE ad.org_id=$1 AND fc.footage_id = ANY($2::uuid[]) AND ad.adset_id = ANY($3::uuid[])
            """,
            org_id,
            parents,
            list(adsets),
        )
    ] if adsets else []
    control = await _sums(db, org_id, control_ids, since) if control_ids else None
    out = []
    for a in arms:
        rule = a["rule"]
        m = measure(rule.get("effect") or "")
        base = {
            "title": rule.get("title"),
            "ad_name": rule.get("ad_name"),
            "finding_id": rule.get("finding_id"),
            "finding": rule.get("why"),
            "measure": m,
            "ads": len(a["ad_ids"]),
        }
        if not a["ad_ids"]:
            out.append({**base, "verdict": "not found"})
            continue
        if not control:
            out.append({**base, "verdict": "waiting", "why": "the original has no ads in the same ad set yet"})
            continue
        mine = await _sums(db, org_id, a["ad_ids"], since)
        x1, n1 = _ratio(mine, m)
        x0, n0 = _ratio(control, m)
        row = {**base, "version": {"x": x1, "n": n1, "rate": x1 / n1 if n1 else None}, "original": {"x": x0, "n": n0, "rate": x0 / n0 if n0 else None}}
        if not _enough(m, mine, control):
            out.append({**row, "verdict": "waiting"})
            continue
        p = z_test(x1, n1, x0, n0)
        better = (x1 / n1 if n1 else 0) > (x0 / n0 if n0 else 0)
        verdict = "no difference" if p is None or p >= ALPHA else ("won" if better else "lost")
        out.append({**row, "p": p, "verdict": verdict})
    return {"since": since.isoformat(), "arms": out}


async def by_finding(db: Database, org_id: UUID) -> dict[str, list[dict]]:
    """Every test verdict, grouped by the finding it tested."""
    res: dict[str, list[dict]] = {}
    for e in await db.fetch("SELECT id, title FROM experiment WHERE org_id=$1 AND kind='test' ORDER BY created_at DESC", org_id):
        r = await read(db, org_id, e["id"])
        for a in r["arms"]:
            if a.get("finding_id"):
                res.setdefault(a["finding_id"], []).append({**a, "experiment_id": str(e["id"]), "test": e["title"]})
    return res


def summary(tests: list[dict]) -> dict | None:
    """What What works shows for a finding: confirmed, failed, mixed or in a test."""
    if not tests:
        return None
    won = sum(t["verdict"] == "won" for t in tests)
    lost = sum(t["verdict"] == "lost" for t in tests)
    state = "confirmed" if won and not lost else "failed" if lost and not won else "mixed" if won and lost else "testing"
    return {"state": state, "won": won, "lost": lost, "tests": len(tests)}
