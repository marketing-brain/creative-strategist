"""The draft reasons; a correction runs on a model that does not.

Measured on the gateway 2026-09-23: gemini-3-flash spends ~96% of its output on reasoning
and ignores every attempt to stop, which is why a correction cost 65,000 output tokens to
apply an edit the gates had already named. The fix is the model, not a parameter.
"""

from cs.insights.account_read.types import RunResult
from cs.insights.account_read.writer import GeminiGatewayAccountReadWriter, write_read


class Writer:
    """Answers with a draft too thin to pass the checks, so the loop keeps correcting."""

    def __init__(self):
        self.models: list[str] = []

    def model_for(self, call) -> str:
        return "fix" if call == self.correct else "draft"

    async def draft(self, system, user):
        self.models.append("draft")
        return '{"overview": "o", "findings": []}', 10, 5

    async def correct(self, system, user):
        self.models.append("fix")
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


async def test_the_first_call_drafts_and_the_rest_correct():
    w = Writer()
    await write_read(_run(), w)

    assert w.models[0] == "draft"
    assert set(w.models[1:]) == {"fix"}, "every retry is a correction"
    assert len(w.models) > 1, "the thin draft should have been sent back"


def test_the_two_calls_use_different_models(settings):
    """The correction model is the one that can be stopped from thinking."""
    w = GeminiGatewayAccountReadWriter(settings)

    assert w.model_for(w.draft) == settings.account_read_model
    assert w.model_for(w.correct) == settings.account_read_fix_model
    assert w.model != w.fix_model
