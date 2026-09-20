"""The slice of an account-read run that the Insights page draws.

A run's `result` holds every graded test (about 13,000 on a mid-sized account, several
megabytes). The page shows the few the written read cites, plus the same attribute measured
on the other funnel metrics. That slice is stored beside the run so a page load never reads
`result`."""

from __future__ import annotations

import re

PAGE_VERSION = 1

UNIT_KEYS = (
    "sha256",
    "headline",
    "spend",
    "creative_ids",
    "rep_creative_id",
    "duration_s",
    "impressions",
    "purchases",
    "video_3s_views",
    "link_clicks",
)

_PID = re.compile(r"\bP(\d+)\b")


def _attr(t: dict) -> tuple:
    return (t.get("group"), t.get("dimension"), str(t.get("value")))


def build_page(result: dict, read: dict | None) -> dict:
    """`tests` are the cited ones, each tagged with its `pid` ("P<n>" is tests[n-1] in the
    run, and that numbering must survive the trim). `sibling_tests` measure an attribute a
    finding cites on the metrics the finding did not cite, so the page can show a click win
    that costs more per purchase. Siblings drop `cell_shas`: the cited test of the same
    attribute already carries the same files."""
    tests = result.get("tests") or []
    read = read or {}
    findings = read.get("findings") or []
    prose = [read.get("overview"), read.get("entangled"), read.get("no_evidence")]
    prose += [f.get("paragraph") for f in findings]
    cited = {int(n) for text in prose if text for n in _PID.findall(text)}
    in_findings = {
        int(m.group(1))
        for f in findings
        for pid in f.get("pattern_ids") or []
        if (m := _PID.fullmatch(pid))
    }
    cited |= in_findings
    cited = {n for n in cited if 1 <= n <= len(tests)}
    attrs = {_attr(tests[n - 1]) for n in in_findings if n in cited}
    siblings = [
        {**t, "cell_shas": []}
        for i, t in enumerate(tests, start=1)
        if i not in cited and _attr(t) in attrs
    ]
    return {
        "v": PAGE_VERSION,
        "summary": result.get("summary") or {},
        "tests": [{**tests[n - 1], "pid": f"P{n}"} for n in sorted(cited)],
        "sibling_tests": siblings,
        "units": [{k: u.get(k) for k in UNIT_KEYS} for u in result.get("units") or []],
    }
