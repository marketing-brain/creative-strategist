import json
from types import SimpleNamespace
from uuid import UUID

from cs.generation.brand import BrandKit
from cs.generation.copy import ClaudeCopyWriter, contains_prohibited


def _kit():
    return BrandKit(UUID(int=1), UUID(int=2), 1, ["#000"], ["Inter"], None, None, "Warm and direct.", ["cures", "guaranteed"])


def test_prohibited_filter():
    assert contains_prohibited("This Cures everything", ["cures"])
    assert not contains_prohibited("Feel better", ["cures"])


async def test_write_then_check(settings):
    responses = [
        json.dumps({"sets": [
            {"headline": "Sleep deeper", "primary_text": "Tired of waking up tired?", "cta_type": "SHOP_NOW"},
            {"headline": "Guaranteed sleep", "primary_text": "x", "cta_type": "SHOP_NOW"},
            {"headline": "Rest easy", "primary_text": "Wind down in minutes.", "cta_type": "LEARN_MORE"},
        ]}),
        json.dumps({"approved": [True, False]}),
    ]
    calls = []

    class M:
        async def create(self, **kw):
            calls.append(kw)
            return SimpleNamespace(stop_reason="end_turn", model="m", content=[SimpleNamespace(type="text", text=responses.pop(0))])

    client = SimpleNamespace(beta=SimpleNamespace(messages=M()))
    sets = await ClaudeCopyWriter(settings, client=client).write("brief", _kit(), 3)
    assert [s.headline for s in sets] == ["Sleep deeper"]
    assert "guaranteed" in calls[0]["messages"][0]["content"].lower()  # prohibited list is in the prompt
    assert len(calls) == 2
