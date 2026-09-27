"""Step 1 of the per-video read: a blind creative profile. One call per video, no metrics.

The prompt is the user's own creative-read section (the "FIRST: WATCH AND UNDERSTAND" part of
their strategist prompt), with the performance sections removed so the description cannot lean
toward the result. Fields that later comparisons count on carry fixed values next to free text.
Stored as experiment_result `profile3:<footage_id>` (v3; v2 stays at `profile:<footage_id>`).

Usage:
    uv run python scripts/profile_video.py --org <uuid> --footage <id> [<id> ...] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

import openai

from cs.core import model_calls
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments.describe import _copy
from cs.tagging.tagger import build_content

SLUG = "tag-clusters"
PURPOSE = "experiments.profile"
VERSION = "profile-v3"
# v3 is stored beside v2 (which the answer key scored), not over it.
KEY_PREFIX = "profile3"
RUNS = 2  # repeat-and-agree: a fixed value counts only when every run gives it

# One set of meanings for the model and for the hand-labelled answer key: undefined labels are
# part of why identical videos got different values in v1.
#
# v3 (2026-09-25): keeps the fields that scored well against the answer key (hook type,
# awareness, proof types, main speaker, production, hook text on screen), drops the ones that
# failed or that code now measures (product interaction, pace, music, music changes, running
# captions, sound-off), and adds everything content-related that could move a viewer to stop,
# click or buy: the offer, the promise, who it speaks to, the argument, who vouches for it, how
# it ends and how it feels.
DEFINITIONS = """hook_type: judge the first line the viewer receives (on-screen text at 0:00 if there is any, otherwise the first spoken line)
  question: asks the viewer a question
  problem_statement: names a symptom or problem the viewer may have ("If you wake up at 3am...")
  personal_confession: the speaker's own admission or story ("I've been taking the wrong one")
  bold_claim: a strong assertion about a problem, product or fact, not framed as the speaker's own story
  social_proof: reviews, ratings, user counts or other people's testimony
  curiosity_gap: withholds something to make the viewer want to know ("Nobody is talking about this")
  demonstration: shows the product or a result in action
  reaction_to_clip: someone reacting to another person's clip
  other
awareness_level: what the ad assumes the viewer already knows when it starts
  unaware: not even that they have the problem
  problem_aware: the problem, but not ways to solve it
  solution_aware: that solutions like magnesium exist, but not this product
  product_aware: this brand or product
  most_aware: ready to buy; goes straight to the offer
proof_types: every kind that appears
  testimonial: the speaker's or customers' own experience
  authority: a doctor, expert, credentialed or famous person's statement
  science_or_mechanism: explains how it works, ingredients, absorption
  reviews_or_ratings: stars, review quotes or counts
  demonstration: shows the product or its features physically
  before_after: a before and after
  comparison: against other products or forms
  none
main_speaker: whoever speaks the most
  creator_on_camera | voiceover | clip_of_other_person (an embedded clip speaks most) | text_only (nobody speaks) | none
production
  phone_ugc: handheld phone style throughout | polished: professional lighting and grading | mixed
hook_text_on_screen: any text on screen in the first 3 seconds

OFFER AND ASK (in the video itself, spoken or on screen; not the ad copy)
offer_types: every offer the video makes
  discount: a percentage or amount off, a coupon or code
  price_drop: says the price went down or is on sale
  bundle: multi-pack, subscribe-and-save or buy-more-save
  free_gift_or_shipping: something free with the order
  guarantee: money-back or satisfaction guarantee
  none
price_stated: an actual price or discount figure is said or shown
urgency: none | limited_time (a sale or deal that ends) | limited_stock (selling out, low stock)
cta_directness: how the video asks for action
  none: no ask
  soft: says where to find it ("it's linked below")
  direct: tells the viewer to act now ("go get it", "tap shop now", "grab it before...")
ending: what the last 5 seconds are
  brand_card: a product or brand card with no spoken ask | spoken_ask: the speaker asks the viewer to act | offer: an offer is the last thing | testimonial: a personal result is the last thing | other

PROMISE
primary_outcome: the main result promised
  falling_asleep | staying_asleep (waking at night, 3am) | stress_or_anxiety | muscle_cramps_or_recovery | morning_energy | menopause_symptoms | mood | digestion | general_health | other
other_outcomes: every other result promised (same values)
speed_claim: how fast it is said to work
  none | immediate (tonight, first night, within an hour) | days | weeks
promise_specificity
  vague: a general benefit ("helps you sleep better")
  specific: a concrete, checkable result ("I stopped waking at 3am", "asleep in 20 minutes")

AUDIENCE
addressed_to: who the ad speaks to, from what is said and shown, never from the speaker's appearance
  general | women_perimenopause_menopause | women_general | men | athletes_or_fitness | parents | older_adults | poor_sleepers | stressed_or_busy | other
narrowness
  broad: anyone could see themselves in it | narrow: it names a specific group or situation

ARGUMENT
mechanism_explained: how much it explains why the product works
  none | brief (one line: "it's liposomal, so it absorbs") | detailed (several reasons, forms, ingredients or how the body uses it)
attacks_other_magnesium: says common, cheap or other forms of magnesium do not work or are wrong (true/false)
objections_answered: every doubt the video answers
  price | taste_or_format | tried_magnesium_before | side_effects_or_safety | grogginess_or_dependency | is_it_legit | none

CREDIBILITY
vouched_by: every source the video leans on
  own_experience | professional_recommendation (a doctor, nurse, chiropractor or coach recommended it) | stated_credential (the speaker states their own credential) | public_figure_clip | customer_reviews | lab_or_third_party_test | studies_or_science | none

FEEL
tone: the dominant register
  calm_informative | warm_personal | urgent_energetic | humorous | confrontational (attacks doctors, the industry, other brands, or uses provocative language)
controversy: uses provocation, profanity, conspiracy or "they don't want you to know" framing (true/false)"""

SYSTEM = """You are a senior performance-creative strategist reviewing one paid-social video ad for the team that made it. You have the ad itself (video with audio) and its ad copy. You do not have, and must not guess, how it performed.

WATCH AND UNDERSTAND THE CREATIVE

Watch the complete video. Include the accompanying ad copy in the descriptions, but fill the OFFER AND ASK fields from the video alone.

Start with the overall experience:
- What is the central idea, and who does the ad appear to address?
- What attracts attention, and what reason does it give someone to continue?
- What does it want the viewer to understand, believe, or want?
- How does it introduce the product, support its promise, and ask for action?
- Does the ending follow naturally from the opening?

Then identify the specific elements that create that experience:
- Message and persuasion: hook, problem or desire, benefit, demonstration, proof, offer, price, qualifications, and CTA.
- Promise: what result, for whom, how fast, and how concretely.
- Argument: why this product, what it says about alternatives, which doubts it answers.
- Credibility: who or what vouches for it.
- Creator and cast: who appears or speaks, their role as presented, delivery style, and product interaction.
- Visual style: setting, production style, lighting, framing, and branding.
- Editing: pacing, cuts, transitions, and changes in rhythm.
- Music and sound: style, mood, energy, effects, changes, and balance with speech.
- Speech and text: tone, clarity, captions, overlays, readability, and sound-off comprehension.

Rules:
- Describe intended persuasion separately from observed facts.
- Do not infer creator identity or credentials from appearance; a credential counts only when it is said or shown.
- Do not consider frame shape or aspect ratio.
- Cite meaningful moments as MM:SS or MM:SS-MM:SS. Quote short lines verbatim only when clear.
- Only log on-screen text that is large enough to read clearly; skip small print.
- Mark unavailable or unclear material rather than inventing it. Do not report music or sound you cannot hear.
- Where a field offers fixed values, pick the one that fits best; use "other" rather than stretching one. For lists, include every value that applies and nothing that does not.
- Use concise descriptions. No performance judgments, recommendations, or suggested changes.
- List at most 12 moments, the ones that matter most. Never repeat an entry.

""" + "DEFINITIONS OF THE FIXED VALUES\n" + DEFINITIONS

HOOK = ["question", "bold_claim", "problem_statement", "personal_confession", "social_proof",
        "curiosity_gap", "demonstration", "reaction_to_clip", "other"]
AWARENESS = ["unaware", "problem_aware", "solution_aware", "product_aware", "most_aware"]
PROOF = ["testimonial", "authority", "science_or_mechanism", "reviews_or_ratings", "demonstration",
         "before_after", "comparison", "none"]
SPEAKER = ["creator_on_camera", "voiceover", "clip_of_other_person", "text_only", "none"]
PRODUCTION = ["phone_ugc", "polished", "mixed"]
SECTION = ["hook", "problem", "agitation", "solution", "proof", "offer", "cta", "other"]
OFFER = ["discount", "price_drop", "bundle", "free_gift_or_shipping", "guarantee", "none"]
URGENCY = ["none", "limited_time", "limited_stock"]
CTA = ["none", "soft", "direct"]
ENDING = ["brand_card", "spoken_ask", "offer", "testimonial", "other"]
OUTCOME = ["falling_asleep", "staying_asleep", "stress_or_anxiety", "muscle_cramps_or_recovery", "morning_energy",
           "menopause_symptoms", "mood", "digestion", "general_health", "other"]
SPEED = ["none", "immediate", "days", "weeks"]
SPECIFICITY = ["vague", "specific"]
AUDIENCE = ["general", "women_perimenopause_menopause", "women_general", "men", "athletes_or_fitness", "parents",
            "older_adults", "poor_sleepers", "stressed_or_busy", "other"]
NARROW = ["broad", "narrow"]
MECHANISM = ["none", "brief", "detailed"]
OBJECTION = ["price", "taste_or_format", "tried_magnesium_before", "side_effects_or_safety",
             "grogginess_or_dependency", "is_it_legit", "none"]
VOUCH = ["own_experience", "professional_recommendation", "stated_credential", "public_figure_clip",
         "customer_reviews", "lab_or_third_party_test", "studies_or_science", "none"]
TONE = ["calm_informative", "warm_personal", "urgent_energetic", "humorous", "confrontational"]


def _o(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": props, "required": list(props)}


S, B, N = {"type": "string"}, {"type": "boolean"}, {"type": ["string", "null"]}


def E(v):
    return {"type": "string", "enum": v}


def L(v):
    return {"type": "array", "items": E(v)}


SCHEMA = _o({
    "creative_profile": _o({
        "concept_and_message": _o({"description": S, "hook_type": E(HOOK), "hook_line": N,
                                   "problem_or_desire": N, "awareness_level": E(AWARENESS)}),
        "product_and_persuasion": _o({"description": S, "proof_types": L(PROOF), "offer_or_price": N, "cta": N}),
        "offer_and_ask": _o({"description": S, "offer_types": L(OFFER), "price_stated": B, "urgency": E(URGENCY),
                             "cta_directness": E(CTA), "ending": E(ENDING), "last_line": N}),
        "promise": _o({"description": S, "primary_outcome": E(OUTCOME), "other_outcomes": L(OUTCOME),
                       "speed_claim": E(SPEED), "promise_specificity": E(SPECIFICITY)}),
        "audience": _o({"description": S, "addressed_to": E(AUDIENCE), "narrowness": E(NARROW)}),
        "argument": _o({"description": S, "mechanism_explained": E(MECHANISM), "attacks_other_magnesium": B,
                        "objections_answered": L(OBJECTION)}),
        "credibility": _o({"description": S, "vouched_by": L(VOUCH)}),
        "feel": _o({"description": S, "tone": E(TONE), "controversy": B}),
        "creator_and_cast": _o({"description": S, "main_speaker": E(SPEAKER)}),
        "visual_style": _o({"description": S, "setting": S, "production": E(PRODUCTION)}),
        "editing": _o({"description": S}),
        "music_and_sound": _o({"description": S}),
        "speech_and_text": _o({"description": S, "hook_text_on_screen": B}),
        "structure": {"type": "array", "items": _o({"section": E(SECTION), "start": S, "end": S})},
        "intended_persuasion": S,
        "ending_follows_opening": S,
    }),
    "moments": {"type": "array", "items": _o({"at": S, "observed": S})},
})


FIXED = [
    ("concept_and_message", "hook_type"), ("concept_and_message", "awareness_level"),
    ("product_and_persuasion", "proof_types"), ("creator_and_cast", "main_speaker"),
    ("visual_style", "production"), ("speech_and_text", "hook_text_on_screen"),
    ("offer_and_ask", "offer_types"), ("offer_and_ask", "price_stated"), ("offer_and_ask", "urgency"),
    ("offer_and_ask", "cta_directness"), ("offer_and_ask", "ending"),
    ("promise", "primary_outcome"), ("promise", "other_outcomes"), ("promise", "speed_claim"),
    ("promise", "promise_specificity"), ("audience", "addressed_to"), ("audience", "narrowness"),
    ("argument", "mechanism_explained"), ("argument", "attacks_other_magnesium"), ("argument", "objections_answered"),
    ("credibility", "vouched_by"), ("feel", "tone"), ("feel", "controversy"),
]


def merge(runs: list[dict]) -> dict:
    """Free text and moments from the first run; every fixed value with whether all runs agree.
    A list (proof types) keeps the items every run named; the rest are marked soft."""
    fixed = {}
    for group, field in FIXED:
        vals = [r["creative_profile"][group][field] for r in runs]
        if isinstance(vals[0], list):
            common = sorted(set.intersection(*(set(v) for v in vals)))
            soft = sorted(set.union(*(set(v) for v in vals)) - set(common))
            fixed[field] = {"value": common, "soft": soft, "agreed": not soft}
        else:
            agreed = all(v == vals[0] for v in vals)
            fixed[field] = {"value": vals[0] if agreed else None, "runs": vals, "agreed": agreed}
    return {"creative_profile": runs[0]["creative_profile"], "moments": runs[0]["moments"], "fixed": fixed}


async def run(org: UUID, ids: list[UUID], dry: bool) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    blobs = blob_store_from_settings(s)
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
    client = openai.AsyncOpenAI(api_key=s.ai_gateway_api_key, base_url=f"{s.ai_gateway_base_url}/v1",
                                max_retries=0, timeout=900)
    model = s.account_read_model
    for fid in ids:
        f = await db.fetchrow(
            "SELECT f.duration_s, f.rep_creative_id, a.path FROM footage f JOIN creative c ON c.id = f.rep_creative_id"
            " JOIN asset a ON a.id = c.primary_asset_id WHERE f.id=$1 AND f.org_id=$2", fid, org)
        copy = await _copy(db, f["rep_creative_id"]) or "(no ad copy)"
        text = (f"Ad copy shown with the video:\n{copy}\n\nThe video is {float(f['duration_s']):.0f} seconds long. "
                "Based on the video and copy above, write the creative profile and moments.")
        print(f"{fid}: {float(f['duration_s']):.0f}s")
        if dry:
            print(SYSTEM + "\n\n[video]\n" + text)
            continue
        video = await blobs.get(f["path"])
        runs, usage = [], []
        for n in range(1, RUNS + 1):
            claim = await model_calls.claim(db, org, PURPOSE, f"{fid}:{VERSION}:run{n}", model)
            try:
                resp = await client.chat.completions.create(
                    model=model, max_tokens=16000,
                    messages=[{"role": "system", "content": SYSTEM},
                              {"role": "user", "content": build_content([], text, (video, "video/mp4"))}],
                    response_format={"type": "json_schema", "json_schema": {"name": "profile", "schema": SCHEMA, "strict": True}},
                )
                if resp.choices[0].finish_reason == "length":
                    # A cut-off answer is usually a loop repeating list entries (2026-09-25:
                    # 51k characters on one video). Recorded as failed; no retry.
                    raise RuntimeError("model response truncated")
                runs.append(json.loads(resp.choices[0].message.content or "{}"))
            except Exception as exc:
                await model_calls.fail(db, claim.id, str(exc))
                print(f"  run {n} failed: {str(exc)[:120]}; skipping this video")
                break
            u = resp.usage
            await model_calls.finish(db, claim.id, input_tokens=u.prompt_tokens, output_tokens=u.completion_tokens)
            usage.append([u.prompt_tokens, u.completion_tokens])
        if len(runs) < RUNS:
            continue
        merged = merge(runs)
        # A free cross-check: running captions show up in the tagger's on-screen text as many
        # short fragments. Disagreement is flagged, never silently resolved.
        tag_run = await db.fetchval(
            "SELECT source_tag_run_id FROM footage_attribute WHERE footage_id=$1 AND source_tag_run_id IS NOT NULL LIMIT 1", fid)
        # Running captions are measured in code now (cs.experiments.profile_read), not asked.
        merged["checks"] = {}
        await db.execute(
            "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
            " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()",
            exp, f"{KEY_PREFIX}:{fid}", {"version": VERSION, "model": model, "usage": usage, "runs": runs, **merged},
        )
        soft = [k for k, v in merged["fixed"].items() if not v["agreed"]]
        print(f"  tokens {usage}; soft fields: {soft or 'none'}")
    await db.close()


async def caption_check(db: Database, tag_run, duration_s: float, cap: dict) -> dict:
    """Running captions leave many short text items in the tagger's on-screen text between the
    opening and the end card; a title at 0:00 and end-card text do not count (v2's first
    heuristic counted the end card and missed an invented-captions case)."""
    if not tag_run:
        return {"tagger_body_text_items": None, "tagger_suggests_captions": None, "captions_disagree_with_tagger": False}
    n_text = await db.fetchval(
        "SELECT count(*) FROM tag WHERE tag_run_id=$1 AND dimension='overlay_texts'"
        " AND t_start_s >= 3 AND t_start_s < $2", tag_run, duration_s - 6)
    tagger_says = n_text >= duration_s / 6
    return {"tagger_body_text_items": n_text, "tagger_suggests_captions": tagger_says,
            "captions_disagree_with_tagger": cap["agreed"] and cap["value"] != tagger_says}




def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--footage", type=UUID, nargs="+", required=True)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.org, a.footage, a.dry_run))


if __name__ == "__main__":
    main()
