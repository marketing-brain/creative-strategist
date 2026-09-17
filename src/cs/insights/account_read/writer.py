"""The account read: brief construction, the writer prompt, parsing, and the checks that keep the
prose honest (numbers from the brief only, cited IDs exist, exemplars exist, non-obviousness
stated)."""

from __future__ import annotations

import json
import re
from typing import Protocol

import openai

from cs.core.config import Settings
from cs.insights.account_read.types import Finding, Read, RunResult, Test

METRIC_NAME = {
    "hook_rate": "hook rate",
    "thruplay_rate": "thruplay rate",
    "link_ctr": "link CTR",
    "purchases_per_1k": "purchases per 1,000 impressions",
    "cpa": "CPA",
}
MAX_MAIN_UNFLAGGED, MAX_MAIN_FLAGGED, MAX_UNITS_LISTED = 60, 20, 60
# Interactions are numerous; the brief carries the sharpest lines per group, strongest groups first.
MAX_GROUPS, MAX_LINES_PER_GROUP = 20, 10
GRADE_ORDER = {"strong": 0, "suggestive": 1, "directional": 2}


def fmt_val(metric: str, v: float) -> str:
    if metric == "cpa":
        return f"${v:.0f}"
    if metric == "purchases_per_1k":
        return f"{v:.2f}"
    return f"{100 * v:.1f}%"


def label(t: Test) -> str:
    v = str(t.value).replace("_", " ")
    if t.bin_edges:
        lo, hi = t.bin_edges
        unit = "s" if t.dimension == "duration_s" else ""
        v += {
            "low": f" (under {lo:g}{unit})",
            "mid": f" ({lo:g} to {hi:g}{unit})",
            "high": f" ({hi:g}{unit} and up)",
        }.get(str(t.value), "")
    return f"{t.dimension.replace('_', ' ')} = {v}"


def pattern_line(pid: str, t: Test) -> str:
    flags = ", ".join(f.replace("_", " ") for f in t.confounds) or "none"
    ch = t.change
    direction = "higher" if ch > 0 else "lower"
    return (
        f"{pid} | {label(t)} | {METRIC_NAME[t.metric]}: {fmt_val(t.metric, t.cell_value)} with vs "
        f"{fmt_val(t.metric, t.comp_value)} without ({abs(ch) * 100:.0f}% {direction}) | "
        f"{t.n_cell} vs {t.n_complement} files | p = {t.p:.3f} | {t.grade} | flags: {flags}"
    )


def _unit_record(u) -> str:
    bits = [
        f'"{u.headline}"',
        f"{round(u.duration_s or 0)}s",
        f"${float(u.spend):,.0f} spend",
        f"{len(u.creative_ids)} cop{'y' if len(u.creative_ids) == 1 else 'ies'}",
    ]
    messenger = next((v for d, v in u.facets if d == "messenger"), None)
    if messenger:
        bits.append(f"messenger {messenger.replace('_', ' ')}")
    if u.hook_verbatim:
        bits.append(f'hook: "{u.hook_verbatim}"')
    if u.concept_summary:
        bits.append(f"concept: {u.concept_summary}")
    if u.insight_paragraph:
        bits.append(f"per-ad insight: {u.insight_paragraph}")
    return "- " + " | ".join(bits)


def build_brief(run: RunResult) -> str:
    s = run.summary
    by_sha = {u.sha256: u for u in run.units}
    ids = {id(t): f"P{i}" for i, t in enumerate(run.tests, start=1)}
    top_units = sorted(run.units, key=lambda u: -u.spend)[:MAX_UNITS_LISTED]
    record = [_unit_record(u) for u in top_units]

    def with_units(t: Test) -> str:
        cell = sorted((by_sha[x] for x in t.cell_shas if x in by_sha), key=lambda u: -u.spend)
        examples = "; ".join(f'"{u.headline}"' for u in cell[:3])
        return pattern_line(ids[id(t)], t) + "\n    cell examples: " + examples

    main = [t for t in run.tests if t.group is None and t.grade != "thin"]
    unflagged = [with_units(t) for t in main if not t.confounds][:MAX_MAIN_UNFLAGGED]
    flagged = [with_units(t) for t in main if t.confounds][:MAX_MAIN_FLAGGED]

    by_group: dict[str, list[Test]] = {}
    for t in run.tests:
        if t.group is not None and t.grade != "thin":
            by_group.setdefault(t.group, []).append(t)
    groups = sorted(by_group.items(), key=lambda kv: -len(kv[1]))[:MAX_GROUPS]
    inter_blocks = []
    for g, ts in groups:
        ts = sorted(ts, key=lambda t: (GRADE_ORDER.get(t.grade, 3), len(t.confounds), t.p))
        lines = ["  " + pattern_line(ids[id(t)], t) for t in ts[:MAX_LINES_PER_GROUP]]
        more = len(ts) - len(lines)
        if more > 0:
            lines.append(f"  (+{more} weaker lines omitted)")
        inter_blocks.append(
            f"\nwithin {g.replace('_', ' ')} ({len(ts)} graded lines):\n" + "\n".join(lines)
        )
    inter_text = "\n".join(inter_blocks) or "\n  none"

    ov = [
        f"  {o['a'][0].replace('_', ' ')} = {o['a'][1]}  and  "
        f"{o['b'][0].replace('_', ' ')} = {o['b'][1]}: {o['shared']} shared files of "
        f"{o['n_a']} and {o['n_b']} (Jaccard {o['jaccard']})"
        for o in run.overlaps
    ]

    def part(x: dict) -> str:
        if x["change"] is None:
            return "not enough files"
        return f"{x['change'] * 100:+.0f}% ({x['n']} files)"

    st = [
        f"  effect of {x['attribute'][0].replace('_', ' ')} = {x['attribute'][1]} on "
        f"{METRIC_NAME[x['metric']]}: overall {x['overall_change'] * 100:+.0f}% | within "
        f"{x['controlling_for'][0].replace('_', ' ')} = {x['controlling_for'][1]}: "
        f"{part(x['within'])}; outside it: {part(x['outside'])}"
        for x in run.stratified
    ]

    ret = []
    for b, r in run.retention_by_reveal.items():
        pct = lambda v: f"{100 * (v or 0):.1f}%"  # noqa: E731
        cpa = f"${r['cpa']}" if r.get("cpa") else "too few purchases"
        ret.append(
            f"  R-{b}: {r['n']} files, median length {r['median_dur_s']}s, product first shown "
            f"at {r['median_reveal_s']}s median | hook rate {pct(r.get('hook_rate'))} | share of "
            f"plays reaching 25% {pct(r.get('p25'))}, 50% {pct(r.get('p50'))}, 75% "
            f"{pct(r.get('p75'))}, 100% {pct(r.get('p100'))} | purchases per 1,000 impressions "
            f"{r.get('ppk')} | CPA {cpa}"
        )

    nl = "\n"
    return f"""ACCOUNT BRIEF

Scope: one Meta ad account, video ads, lifetime metrics {s.get("first_day")} to {s.get("last_day")}.
Unit: a distinct video file; ad creatives reusing one file pool their metrics and count once. {s["units"]} files ({s["creatives"]} creatives incl. copies). Spend ${s["spend"]:,}, {s["purchases"]:,} purchases.
Metrics: hook rate = 3-second views / impressions; thruplay rate = thruplays / plays; link CTR = link clicks / impressions; purchases per 1,000 impressions and CPA are pooled per group. Rate metrics compare per-file medians (Mann-Whitney U); pooled metrics use a permutation test. Grades: strong = passes Benjamini-Hochberg at q 0.10 across every test in this run; suggestive = raw p under 0.10; directional = p under 0.25 with at least a 15% change (a lead, not evidence). Flags: budget concentration = one file holds half or more of the group's impressions; seasonality = the groups ran in different periods, so time confounds the comparison.

=== THE ADS (top {len(top_units)} files by spend; what each one is) ===
{nl.join(record)}

=== GRADED PATTERNS, whole account (unflagged first) ===
{nl.join(unflagged) or "  none"}
  (flagged, use with care)
{nl.join(flagged) or "  none"}

=== PATTERNS WITHIN GROUPS (interactions; the group is the set of files that carry the grouping attribute) ==={inter_text}

=== ENTANGLED ATTRIBUTES (the same files carry several attributes) ===
{nl.join(ov) or "  none above Jaccard 0.6"}
Stratified checks (does the effect survive when the partner attribute is held constant?):
{nl.join(st) or "  none"}

=== RETENTION BY PRODUCT-REVEAL TIMING (the bins are entangled with video length; read the median length) ===
{nl.join(ret) or "  none"}
"""  # noqa: E501


SYSTEM = """\
You are a senior performance-creative strategist writing the account-level read for the team that makes this brand's video ads. You have two kinds of material: a qualitative record of what each ad is (headline, verbatim hook, concept summary, and where one exists the per-ad insight paragraph), and statistically graded comparisons of how ads with each attribute performed against ads without it, including comparisons inside groups, entanglement checks and retention by product-reveal timing.

Your job is the amalgamation. A finding is not "attribute X has metric Y higher"; that is a table row and the reader already has the table. A finding says what the ads that share the attribute are actually doing, what viewer behavior that plausibly produces, which files show it and which break it, and what the team could not have seen from the numbers alone. Write for a smart client who will make the next ad from this.

RULES
- All numbers are precomputed. Reproduce them exactly as written. Do not calculate, combine, re-round or invent numbers. Cite pattern IDs (P1, P2, ...) for every quantitative claim; retention rows may be cited as R-<bin>.
- Grades carry weight: strong is evidence, suggestive is a lead, directional is a hint. Titles must not outrun the grade. A cell under 10 files is small; say so.
- Flags matter. A seasonality flag means time confounds the comparison: say so or leave it out. A budget concentration flag means one file dominates: name it.
- Entanglement matters most. When the stratified checks show an effect vanishing once another attribute is held constant, say the two cannot be separated with this data and do not present them as separate findings.
- Attention, click and conversion are different layers. Say which layer each claim is about. A CTR lift with a CPA penalty is a finding in itself.
- Every finding must have: a mechanism (drawn from the qualitative record, in hypothesis language), two or three exemplar files named by their exact headline as written in the brief, an exception where one exists (a file in the cell that breaks the pattern or a file outside it that behaves like it), and why_non_obvious: one sentence on what the metric table alone could not show.
- Rank findings by decision value for the next ad, not by p-value. Three to six findings. Fewer and sharper beats coverage.
- The overview answers, in prose and before any number: what does this account's creative actually do, and where is it wrong about itself?
- "No signal" and "too few files" are different answers. Report both plainly.
- Hypothesis language: "is consistent with", "one plausible explanation is". These are correlations from an optimizer-driven account, not a controlled test.
- Plain prose. No bullet lists or headings inside paragraphs.

OUTPUT (JSON)
- overview: one paragraph.
- findings: 3 to 6 objects, each with title (plain statement, under 12 words), layer (attention | retention | click | conversion | funnel), paragraph (3 to 6 sentences, evidence with exact numbers and file counts, then interpretation), mechanism (1 to 2 sentences), exemplars (2 to 3 exact headlines), exception (1 sentence, or "none found"), why_non_obvious (1 sentence), pattern_ids, confidence (strong | suggestive | directional | mixed).
- entangled: one paragraph naming attributes that cannot be separated with this data and what that means for reading the findings.
- no_evidence: one paragraph on what showed no difference and what could not be tested, naming attributes.
- next_tests: 3 to 5 one-sentence tests that follow from the findings, each naming the attribute to vary and the metric expected to move. The only place recommendations belong.
- data_needed: 2 to 4 one-sentence items on what would most sharpen this read.
Return valid JSON only.
"""  # noqa: E501

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)
_NUMBER = re.compile(r"(?<![\w.])\$?[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")


def _numbers(text: str) -> set[float]:
    """Metric-looking numbers in text, by absolute value. Small bare integers (file counts,
    'three sentences') are not claims and are ignored."""
    out: set[float] = set()
    for m in _NUMBER.finditer(text):
        raw = m.group(0)
        tok = raw.replace(",", "").replace("$", "").replace("%", "").replace("+", "")
        tok = tok.replace("−", "-")
        try:
            v = float(tok)
        except ValueError:
            continue
        if abs(v) < 100 and v.is_integer() and "%" not in raw and "$" not in raw:
            continue
        out.add(round(abs(v), 2))
    return out


def parse_read(text: str) -> Read:
    m = _FENCE.match(text)
    body = json.loads(m.group(1) if m else text)
    findings = []
    for f in body.get("findings") or []:
        findings.append(
            Finding(
                title=str(f.get("title", "")),
                layer=str(f.get("layer", "")),
                paragraph=str(f.get("paragraph", "")),
                mechanism=str(f.get("mechanism", "")),
                exemplars=[str(x) for x in (f.get("exemplars") or [])],
                exception=str(f.get("exception", "")),
                why_non_obvious=str(f.get("why_non_obvious", "")),
                pattern_ids=[str(x) for x in (f.get("pattern_ids") or [])],
                confidence=str(f.get("confidence", "")),
            )
        )
    return Read(
        overview=str(body.get("overview", "")),
        findings=findings,
        entangled=str(body.get("entangled", "")),
        no_evidence=str(body.get("no_evidence", "")),
        next_tests=[str(x) for x in (body.get("next_tests") or [])],
        data_needed=[str(x) for x in (body.get("data_needed") or [])],
    )


def entanglement_collapses(run: RunResult) -> dict[str, str]:
    """Pattern id -> partner attribute label, for every main-effect test whose effect falls to
    under a third of its overall size once the partner attribute is held constant."""
    ids = {
        (t.dimension, t.value, t.metric): f"P{i}"
        for i, t in enumerate(run.tests, start=1)
        if t.group is None
    }
    out: dict[str, str] = {}
    for x in run.stratified:
        within = x.get("within") or {}
        overall = x.get("overall_change") or 0
        if within.get("change") is None or not overall:
            continue
        if abs(within["change"]) < abs(overall) / 3:
            attr, partner = tuple(x["attribute"]), x["controlling_for"]
            pid = ids.get((attr[0], attr[1], x["metric"]))
            if pid and pid not in out:
                out[pid] = f"{partner[0].replace('_', ' ')} = {str(partner[1]).replace('_', ' ')}"
    return out


GENERIC_VALUES = {"low", "mid", "high", "none", "other", "mixed", "yes", "no"}


def _mentions(text: str, partner_label: str) -> bool:
    """Does the text name the partner attribute? A generic tercile or enum value ('high',
    'none') only counts together with its dimension's first word ('duration')."""
    dim, _, value = partner_label.partition(" = ")
    low, value = text.lower(), value.lower()
    dim_word = dim.lower().split()[0] if dim else ""
    if value in GENERIC_VALUES:
        return bool(dim_word) and dim_word in low
    return value in low or value.replace(" ", "_") in low


def check_read(
    read: Read, brief: str, n_tests: int, collapses: dict[str, str] | None = None
) -> list[str]:
    allowed = _numbers(brief)
    problems: list[str] = []
    prose = [
        ("overview", read.overview),
        ("entangled", read.entangled),
        ("no_evidence", read.no_evidence),
    ]
    prose += [
        (f"finding '{f.title}'", " ".join((f.paragraph, f.mechanism, f.exception)))
        for f in read.findings
    ]
    for where, text in prose:
        bad = sorted(_numbers(text) - allowed)
        if bad:
            problems.append(f"{where}: numbers not in the brief: {bad}")
    valid_ids = {f"P{i}" for i in range(1, n_tests + 1)}
    for f in read.findings:
        missing = [p for p in f.pattern_ids if p not in valid_ids and not p.startswith("R-")]
        if missing:
            problems.append(f"finding '{f.title}': unknown pattern ids {missing}")
        for ex in f.exemplars:
            if f'"{ex}"' not in brief:
                problems.append(f"finding '{f.title}': exemplar not in the brief: {ex!r}")
        if not f.exemplars:
            problems.append(f"finding '{f.title}': no exemplars")
        if not f.why_non_obvious.strip():
            problems.append(f"finding '{f.title}': why_non_obvious is empty")
    for f in read.findings:
        for pid in f.pattern_ids:
            partner = (collapses or {}).get(pid)
            if not partner:
                continue
            finding_text = " ".join((f.paragraph, f.mechanism, f.exception, f.why_non_obvious))
            if not (_mentions(finding_text, partner) or _mentions(read.entangled, partner)):
                problems.append(
                    f"finding '{f.title}' cites {pid}, whose effect vanishes once "
                    f"'{partner}' is held constant; name that entanglement in the finding or "
                    "in 'entangled'"
                )
    for partner in sorted(set((collapses or {}).values())):
        cited = any(
            pid in f.pattern_ids
            for f in read.findings
            for pid in (collapses or {})
            if (collapses or {})[pid] == partner
        )
        if cited and not _mentions(read.entangled, partner):
            problems.append(
                f"'entangled' must name the pair whose effects cannot be separated: '{partner}'"
            )
    if not read.findings:
        problems.append("no findings")
    return problems


class AccountReadWriter(Protocol):
    async def complete(self, system: str, user: str) -> tuple[str, int, int]: ...


class GeminiGatewayAccountReadWriter:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.creative_insight_model

    async def complete(self, system: str, user: str) -> tuple[str, int, int]:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        usage = getattr(resp, "usage", None)
        text = resp.choices[0].message.content or "{}"
        return (
            text,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
        )


async def write_read(
    run: RunResult, writer: AccountReadWriter
) -> tuple[Read | None, list[str], int, int]:
    """One draft, one retry with the problems named; None when the retry still fails."""
    brief = build_brief(run)
    collapses = entanglement_collapses(run)
    tokens_in = tokens_out = 0
    feedback: list[str] = []
    for _ in range(2):
        user = brief + (
            "\n\nWrite the overview, findings, entangled, no_evidence, next_tests and data_needed."
        )
        if feedback:
            user += (
                "\n\nYour previous draft broke the rules: "
                + "; ".join(feedback)
                + ". Rewrite, citing only numbers and headlines that appear in the brief exactly "
                "as written."
            )
        text, ti, to = await writer.complete(SYSTEM, user)
        tokens_in, tokens_out = tokens_in + ti, tokens_out + to
        try:
            read = parse_read(text)
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            feedback = [f"response was not valid JSON: {exc}"]
            continue
        feedback = check_read(read, brief, len(run.tests), collapses)
        if not feedback:
            return read, [], tokens_in, tokens_out
    return None, feedback, tokens_in, tokens_out
