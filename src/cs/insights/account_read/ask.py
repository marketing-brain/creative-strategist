"""Ask box: map a plain-language question onto the stored run, compute the targeted comparisons,
write the answer under the same rules as the account read."""

from __future__ import annotations

import json
import re

from cs.core.config import Settings
from cs.insights.account_read.stats import benjamini_hochberg, compare, grade
from cs.insights.account_read.types import Test, Unit, to_json, unit_from_json
from cs.insights.account_read.writer import GeminiGatewayAccountReadWriter, _numbers, pattern_line

LAYER_METRICS = {
    "attention": ("hook_rate",),
    "retention": ("thruplay_rate",),
    "click": ("link_ctr",),
    "conversion": ("purchases_per_1k", "cpa"),
    "all": ("hook_rate", "thruplay_rate", "link_ctr", "purchases_per_1k", "cpa"),
}
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _default_writer(settings: Settings):
    return GeminiGatewayAccountReadWriter(settings)


ASK_WRITER_FACTORY = _default_writer


def _loads(text: str) -> dict:
    m = _FENCE.match(text)
    body = json.loads(m.group(1) if m else text)
    return body if isinstance(body, dict) else {}


def _vocab(units: list[Unit]) -> dict[str, list[str]]:
    counts: dict[tuple[str, str], int] = {}
    for u in units:
        for f in u.facets:
            counts[f] = counts.get(f, 0) + 1
    vocab: dict[str, list[str]] = {}
    for (d, v), n in sorted(counts.items()):
        if n >= 5:
            vocab.setdefault(d, []).append(v)
    return vocab


MAP_SYSTEM = """\
You translate a strategist's question about an ad account into a query over tagged creative attributes.
You are given the attribute dimensions and the values that exist with enough data. Return JSON:
{"targets": [<dimension> or [<dimension>, <value>], ...], "group": [<dimension>, <value>] or null, "layer": "attention" | "retention" | "click" | "conversion" | "all"}
- targets: the attribute(s) the question asks about. A bare dimension means "compare every value of it".
- group: when the question says "for each X" or "within X", the attribute that defines the group; else null. Use one concrete value that exists; if the question names a persona or audience, map it to the closest audience_callouts value.
- layer: "trust" or "hold" -> retention; "attention" or "hook" -> attention; "clicks" -> click; "CAC", "CPA", "conversions", "purchases" -> conversion; otherwise all.
If the question asks about something that is not a tagged attribute, return {"unmappable": "<what is missing, in one sentence>"}. Return valid JSON only.
"""  # noqa: E501

ANSWER_SYSTEM = """\
You are a senior performance-creative strategist answering one question about an ad account from precomputed, graded comparisons. Rules: reproduce numbers exactly as written and cite pattern IDs (P1, P2, ...); strong is evidence, suggestive a lead, directional a hint; name flags; cells under 10 files are small; hypothesis language; say plainly when the data shows no difference or has too few files. Return JSON: {"verdict": "answerable" | "directional" | "not yet", "answer": <3 to 7 sentences, direct answer first, then evidence, then caveat>, "pattern_ids": [...], "what_would_settle_it": <one sentence>}. Return valid JSON only.
"""  # noqa: E501


async def map_question(question: str, result_json: dict, writer) -> dict:
    """A query dict, or {"unmappable": reason} when the tags do not capture the question."""
    units = [unit_from_json(u) for u in result_json.get("units", [])]
    vocab = _vocab(units)
    user = (
        "DIMENSIONS AND VALUES:\n"
        + "\n".join(f"- {d}: {', '.join(vs)}" for d, vs in vocab.items())
        + f"\n\nQUESTION: {question}"
    )
    text, _, _ = await writer.complete(MAP_SYSTEM, user)
    body = _loads(text)
    if "unmappable" in body or not body.get("targets"):
        reason = body.get("unmappable") or "That asks about something the tags do not capture yet."
        return {"unmappable": str(reason)}
    targets = [tuple(t) if isinstance(t, list) else str(t) for t in body["targets"]]
    group = tuple(body["group"]) if body.get("group") else None
    layer = body.get("layer") if body.get("layer") in LAYER_METRICS else "all"
    return {"targets": targets, "group": group, "layer": layer}


def targeted_tests(query: dict, units: list[Unit], draws: int) -> list[Test]:
    group = query["group"]
    scope = [u for u in units if u.has(*group)] if group else list(units)
    group_label = f"{group[0]}={group[1]}" if group else None
    facets: list[tuple[str, str]] = []
    for t in query["targets"]:
        if isinstance(t, tuple):
            facets.append(t)
        else:
            facets += sorted({(d, v) for u in scope for d, v in u.facets if d == t})
    metrics = LAYER_METRICS[query["layer"]]
    tests: list[Test] = []
    for dim, val in facets:
        cell = [u for u in scope if u.has(dim, val)]
        comp = [u for u in scope if not u.has(dim, val)]
        found = compare(cell, comp, dimension=dim, value=val, group=group_label, draws=draws)
        tests += [t for t in found if t.metric in metrics]
    passing = benjamini_hochberg([t.p for t in tests]) if tests else set()
    for i, t in enumerate(tests):
        t.grade = grade(t.p, t.change, i in passing)
    tests.sort(key=lambda t: t.p)
    return tests


def cap_verdict(claimed: str, cited: list[dict]) -> str:
    strong = any(c["grade"] == "strong" for c in cited)
    clean_sugg = sum(1 for c in cited if c["grade"] == "suggestive" and not c["confounds"])
    if strong or clean_sugg >= 3:
        return "answerable"
    if any(c["grade"] in ("suggestive", "directional") for c in cited):
        return "directional"
    return "not yet"


async def answer(question: str, result_json: dict, writer, draws: int = 300) -> dict:
    query = await map_question(question, result_json, writer)
    if "unmappable" in query:
        return query
    units = [unit_from_json(u) for u in result_json.get("units", [])]
    tests = targeted_tests(query, units, draws)
    if not tests:
        return {
            "verdict": "not yet",
            "answer": (
                "Too few files carry those attributes to compare. Each side needs 5 files, "
                "and purchase metrics need 20 purchases per side."
            ),
            "pattern_ids": [],
            "tests": [],
            "what_would_settle_it": "More tagged videos with these attributes.",
        }
    lines = [pattern_line(f"P{i}", t) for i, t in enumerate(tests, start=1)]
    user = "QUESTION: " + question + "\n\nCOMPARISONS:\n" + "\n".join(lines)
    text, _, _ = await writer.complete(ANSWER_SYSTEM, user)
    body = _loads(text)
    bad = sorted(_numbers(str(body.get("answer", ""))) - _numbers(user))
    if bad:
        retry = user + (
            f"\n\nYour previous draft used numbers not in the comparisons: {bad}. "
            "Rewrite using only the numbers above."
        )
        text, _, _ = await writer.complete(ANSWER_SYSTEM, retry)
        body = _loads(text)
    ids = {f"P{i}" for i in range(1, len(tests) + 1)}
    cited_ids = [p for p in body.get("pattern_ids", []) if p in ids]
    cited = [to_json(tests[int(p[1:]) - 1]) for p in cited_ids]
    return {
        "verdict": cap_verdict(str(body.get("verdict", "not yet")), cited),
        "answer": str(body.get("answer", "")),
        "pattern_ids": cited_ids,
        "tests": [to_json(t) for t in tests],
        "what_would_settle_it": str(body.get("what_would_settle_it", "")),
    }
