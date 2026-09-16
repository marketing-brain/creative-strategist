from __future__ import annotations

from cs.insights.creative_insight import WrittenInsight
from cs.insights.types import InsightDraft


class FakeExplainer:
    def __init__(self):
        self.seen: list[InsightDraft] = []

    async def explain(self, draft: InsightDraft, context: dict) -> tuple[str, str]:
        self.seen.append(draft)
        return "fake explanation", "fake test"


class FakeInsightWriter:
    model = "fake"

    def __init__(self, paragraphs: list[str] | None = None, refuse: bool = False):
        self.paragraphs = list(paragraphs or ["Attention is strong at 00:00."])
        self.refuse = refuse
        self.calls: list[dict] = []

    async def write(self, images, video, user_text) -> WrittenInsight:
        self.calls.append(
            {"n_images": len(images), "has_video": video is not None, "user_text": user_text}
        )
        if self.refuse:
            return WrittenInsight(model="fake", refused=True)
        paragraph = self.paragraphs.pop(0) if len(self.paragraphs) > 1 else self.paragraphs[0]
        return WrittenInsight(
            paragraph=paragraph,
            creative_profile={"concept_and_message": "a product close-up", "editing": None},
            moments=[{"at": "00:00-00:03", "observed": "a face"}],
            input_tokens=100,
            output_tokens=50,
            model="fake",
        )
