import json
from datetime import date
from decimal import Decimal

from cs.insights.account_read.ask import answer, cap_verdict, map_question, parse_query, read_as
from cs.insights.account_read.types import Test, Unit, to_json
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


async def test_map_question_parses_compare_targets_group_and_layer():
    w = FakeAccountReadWriter(
        [
            json.dumps(
                {
                    "kind": "compare",
                    "targets": ["messenger_age_band"],
                    "group": ["audience_callouts", "magnesium_users"],
                    "layer": "click",
                }
            )
        ]
    )
    q = await map_question("Which messenger age works for magnesium users?", _result_json(), w)
    assert q == {
        "kind": "compare",
        "targets": ["messenger_age_band"],
        "group": ("audience_callouts", "magnesium_users"),
        "layer": "click",
    }
    assert "messenger_age_band" in w.calls[0][1]  # the dimension list was offered
    assert "spend_gap" in w.calls[0][0]  # the kinds are described in the system prompt


async def test_map_question_unmappable():
    w = FakeAccountReadWriter([json.dumps({"unmappable": "persona is not tagged"})])
    out = await map_question("trust by persona", _result_json(), w)
    assert out == {"unmappable": "persona is not tagged"}


def test_parse_query_kinds_without_arguments():
    assert parse_query({"kind": "spend_gap", "layer": "conversion"}) == {
        "kind": "spend_gap",
        "layer": "conversion",
    }
    assert parse_query({"kind": "untried"}) == {"kind": "untried", "layer": "all"}


def test_parse_query_anchor_defaults_to_the_layer_rank_metric():
    assert parse_query({"kind": "nearest", "anchor": {"best": "cpa"}, "layer": "conversion"}) == {
        "kind": "nearest",
        "anchor": {"best": "cpa"},
        "layer": "conversion",
    }
    assert parse_query({"kind": "recipe", "anchor": {"named": "pharmacist"}}) == {
        "kind": "recipe",
        "anchor": {"named": "pharmacist"},
        "layer": "all",
    }
    assert parse_query({"kind": "recipe", "layer": "retention"})["anchor"] == {
        "best": "thruplay_rate"
    }
    assert parse_query({"kind": "nearest", "anchor": {"best": "nope"}})["anchor"] == {"best": "cpa"}


def test_parse_query_controlled_requires_target_and_defaults_control_to_spend():
    q = parse_query(
        {
            "kind": "controlled",
            "target": ["messenger", "expert_on_camera"],
            "control": ["proof_devices", "expert_credential"],
            "layer": "retention",
        }
    )
    assert q == {
        "kind": "controlled",
        "target": ("messenger", "expert_on_camera"),
        "control": ("proof_devices", "expert_credential"),
        "layer": "retention",
    }
    assert (
        parse_query({"kind": "controlled", "target": ["a", "b"], "control": "spend"})["control"]
        == "spend"
    )
    assert (
        parse_query({"kind": "controlled", "target": ["a", "b"], "control": 7})["control"]
        == "spend"
    )
    assert "unmappable" in parse_query({"kind": "controlled", "control": "spend"})


def test_parse_query_unknown_kind_falls_back_to_compare_or_unmappable():
    assert parse_query({"kind": "wat", "targets": ["registers"]})["kind"] == "compare"
    assert "unmappable" in parse_query({"kind": "wat"})
    assert "unmappable" in parse_query({})


def test_parse_query_defaults_a_malformed_layer_to_all():
    assert parse_query({"kind": "spend_gap", "layer": ["click"]})["layer"] == "all"
    assert parse_query({"kind": "untried", "layer": {"x": 1}})["layer"] == "all"
    assert parse_query({"kind": "untried", "layer": 7})["layer"] == "all"


def test_read_as_per_kind():
    assert (
        read_as(
            {
                "kind": "compare",
                "targets": [("registers", "fear")],
                "group": None,
                "layer": "conversion",
            }
        )
        == "registers = fear against everything else, on conversion metrics"
    )
    assert (
        read_as(
            {
                "kind": "compare",
                "targets": ["messenger"],
                "group": ("audience_callouts", "magnesium_users"),
                "layer": "click",
            }
        )
        == "every value of messenger against everything else within audience callouts = "
        "magnesium users, on click metrics"
    )
    assert (
        read_as({"kind": "spend_gap", "layer": "all"})
        == "spend share of every graded attribute against its effect, on all metrics"
    )
    assert (
        read_as({"kind": "untried", "layer": "click"})
        == "pairs and triples of attributes with clean positive effects on click metrics that no "
        "file carries together"
    )
    assert (
        read_as({"kind": "nearest", "anchor": {"best": "cpa"}, "layer": "conversion"})
        == "the five files most like the best-CPA file that do worse than it, on conversion metrics"
    )
    assert (
        read_as(
            {
                "kind": "controlled",
                "target": ("messenger", "expert_on_camera"),
                "control": "spend",
                "layer": "retention",
            }
        )
        == "messenger = expert on camera re-tested within each spend tercile, on retention metrics"
    )
    assert (
        read_as(
            {
                "kind": "controlled",
                "target": ("messenger", "expert_on_camera"),
                "control": ("proof_devices", "expert_credential"),
                "layer": "all",
            }
        )
        == "messenger = expert on camera re-tested inside and outside proof devices = expert "
        "credential, on all metrics"
    )
    assert (
        read_as({"kind": "recipe", "anchor": {"named": "pharmacist"}, "layer": "all"})
        == 'the ingredients of the file matching "pharmacist", ranked by their graded effects on '
        "all metrics"
    )


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


def _graded(dim, val, shas, change=0.5, metric="link_ctr", grade="suggestive"):
    return to_json(
        Test(
            dimension=dim,
            value=val,
            group=None,
            metric=metric,
            kind="per_unit",
            n_cell=len(shas),
            n_complement=14 - len(shas),
            cell_value=0.06,
            comp_value=0.03,
            change=change,
            p=0.02,
            grade=grade,
            confounds=[],
            cell_shas=list(shas),
        )
    )


async def test_answer_compare_carries_kind_and_read_as():
    w = FakeAccountReadWriter(
        [
            json.dumps({"kind": "compare", "targets": ["messenger_age_band"], "layer": "click"}),
            json.dumps(
                {
                    "verdict": "answerable",
                    "answer": "Older messengers earn more clicks.",
                    "pattern_ids": ["P1"],
                    "what_would_settle_it": "More files.",
                }
            ),
        ]
    )
    out = await answer("does messenger age matter for clicks?", _result_json(), w)
    assert out["kind"] == "compare"
    assert (
        out["read_as"]
        == "every value of messenger age band against everything else, on click metrics"
    )
    assert out["facts"] == []
    assert "READ AS: every value of messenger age band" in w.calls[1][1]


async def test_answer_empty_analysis_short_circuits_without_a_writer_call():
    w = FakeAccountReadWriter([json.dumps({"kind": "untried", "layer": "click"})])
    out = await answer("what have we never combined?", _result_json(), w)
    assert len(w.calls) == 1
    assert out["kind"] == "untried" and out["verdict"] == "not yet"
    assert out["answer"].startswith("Fewer than two attributes")
    assert out["facts"] == [] and out["tests"] == [] and out["pattern_ids"] == []


async def test_answer_named_anchor_miss_is_unmappable():
    w = FakeAccountReadWriter(
        [json.dumps({"kind": "recipe", "anchor": {"named": "zebra"}, "layer": "all"})]
    )
    out = await answer("what makes the zebra ad work?", _result_json(), w)
    assert out == {"unmappable": 'No file matches "zebra".'}
    assert len(w.calls) == 1


async def test_answer_controlled_with_unknown_partner_is_unmappable():
    w = FakeAccountReadWriter(
        [
            json.dumps(
                {
                    "kind": "controlled",
                    "target": ["messenger_age_band", "40_to_60"],
                    "control": ["registers", "fear"],
                    "layer": "click",
                }
            )
        ]
    )
    out = await answer(
        "is the older-messenger effect real once you control for fear?", _result_json(), w
    )
    assert out == {"unmappable": "No file carries registers = fear."}


async def test_answer_facts_are_numbered_in_the_prompt_and_cap_ignores_f_ids():
    result = _result_json()
    odd = [f"u{i}" for i in range(14) if i % 2]
    even = [f"u{i}" for i in range(14) if not i % 2]
    result["tests"] = [
        _graded("messenger_age_band", "40_to_60", odd, change=0.5),
        # Positive, so the heavily funded 25_to_40 side is never flagged as over-funded and
        # the starved 40_to_60 row is F1.
        _graded("messenger_age_band", "25_to_40", even, change=0.2),
    ]
    result["overlaps"] = []
    w = FakeAccountReadWriter(
        [
            json.dumps({"kind": "spend_gap", "layer": "click"}),
            json.dumps(
                {
                    "verdict": "answerable",
                    "answer": "Spend is even; nothing to move.",
                    "pattern_ids": ["F1", "P9"],
                    "what_would_settle_it": "n/a",
                }
            ),
        ]
    )
    out = await answer("where is the money going?", result, w)
    # Even spend across the fixture means no gap: the empty path.
    assert out["verdict"] == "not yet" and len(w.calls) == 1

    # Skew the spend so 40_to_60 is starved and the writer runs.
    for u in result["units"]:
        u["spend"] = 100 if u["sha256"] in odd else 5000
    w = FakeAccountReadWriter(
        [
            json.dumps({"kind": "spend_gap", "layer": "click"}),
            json.dumps(
                {
                    "verdict": "answerable",
                    "answer": "The older messenger is starved.",
                    "pattern_ids": ["F1", "P9"],
                    "what_would_settle_it": "n/a",
                }
            ),
        ]
    )
    out = await answer("where is the money going?", result, w)
    prompt = w.calls[1][1]
    assert "FACTS:\nF1 | starved | messenger age band = 40 to 60" in prompt
    assert "COMPARISONS:\nP1 | " in prompt
    assert out["facts"][0]["id"] == "F1" and out["facts"][0]["text"].startswith("starved |")
    assert out["pattern_ids"] == ["F1"]  # P9 does not exist; F1 is kept
    assert out["verdict"] == "not yet"  # F-ids never raise the verdict


async def test_answer_ranks_nearest_by_the_named_anchor_metric():
    result = _result_json()
    for u in result["units"]:
        u["spend"] = 500 if u["sha256"] == "u0" else 1000  # u0 is the best-CPA file
    w = FakeAccountReadWriter(
        [
            json.dumps({"kind": "nearest", "anchor": {"best": "cpa"}, "layer": "conversion"}),
            json.dumps(
                {
                    "verdict": "directional",
                    "answer": "The lookalikes cost more per purchase.",
                    "pattern_ids": [],
                    "what_would_settle_it": "n/a",
                }
            ),
        ]
    )
    out = await answer("what separates our best CPA video from its lookalikes?", result, w)
    assert out["kind"] == "nearest"
    texts = [f["text"] for f in out["facts"]]
    assert texts[0].startswith('anchor | "Ad 0"')
    assert "CPA $17" in texts[0]
    assert len([t for t in texts if t.startswith("neighbour |")]) == 5
