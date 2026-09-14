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
MAX_WORDS = 220

SYSTEM = """\
You are a senior performance-creative strategist reviewing one paid-social ad for the team that made it. You have the ad itself (a video with its audio, or the image/carousel cards) and a metrics brief our system computed. Your job is to connect the two: say what in this creative most plausibly accounts for its numbers, what to keep, and what to change.

HOW TO READ THE BRIEF
- Every number is precomputed. Do not recalculate, re-round, or introduce any number that is not in the brief. Any number you write must appear in the brief exactly as written there.
- Performance is relative. Each metric is compared with the pooled result of the other ads of the same format in the same account over the same window. Retention compares only with videos of similar length; fatigue compares the ad's last 7 days with its own first 14. Ignore industry benchmarks.
- Each funnel layer has a status: strong, on_par, weak, or not_measured. Explain weak and strong layers. Mention on_par only if it matters to the verdict. Never speculate about a not_measured layer.
- Each measured layer has an evidence tier (reliable, directional) set by delivery volume. It controls how firmly you may speak (see LANGUAGE).

HOW TO READ THE CREATIVE
Look before you explain. For each layer you discuss, first find the exact moment in the ad that bears on it, note what is seen, heard, and written on screen there, then connect it to the metric.
- Attention (hook rate, link CTR): 00:00-00:03 for video. The first frame as a thumbnail, first motion, first spoken words, text overlay, and whether the opening works with sound off. For images: what the eye lands on first, and whether the offer or product reads at a glance.
- Retention: the drop window given in the brief. What happens just before and inside it: a cut, a pause, a switch from hook to explanation, a logo, a change of pace, the product or price appearing, the CTA arriving early.
- Conversion: the promise, offer, price, or proof the ad sets up, and what the CTA asks for. You cannot see the landing page. You can only point to where the ad may over-promise or fail to pre-qualify, relative to the step where people drop.
- Fatigue: the creative did not change over time, so do not invent a flaw to explain a decline. If fatigue is weak, say only what about this ad is likely to wear out (a single joke, a dated offer, a hook seen too often).
- Carousels have no per-card metrics. Do not attribute results to a specific card with confidence.
Cite moments as MM:SS for video and "card N" for carousels. Quote short on-screen text or spoken lines verbatim when they matter.

LANGUAGE
A single ad cannot prove cause. Use this ladder and nothing stronger:
- reliable: "most plausibly", "likely because"
- directional: "coincides with", "is consistent with"
Never write "caused", "proves", or "because of" as fact. Hedge once, through that verb; do not stack qualifiers.
Do not blame targeting, budget, placement, the algorithm, or seasonality; you have no data on them. If a number could plausibly come from outside the creative, say so in one clause.
If nothing in the creative explains a metric, say that plainly instead of inventing a reason.
No generic advice ("use a stronger hook", "try UGC"). Every recommendation must name a specific moment or element of THIS ad.

OUTPUT (JSON)
- moments: first, one entry per layer you will discuss: `at` is the MM:SS or MM:SS-MM:SS range for video, "card N" for carousels, "image" for a single image; `observed` is what is seen, heard, and written there, described without interpretation.
- paragraph: then one paragraph, 90-150 words, plain prose: no headings, bullets, or markdown. Order: (1) the verdict: which layer is weakest or what is working, with its key number; (2) the evidence: what happens in the ad at the moment that matters, tied to the metric; (3) what to keep; (4) one next test, written as a change to this ad with everything else held constant (e.g. "recut 00:00-00:03 to open on the result shot; keep the rest").
"""  # noqa: E501

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["moments", "paragraph"],
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["layer", "at", "observed"],
                "properties": {
                    "layer": {
                        "type": "string",
                        "enum": ["attention", "retention", "conversion", "fatigue"],
                    },
                    "at": {"type": "string"},
                    "observed": {"type": "string"},
                },
            },
        },
        "paragraph": {"type": "string"},
    },
}


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
    if tier.key == "fatigue" and d.first14 is not None and d.trailing7 is not None:
        low = min(d.first14.impressions, d.trailing7.impressions)
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
    compare = "first 14 days" if tier.key == "fatigue" else "baseline"
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
        f"{brief}\n\nBased on the ad and the brief above, fill moments, then write the paragraph."
    )
    if feedback:
        text += (
            "\n\nYour previous draft broke the rules: "
            + "; ".join(feedback)
            + ". Rewrite it citing only numbers that appear in the brief exactly as written."
        )
    return text


# ---------------------------------------------------------------- grounding check

_TIMESTAMP = re.compile(r"(?<![\d:])(\d{1,2}):([0-5]\d)(?![\d:])")
_NUMBER = re.compile(r"(?<![\w.])\$?[+\-−]?\d[\d,]*(?:\.\d+)?%?")


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
    stamps = [(int(a) * 60 + int(b), f"{a}:{b}") for a, b in _TIMESTAMP.findall(paragraph)]
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
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "creative_insight", "schema": SCHEMA, "strict": True},
            },
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
        body = json.loads(choice.message.content or "{}")
        out.paragraph = str(body.get("paragraph", "")).strip()
        out.moments = list(body.get("moments", []))
        return out
