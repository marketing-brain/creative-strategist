from __future__ import annotations

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
