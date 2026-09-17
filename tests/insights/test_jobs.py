from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from cs.insights import jobs as insight_jobs
from cs.insights.explain import GeminiGatewayExplainer
from cs.insights.types import InsightDraft
from tests.insights.seed import seed_account


async def test_schedules_enqueue_per_active_account(db, settings, org_id):
    await seed_account(db, org_id, settings)
    await insight_jobs.periodic_fatigue(db, settings)
    types = sorted(r["job_type"] for r in await db.fetch("SELECT job_type FROM job"))
    assert types == ["insights.compute_fatigue"]


def _draft():
    return InsightDraft(
        "pattern",
        "hook_type",
        "question",
        "ctr",
        date(2026, 8, 1),
        date(2026, 8, 31),
        6,
        Decimal("1"),
        0.02,
        0.03,
        0.5,
    )


def _completions_client(text, finish_reason="stop"):
    class M:
        async def create(self, **kw):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=finish_reason,
                        message=SimpleNamespace(content=text, refusal=None),
                    )
                ],
                model="google/gemini-2.5-flash-lite",
            )

    return SimpleNamespace(chat=SimpleNamespace(completions=M()))


async def test_gemini_explainer_parses_structured_output(settings):
    client = _completions_client('{"explanation": "E", "suggested_test": "T"}')
    assert await GeminiGatewayExplainer(settings, client=client).explain(
        _draft(), {"dimension_description": "x", "account_name": "A"}
    ) == ("E", "T")


async def test_gemini_explainer_falls_back_to_template_on_refusal(settings):
    client = _completions_client(None, finish_reason="content_filter")
    explanation, suggested_test = await GeminiGatewayExplainer(settings, client=client).explain(
        _draft(), {"dimension_description": "x", "account_name": "A"}
    )
    assert "hook_type=question" in explanation and suggested_test
