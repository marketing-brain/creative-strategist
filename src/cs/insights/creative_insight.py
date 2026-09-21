"""One free-text insight per creative: the model watches the ad against its diagnosis.

The numbers say *where* a creative under- or over-delivers (the deterministic diagnosis,
diagnosis.py); the model's job is to look at that exact moment of the ad and say *why*.
It never computes: every number it may cite is precomputed into the brief, and the
paragraph is rejected if it cites a number or timestamp the brief does not contain.
How firmly it may speak is set here from delivery volume (evidence tiers), not by the model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import openai

from cs.core.config import Settings
from cs.insights.diagnose_creative import DiagnosedCreative
from cs.insights.diagnosis import Metric, Tier
from cs.tagging.tagger import build_content

EvidenceTier = Literal["reliable", "directional", "insufficient"]

MEASURED = ("strong", "on_par", "weak")
RELIABLE_IMPRESSIONS = 10_000
RELIABLE_PLAYS = 1_000
RELIABLE_STEP_DENOMINATOR = 500
RELIABLE_STEP_EVENTS = 30
MAX_WORDS = 250  # the prompt sets no target; this is the retry threshold for a runaway draft

SYSTEM = """\
You are a senior performance-creative strategist reviewing one paid-social ad for the team that made it. You have the ad itself—a video with audio, an image, or carousel cards—and a metrics brief our system computed.

Analyze it as a human strategist would: experience the creative first, understand what it is trying to do, then examine the numbers and develop evidence-grounded insights. This analysis feeds a later comparison across creatives. Do not recommend changes, tests, or actions.

FIRST: WATCH AND UNDERSTAND THE CREATIVE

Watch the complete video before using the metrics to interpret it. For carousels, inspect every supplied card in sequence; for images, examine the full composition. Include accompanying ad copy when available.

Start with the overall experience:
- What is the central idea, and who does the ad appear to address?
- What attracts attention, and what reason does it give someone to continue?
- What does it want the viewer to understand, believe, or want?
- How does it introduce the product, support its promise, and ask for action?
- Does the ending follow naturally from the opening?

Then identify the specific elements that create that experience:
- Message and persuasion: hook, problem or desire, benefit, demonstration, proof, offer, price, qualifications, and CTA.
- Creator and cast: who appears or speaks, their role as presented, delivery style, and product interaction.
- Visual style: format, setting, production style, lighting, framing, and branding.
- Editing: pacing, cuts, transitions, and changes in rhythm.
- Music and sound: style, mood, energy, effects, changes, and balance with speech.
- Speech and text: tone, clarity, captions, overlays, readability, and sound-off comprehension.

Build this creative read without deciding whether the ad performed well. Describe intended persuasion separately from observed facts. Do not infer creator identity or credentials from appearance.

Cite meaningful moments as MM:SS or MM:SS-MM:SS for video, "card N" for carousels, and "image" for static ads. Quote short lines verbatim only when clear. Mark unavailable or unclear material rather than inventing it.

SECOND: READ THE PERFORMANCE

Read the full metrics brief and establish the overall pattern before explaining individual numbers.

- All performance numbers and comparisons are precomputed. Do not calculate, re-round, or invent them. Reproduce numbers exactly as supplied. Timestamps and numbers present in the creative are also allowed.
- Use the supplied metric definitions, account-relative comparisons, and time windows. Ignore industry benchmarks.
- Each layer has a status: strong, on_par, weak, or not_measured. Discuss meaningful strengths and weaknesses. Never speculate about unmeasured results.
- Evidence tiers—reliable or directional—describe support for the measured result, not certainty about its explanation.
- Consider the metrics together, using the supplied campaign objective when available. The weakest metric is not automatically the main issue, and strong engagement does not automatically mean strong business performance.

THIRD: RECONCILE THE TWO

Return to your initial creative read and ask:
- Which results fit what the creative appears designed to do?
- Which results challenge that interpretation?
- Which specific elements or relationships offer a plausible explanation?
- What remains unexplained?

Revise your interpretation when the evidence warrants it. Do not retrofit every creative feature into a story that matches the numbers.

For each main insight, connect:
specific creative evidence → plausible viewer response → relevant measured behavior.

Consider the whole ad. A relationship between the opening promise and later proof may matter more than an isolated cut, line, or shot.

- Attention: distinguish attracting attention from giving someone a reason to click.
- Retention: inspect the supplied drop window and what precedes it. Do not imply more timing precision than the data supports.
- Conversion: examine the expectations, proof, offer, and qualifications the ad establishes. Do not invent landing-page or checkout problems.
- Fatigue: a performance decline alone does not establish wear-out or repeated exposure.
- Carousels: without per-card metrics, do not confidently attribute results to individual cards.

If no defensible creative explanation exists, say so.

LANGUAGE

Keep observations, performance findings, and hypotheses distinct. Use language such as "one plausible explanation is" or "is consistent with." Do not present causation or viewer reactions as established facts.

Do not blame targeting, budget, placement, the algorithm, or seasonality without evidence. Briefly acknowledge outside influences when they materially limit the interpretation.

OUTPUT (JSON)

- creative_profile: a compact object with these fields: concept_and_message, product_and_persuasion, creator_and_cast, visual_style, editing, music_and_sound, speech_and_text, structure. Use concise descriptions and consistent labels across ads. Describe the creative without performance judgments; use null for unavailable information.
- moments: key observations spanning the complete creative. Each entry contains at and observed. Keep observations factual.
- paragraph: plain prose, as long as the evidence warrants (one or more paragraphs). Start with the overall performance pattern and its key supplied numbers, then explain the strongest creative connections and any material contradiction or uncertainty. Prioritize a coherent interpretation over listing metrics. Do not force explanations or pad sparse evidence.

Do not include recommendations, what to keep, or what to change. Return valid JSON only.
"""  # noqa: E501

PROFILE_FIELDS = (
    "concept_and_message",
    "product_and_persuasion",
    "creator_and_cast",
    "visual_style",
    "editing",
    "music_and_sound",
    "speech_and_text",
    "structure",
)

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def parse_body(text: str) -> dict[str, Any]:
    """The model's reply as a dict. The prompt asks for JSON and the request pins JSON mode,
    but nothing enforces the shape: a fenced block is unwrapped, and anything that is not a
    JSON object becomes an empty one, which the grounding check then rejects as an empty
    paragraph and retries."""
    if not text:
        return {}
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        body = json.loads(text)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def parse_insight(body: dict[str, Any]) -> tuple[str, dict[str, str | None], list[dict]]:
    paragraph = body.get("paragraph")
    paragraph = paragraph.strip() if isinstance(paragraph, str) else ""
    raw = body.get("creative_profile")
    raw = raw if isinstance(raw, dict) else {}
    profile = {
        f: (v if isinstance(v := raw.get(f), str) and v.strip() else None) for f in PROFILE_FIELDS
    }
    moments = [
        {"at": str(m.get("at", "")), "observed": str(m.get("observed", ""))}
        for m in body.get("moments") or []
        if isinstance(m, dict)
    ]
    return paragraph, profile, moments


# ---------------------------------------------------------------- evidence tiers


def evidence_tier(tier: Tier, d: DiagnosedCreative) -> EvidenceTier:
    """How much delivery sits behind a measured layer. The diagnosis floors already make a
    measured layer at least directional; reliable needs roughly 10x those floors."""
    if tier.status not in MEASURED:
        return "insufficient"
    if tier.key == "attention":
        return "reliable" if d.window.impressions >= RELIABLE_IMPRESSIONS else "directional"
    if tier.key == "retention":
        return "reliable" if d.window.video_plays >= RELIABLE_PLAYS else "directional"
    if tier.key == "conversion":
        readable = [s for s in tier.steps or [] if s.readable]
        if (
            readable
            and min(s.denominator for s in readable) >= RELIABLE_STEP_DENOMINATOR
            and readable[-1].numerator >= RELIABLE_STEP_EVENTS
        ):
            return "reliable"
        return "directional"
    if tier.key == "fatigue" and d.prior14 is not None and d.trailing7 is not None:
        low = min(d.prior14.impressions, d.trailing7.impressions)
        return "reliable" if low >= RELIABLE_IMPRESSIONS else "directional"
    return "directional"


def is_readable(d: DiagnosedCreative) -> bool:
    return any(t.status in MEASURED for t in d.diagnosis.tiers)


# ---------------------------------------------------------------- brief


def mmss(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def _pct(x: float, places: int) -> str:
    return f"{x * 100:.{places}f}%"


_DESCRIPTIONS = {
    "hook_rate": ("hook rate (3s views ÷ impressions)", 1),
    "hold_rate": ("hold rate (ThruPlay ÷ 3s views)", 1),
    "link_ctr": ("link CTR (link clicks ÷ impressions)", 2),
    "engagement_rate": ("engagement rate (reactions, comments, saves ÷ impressions)", 2),
}


def _fmt(m: Metric, v: float) -> str:
    if m.key == "cpa":
        return f"${v:,.2f}"
    if m.key == "frequency":
        return f"{v:.2f}"
    return _pct(v, _DESCRIPTIONS.get(m.key, ("", 1))[1])


def _metric_line(m: Metric, compare: str) -> str | None:
    if m.value is None:
        return None
    if m.key == "cpa":
        name = "CPA (spend ÷ conversions)"
    elif m.key == "frequency":
        name = "frequency (impressions ÷ reach)"
    else:
        name = _DESCRIPTIONS.get(m.key, (m.label, 1))[0]
    line = f"{name}: {_fmt(m, m.value)}"
    if m.baseline is None:
        return line
    line += f" vs {_fmt(m, m.baseline)} {compare}"
    if m.key == "cpa" and m.baseline:
        change = abs(m.value - m.baseline) / m.baseline
        direction = "lower" if m.value < m.baseline else "higher"
        line += f" ({change * 100:.0f}% {direction}; lower is better)"
    elif m.lift is not None:
        line += f" ({'+' if m.lift >= 0 else '-'}{abs(m.lift) * 100:.0f}%)"
    return line


def _attr(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ")


def _layer_lines(tier: Tier, d: DiagnosedCreative, duration_ms: int | None) -> list[str]:
    compare = "prior 14 days" if tier.key == "fatigue" else "baseline"
    lines = [ln for m in tier.metrics if (ln := _metric_line(m, compare))]
    if tier.key == "attention":
        lines.append(f"volume: {d.window.impressions:,} impressions")
    if tier.key == "retention" and tier.curve is not None:
        dur_s = duration_ms / 1000 if duration_ms else None
        lines.append(f"volume: {d.window.video_plays:,} plays")
        if tier.curve.baseline_label:
            lines.append(f"baseline for this layer: the {tier.curve.baseline_label}")
        if tier.curve.avg_watch_s is not None:
            watch = f"average watch time: {tier.curve.avg_watch_s:.1f}s"
            lines.append(watch + (f" of {dur_s:.1f}s" if dur_s else ""))
        lines.append("share of plays still watching, this ad vs baseline:")
        for p in tier.curve.points:
            if p.p == 0 or p.share is None or p.baseline_share is None:
                continue
            at = f" ({mmss(p.p / 100 * dur_s)})" if dur_s else ""
            lines.append(
                f"  {p.p}% of runtime{at}: {_pct(p.share, 0)} vs {_pct(p.baseline_share, 0)}"
            )
        seg = tier.curve.drop_segment
        if seg:
            if dur_s:
                where = f"{mmss(seg[0] / 100 * dur_s)}-{mmss(seg[1] / 100 * dur_s)}"
            else:
                where = f"{seg[0]}%-{seg[1]}% of runtime"
            lines.append(f"largest excess drop vs baseline: {where}")
    if tier.key == "conversion":
        for s in tier.steps or []:
            if s.readable and s.rate is not None and s.baseline_rate is not None:
                ln = f"{s.label}: {_pct(s.rate, 1)} vs {_pct(s.baseline_rate, 1)} baseline"
                if s.lift is not None:
                    ln += f" ({'+' if s.lift >= 0 else '-'}{abs(s.lift) * 100:.0f}%)"
                lines.append(f"{ln} · {s.numerator:,} of {s.denominator:,}")
            else:
                lines.append(f"{s.label}: not readable yet")
    if tier.key == "fatigue" and d.days_live is not None:
        lines.append(f"live for {d.days_live} days")
    if tier.key == "context":
        lines.append(
            f"Meta's optimiser chose this audience, not the advertiser. Compared with "
            f"{tier.comparison}."
        )
        if tier.audience:
            lines.append("who Meta found, not who was targeted (share of impressions):")
            for s in tier.audience:
                gap = (s.share - s.baseline_share) * 100
                lines.append(
                    f"  {s.label}: {_pct(s.share, 0)} vs {_pct(s.baseline_share, 0)} for the "
                    f"comparison set ({'+' if gap >= 0 else '-'}{abs(gap):.0f} points)"
                )
    return lines


def render_brief(creative: Mapping[str, Any], d: DiagnosedCreative, duration_ms: int | None) -> str:
    dx = d.diagnosis
    fmt = creative["format"]
    ad = [f"format: {fmt}" + (f" · duration {mmss(duration_ms / 1000)}" if duration_ms else "")]
    for key in ("headline", "primary_text", "description"):
        if creative.get(key):
            ad.append(f"{key}: {creative[key]}")
    if creative.get("cta_type") or creative.get("link_url"):
        ad.append(
            f"cta: {creative.get('cta_type') or 'none'} → {creative.get('link_url') or 'no link'}"
        )
    if d.first_day is not None:
        ad.append(f"first delivery: {d.first_day.isoformat()}")
    out = ["<ad>", *ad, "</ad>", ""]
    out.append(
        f"<window>{dx.window_start.isoformat()} to {dx.window_end.isoformat()}, compared with "
        f"{dx.baseline_creatives} other {fmt} ads in this account</window>"
    )
    out.append("")
    broken = next((t for t in dx.tiers if t.tier == dx.broken_tier), None)
    out.append(
        f"<verdict>{'weakest layer: ' + broken.key if broken else 'no weak layer'}</verdict>"
    )
    for t in dx.tiers:
        out.append("")
        if t.status not in MEASURED:
            reason = f' reason="{_attr(t.reason)}"' if t.reason else ""
            out.append(f'<layer name="{t.key}" status="not_measured"{reason}/>')
            continue
        out.append(f'<layer name="{t.key}" status="{t.status}" tier="{evidence_tier(t, d)}">')
        out.extend(_layer_lines(t, d, duration_ms))
        out.append("</layer>")
    return "\n".join(out)


def user_text(brief: str, feedback: list[str] | None = None) -> str:
    text = (
        f"{brief}\n\nBased on the ad and the brief above, fill creative_profile and moments, "
        "then write the paragraph."
    )
    if feedback:
        text += (
            "\n\nYour previous draft broke the rules: "
            + "; ".join(feedback)
            + ". Rewrite it citing only numbers that appear in the brief exactly as written."
        )
    return text


# ---------------------------------------------------------------- grounding check

# MM:SS, but not a clock time the ad itself shows ("3:17 am"), which is not a video position.
_CLOCK = re.compile(r"(?<![\d:])\d{1,2}:[0-5]\d\s?(?:am|pm|AM|PM|a\.m\.|p\.m\.)\b")
_TIMESTAMP = re.compile(r"(?<![\d:])(\d{1,2}):([0-5]\d)(?![\d:])")
# A comma counts as a thousands separator only when three digits follow it, so "over 55, and"
# is the word 55 and not the marked token "55,".
_NUMBER = re.compile(r"(?<![\w.])\$?[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")


def _numbers(text: str) -> dict[float, str]:
    """Significant numbers in text, keyed by absolute value. Bare integers under 100 with no
    %/$/decimal ("3 seconds", "card 2", "30 days") are wording, not metrics, and are skipped."""
    found: dict[float, str] = {}
    for m in _NUMBER.finditer(_TIMESTAMP.sub(" ", text)):
        token = m.group(0)
        core = token.lstrip("$+-−").rstrip("%").replace(",", "")
        try:
            value = abs(float(core))
        except ValueError:
            continue
        marked = "%" in token or "$" in token or "." in core or "," in token
        if marked or value >= 100:
            found[value] = token
    return found


def grounding_problems(paragraph: str, brief: str, fmt: str, duration_ms: int | None) -> list[str]:
    problems: list[str] = []
    if not paragraph.strip():
        return ["the paragraph is empty"]
    if len(paragraph.split()) > MAX_WORDS:
        problems.append(f"the paragraph is longer than {MAX_WORDS} words")
    allowed = _numbers(brief)
    invented = [tok for v, tok in _numbers(paragraph).items() if v not in allowed]
    if invented:
        problems.append("numbers not in the brief: " + ", ".join(invented))
    stamps = [
        (int(a) * 60 + int(b), f"{a}:{b}")
        for a, b in _TIMESTAMP.findall(_CLOCK.sub(" ", paragraph))
    ]
    if stamps and fmt != "video":
        problems.append("timestamps cited for a non-video ad")
    elif stamps and duration_ms:
        late = [s for secs, s in stamps if secs > duration_ms / 1000 + 1]
        if late:
            problems.append("timestamps past the end of the video: " + ", ".join(late))
    return problems


# ---------------------------------------------------------------- model call


@dataclass
class WrittenInsight:
    paragraph: str = ""
    creative_profile: dict[str, str | None] = field(default_factory=dict)
    moments: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    refused: bool = False


class InsightWriter(Protocol):
    model: str

    async def write(
        self,
        images: list[tuple[bytes, str]],
        video: tuple[bytes, str] | None,
        user_text: str,
    ) -> WrittenInsight: ...


class GeminiGatewayInsightWriter:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.creative_insight_model

    async def write(
        self,
        images: list[tuple[bytes, str]],
        video: tuple[bytes, str] | None,
        user_text: str,
    ) -> WrittenInsight:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_content(images, user_text, video)},
            ],
            # JSON mode only: parseable JSON, no enforced shape. The prompt is the single
            # source of truth for the fields; parse_insight tolerates whatever is missing.
            response_format={"type": "json_object"},
        )
        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        out = WrittenInsight(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=resp.model,
        )
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            out.refused = True
            return out
        out.paragraph, out.creative_profile, out.moments = parse_insight(
            parse_body(choice.message.content or "")
        )
        return out
