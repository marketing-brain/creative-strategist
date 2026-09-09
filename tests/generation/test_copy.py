import json
from types import SimpleNamespace
from uuid import UUID

from cs.generation.brand import BrandKit
from cs.generation.copy import GeminiGatewayCopyWriter, contains_prohibited


def _kit():
    return BrandKit(
        UUID(int=1),
        UUID(int=2),
        1,
        ["#000"],
        ["Inter"],
        None,
        None,
        "Warm and direct.",
        ["cures", "guaranteed"],
    )


def test_prohibited_filter():
    assert contains_prohibited("This Cures everything", ["cures"])
    assert not contains_prohibited("Feel better", ["cures"])


def _completions_client(responses, finish_reason="stop"):
    calls = []

    class M:
        async def create(self, **kw):
            calls.append(kw)
            text = responses.pop(0) if responses else None
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=finish_reason,
                        message=SimpleNamespace(content=text, refusal=None),
                    )
                ],
                model="google/gemini-3-flash",
            )

    return SimpleNamespace(chat=SimpleNamespace(completions=M())), calls


async def test_write_then_check(settings):
    responses = [
        json.dumps(
            {
                "sets": [
                    {
                        "headline": "Sleep deeper",
                        "primary_text": "Tired of waking up tired?",
                        "cta_type": "SHOP_NOW",
                    },
                    {"headline": "Guaranteed sleep", "primary_text": "x", "cta_type": "SHOP_NOW"},
                    {
                        "headline": "Rest easy",
                        "primary_text": "Wind down in minutes.",
                        "cta_type": "LEARN_MORE",
                    },
                ]
            }
        ),
        json.dumps({"approved": [True, False]}),
    ]
    client, calls = _completions_client(responses)
    sets = await GeminiGatewayCopyWriter(settings, client=client).write("brief", _kit(), 3)
    assert [s.headline for s in sets] == ["Sleep deeper"]
    assert (
        "guaranteed" in calls[0]["messages"][-1]["content"].lower()
    )  # prohibited list is in the prompt
    assert len(calls) == 2


async def test_write_returns_empty_on_refusal(settings):
    client, calls = _completions_client([], finish_reason="content_filter")
    sets = await GeminiGatewayCopyWriter(settings, client=client).write("brief", _kit(), 3)
    assert sets == [] and len(calls) == 1  # never reaches the compliance-check call
