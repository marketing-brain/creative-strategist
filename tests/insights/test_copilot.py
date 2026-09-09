import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from cs.insights.copilot import (
    CopilotTools,
    GeminiGatewayCopilot,
    append_message,
    create_conversation,
    history,
)
from tests.insights.seed import seed_account, seed_creative


async def test_tools_return_serializable_data(db, org_id, settings):
    acct = await seed_account(db, org_id, settings)
    today = date(2026, 9, 1)
    cid = await seed_creative(
        db,
        org_id,
        acct,
        "q1",
        "image",
        {"hook_type": "question"},
        [(today, Decimal("10"), 1000, 20, Decimal("1"), Decimal("40"))],
    )
    tools = CopilotTools(db, org_id)
    m = await tools.metrics_by_creative(str(cid), "2026-08-01", "2026-09-30")
    assert m[0]["day"] == "2026-09-01" and m[0]["ctr"] == 0.02
    assert (await tools.tags_for_creative(str(cid)))[0]["dimension"] == "hook_type"
    top = await tools.top_creatives_by_dimension(
        "hook_type", "question", "ctr", "2026-08-01", "2026-09-30"
    )
    assert top[0]["creative_id"] == str(cid) and top[0]["ctr"] == 0.02
    assert await tools.insights() == [] and await tools.fatigue_alerts() == []


async def test_conversation_persistence(db, org_id):
    conv = await create_conversation(db, org_id, "u1", "t")
    await append_message(db, org_id, conv, "user", "hi")
    await append_message(db, org_id, conv, "assistant", "hello")
    assert await history(db, org_id, conv) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


class _FakeTools:
    async def fatigue_alerts(self, limit: int = 10) -> list[dict]:
        return [{"creative_id": "c1", "lift": -0.3}]


def _tool_call(name, args, call_id="call_1"):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args))
    )


def _completion(content=None, tool_calls=None, finish_reason="stop", refusal=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, tool_calls=tool_calls, refusal=refusal),
            )
        ],
        model="google/gemini-2.5-flash",
    )


def _client(responses):
    calls = []

    class M:
        async def create(self, **kw):
            calls.append(kw)
            return responses.pop(0)

    return SimpleNamespace(chat=SimpleNamespace(completions=M())), calls


async def test_gemini_copilot_calls_a_tool_then_answers(settings):
    responses = [
        _completion(tool_calls=[_tool_call("fatigue_alerts", {"limit": 5})]),
        _completion(content="Creative c1 is fatiguing, down 30%."),
    ]
    client, calls = _client(responses)
    reply = await GeminiGatewayCopilot(settings, client=client).answer(
        [{"role": "user", "content": "any fatigue?"}], _FakeTools()
    )
    assert reply == "Creative c1 is fatiguing, down 30%."
    assert len(calls) == 2
    # the tool result was fed back as a `tool`-role message before the second call
    second_call_messages = calls[1]["messages"]
    tool_msg = next(m for m in second_call_messages if m["role"] == "tool")
    assert json.loads(tool_msg["content"]) == [{"creative_id": "c1", "lift": -0.3}]


async def test_gemini_copilot_handles_refusal(settings):
    client, _ = _client([_completion(finish_reason="content_filter")])
    reply = await GeminiGatewayCopilot(settings, client=client).answer(
        [{"role": "user", "content": "hack something"}], _FakeTools()
    )
    assert reply == "I can't help with that request."


async def test_gemini_copilot_gives_up_after_max_iterations(settings):
    responses = [_completion(tool_calls=[_tool_call("fatigue_alerts", {})]) for _ in range(10)]
    client, calls = _client(responses)
    reply = await GeminiGatewayCopilot(settings, client=client).answer(
        [{"role": "user", "content": "loop forever"}], _FakeTools()
    )
    assert reply == "I could not produce an answer."


async def test_gemini_copilot_tells_the_model_todays_date(settings):
    """Regression test: without this, the model resolves 'last 10 days' against its
    own stale training-era notion of 'today' instead of the real current date, and
    silently queries the wrong window (confirmed live -- it queried July 2024)."""
    from datetime import date as _date

    client, calls = _client([_completion(content="ok")])
    await GeminiGatewayCopilot(settings, client=client).answer(
        [{"role": "user", "content": "how did my ads do this week?"}], _FakeTools()
    )
    system_msg = calls[0]["messages"][0]
    assert system_msg["role"] == "system"
    assert _date.today().isoformat() in system_msg["content"]


async def test_gemini_copilot_forces_a_final_answer_on_the_last_iteration(settings):
    """If the model is still trying to call tools right up to the iteration cap, the
    last call goes out with no tools offered so it's forced to synthesize a real
    answer from what it already has, instead of just giving up."""
    responses = [_completion(tool_calls=[_tool_call("fatigue_alerts", {})]) for _ in range(9)]
    responses.append(_completion(content="Based on what I found, one creative is fatiguing."))
    client, calls = _client(responses)
    reply = await GeminiGatewayCopilot(settings, client=client).answer(
        [{"role": "user", "content": "keep digging"}], _FakeTools()
    )
    assert reply == "Based on what I found, one creative is fatiguing."
    assert len(calls) == 10
    assert calls[-1]["tools"] is None
    assert len(calls) == 10
