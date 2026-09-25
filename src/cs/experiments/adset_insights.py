"""The ad set read, boiled down to a few decisions a buyer can act on. No model calls.

Each ad gets at most one card, for the one action its numbers support:
  scale        cheaper per purchase than both its ad set and the account
  cut          dearer than both
  recut        loses people in the first 3 seconds, but the rest of the ad works
  fix_middle   stops people, then loses them before the click
  fix_promise  gets the click, not the purchase
Cards are ranked by money at stake (the ad's spend times the size of its gap), and only the
top few are shown. Titles and numbers come from the read itself, so a card can never claim
more than the read found; the "why" line quotes the facts at the step that broke.
"""

from __future__ import annotations

import re
from typing import Any

MAX_CARDS = 4
MIN_GAP = 0.15  # the read's own bar for calling a difference

# Words in ad names that say when, who or what product, never what the ad is.
_SKIP = re.compile(
    r"^(magashwa|copy|by\w+|tt\d+|\d+\w{0,2}|testing|new|format|statics?|ads?|blog|inhouse|chatgpt|"
    r"creator|meta_ugc|ugc|tiktok|post|hook\w*|brief|image\d*|var\d+|s\d+(-\d+)?|r\d+|wk\d+|"
    r"\d{1,2}(st|nd|rd|th)?|\d{1,2}(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*|jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*|"
    r"keshav|yadu|vansh|divij|satvik|archish|moe|mayankjain|manini|shivangi|saket|trybe=.*)$",
    re.I,
)


def short_name(name: str | None) -> str:
    """A long ad name cut to what a buyer calls it: its version and its idea, e.g.
    'v212 · ThatGirlSaid34'. The idea is the last CamelCase or dotted handle that is not a
    date, an author or the product's own name; failing that, the longest plain word."""
    if not name:
        return "Untitled"
    base = re.sub(r"\s*[–-]\s*Copy(\s*\d+)?\s*$", "", name, flags=re.I)
    base = re.sub(r"(?<![a-z])v_s(\d+)", r"vS\1", base)  # keep "v_s111" whole through the split
    parts = [p for p in re.split(r"[_\s]+", base) if p]
    raw = next((p for p in parts if re.fullmatch(r"(v|META|vS)\d+", p)), None)
    version = "v_s" + raw[2:] if raw and raw.startswith("vS") else raw
    words = [p for p in parts if p != raw and re.search(r"[a-z]", p, re.I) and not _SKIP.match(p)
             and "rootlabs" not in p.lower()]
    shaped = [p for p in words if re.search(r"[a-z][A-Z]", p) or "." in p]
    idea = shaped[-1] if shaped else (max(words, key=len) if words else None)
    if version and idea:
        return f"{version} · {idea}"
    return (idea or version or base)[:40]


def _pct(x: float | None, digits: int = 0) -> str:
    return "–" if x is None else f"{x * 100:.{digits}f}%"


def _gap(x: float) -> str:
    return f"{round(abs(x) * 100)}%"


def _quote(text: str | None, words: int = 12) -> str | None:
    if not text:
        return None
    first = re.split(r"\s*/\s*", text.strip())[0]
    w = first.split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def _step(why: dict, key: str) -> dict | None:
    d = why.get("decomposition") or {}
    return next((s for s in d.get("steps", []) if s["key"] == key), None)


def _good(step: dict | None) -> bool | None:
    """True/False when a step clearly helped/hurt by at least the bar; None when level or unclear."""
    if not step or step.get("push") is None or not step.get("clear") or abs(step["push"]) < MIN_GAP:
        return None
    return step["push"] < 0


_WORD = {"better": True, "worse": False}


def _stat(label: str, mine: str, group: str, group_good: bool | None, account: str, account_good: bool | None) -> dict:
    """One number, compared twice: with the rest of its group (which colours it) and with the
    account over the same days."""
    return {"label": label, "mine": mine, "group": group, "group_good": group_good,
            "account": account, "account_good": account_good}


def _acct_step(why: dict, key: str) -> dict | None:
    return next((s for s in why.get("account_steps", []) if s["key"] == key), None)


def _step_stat(why: dict, key: str) -> dict | None:
    st, ac = _step(why, key), _acct_step(why, key)
    if not st:
        return None
    return _stat(st["label"], _val(st), _val(st, True), _good(st), _val(ac, True) if ac else "–", _good(ac))


def _cpa_stat(row: dict, d: dict, cpa) -> dict:
    acct = (row.get("account") or {}).get("cpa", {}).get("value")
    return _stat("Cost per purchase", cpa(d.get("cpa")), cpa(d.get("siblings_cpa")),
                 _WORD.get(row["vs_siblings"]["cpa"]), cpa(acct), _WORD.get(row["vs_account"]["cpa"]))


def _classify(row: dict, fmt: str) -> tuple[str, float] | None:
    """(kind, stake) for the one action this ad's numbers support, or None."""
    why = row["why"]
    d = why.get("decomposition")
    spend = row["totals"]["spend"]
    vs_set, vs_acct = row["vs_siblings"]["cpa"], row["vs_account"]["cpa"]
    gap = (d or {}).get("cpa_gap")
    if gap is not None and vs_set == "better" and vs_acct in ("better", "level"):
        return "scale", spend * abs(gap)
    main = why.get("main_step")
    if gap is not None and vs_set == "worse" and vs_acct == "worse":
        # Dearer overall, but if the only thing broken is the opening and the rest works,
        # the useful call is to recut, not to cut.
        click = _step(why, "click")
        if main == "stop" and click and click["clear"] and (click["push"] or 0) < 0:
            return "recut", spend * abs(gap)
        return "cut", spend * abs(gap)
    step = _step(why, main) if main else None
    if not step or step["push"] is None or step["push"] <= 0:
        return None  # its biggest clear step helped it: nothing to fix
    if main == "stop":
        click = _step(why, "click")
        if click and click["push"] is not None and click["push"] < 0:
            return "recut", spend * step["push"]
        return "recut", spend * step["push"] * 0.8
    if main in ("click", "ctr"):
        return "fix_middle", spend * step["push"]
    if main == "buy":
        return "fix_promise", spend * step["push"]
    return None


def _why(kind: str, row: dict, rows: list[dict]) -> str | None:
    """One line of facts at the step that matters, quoted rather than interpreted."""
    why = row["why"]
    opening = why.get("opening") or {}
    tags = why.get("step_tags") or {}
    n = why.get("siblings") or 0
    only = sorted(
        {f"{t['value'].replace('_', ' ')}" for ts in tags.values() for t in ts
         if t["siblings_with"] == 0 and not t["unreliable"]}
    )
    if kind == "recut":
        best = next((o for o in why.get("sibling_openings", []) if o.get("words")), None)
        mine, theirs = _quote(opening.get("words")), _quote(best.get("words") if best else None)
        if mine and theirs:
            return f"Opens with “{mine}” The best opener here: “{theirs}”"
        return f"Opens with “{mine}”" if mine else None
    if kind == "fix_middle":
        missing = _missing(row, rows, ("click", "ctr"))
        if missing:
            return f"Most of the others have {missing[0][0]} ({missing[0][1]} of {n}); this one doesn't."
        ret, sib = row.get("retention"), why.get("sibling_retention")
        if ret and sib:
            return f"Keeps {_pct(ret[1]['share'])} to the halfway point; the others keep {_pct(sib[1]['share'])}."
        return None
    if kind == "fix_promise":
        missing = _missing(row, rows, ("buy",))
        if missing:
            return f"Most of the others promise {missing[0][0]} ({missing[0][1]} of {n}); this one doesn't."
        return None
    if kind in ("scale", "cut"):
        parts = []
        if (q := _quote(opening.get("words"), 10)):
            parts.append(f"Opens with “{q}”")
        if only and kind == "scale":
            parts.append(f"the only ad here with {', '.join(only[:2])}")
        return "; ".join(parts) + "." if parts else None
    return None


def _missing(row: dict, rows: list[dict], steps: tuple[str, ...]) -> list[tuple[str, int]]:
    """Tags most siblings carry at these steps that this ad lacks, most common first."""
    def held(r: dict) -> set[tuple[str, str]]:
        # One set per ad: a tag listed under two steps still counts once, and "none" or
        # "other" is the absence of a trait, not a trait.
        return {
            (t["dimension"], t["value"])
            for st in steps
            for t in (r["why"].get("step_tags") or {}).get(st, [])
            if not t["unreliable"] and t["value"] not in ("none", "other")
        }

    mine = held(row)
    counts: dict[tuple[str, str], int] = {}
    others = [r for r in rows if r["id"] != row["id"]]
    for r in others:
        for key in held(r):
            counts[key] = counts.get(key, 0) + 1
    half = max(2, (len(others) + 1) // 2)
    out = [(v.replace("_", " "), c) for (dim, v), c in counts.items() if c >= half and (dim, v) not in mine]
    return sorted(out, key=lambda x: -x[1])


def _card(kind: str, row: dict, rows: list[dict], fmt: str, money, noun: str = "ad set") -> dict:
    why = row["why"]
    d = why.get("decomposition") or {}
    name = short_name(row["name"])
    gap = d.get("cpa_gap")
    cpa = lambda x: "–" if x is None else money(x)  # noqa: E731
    stats: list[dict] = []
    compare_with = None
    if kind == "scale":
        best_here = all(
            (r["totals"]["cpa"]["value"] or 1e9) >= (row["totals"]["cpa"]["value"] or 0)
            for r in rows if r["totals"]["cpa"]["readable"]
        )
        title = f"{name}: {f'cheapest sales in the {noun}' if best_here else f'{_gap(gap)} cheaper per sale'}"
        stats.append(_cpa_stat(row, d, cpa))
    elif kind == "cut":
        title = f"{name}: {_gap(gap)} dearer per sale, and worse than the account"
        stats.append(_cpa_stat(row, d, cpa))
        if why.get("main_step") and (st := _step_stat(why, why["main_step"])):
            stats.append(st)
    elif kind == "recut":
        title = (
            f"{name}: {_gap(gap)} dearer per sale, but only the opening is broken"
            if gap is not None and gap >= MIN_GAP
            else f"{name}: loses people in the first 3 seconds; the rest works"
        )
        stop, click = _step(why, "stop"), _step(why, "click")
        stats.append(_step_stat(why, "stop"))
        if click and click["push"] is not None and click["push"] < 0:
            stats.append(_step_stat(why, "click"))
        best = next((o for o in why.get("sibling_openings", []) if o.get("words")), None)
        compare_with = best["id"] if best else None
    elif kind == "fix_middle":
        step = _step(why, "click") or _step(why, "ctr")
        stop = _step(why, "stop")
        title = (
            f"{name} stops people, then loses them before the click"
            if stop and (stop["push"] or 0) <= 0.1
            else f"{name} loses people before the click"
        )
        if stop:
            stats.append(_step_stat(why, "stop"))
        stats.append(_step_stat(why, step["key"]))
    else:  # fix_promise
        title = f"{name} gets the click, not the sale"
        stats.append(_step_stat(why, "buy"))
    return {
        "kind": kind,
        "title": title,
        "stats": [st for st in stats if st],
        "why": _why(kind, row, rows),
        "ad": row["id"],
        "compare_with": compare_with,
        "spend_share": why.get("spend_share"),
    }


def _val(step: dict | None, theirs: bool = False) -> str:
    if not step:
        return "–"
    v = step["theirs"] if theirs else step["mine"]
    if v is None:
        return "–"
    return f"${v:,.0f}" if step["key"] == "cpm" else _pct(v, 1 if v < 0.1 else 0)


def headline(f: dict, money, noun: str = "ad set") -> dict | None:
    """The ad set (or cluster) as a whole against the account, in one line."""
    d = f.get("diagnosis")
    if not d:
        return None
    tiers = {t["key"]: t for t in d["tiers"]}
    bits = []
    for key in ("attention", "conversion"):
        t = tiers.get(key)
        if not t or not t.get("metrics"):
            continue
        m = t["metrics"][0]
        if m.get("value") is None or m.get("baseline") is None:
            continue
        is_money = key == "conversion"
        show = (lambda x: money(x)) if is_money else (lambda x: _pct(x))
        # Graded on the number shown, not the tier: the conversion tier's status reads the steps
        # after the click, so "behind on CPA" could sit beside a lower CPA. `lift` is signed so
        # that positive is better (CPA's is flipped in the diagnosis).
        lift = m.get("lift")
        status = (
            "strong" if lift is not None and lift >= MIN_GAP
            else "weak" if lift is not None and lift <= -MIN_GAP
            else "on_par"
        )
        bits.append({"label": m["label"], "mine": show(m["value"]), "theirs": show(m["baseline"]),
                     "status": status})
    status = [b["status"] for b in bits]
    if "weak" in status and "strong" in status:
        ahead = next(b["label"] for b in bits if b["status"] == "strong")
        behind = next(b["label"] for b in bits if b["status"] == "weak")
        title = f"Against the account: ahead on {ahead}, behind on {behind}"
    elif "weak" in status:
        title = f"As a whole, this {noun} trails the account"
    elif "strong" in status:
        title = f"As a whole, this {noun} beats the account"
    else:
        title = f"As a whole, this {noun} is on par with the account"
    return {"title": title, "stats": bits}


GROUP_TITLE = {
    "cut": "{n} ads cost more per sale than the rest and the account",
    "recut": "{n} ads lose people in the first 3 seconds",
    "fix_middle": "{n} ads lose people before the click",
    "fix_promise": "{n} ads get clicks that don't buy",
}


def cards(f: dict, money, noun: str = "ad set") -> list[dict]:
    """The top decisions, one card per ad; ads that need the same fix share one card, so
    four near-identical cards become one line with four ads."""
    rows = f["rows"]
    picked = []
    for r in rows:
        c = _classify(r, f["format"])
        if c:
            picked.append((c[1], c[0], r))
    picked.sort(key=lambda x: -x[0])
    by_kind: dict[str, list] = {}
    for stake, kind, r in picked:
        by_kind.setdefault(kind, []).append((stake, r))
    out = []
    for kind, items in by_kind.items():
        if kind == "scale" or len(items) == 1:
            out += [(stake, _card(kind, r, rows, f["format"], money, noun)) for stake, r in items]
            continue
        lead = _card(kind, items[0][1], rows, f["format"], money, noun)
        lead["title"] = GROUP_TITLE[kind].format(n=len(items))
        lead["also"] = [r["id"] for _, r in items[1:]]
        # Each ad with its own number at the step that broke, worst first; no shared figures.
        step_key = {"recut": "stop", "fix_promise": "buy"}.get(kind)
        lines = []
        for _, r in items:
            key = step_key or r["why"].get("main_step") or ("click" if f["format"] == "video" else "ctr")
            got = _step_stat(r["why"], key) or _step_stat(r["why"], "ctr")
            if got:
                lines.append({"ad": r["id"], "name": short_name(r["name"]), **got})
        lead["stats"] = []
        lead["ads"] = lines
        lead["why"] = None
        out.append((sum(st for st, _ in items), lead))
    out.sort(key=lambda x: -x[0])
    return [c for _, c in out[:MAX_CARDS]]
