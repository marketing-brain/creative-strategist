from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from math import comb
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.core.repo import rollups
from cs.experiments import adset_read, paired
from cs.make import test_read

router = APIRouter(prefix="/experiments")
_COPY_SUFFIX = re.compile(r"(\s*[–-]\s*Copy(\s*\d+)?)+\s*$", re.IGNORECASE)


def _step(r) -> dict:
    return {
        "key": r["key"],
        "title": r["title"],
        "detail": r["detail"],
        "status": r["status"],
        "waiting_on": r["waiting_on"],
        "progress_done": r["progress_done"],
        "progress_total": r["progress_total"],
        "cost_usd": float(r["cost_usd"]),
        "result_note": r["result_note"],
        "started_at": r["started_at"].isoformat() if r["started_at"] else None,
        "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
    }


def score(pairs: list[tuple[str, str]]) -> dict:
    """How well discovered groups match the labels. `pairs` is (label, group) per file.

    purity: share of files that sit with the majority label of their group (1.0 = no group
    mixes families). completeness: share of files in the group that holds most of their
    family (1.0 = no family is split). ARI: agreement corrected for chance (0 = random,
    1 = identical partitions)."""
    n = len(pairs)
    if n < 2:
        return {"files": n, "purity": None, "completeness": None, "ari": None}
    cells = Counter(pairs)
    by_group: dict[str, Counter] = defaultdict(Counter)
    by_label: dict[str, Counter] = defaultdict(Counter)
    for (lab, grp), c in cells.items():
        by_group[grp][lab] += c
        by_label[lab][grp] += c
    purity = sum(max(c.values()) for c in by_group.values()) / n
    completeness = sum(max(c.values()) for c in by_label.values()) / n
    index = sum(comb(c, 2) for c in cells.values())
    a = sum(comb(sum(c.values()), 2) for c in by_label.values())
    b = sum(comb(sum(c.values()), 2) for c in by_group.values())
    expected = a * b / comb(n, 2)
    top = (a + b) / 2
    ari = (index - expected) / (top - expected) if top != expected else None
    return {"files": n, "purity": purity, "completeness": completeness, "ari": ari}


@router.get("")
async def list_experiments(
    kind: str | None = None, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    """All experiments, or only tests / only research when `kind` is given."""
    rows = await db.fetch(
        """
        SELECT e.id, e.slug, e.title, e.question, e.status, e.created_at, e.kind,
               (SELECT count(DISTINCT i.footage_id) FROM experiment_item i WHERE i.experiment_id = e.id) AS files,
               (SELECT count(*) FILTER (WHERE s.status = 'done') FROM experiment_step s
                 WHERE s.experiment_id = e.id) AS steps_done,
               (SELECT count(*) FROM experiment_step s WHERE s.experiment_id = e.id) AS steps,
               (SELECT s.title FROM experiment_step s WHERE s.experiment_id = e.id
                  AND s.status <> 'done' ORDER BY s.ordinal LIMIT 1) AS current_step,
               (SELECT s.status FROM experiment_step s WHERE s.experiment_id = e.id
                  AND s.status <> 'done' ORDER BY s.ordinal LIMIT 1) AS current_status,
               (SELECT s.waiting_on FROM experiment_step s WHERE s.experiment_id = e.id
                  AND s.status <> 'done' ORDER BY s.ordinal LIMIT 1) AS waiting_on
        FROM experiment e WHERE e.org_id = $1 AND ($2::text IS NULL OR e.kind = $2) ORDER BY e.created_at DESC
        """,
        auth.org_id,
        kind,
    )
    return [
        {
            "id": str(r["id"]),
            "slug": r["slug"],
            "title": r["title"],
            "question": r["question"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat(),
            "files": int(r["files"]),
            "steps": int(r["steps"]),
            "steps_done": int(r["steps_done"]),
            "current_step": r["current_step"],
            "current_status": r["current_status"],
            "kind": r["kind"],
            "waiting_on": r["waiting_on"],
        }
        for r in rows
    ]


@router.get("/{experiment_id}")
async def get_experiment(
    experiment_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """Everything the page shows, in one read: the steps as they stand, every file with its
    label and (once they exist) its format description and discovered group, and the score."""
    e = await db.fetchrow(
        "SELECT * FROM experiment WHERE id=$1 AND org_id=$2", experiment_id, auth.org_id
    )
    if not e:
        raise HTTPException(404)
    steps = await db.fetch(
        "SELECT * FROM experiment_step WHERE experiment_id=$1 ORDER BY ordinal", experiment_id
    )
    run = await db.fetchval(
        "SELECT run FROM experiment_group WHERE experiment_id=$1 ORDER BY run DESC LIMIT 1",
        experiment_id,
    )
    groups = await db.fetch(
        "SELECT id, ordinal, name, definition FROM experiment_group"
        " WHERE experiment_id=$1 AND run=$2 ORDER BY ordinal",
        experiment_id,
        run,
    )
    items = await db.fetch(
        """
        SELECT i.footage_id, i.label, i.label_source, f.format, f.duration_s,
               d.answers->>'format' AS format_answer,
               (SELECT m.group_id FROM experiment_member m JOIN experiment_group g ON g.id = m.group_id
                 WHERE m.footage_id = i.footage_id AND g.experiment_id = i.experiment_id
                   AND g.run = $2 LIMIT 1) AS group_id,
               f.rep_creative_id,
               (SELECT ad.name FROM ad WHERE ad.creative_id = f.rep_creative_id
                 ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS ad_name,
               (SELECT t.path FROM asset t WHERE t.creative_id = f.rep_creative_id
                 AND t.kind = 'thumbnail' ORDER BY t.seq LIMIT 1) AS poster_path,
               (SELECT a.path FROM creative c JOIN asset a ON a.id = c.primary_asset_id
                 WHERE c.id = f.rep_creative_id) AS primary_path
        FROM experiment_item i JOIN footage f ON f.id = i.footage_id
        LEFT JOIN footage_description d ON d.footage_id = i.footage_id
             AND d.prompt_version = 'describe-v1'
        WHERE i.experiment_id = $1
        ORDER BY i.label, ad_name
        """,
        experiment_id,
        run,
    )
    out_items = []
    for r in items:
        poster = r["poster_path"] or (r["primary_path"] if r["format"] == "image" else None)
        out_items.append(
            {
                "id": str(r["footage_id"]),
                "creative_id": str(r["rep_creative_id"]) if r["rep_creative_id"] else None,
                "label": r["label"],
                "label_source": r["label_source"],
                "format": r["format"],
                "duration_s": float(r["duration_s"]) if r["duration_s"] is not None else None,
                "name": _COPY_SUFFIX.sub("", r["ad_name"]) if r["ad_name"] else None,
                "poster_url": await blobs.url_for(poster) if poster else None,
                "format_answer": r["format_answer"],
                "group_id": str(r["group_id"]) if r["group_id"] else None,
            }
        )
    # One pair per (file, ad set): a file in two ad sets is judged against each of them.
    pairs = [(i["label"], i["group_id"]) for i in out_items if i["group_id"]]
    # Videos an experiment produced (e.g. a generated edit and its side-by-side), stored as
    # experiment_result key 'media' so any experiment can show what it made.
    media_row = await db.fetchval(
        "SELECT payload FROM experiment_result WHERE experiment_id=$1 AND key='media'", experiment_id
    )
    media = [
        {**m, "url": await blobs.url_for(m["path"])}
        for m in ((media_row or {}).get("videos") or [])
        if str(m.get("path", "")).startswith(f"{auth.org_id}/")
    ]
    # A launched test reads itself back: each version against the original on its measure.
    test_read_out = await test_read.read(db, auth.org_id, experiment_id) if e["kind"] == "test" else None
    return {
        "media": media,
        "test_read": test_read_out,
        "id": str(e["id"]),
        "title": e["title"],
        "kind": e["kind"],
        "question": e["question"],
        "method": e["method"],
        "rules": e["rules"],
        "status": e["status"],
        "created_at": e["created_at"].isoformat(),
        "steps": [_step(s) for s in steps],
        "items": out_items,
        "run": run,
        "groups": [
            {"id": str(g["id"]), "name": g["name"], "definition": g["definition"]} for g in groups
        ],
        "score": score(pairs) if pairs else None,
    }


@router.get("/{experiment_id}/adsets")
async def experiment_adsets(
    experiment_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> list[dict]:
    """The ad sets an experiment's files are labeled with, for the ad set picker."""
    if not await db.fetchval(
        "SELECT 1 FROM experiment WHERE id=$1 AND org_id=$2", experiment_id, auth.org_id
    ):
        raise HTTPException(404)
    return await adset_read.adset_labels(db, experiment_id)


@router.get("/{experiment_id}/adset-read")
async def experiment_adset_read(
    experiment_id: UUID,
    label: str = Query(..., max_length=300),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """One ad set read three ways (cs.experiments.adset_read). Computed on request; no model."""
    if not await db.fetchval(
        "SELECT 1 FROM experiment WHERE id=$1 AND org_id=$2", experiment_id, auth.org_id
    ):
        raise HTTPException(404)
    out = await adset_read.read(db, auth.org_id, experiment_id, label)
    if out is None:
        raise HTTPException(404, "no files in that ad set")
    ids = [r["creative_id"] for f in out["formats"] for r in f["rows"] if r["creative_id"]]
    posters = {
        str(r["creative_id"]): r["path"]
        for r in await db.fetch(
            """
            SELECT c.id AS creative_id,
                   COALESCE((SELECT t.path FROM asset t WHERE t.creative_id = c.id
                              AND t.kind = 'thumbnail' ORDER BY t.seq LIMIT 1),
                            CASE WHEN c.format = 'image' THEN a.path END) AS path
            FROM creative c LEFT JOIN asset a ON a.id = c.primary_asset_id
            WHERE c.id = ANY($1::uuid[])
            """,
            ids,
        )
        if r["path"]
    }
    for f in out["formats"]:
        for r in f["rows"]:
            path = posters.get(r["creative_id"] or "")
            r["poster_url"] = await blobs.url_for(path) if path else None
    return out


@router.get("/{experiment_id}/patterns")
async def experiment_patterns(
    experiment_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    """Patterns across ad sets (cs.experiments.paired): each ad set a controlled test, tags
    counted by how often the side carrying them won. Computed on request; no model."""
    if not await db.fetchval(
        "SELECT 1 FROM experiment WHERE id=$1 AND org_id=$2", experiment_id, auth.org_id
    ):
        raise HTTPException(404)
    out = await paired.patterns(db, auth.org_id, experiment_id)
    # What chance alone would put under the leaning bar, so the page can say it plainly.
    out["expected_by_chance"] = round(out["tested"] * paired.P_LEANING)
    return out


# Duration buckets for the themes view: (upper edge in seconds, name); the last has no edge.
_DURATION = ((15, "Under 15s"), (30, "15–30s"), (60, "30–60s"), (90, "60–90s"), (120, "90–120s"), (180, "2–3 min"), (None, "3 min+"))


def _duration_bucket(s: float | None) -> tuple[int, str]:
    if s is None:
        return 99, "Unknown length"
    for i, (hi, name) in enumerate(_DURATION):
        if hi is None or s < hi:
            return i, name
    return len(_DURATION), _DURATION[-1][1]


@router.get("/{experiment_id}/themes")
async def experiment_themes(
    experiment_id: UUID,
    axis: str = Query("format", pattern="^(format|content|funnel_stage|funnel|duration|cluster|who|animated|reveal)$"),
    min_spend: float = Query(0, ge=0),
    window: str = Query("first_7d", pattern="^(first_7d|last_30d)$"),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """The experiment's videos grouped by one axis, each group with its files (and the
    model's reason for putting each there) and pooled numbers over `window`: each file's first
    7 days, or the account's last 30 days (the creative page's window: 30 days ending on the
    newest day with delivery)."""
    from cs.experiments import themes as th

    if not await db.fetchval(
        "SELECT 1 FROM experiment WHERE id=$1 AND org_id=$2", experiment_id, auth.org_id
    ):
        raise HTTPException(404)
    rows = await db.fetch(
        """
        SELECT DISTINCT ON (f.id) f.id, f.duration_s, f.rep_creative_id,
               (SELECT ad.name FROM ad WHERE ad.creative_id = f.rep_creative_id
                 ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS name,
               (SELECT t.path FROM asset t WHERE t.creative_id = f.rep_creative_id
                 AND t.kind = 'thumbnail' ORDER BY t.seq LIMIT 1) AS poster,
               w.spend, w.impressions, w.views_3s, w.link_clicks, w.purchases,
               COALESCE(life.spend, 0) AS lifetime_spend
        FROM experiment_item i JOIN footage f ON f.id = i.footage_id
        LEFT JOIN footage_metric_window w ON w.footage_id = f.id AND w.window_kind = 'first_7d'
        LEFT JOIN footage_metric_window life ON life.footage_id = f.id AND life.window_kind = 'lifetime'
        WHERE i.experiment_id = $1
        """,
        experiment_id,
    )
    # The spend filter reads lifetime spend: a video that proved itself over its whole run,
    # whatever the window shows.
    all_rows = len(rows)
    rows = [r for r in rows if float(r["lifetime_spend"]) >= min_spend]
    span = None
    idle = 0
    if window == "last_30d":
        span = await rollups.rollup_window(db, auth.org_id)
        sums = {}
        if span:
            for m in await db.fetch(
                """
                SELECT fc.footage_id, sum(m.spend) AS spend, sum(m.impressions) AS impressions,
                       sum(m.video_3s_views) AS views_3s, sum(m.link_clicks) AS link_clicks,
                       sum(m.purchases) AS purchases
                FROM metric_daily m JOIN footage_creative fc ON fc.creative_id = m.creative_id
                WHERE m.org_id = $1 AND fc.footage_id = ANY($2::uuid[]) AND m.day BETWEEN $3 AND $4
                GROUP BY fc.footage_id
                """,
                auth.org_id,
                [r["id"] for r in rows],
                span[0],
                span[1],
            ):
                sums[m["footage_id"]] = m
        kept = []
        for r in rows:
            m = sums.get(r["id"])
            if not m or not m["impressions"]:
                idle += 1
                continue
            d = dict(r)
            for k in ("spend", "impressions", "views_3s", "link_clicks", "purchases"):
                d[k] = m[k]
            kept.append(d)
        rows = kept
    labels: dict[str, dict[UUID, tuple[str, str | None]]] = defaultdict(dict)
    for r in await db.fetch(
        "SELECT footage_id, axis, value, reason FROM experiment_label WHERE experiment_id=$1",
        experiment_id,
    ):
        labels[r["axis"]][r["footage_id"]] = (r["value"], r["reason"])

    from cs.experiments import tag_clusters as tc

    clusters = tc.cluster_map(
        {f: {ax: labels[ax][f][0] for ax in ("who", "animated", "reveal") if f in labels[ax]}
         for f in labels.get("who", {})}
    )

    def key_of(r) -> tuple[int, str, str, str | None] | None:
        """(sort order, group key, group name, reason) for this video on the chosen axis."""
        # Axes stored as display labels (experiment 5's tag clusters), and their combination.
        if axis == "cluster":
            name = clusters.get(r["id"])
            if name is None:
                return None
            shown = "AI (animated or AI-generated)" if name == "AI" else name
            return 0, name, shown, labels["who"].get(r["id"], (None, None))[1]
        if axis in ("who", "animated", "reveal") or (axis == "duration" and labels.get("duration")):
            got = labels[axis].get(r["id"])
            return (0, got[0], got[0], got[1]) if got else None
        if axis == "duration":
            order, name = _duration_bucket(float(r["duration_s"]) if r["duration_s"] is not None else None)
            return order, name, name, None
        src = "funnel_stage" if axis == "funnel" else axis
        got = labels[src].get(r["id"])
        if not got:
            return None
        value, reason = got
        if axis == "funnel":
            roll = th.FUNNEL_ROLLUP.get(value, value)
            return ["top", "middle", "bottom"].index(roll), roll, f"{roll.capitalize()} of funnel", reason
        names = th.LABELS[src]
        keys = list(names)
        return (keys.index(value) if value in keys else 98), value, names.get(value, value), reason

    groups: dict[str, dict] = {}
    unlabelled = 0
    for r in rows:
        k = key_of(r)
        if k is None:
            unlabelled += 1
            continue
        order, gk, gname, reason = k
        g = groups.setdefault(gk, {"key": gk, "name": gname, "order": order, "files": [],
                                   "sums": defaultdict(float)})
        for m in ("spend", "impressions", "views_3s", "link_clicks", "purchases"):
            g["sums"][m] += float(r[m] or 0)
        g["files"].append(
            {
                "id": str(r["id"]),
                "creative_id": str(r["rep_creative_id"]) if r["rep_creative_id"] else None,
                "name": _COPY_SUFFIX.sub("", r["name"]) if r["name"] else None,
                "duration_s": float(r["duration_s"]) if r["duration_s"] is not None else None,
                "poster_url": await blobs.url_for(r["poster"]) if r["poster"] else None,
                "reason": reason,
                "spend": float(r["spend"] or 0),
            }
        )
    out = []
    for g in groups.values():
        s = g.pop("sums")
        imps = s["impressions"]
        g["totals"] = {
            "files": len(g["files"]),
            "spend": s["spend"],
            "purchases": int(s["purchases"]),
            "hook_rate": s["views_3s"] / imps if imps else None,
            "link_ctr": s["link_clicks"] / imps if imps else None,
            "cpa": s["spend"] / s["purchases"] if s["purchases"] else None,
            "median_duration_s": statistics.median(lens) if (lens := [f["duration_s"] for f in g["files"] if f["duration_s"] is not None]) else None,
        }
        g["files"].sort(key=lambda f: -f["spend"])
        out.append(g)
    # Largest first, 'other' and unknowns last.
    ordered = axis == "funnel" or (axis == "duration" and not labels.get("duration"))
    out.sort(key=lambda g: (g["key"] in ("other", "Other"), g["order"] if ordered else -len(g["files"])))
    # Which views this experiment's labels support, for the page's tabs.
    has = set(labels)
    axes = (
        [("cluster", "Clusters")]
        if "who" in has
        else [("format", "Format"), ("content", "Content"), ("funnel", "Funnel stage"),
              ("funnel_stage", "Awareness level"), ("duration", "Duration")]
    )
    return {"axis": axis, "axes": [{"key": k, "label": v} for k, v in axes], "groups": out,
            "unlabelled": unlabelled, "total": len(rows), "all": all_rows, "min_spend": min_spend,
            "window": window, "idle": idle,
            "span": {"start": span[0].isoformat(), "end": span[1].isoformat()} if span else None}


@router.get("/{experiment_id}/cluster-read")
async def experiment_cluster_read(
    experiment_id: UUID,
    key: str = Query(..., max_length=300),
    axis: str = Query("cluster", pattern="^(format|content|funnel_stage|funnel|duration|cluster|who|animated|reveal)$"),
    min_spend: float = Query(0, ge=0),
    window: str = Query("last_30d", pattern="^(first_7d|last_30d)$"),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """One cluster read the way an ad set is read (cs.experiments.adset_read): the cluster
    against the account, each video against the rest of the cluster and the account, why, and
    the decision cards. Its videos are exactly the ones the clusters view shows for `key` under
    the same spend filter and window; over the last 30 days all their delivery counts."""
    view = await experiment_themes(experiment_id, axis, min_spend, window, auth, db, blobs)
    group = next((g for g in view["groups"] if g["key"] == key), None)
    if group is None:
        raise HTTPException(404, "no such cluster under these filters")
    span = None
    if window == "last_30d" and view.get("span"):
        from datetime import date as _date

        span = (_date.fromisoformat(view["span"]["start"]), _date.fromisoformat(view["span"]["end"]))
    out = await adset_read.read(
        db, auth.org_id, experiment_id, None,
        file_ids=[UUID(f["id"]) for f in group["files"]], span=span, noun="cluster",
    )
    if out is None:
        raise HTTPException(404, "no delivery in this cluster")
    posters = {f["id"]: f["poster_url"] for f in group["files"]}
    for f in out["formats"]:
        for r in f["rows"]:
            r["poster_url"] = posters.get(r["id"])
    out["cluster"] = {"key": key, "name": group["name"], "files": len(group["files"])}
    return out


@router.get("/{experiment_id}/cluster-compare")
async def experiment_cluster_compare(
    experiment_id: UUID,
    axis: str = Query("cluster", pattern="^(format|content|funnel_stage|funnel|duration|cluster|who|animated|reveal)$"),
    min_spend: float = Query(0, ge=0),
    window: str = Query("last_30d", pattern="^(first_7d|last_30d)$"),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """Clusters against each other with the Insights tab's statistics (cs.experiments.cluster_compare):
    every cluster against all the others, and the results that hold up as decision cards. Same
    videos as the clusters view under the same filters.
    The first-7-days window reads lifetime delivery here: each video's own first week is not a
    shared span."""
    from datetime import date as _date

    from cs.experiments import cluster_compare as cc

    view = await experiment_themes(experiment_id, axis, min_spend, window, auth, db, blobs)
    from cs.experiments import tag_clusters as tc

    # "Other" is a mixed bag, not a kind of ad: it stays in the account, gets no card.
    groups = {g["key"]: [UUID(f["id"]) for f in g["files"]] for g in view["groups"] if g["key"] != tc.OTHER}
    names = {g["key"]: g["name"] for g in view["groups"]}
    span = (
        (_date.fromisoformat(view["span"]["start"]), _date.fromisoformat(view["span"]["end"]))
        if view.get("span") else None
    )
    # The account: every video with delivery over the same days, whatever its spend or tags.
    everything = [
        r["id"] for r in await db.fetch(
            "SELECT id FROM footage WHERE org_id = $1 AND format = 'video'", auth.org_id
        )
    ]
    account = await cc.units(db, auth.org_id, everything, span)
    by_id = {f: account[f] for fs in groups.values() for f in fs if f in account}
    vs_rest = cc.one_vs_rest(groups, names, by_id, account)
    acct = {**cc._pooled(list(account.values())), **cc.typical(list(account.values()))}
    # The top three videos by spend in each cluster, to show beside its card.
    thumbs = {
        g["key"]: [{k: f[k] for k in ("id", "creative_id", "name", "poster_url", "duration_s")} for f in g["files"][:3]]
        for g in view["groups"]
    }
    read = cc.cards(vs_rest, acct)
    # A cluster whose content has been read shows those findings instead of the generic funnel
    # comparison (the user, 2026-09-26: "replace the old, generic insights").
    content = {
        r["key"].split(":", 1)[1]: r["payload"].get("reviewed") or []
        for r in await db.fetch(
            "SELECT key, payload FROM experiment_result WHERE experiment_id=$1 AND key LIKE 'profile_read:%'",
            experiment_id,
        )
    }
    for c in read["cards"]:
        c["thumbs"] = thumbs.get(c["key"], [])
        c["content"] = [
            {"title": x["title"], "strength": x["strength"], "p": x.get("p")} for x in content.get(c["key"], [])
        ]
    return {
        **read,
        "account": acct,
        "vs_rest": vs_rest,
        "span": view.get("span"),
        "window": window,
    }


async def _stored_read(db: Database, blobs: BlobStore, org_id: UUID, experiment_id: UUID, key: str) -> dict:
    """A read stored under `key` by an experiment script, in exactly the shape
    /insights/account-read returns, so the page draws it with the Insights component."""
    from cs.insights.account_read import repo as account_read_repo

    row = await db.fetchrow(
        "SELECT r.payload, r.created_at FROM experiment_result r JOIN experiment e ON e.id = r.experiment_id"
        " WHERE r.experiment_id=$1 AND e.org_id=$2 AND r.key=$3",
        experiment_id,
        org_id,
        key,
    )
    if row is None:
        return {"status": "none"}
    p = row["payload"]
    page = p.get("page") or {}
    units = [dict(u) for u in page.get("units", [])]
    rep_ids = [UUID(u["rep_creative_id"]) for u in units if u.get("rep_creative_id")]
    posters = await account_read_repo.poster_paths(db, org_id, rep_ids)
    for u in units:
        path = posters.get(u.get("rep_creative_id") or "")
        u["poster_url"] = await blobs.url_for(path) if path else None
    summary = page.get("summary", {})
    return {
        "status": p["status"],
        "n_units": p.get("n_units"),
        "stats": p.get("stats"),
        "created_at": row["created_at"].isoformat(),
        "window_start": summary.get("first_day"),
        "window_end": summary.get("last_day"),
        "read": p.get("read"),
        "summary": summary,
        "tests": page.get("tests", []),
        "sibling_tests": page.get("sibling_tests", []),
        "units": units,
        "coverage": None,
        "error": p.get("error"),
        "newer_failed_error": None,
    }


@router.get("/{experiment_id}/within-read")
async def experiment_within_read(
    experiment_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """The Insights read redone within clusters (scripts/write_within_read.py)."""
    return await _stored_read(db, blobs, auth.org_id, experiment_id, "within_read")


@router.get("/{experiment_id}/cluster-insights")
async def experiment_cluster_insights(
    experiment_id: UUID,
    key: str = Query(..., max_length=300),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """The Insights read inside one cluster (scripts/write_cluster_reads.py): a written read when
    the cluster has more suggestive results than chance, else its counts ('chance'), or
    'insufficient' under 10 videos."""
    return await _stored_read(db, blobs, auth.org_id, experiment_id, f"cluster_read:{key}")


@router.get("/{experiment_id}/cluster-leads")
async def experiment_cluster_leads(
    experiment_id: UUID,
    key: str = Query(..., max_length=300),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """Leads from watching the videos (scripts/cluster_side_by_side.py): the 3 best and 3 worst
    videos of one cluster by cost per purchase, their first 30 seconds read side by side by a
    model. Not graded; each point names timestamps a person can check."""
    if not await db.fetchval(
        "SELECT 1 FROM experiment WHERE id=$1 AND org_id=$2", experiment_id, auth.org_id
    ):
        raise HTTPException(404)
    row = await db.fetchrow(
        "SELECT payload, created_at FROM experiment_result WHERE experiment_id=$1 AND key=$2",
        experiment_id, f"side_by_side:{key}",
    )
    if not row:
        return {"status": "none"}
    p = row["payload"]
    ids = [UUID(v["footage_id"]) for v in p["videos"]]
    extra = {
        r["id"]: r
        for r in await db.fetch(
            """
            SELECT f.id, f.rep_creative_id,
                   (SELECT t.path FROM asset t WHERE t.creative_id = f.rep_creative_id
                     AND t.kind = 'thumbnail' ORDER BY t.seq LIMIT 1) AS poster
            FROM footage f WHERE f.id = ANY($1::uuid[])
            """,
            ids,
        )
    }
    videos = []
    for v in p["videos"]:
        e = extra.get(UUID(v["footage_id"]))
        spend = re.search(r"spend \$([\d,]+)", v.get("card", ""))
        videos.append({
            "label": v["label"],
            "name": _COPY_SUFFIX.sub("", v["name"]) if v.get("name") else v["label"],
            "cpa": v["cpa"],
            "spend": float(spend.group(1).replace(",", "")) if spend else None,
            "rep_creative_id": str(e["rep_creative_id"]) if e and e["rep_creative_id"] else None,
            "poster_url": await blobs.url_for(e["poster"]) if e and e["poster"] else None,
        })
    return {"status": "done", "created_at": row["created_at"].isoformat(), "model": p.get("model"),
            "videos": videos, "answer": p["answer"]}


@router.get("/{experiment_id}/profile-read")
async def experiment_profile_read(
    experiment_id: UUID,
    key: str = Query(..., max_length=300),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """What separates a cluster's best videos from its worst, from blind profiles
    (scripts/write_profile_read.py): the insights checked against the data
    (scripts/review_profile_read.py) and the model's own read, each finding with the counts
    code computed and what those counts contradict."""
    if not await db.fetchval(
        "SELECT 1 FROM experiment WHERE id=$1 AND org_id=$2", experiment_id, auth.org_id
    ):
        raise HTTPException(404)
    row = await db.fetchrow(
        "SELECT payload, created_at FROM experiment_result WHERE experiment_id=$1 AND key=$2",
        experiment_id, f"profile_read:{key}",
    )
    if not row:
        return {"status": "none"}
    p = row["payload"]
    ids = [UUID(v["id"]) for v in p["videos"].values()]
    extra = {
        r["id"]: r
        for r in await db.fetch(
            """
            SELECT f.id, f.rep_creative_id,
                   (SELECT t.path FROM asset t WHERE t.creative_id = f.rep_creative_id
                     AND t.kind = 'thumbnail' ORDER BY t.seq LIMIT 1) AS poster
            FROM footage f WHERE f.id = ANY($1::uuid[])
            """,
            ids,
        )
    }
    videos = {}
    for code, v in p["videos"].items():
        e = extra.get(UUID(v["id"]))
        videos[code] = {
            "code": code,
            "name": _COPY_SUFFIX.sub("", v["name"]) if v.get("name") else code,
            "side": v["side"], "cpa": v["cpa"], "spend": v["spend"],
            "rep_creative_id": str(e["rep_creative_id"]) if e and e["rep_creative_id"] else None,
            "poster_url": await blobs.url_for(e["poster"]) if e and e["poster"] else None,
        }
    a = p.get("answer")
    return {
        "status": "done", "created_at": row["created_at"].isoformat(), "model": p.get("model"),
        "videos": videos, "reviewed": p.get("reviewed", []),
        "model_read": {"read": a["read"], "findings": a["findings"], "unexplained": a["unexplained"], "caveats": a["caveats"],
                       "errors": p.get("model_read_errors", [])} if a else None,
    }
