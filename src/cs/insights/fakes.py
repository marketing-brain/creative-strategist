from __future__ import annotations


class FakeAccountReadWriter:
    """Returns canned JSON texts in order; records the prompts it was given."""

    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.calls: list[tuple[str, str]] = []
        self.kinds: list[str] = []

    def model_for(self, call) -> str:
        return "fake-fix" if call == self.correct else "fake-draft"

    async def complete(self, system: str, user: str) -> tuple[str, int, int]:
        return await self._answer(system, user, "complete")

    async def draft(self, system: str, user: str) -> tuple[str, int, int]:
        return await self._answer(system, user, "draft")

    async def correct(self, system: str, user: str) -> tuple[str, int, int]:
        return await self._answer(system, user, "correct")

    async def _answer(self, system: str, user: str, kind: str) -> tuple[str, int, int]:
        self.calls.append((system, user))
        self.kinds.append(kind)
        text = self.texts.pop(0) if self.texts else "{}"
        return text, 10, 5
