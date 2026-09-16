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


class FakeTagger:
    def __init__(self, tags_by_call: list[list[dict]] | None = None, refuse: bool = False):
        self.tags_by_call = list(tags_by_call or [])
        self.refuse = refuse
        self.calls: list[dict] = []

    async def tag(self, images, user_text, schema, video=None) -> TagResult:
        self.calls.append(
            {
                "n_images": len(images),
                "user_text": user_text,
                "schema": schema,
                "has_video": video is not None,
            }
        )
        if self.refuse:
            return TagResult(model="fake", refused=True)
        tags = self.tags_by_call.pop(0) if self.tags_by_call else DEFAULT_TAGS
        return TagResult(tags=list(tags), input_tokens=100, output_tokens=50, model="fake")
