from __future__ import annotations

from cs.tagging.tagger import TagResult

DEFAULT_TAGS = [
    {
        "dimension": "hook_type",
        "value": "question",
        "confidence": 0.9,
        "evidence": "opens with a question",
    },
    {
        "dimension": "message_angle",
        "value": "pain_relief",
        "confidence": 0.8,
        "evidence": "mentions tiredness",
    },
    {"dimension": "visual_style", "value": "ugc", "confidence": 0.7, "evidence": "handheld look"},
    {
        "dimension": "hook_summary",
        "value": "Asks if you are tired of waking up tired",
        "confidence": 0.9,
        "evidence": "headline",
    },
]


def _single(value: str, confidence: float, evidence: str) -> dict:
    return {"value": value, "confidence": confidence, "evidence": evidence}


# Timeline fits inside the 4-second fixture clip used by the runner tests.
DEFAULT_V2_PAYLOAD = {
    "single": {
        "messenger": _single("expert_on_camera", 0.9, "white coat; says 'as a pharmacist'"),
        "narration": _single("on_camera_speech", 1.0, "speaks to camera throughout"),
        "captions": _single("text_cards", 0.9, "headline overlays, no subtitles"),
        "opening_shot": _single("face_to_camera", 1.0, "face fills 0-3s"),
        "production": _single("phone_ugc", 0.8, "handheld, home lighting"),
        "setting": _single("home", 0.9, "kitchen behind her"),
        "product_reveal": _single("after_10s", 1.0, "pouch at 0:55"),
        "brand_presence": _single("prominent", 1.0, "logo on pouch"),
        "ending": _single("cta_card", 1.0, "end card with button"),
        "music": _single("none", 1.0, "no music bed"),
    },
    "multi": {
        "hook_devices": [
            {"value": "warning_or_risk", "t_start_s": 0.0, "evidence": "'Worst OTC sleep aids'"},
            {"value": "audience_callout", "t_start_s": 1.0, "evidence": "'if you take benadryl'"},
            {"value": "warning_or_risk", "t_start_s": 2.0, "evidence": "duplicate on purpose"},
        ],
        "proof_devices": [
            {"value": "expert_credential", "t_start_s": 3.0, "evidence": "'as a pharmacist'"},
            {"value": "lab_report_or_certificate", "t_start_s": 3.5, "evidence": "CoA on screen"},
        ],
        "angles": [
            {"value": "risk_avoidance", "evidence": "dementia warning"},
            {"value": "natural_alternative", "evidence": "magnesium instead"},
        ],
        "offer_elements": [{"value": "discount_percent", "t_start_s": 3.6, "evidence": "20% off"}],
        "cta_modes": [
            {"value": "spoken", "t_start_s": 3.7, "evidence": "'tap below'"},
            {"value": "end_card", "t_start_s": 3.8, "evidence": "card"},
        ],
        "registers": [
            {"value": "fear", "evidence": "warning"},
            {"value": "authority", "evidence": "credential"},
        ],
    },
    "ordered": {
        "beats": [
            {"value": "hook", "t_start_s": 0.0},
            {"value": "problem", "t_start_s": 1.0},
            {"value": "proof", "t_start_s": 1.5},
            {"value": "solution_intro", "t_start_s": 2.0},
            {"value": "offer", "t_start_s": 3.6},
            {"value": "cta", "t_start_s": 3.7},
        ]
    },
    "open": {
        "concept_tags": [
            {"slug": "Pharmacist Warning", "evidence": "pharmacist warns against OTC aids"},
            {"slug": "otc_sleep_aid_risks", "evidence": "names diphenhydramine risks"},
        ],
        "topics": [
            {"slug": "sleep", "evidence": "throughout"},
            {"slug": "magnesium", "evidence": "0:47"},
        ],
        "comparison_targets": [
            {"slug": "benadryl", "evidence": "0:01"},
            {"slug": "melatonin", "evidence": "0:20"},
        ],
        "audience_callouts": [{"slug": "benadryl users", "evidence": "0:01"}],
        "claims": [{"slug": "third_party_tested", "evidence": "CoA at 1:13"}],
        "visual_motifs": [
            {"slug": "pharmacy coat", "evidence": "0:00"},
            {"slug": "lab_report_screenshot", "evidence": "1:13"},
        ],
    },
    "text": {
        "hook_verbatim": (
            "Worst OTC Sleep Aids!! / As a pharmacist with almost ten years of experience"
        ),
        "concept_summary": (
            "A pharmacist warns against common OTC sleep aids. She recommends magnesium "
            "gummies as a tested alternative."
        ),
        "transcript": (
            "As a pharmacist with almost ten years of experience, these are the worst sleep aids."
        ),
    },
}


class FakeTagger:
    def __init__(
        self,
        tags_by_call: list[list[dict]] | None = None,
        payloads_by_call: list[dict] | None = None,
        refuse: bool = False,
    ):
        self.tags_by_call = list(tags_by_call or [])
        self.payloads_by_call = list(payloads_by_call or [])
        self.refuse = refuse
        self.calls: list[dict] = []

    async def tag(self, images, user_text, schema, video=None, system=None) -> TagResult:
        self.calls.append(
            {
                "n_images": len(images),
                "user_text": user_text,
                "schema": schema,
                "has_video": video is not None,
                "system": system,
            }
        )
        if self.refuse:
            return TagResult(model="fake", refused=True)
        nested = "single" in schema.get("properties", {})
        if nested:
            payload = self.payloads_by_call.pop(0) if self.payloads_by_call else DEFAULT_V2_PAYLOAD
            return TagResult(
                payload=dict(payload), input_tokens=100, output_tokens=50, model="fake"
            )
        tags = self.tags_by_call.pop(0) if self.tags_by_call else DEFAULT_TAGS
        return TagResult(
            tags=list(tags),
            payload={"tags": list(tags)},
            input_tokens=100,
            output_tokens=50,
            model="fake",
        )
