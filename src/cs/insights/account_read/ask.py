"""Ask box: map a plain-language question onto the stored run, compute the targeted comparisons,
write the answer under the same rules as the account read."""

from __future__ import annotations

import json
import re

from cs.core.config import Settings
from cs.insights.account_read import analyzers
from cs.insights.account_read.stats import benjamini_hochberg, compare, grade
from cs.insights.account_read.types import (
    METRICS,
    Test,
    Unit,
    test_from_json,
    to_json,
    unit_from_json,
)
from cs.insights.account_read.writer import (
    METRIC_NAME,
    GeminiGatewayAccountReadWriter,
    _numbers,
    pattern_line,
)

LAYER_METRICS = {
    "attention": ("hook_rate",),
    "retention": ("thruplay_rate",),
    "click": ("link_ctr",),
    "conversion": ("purchases_per_1k", "cpa"),
    "all": ("hook_rate", "thruplay_rate", "link_ctr", "purchases_per_1k", "cpa"),
}
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)

KINDS = ("compare", "spend_gap", "untried", "nearest", "controlled", "recipe")
LAYER_WORDS = {
    "attention": "attention metrics",
    "retention": "retention metrics",
    "click": "click metrics",
    "conversion": "conversion metrics",
    "all": "all metrics",
}
_UNMAPPABLE = "That asks about something the tags do not capture yet."


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


def rank_metric_for_layer(layer: str) -> str:
    return "cpa" if layer == "all" else LAYER_METRICS[layer][0]


def _facet(v) -> tuple[str, str] | None:
    return (str(v[0]), str(v[1])) if isinstance(v, list | tuple) and len(v) == 2 else None


def _anchor(a, layer: str) -> dict:
    if isinstance(a, dict):
        if a.get("named"):
            return {"named": str(a["named"])}
        if a.get("best") in METRICS:
            return {"best": str(a["best"])}
    return {"best": rank_metric_for_layer(layer)}


def parse_query(body: dict) -> dict:
    """The model's map JSON -> a parsed query, or {"unmappable": reason}."""
    if "unmappable" in body:
        return {"unmappable": str(body["unmappable"])}
    raw_layer = body.get("layer")
    layer = raw_layer if isinstance(raw_layer, str) and raw_layer in LAYER_METRICS else "all"
    kind = body.get("kind")
    if kind not in KINDS:
        kind = "compare" if body.get("targets") else None
    if kind is None:
        return {"unmappable": _UNMAPPABLE}
    if kind == "compare":
        if not body.get("targets"):
            return {"unmappable": _UNMAPPABLE}
        targets = [tuple(t) if isinstance(t, list) else str(t) for t in body["targets"]]
        return {
            "kind": kind,
            "targets": targets,
            "group": _facet(body.get("group")),
            "layer": layer,
        }
    if kind in ("spend_gap", "untried"):
        return {"kind": kind, "layer": layer}
    if kind in ("nearest", "recipe"):
        return {"kind": kind, "anchor": _anchor(body.get("anchor"), layer), "layer": layer}
    target = _facet(body.get("target"))
    if target is None:
        return {"unmappable": "The attribute to re-test was not named."}
    control = body.get("control")
    parsed_control = "spend" if control == "spend" else (_facet(control) or "spend")
    return {"kind": "controlled", "target": target, "control": parsed_control, "layer": layer}


def _words(f) -> str:
    if isinstance(f, tuple):
        return f"{f[0].replace('_', ' ')} = {str(f[1]).replace('_', ' ')}"
    return f"every value of {str(f).replace('_', ' ')}"


def _anchor_words(a: dict) -> str:
    if "best" in a:
        return f"the best-{METRIC_NAME[a['best']]} file"
    return f'the file matching "{a["named"]}"'


def read_as(query: dict) -> str:
    """How the question was read, rendered from the parsed query so it cannot drift from what
    actually ran."""
    on = LAYER_WORDS[query["layer"]]
    kind = query["kind"]
    if kind == "compare":
        targets = ", ".join(_words(t) for t in query["targets"])
        within = f" within {_words(query['group'])}" if query.get("group") else ""
        return f"{targets} against everything else{within}, on {on}"
    if kind == "spend_gap":
        return f"spend share of every graded attribute against its effect, on {on}"
    if kind == "untried":
        return (
            f"pairs and triples of attributes with clean positive effects on {on} that no file "
            "carries together"
        )
    if kind == "nearest":
        return (
            f"the five files most like {_anchor_words(query['anchor'])} that do worse than it, "
            f"on {on}"
        )
    if kind == "controlled":
        c = query["control"]
        where = "within each spend tercile" if c == "spend" else f"inside and outside {_words(c)}"
        return f"{_words(query['target'])} re-tested {where}, on {on}"
    return (
        f"the ingredients of {_anchor_words(query['anchor'])}, ranked by their graded effects "
        f"on {on}"
    )


MAP_SYSTEM = """\
You translate a strategist's question about an ad account into one query over its tagged creative attributes and stored comparisons. You are given the attribute dimensions and the values that exist with enough data. Return JSON with a "kind" and that kind's fields:
- {"kind": "compare", "targets": [<dimension> or [<dimension>, <value>], ...], "group": [<dimension>, <value>] or null, "layer": L}. The default: "do X videos convert better", "which messenger works for magnesium users". targets: the attribute(s) asked about; a bare dimension means "compare every value of it". group: for "for each X" or "within X", one concrete value that exists; a persona or audience maps to the closest audience_callouts value; else null.
- {"kind": "spend_gap", "layer": L}: where the money goes versus where the evidence points; over- or under-funded attributes; budget allocation.
- {"kind": "untried", "layer": L}: proven attributes never combined; what to make next; gaps; untested combinations.
- {"kind": "nearest", "anchor": A, "layer": L}: what separates a winner from the ads most like it; why a lookalike failed; nearest neighbours of one video.
- {"kind": "controlled", "target": [<dimension>, <value>], "control": [<dimension>, <value>] or "spend", "layer": L}: is an effect real or explained by spend or by another attribute; "after controlling for"; "or is it just because we spent more".
- {"kind": "recipe", "anchor": A, "layer": L}: which ingredient of one video does the work; what makes our best ad work.
A (anchor) is {"best": <metric>} with metric one of hook_rate, thruplay_rate, link_ctr, purchases_per_1k, cpa, or {"named": "<the words in the question that identify the video>"}. "best CPA video" -> {"best": "cpa"}; "the pharmacist ad" -> {"named": "pharmacist"}; "our biggest ad" -> {"best": "cpa"}.
L (layer): "trust" or "hold" -> retention; "attention" or "hook" -> attention; "clicks" -> click; "CAC", "CPA", "conversions", "purchases" -> conversion; otherwise all.
If the question asks about something none of these kinds can answer from tagged attributes, return {"unmappable": "<what is missing, in one sentence>"}. Return valid JSON only.
"""  # noqa: E501

ANSWER_SYSTEM = """\
You are a senior performance-creative strategist answering one question about an ad account from precomputed, graded comparisons. Rules: reproduce numbers exactly as written and cite pattern IDs (P1, P2, ...); strong is evidence, suggestive a lead, directional a hint; name flags; cells under 10 files are small; hypothesis language; say plainly when the data shows no difference or has too few files. FACTS are descriptive counts and shares; COMPARISONS are graded tests. Cite both by id (F1, P1). The READ AS line is how the question was interpreted; if it misses the question, say so in one clause. Return JSON: {"verdict": "answerable" | "directional" | "not yet", "answer": <3 to 7 sentences, direct answer first, then evidence, then caveat>, "pattern_ids": [...], "what_would_settle_it": <one sentence>}. Return valid JSON only.
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
    return parse_query(_loads(text))


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


_SETTLE = "More tagged videos with these attributes."
_TOO_FEW = (
    "Too few files carry those attributes to compare. Each side needs 5 files, "
    "and purchase metrics need 20 purchases per side."
)


def _empty(base: dict, reason: str) -> dict:
    return {
        **base,
        "verdict": "not yet",
        "answer": reason,
        "pattern_ids": [],
        "facts": [],
        "tests": [],
        "what_would_settle_it": _SETTLE,
    }


def _analyse(query: dict, result_json: dict, draws: int) -> analyzers.Analysis | dict:
    """Run the kind's analyzer. Returns {"unmappable": ...} when the query names something the
    run does not hold."""
    units = [unit_from_json(u) for u in result_json.get("units", [])]
    if query["kind"] == "compare":
        found = targeted_tests(query, units, draws)
        return analyzers.Analysis(tests=found, empty_reason=None if found else _TOO_FEW)
    tests = [test_from_json(t) for t in result_json.get("tests", [])]
    overlaps = result_json.get("overlaps", [])
    metrics = LAYER_METRICS[query["layer"]]
    anchor = None
    metric = None
    if query["kind"] in ("nearest", "recipe"):
        a = query["anchor"]
        metric = a.get("best") or analyzers.rank_metric(metrics)
        anchor = analyzers.resolve_anchor(a, units, metric)
        if anchor is None:
            if "named" in a:
                return {"unmappable": f'No file matches "{a["named"]}".'}
            return analyzers.Analysis(
                empty_reason=f"No file clears the floor for {METRIC_NAME[metric]}."
            )
    if query["kind"] == "controlled" and query["control"] != "spend":
        cd, cv = query["control"]
        if not any(u.has(cd, cv) for u in units):
            return {"unmappable": f"No file carries {_words((cd, cv))}."}
    return analyzers.run(
        query, units, tests, overlaps, metrics, draws, anchor=anchor, metric=metric
    )


async def answer(question: str, result_json: dict, writer, draws: int = 300) -> dict:
    query = await map_question(question, result_json, writer)
    if "unmappable" in query:
        return query
    reading = read_as(query)
    base = {"kind": query["kind"], "read_as": reading}
    analysis = _analyse(query, result_json, draws)
    if isinstance(analysis, dict):
        return analysis
    if analysis.empty_reason:
        return _empty(base, analysis.empty_reason)
    tests = analysis.tests
    plines = [pattern_line(f"P{i}", t) for i, t in enumerate(tests, start=1)]
    flines = [f"F{i} | {f}" for i, f in enumerate(analysis.facts, start=1)]
    user = f"QUESTION: {question}\n\nREAD AS: {reading}\n"
    if flines:
        user += "\nFACTS:\n" + "\n".join(flines) + "\n"
    user += "\nCOMPARISONS:\n" + ("\n".join(plines) if plines else "(none)")
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
    valid = {f"P{i}" for i in range(1, len(tests) + 1)} | {
        f"F{i}" for i in range(1, len(analysis.facts) + 1)
    }
    cited_ids = [p for p in body.get("pattern_ids", []) if p in valid]
    cited = [to_json(tests[int(p[1:]) - 1]) for p in cited_ids if p.startswith("P")]
    return {
        **base,
        "verdict": cap_verdict(str(body.get("verdict", "not yet")), cited),
        "answer": str(body.get("answer", "")),
        "pattern_ids": cited_ids,
        "facts": [{"id": f"F{i}", "text": f} for i, f in enumerate(analysis.facts, start=1)],
        "tests": [to_json(t) for t in tests],
        "what_would_settle_it": str(body.get("what_would_settle_it", "")),
    }
