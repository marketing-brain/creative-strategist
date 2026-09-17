import json
from datetime import date
from decimal import Decimal

from cs.insights.account_read.ask import answer, cap_verdict, map_question
from cs.insights.account_read.types import Unit, to_json
from cs.insights.fakes import FakeAccountReadWriter


def _units():
    us = []
    for i in range(14):
        facets = {
            ("messenger_age_band", "40_to_60" if i % 2 else "25_to_40"),
            ("audience_callouts", "magnesium_users"),
        }
        us.append(
            Unit(
                sha256=f"u{i}",
                creative_ids=[],
                rep_creative_id=None,
                headline=f"Ad {i}",
                duration_s=60.0,
                spend=Decimal(1000),
                impressions=20_000,
                plays=15_000,
                video_3s_views=8_000,
                thruplays=3_000,
                p25=8000,
                p50=5000,
                p75=3000,
                p100=2000,
                link_clicks=1_200 if i % 2 else 600,
                purchases=30,
                first_day=date(2026, 6, 1),
                last_day=date(2026, 6, 30),
                facets=facets,
                numeric={},
                first_product_s=None,
                concept_summary=None,
                hook_verbatim=None,
                insight_paragraph=None,
            )
        )
    return us


def _result_json():
    return {
        "units": [to_json(u) for u in _units()],
        "tests": [],
        "summary": {"units": 14, "dimensions": ["messenger_age_band", "audience_callouts"]},
    }


async def test_map_question_parses_targets_group_and_layer():
    w = FakeAccountReadWriter(
        [
            json.dumps(
                {
                    "targets": ["messenger_age_band"],
                    "group": ["audience_callouts", "magnesium_users"],
                    "layer": "click",
                }
            )
        ]
    )
    q = await map_question("Which messenger age works for magnesium users?", _result_json(), w)
    assert q == {
        "targets": ["messenger_age_band"],
        "group": ("audience_callouts", "magnesium_users"),
        "layer": "click",
    }
    assert "messenger_age_band" in w.calls[0][1]  # the dimension list was offered


async def test_map_question_unmappable():
    w = FakeAccountReadWriter([json.dumps({"unmappable": "persona is not tagged"})])
    out = await map_question("trust by persona", _result_json(), w)
    assert out == {"unmappable": "persona is not tagged"}


async def test_answer_runs_targeted_tests_and_writes_prose():
    mapper = json.dumps({"targets": ["messenger_age_band"], "group": None, "layer": "click"})
    prose = json.dumps(
        {
            "verdict": "answerable",
            "answer": "40 to 60 messengers show link CTR 6.0% vs 3.0% (P1).",
            "pattern_ids": ["P1"],
            "what_would_settle_it": "more files",
        }
    )
    out = await answer(
        "Which age band earns clicks?",
        _result_json(),
        FakeAccountReadWriter([mapper, prose]),
        draws=100,
    )
    assert "unmappable" not in out
    assert any(
        t["dimension"] == "messenger_age_band" and t["metric"] == "link_ctr" for t in out["tests"]
    )
    assert out["verdict"] in ("answerable", "directional", "not yet")
    assert "P1" in out["pattern_ids"]


def test_cap_verdict_never_exceeds_evidence():
    strong = [{"grade": "strong", "confounds": []}]
    sugg = [{"grade": "suggestive", "confounds": []}] * 2
    assert cap_verdict("answerable", strong) == "answerable"
    assert cap_verdict("answerable", sugg) == "directional"
    assert cap_verdict("answerable", []) == "not yet"
