"""The draft thinks; a correction does not.

A correction applies edits the gates have already named and located. Letting the model
deliberate over that cost 65,000 output tokens against a 1,800-token answer, five minutes a
call, and three runs that died when the response outlasted the connection (2026-09-23).
"""

from cs.insights.account_read.types import RunResult
from cs.insights.account_read.writer import NO_REASONING, write_read


class Writer:
    """Answers with a draft too thin to pass the checks, so the loop keeps correcting."""

    def __init__(self):
        self.reasoning: list[dict | None] = []

    async def complete(self, system, user, reasoning=None):
        self.reasoning.append(reasoning)
        return '{"overview": "o", "findings": []}', 10, 5


def _run() -> RunResult:
    return RunResult(
        units=[],
        tests=[],
        overlaps=[],
        stratified=[],
        retention_by_reveal={},
        summary={"units": 0, "creatives": 0, "spend": 0, "purchases": 0},
        bin_edges={},
    )


async def test_the_draft_thinks_and_every_correction_does_not():
    w = Writer()
    await write_read(_run(), w)

    assert w.reasoning[0] is None, "the draft takes the model's own default"
    assert all(r == NO_REASONING for r in w.reasoning[1:]), "corrections do not deliberate"
    assert len(w.reasoning) > 1, "the thin draft should have been sent back"
