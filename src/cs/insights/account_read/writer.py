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
MAX_ATTEMPTS = 4  # first draft plus three retries with the problems named
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


MONEY_METRICS = ("purchases_per_1k", "cpa")
GRADE_RANK = {"strong": 3, "suggestive": 2, "directional": 1, "thin": 0}


def _attr(t: Test) -> tuple:
    return (t.group, t.dimension, str(t.value))


def money_siblings(tests: list[Test]) -> dict[tuple, list[Test]]:
    """Attribute -> its purchases-per-1,000 and CPA tests, whatever their grade."""
    out: dict[tuple, list[Test]] = {}
    for t in tests:
        if t.metric in MONEY_METRICS:
            out.setdefault(_attr(t), []).append(t)
    return out


def pays(t: Test) -> bool:
    """A purchase or cost result that is good news and at least a lead."""
    good = t.change < 0 if t.metric == "cpa" else t.change > 0
    return good and GRADE_RANK.get(t.grade, 0) >= GRADE_RANK["suggestive"]


def costs(t: Test, at_least: str = "directional") -> bool:
    """A purchase or cost result that is bad news and at least a hint."""
    bad = t.change > 0 if t.metric == "cpa" else t.change < 0
    return bad and GRADE_RANK.get(t.grade, 0) >= GRADE_RANK[at_least]


def downstream(pid: str, t: Test) -> str:
    direction = "higher" if t.change > 0 else "lower"
    grade = "no clear difference" if t.grade == "thin" else t.grade
    return (
        f"{pid} {METRIC_NAME[t.metric]} {fmt_val(t.metric, t.cell_value)} with vs "
        f"{fmt_val(t.metric, t.comp_value)} without ({abs(t.change) * 100:.0f}% {direction}, "
        f"{grade})"
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
    return "- " + " | ".join(bits)


def build_brief(run: RunResult) -> str:
    s = run.summary
    by_sha = {u.sha256: u for u in run.units}
    ids = {id(t): f"P{i}" for i, t in enumerate(run.tests, start=1)}
    top_units = sorted(run.units, key=lambda u: -u.spend)[:MAX_UNITS_LISTED]
    record = [_unit_record(u) for u in top_units]

    money = money_siblings(run.tests)

    def with_units(t: Test) -> str:
        cell = sorted((by_sha[x] for x in t.cell_shas if x in by_sha), key=lambda u: -u.spend)
        examples = "; ".join(f'"{u.headline}"' for u in cell[:3])
        line = pattern_line(ids[id(t)], t) + "\n    cell examples: " + examples
        # Thin lines are left out of the brief, and the purchase lines of an attribute that
        # wins attention are usually thin. Without them the writer sees the CTR lift and never
        # the flat purchases beside it, and writes "qualifies buyers" (org 3 read, 2026-09-19).
        if t.metric not in MONEY_METRICS:
            down = [downstream(ids[id(m)], m) for m in money.get(_attr(t), [])]
            if down:
                line += "\n    downstream, same files: " + "; ".join(down)
        return line

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
Metrics: hook rate = 3-second views / impressions; thruplay rate = thruplays / plays; link CTR = link clicks / impressions; purchases per 1,000 impressions and CPA are pooled per group. Rate metrics compare per-file medians (Mann-Whitney U); pooled metrics use a permutation test. Grades: strong = passes Benjamini-Hochberg at q 0.10 across every test in this run; suggestive = raw p under 0.10; directional = p under 0.25 with at least a 15% change (a lead, not evidence). Flags: budget concentration = one file holds half or more of the group's impressions; seasonality = the groups ran in different periods, so time confounds the comparison. tagger disagreement = files tagged more than once did not get this attribute consistently, so which videos belong to the group is partly noise.

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
You are a senior performance-creative strategist writing the account-level read for the team that makes this brand's video ads. You have two kinds of material: a qualitative record of what each ad is (headline, verbatim hook, concept summary), and statistically graded comparisons of how ads with each attribute performed against ads without it, including comparisons inside groups, entanglement checks and retention by product-reveal timing.

Your job is the amalgamation. A finding is not "attribute X has metric Y higher"; that is a table row and the reader already has the table. A finding says what the ads that share the attribute are actually doing, what viewer behavior that plausibly produces, which files show it and which break it, and what the team could not have seen from the numbers alone. Write for a smart client who will make the next ad from this.

RULES
- All numbers are precomputed. Reproduce them exactly as written. Do not calculate, combine, re-round or invent numbers. Cite pattern IDs (P1, P2, ...) for every quantitative claim; retention rows may be cited as R-<bin>.
- Grades carry weight: strong is evidence, suggestive is a lead, directional is a hint. Titles must not outrun the grade. A cell under 10 files is small; say so.
- Flags matter. A seasonality flag means time confounds the comparison: say so or leave it out. A budget concentration flag means one file dominates: name it. A tagger disagreement flag means the grouping itself is shaky: never lead a finding with it, and when you cite it say the tag is inconsistent.
- Entanglement matters most. When the stratified checks show an effect vanishing once another attribute is held constant, say the two cannot be separated with this data and do not present them as separate findings.
- Attention, click and conversion are different layers. Say which layer each claim is about. A CTR lift with a CPA penalty is a finding in itself.
- Follow every attribute to the purchase. Each attention or click line carries a "downstream, same files" line with its purchases per 1,000 impressions and CPA. Read it before you interpret a lift. When it is graded and worse (higher CPA, fewer purchases), the finding must say so and cite its pattern ID. When it shows no clear difference, do not claim the lift reaches the purchase. Words about buyers (intent, qualified, converts, purchase, sales, closer) belong in a title only when a cited purchases or CPA line is suggestive or strong in the good direction. More clicks with flat purchases is more clicks, not better traffic.
- A finding's confidence is the grade of the weakest graded comparison it cites (lines showing no clear difference aside), or "mixed". One strong line does not make a finding strong.
- Write only about what the brief measures: the ad, the view, the click, and purchases attributed to the ad. It holds nothing on the landing page, cart or checkout, so make no claim about them; ask for that data in data_needed instead.
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
    if isinstance(body, list):  # a model sometimes wraps the object in an array
        body = next((x for x in body if isinstance(x, dict)), {})
    if not isinstance(body, dict):
        raise TypeError("response is not a JSON object")
    findings = []
    for f in body.get("findings") or []:
        if not isinstance(f, dict):
            continue
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


def _headline_in_brief(exemplar: str, brief: str) -> bool:
    """Exemplars must be headlines quoted in the brief. Tolerate trailing whitespace and the
    90-character truncation the unit record applies: a quoted headline that the exemplar
    starts with, or that starts with the exemplar (30+ chars), counts."""
    ex = " ".join(exemplar.split()).strip().lower()
    if not ex:
        return False
    for m in re.finditer(r'"([^"\n]{3,})"', brief):
        h = " ".join(m.group(1).split()).strip().lower()
        if h == ex or (len(ex) >= 30 and (h.startswith(ex) or ex.startswith(h))):
            return True
    return False


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
            if not _headline_in_brief(ex, brief):
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
    problems += _off_brief_stages(read, brief)
    return problems


_BUYER_WORDS = re.compile(
    r"\b(intent|qualif\w*|convert\w*|conversions?|purchas\w*|buyers?|sales?|revenue|closer)\b",
    re.I,
)
# Funnel stages the account read holds no data on. Naming one is a claim with nothing under it
# (the org 3 read blamed "drop-offs at the checkout stage", 2026-09-19).
_UNMEASURED_STAGES = ("checkout", "landing page", "add to cart", "add-to-cart", "cart abandon")


def _off_brief_stages(read: Read, brief: str) -> list[str]:
    low_brief = brief.lower()
    texts = [("overview", read.overview), ("entangled", read.entangled)]
    texts += [
        (f"finding '{f.title}'", " ".join((f.title, f.paragraph, f.mechanism, f.why_non_obvious)))
        for f in read.findings
    ]
    return [
        f"{where}: says '{term}', which the brief holds no data on; drop the claim or move "
        "the request for that data to data_needed"
        for where, text in texts
        for term in _UNMEASURED_STAGES
        if term in text.lower() and term not in low_brief
    ]


_METRIC_LAYER = {"hook_rate": "attention", "thruplay_rate": "retention", "link_ctr": "click"}
_LAYER_ORDER = ("attention", "retention", "click")


def settle_layers(read: Read, tests: list[Test]) -> None:
    """File a finding under the layer its evidence is about. The writer reaches for "funnel"
    or "conversion" when a finding spans hook and click but cites no purchase line; that is a
    label, so it is corrected here rather than spent as a retry (dry run, 2026-09-20). The
    furthest stage its cited lines reach is the honest one."""
    for f in read.findings:
        if f.layer not in ("conversion", "funnel"):
            continue
        cited = [
            tests[int(p[1:]) - 1]
            for p in f.pattern_ids
            if re.fullmatch(r"P\d+", p) and 1 <= int(p[1:]) <= len(tests)
        ]
        if any(t.metric in MONEY_METRICS for t in cited):
            continue
        reached = [_METRIC_LAYER[t.metric] for t in cited if t.metric in _METRIC_LAYER]
        if reached:
            f.layer = max(reached, key=_LAYER_ORDER.index)


def check_money(read: Read, tests: list[Test]) -> list[str]:
    """A finding may not claim more than its purchase and CPA lines show. Every attribute it
    cites on an attention or click metric must be followed to the purchase, buyer language in
    a title needs a purchase or CPA line that pays, and confidence is capped by the weakest
    cited grade."""
    money = money_siblings(tests)
    ids = {id(t): f"P{i}" for i, t in enumerate(tests, start=1)}
    problems: list[str] = []
    for f in read.findings:
        cited = [
            tests[int(p[1:]) - 1]
            for p in f.pattern_ids
            if re.fullmatch(r"P\d+", p) and 1 <= int(p[1:]) <= len(tests)
        ]
        cited_money = [t for t in cited if t.metric in MONEY_METRICS]
        # Account-wide lines only: those are the ones the brief prints a downstream line
        # under. A gate may demand only what the writer was shown (the dry run stalled on a
        # within-group CPA line, P11176, that appears nowhere in the brief).
        lifts = [t for t in cited if t.metric not in MONEY_METRICS and t.group is None]
        for attr in dict.fromkeys(_attr(t) for t in lifts):
            # Only graded bad news has to be cited. A downstream line with no clear
            # difference is shown on the page beside the finding either way, and demanding a
            # citation for each one cost the writer every retry it had (dry run, 2026-09-20).
            sibs = [m for m in money.get(attr, []) if costs(m)]
            if sibs and not any(m in cited_money for m in sibs):
                lines = "; ".join(downstream(ids[id(m)], m) for m in sibs)
                problems.append(
                    f"finding '{f.title}' reports an attention or click result for "
                    f"{label(sibs[0])} and leaves out that the same files do worse "
                    f"downstream. State it in the paragraph and cite it: {lines}"
                )
        if f.layer in ("conversion", "funnel") and not cited_money:
            problems.append(
                f"finding '{f.title}' is filed under '{f.layer}' but cites no purchases or CPA "
                "comparison; cite one or file it under the layer its evidence is about"
            )
        # Good or bad, a title about buyers needs a purchase line that is at least a lead.
        # Whether the title has the direction right is for the reader, who sees the line.
        word = _BUYER_WORDS.search(f.title)
        if word and not any(pays(t) or costs(t, "suggestive") for t in cited_money):
            problems.append(
                f"finding '{f.title}': the title says '{word.group(0)}', a claim about buyers, "
                "but no cited purchases or CPA comparison is suggestive or strong. Retitle it "
                "for what the cited metric shows (more clicks, longer watching), or cite a "
                "purchases or CPA line that carries it"
            )
        # Lines that show no clear difference are reported, not leaned on, so they do not
        # pull the confidence down; every graded line the finding rests on does.
        graded = [r for t in cited if (r := GRADE_RANK.get(t.grade, 0)) > 0]
        claimed = GRADE_RANK.get(f.confidence)
        if graded and claimed is not None and claimed > min(graded):
            weakest = next(g for g, r in GRADE_RANK.items() if r == min(graded))
            problems.append(
                f"finding '{f.title}': confidence '{f.confidence}' outruns its weakest cited "
                f"comparison; use '{weakest}' or 'mixed'"
            )
    return problems


class AccountReadWriter(Protocol):
    async def complete(self, system: str, user: str) -> tuple[str, int, int]: ...


class GeminiGatewayAccountReadWriter:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.account_read_model

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
    """One draft plus retries with the problems named; None when the last one still fails."""
    brief = build_brief(run)
    collapses = entanglement_collapses(run)
    tokens_in = tokens_out = 0
    feedback: list[str] = []
    draft = ""
    for _ in range(MAX_ATTEMPTS):
        user = brief + (
            "\n\nWrite the overview, findings, entangled, no_evidence, next_tests and data_needed. "
            "Return one JSON object with exactly those keys."
        )
        if feedback:
            # The draft goes back with its problems. Asked to rewrite from nothing, the model
            # fixed what was named and broke something new each time (2, then 12, then 4
            # problems over one dry run, 2026-09-20); asked to correct, it converges.
            user += (
                "\n\nYour previous draft:\n"
                + draft
                + "\n\nYour previous draft broke the rules: "
                + "; ".join(feedback)
                + ". Correct only what is named. Leave every other sentence, number, headline "
                "and pattern ID exactly as it is, cite only numbers and headlines that appear "
                "in the brief exactly as written, and return the full JSON object."
            )
        text, ti, to = await writer.complete(SYSTEM, user)
        draft = text
        tokens_in, tokens_out = tokens_in + ti, tokens_out + to
        try:
            read = parse_read(text)
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            feedback = [f"response was not valid JSON: {exc}"]
            continue
        settle_layers(read, run.tests)
        feedback = check_read(read, brief, len(run.tests), collapses)
        feedback += check_money(read, run.tests)
        if not feedback:
            return read, [], tokens_in, tokens_out
    return None, feedback, tokens_in, tokens_out
