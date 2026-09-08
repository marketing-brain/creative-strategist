from datetime import date
from decimal import Decimal

from cs.insights.copilot import CopilotTools, append_message, create_conversation, history
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
