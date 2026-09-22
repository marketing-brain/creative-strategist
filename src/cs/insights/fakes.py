from __future__ import annotations


class FakeAccountReadWriter:
    """Returns canned JSON texts in order; records the prompts it was given."""

    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> tuple[str, int, int]:
        self.calls.append((system, user))
        text = self.texts.pop(0) if self.texts else "{}"
        return text, 10, 5
