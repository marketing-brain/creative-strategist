from __future__ import annotations

from cs.insights.creative_insight import WrittenInsight
from cs.insights.types import InsightDraft


class FakeExplainer:
    def __init__(self):
        self.seen: list[InsightDraft] = []

    async def explain(self, draft: InsightDraft, context: dict) -> tuple[str, str]:
        self.seen.append(draft)
        return "fake explanation", "fake test"


class FakeCopilot:
    def __init__(self, reply: str = "fake answer"):
        self.reply = reply
        self.calls: list[list[dict]] = []

    async def answer(self, history: list[dict], tools) -> str:
        self.calls.append(history)
        rows = await tools.insights(kind="pattern", limit=3)
        return f"{self.reply} ({len(rows)} insights)"


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
            moments=[{"layer": "attention", "at": "00:00-00:03", "observed": "a face"}],
            input_tokens=100,
            output_tokens=50,
            model="fake",
        )
