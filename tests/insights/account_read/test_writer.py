import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal

from cs.insights.account_read.types import Finding, Read, RunResult, Test, Unit
from cs.insights.account_read.writer import build_brief, check_read, parse_read, write_read
from cs.insights.fakes import FakeAccountReadWriter


def _unit(sha, headline, facets, spend=1000):
    return Unit(
        sha256=sha,
        creative_ids=[],
        rep_creative_id=None,
        headline=headline,
        duration_s=60.0,
        spend=Decimal(spend),
        impressions=10_000,
        plays=8_000,
        video_3s_views=4_000,
        thruplays=1_200,
        p25=4_000,
        p50=2_500,
        p75=1_500,
        p100=1_000,
        link_clicks=300,
        purchases=30,
        first_day=date(2026, 6, 1),
        last_day=date(2026, 6, 30),
        facets=set(facets),
        numeric={},
        first_product_s=None,
        concept_summary="Pharmacist warns about Benadryl.",
        hook_verbatim="Worst OTC sleep aids",
        insight_paragraph="Strong filter hook.",
    )


def _run():
    units = [
        _unit(
            f"u{i}",
            f"Ad {i}",
            {("hook_devices", "warning_or_risk")} if i % 2 == 0 else {("angles", "convenience")},
        )
        for i in range(12)
    ]
    t = Test(
        dimension="hook_devices",
        value="warning_or_risk",
        group=None,
        metric="link_ctr",
        kind="per_unit",
        n_cell=6,
        n_complement=6,
        cell_value=0.045,
        comp_value=0.030,
        change=0.5,
        p=0.004,
        grade="suggestive",
        confounds=[],
        cell_shas=[u.sha256 for u in units if u.has("hook_devices", "warning_or_risk")],
    )
    summary = {
        "units": 12,
        "creatives": 12,
        "spend": 12000,
        "purchases": 360,
        "tests": 1,
        "interaction_tests": 0,
        "by_grade": {"strong": 0, "suggestive": 1, "directional": 0, "thin": 0},
        "dimensions": ["hook_devices"],
        "first_day": "2026-06-01",
        "last_day": "2026-06-30",
    }
    return RunResult(
        units=units, tests=[t], overlaps=[], stratified=[], retention_by_reveal={}, summary=summary
    )


def _finding():
    return Finding(
        title="Warning hooks earn the click",
        layer="click",
        paragraph="Warning hooks show link CTR 4.5% with vs 3.0% without (P1), a 50% higher rate.",
        mechanism="The warning frames the category as risky, so the click is a check.",
        exemplars=["Ad 0", "Ad 2"],
        exception="Ad 4 opens with a warning but converts no better.",
        why_non_obvious="Every warning ad is also a pharmacist, so this may be authority not fear.",
        pattern_ids=["P1"],
        confidence="suggestive",
    )


def _read(**over):
    base = dict(
        overview="Warning-led pharmacist ads earn clicks.",
        findings=[_finding()],
        entangled="none",
        no_evidence="fear register untested",
        next_tests=["Test a creator delivering the same warning."],
        data_needed=["More files."],
    )
    base.update(over)
    return Read(**base)


def _json(read: Read) -> str:
    return json.dumps(asdict(read))


def test_brief_carries_qualitative_record_and_pattern_lines():
    b = build_brief(_run())
    assert (
        "P1 | hook devices = warning or risk | link CTR: 4.5% with vs 3.0% without (50% higher)"
        in b
    )
    assert "Worst OTC sleep aids" in b and "Pharmacist warns about Benadryl." in b
    assert "Strong filter hook." in b and '"Ad 0"' in b


def test_check_read_rejects_invented_numbers_missing_exemplars_and_empty_why():
    brief = build_brief(_run())
    assert check_read(_read(), brief, n_tests=1) == []
    assert any("99" in p for p in check_read(_read(overview="CTR rose 99%."), brief, 1))
    f = _finding()
    f.exemplars = ["Ad that does not exist"]
    assert any("exemplar" in p for p in check_read(_read(findings=[f]), brief, 1))
    f2 = _finding()
    f2.why_non_obvious = ""
    assert any("why_non_obvious" in p for p in check_read(_read(findings=[f2]), brief, 1))
    f3 = _finding()
    f3.pattern_ids = ["P9"]
    assert any("P9" in p for p in check_read(_read(findings=[f3]), brief, 1))


def test_parse_read_tolerates_fences_and_missing_fields():
    r = parse_read('```json\n{"overview": "o", "findings": [], "next_tests": ["t"]}\n```')
    assert r.overview == "o" and r.findings == [] and r.entangled == ""
    assert r.next_tests == ["t"] and r.data_needed == []


async def test_write_read_retries_once_then_rejects():
    run = _run()
    good, bad = _json(_read()), _json(_read(overview="CTR rose 99%."))
    read, problems, _, _ = await write_read(run, FakeAccountReadWriter([bad, good]))
    assert read is not None and problems == []
    read2, problems2, _, _ = await write_read(run, FakeAccountReadWriter([bad, bad]))
    assert read2 is None and problems2


def test_check_read_requires_naming_a_collapsing_entanglement():
    brief = build_brief(_run())
    collapses = {"P1": "proof devices = expert credential"}
    problems = check_read(_read(), brief, 1, collapses)
    assert any("entangle" in p and "expert credential" in p for p in problems)
    # the finding names the partner but the entangled paragraph does not: one problem left
    f = _finding()
    f.exception = (
        "The same files carry the expert credential proof, so the two cannot be separated."
    )
    problems = check_read(_read(findings=[f]), brief, 1, collapses)
    assert problems == [
        "'entangled' must name the pair whose effects cannot be separated: "
        "'proof devices = expert credential'"
    ]
    # the entangled paragraph naming it satisfies the finding-level check too
    problems = check_read(
        _read(entangled="Warning hooks ride on expert credential files."), brief, 1, collapses
    )
    assert problems == []
    both = _read(findings=[f], entangled="Warning hooks ride on expert credential files.")
    assert check_read(both, brief, 1, collapses) == []


def test_entanglement_collapses_detects_vanishing_effect():
    from cs.insights.account_read.writer import entanglement_collapses

    run = _run()
    run.stratified = [
        {
            "attribute": ["hook_devices", "warning_or_risk"],
            "controlling_for": ["proof_devices", "expert_credential"],
            "metric": "link_ctr",
            "overall_change": 0.5,
            "within": {"change": 0.02, "n": "6 vs 4"},
            "outside": {"change": 0.6, "n": "3 vs 30"},
        },
        {
            "attribute": ["hook_devices", "warning_or_risk"],
            "controlling_for": ["angles", "convenience"],
            "metric": "link_ctr",
            "overall_change": 0.5,
            "within": {"change": 0.45, "n": "6 vs 4"},
            "outside": {"change": 0.5, "n": "3 vs 30"},
        },
    ]
    assert entanglement_collapses(run) == {"P1": "proof devices = expert credential"}


def test_generic_partner_values_need_the_dimension_name_and_entangled_must_name_pairs():
    brief = build_brief(_run())
    collapses = {"P1": "duration s = high"}
    f = _finding()
    f.paragraph = "A high-consideration product; link CTR 4.5% with vs 3.0% without (P1)."
    # "high" alone is an accident of prose, not a mention of the duration tercile
    problems = check_read(_read(findings=[f]), brief, 1, collapses)
    assert any("entangle" in p for p in problems)
    f.exception = "The same files are the long videos (duration high), so the two cannot be split."
    problems = check_read(_read(findings=[f]), brief, 1, collapses)
    # the finding now names it, but the entangled paragraph still does not
    assert problems == [
        "'entangled' must name the pair whose effects cannot be separated: 'duration s = high'"
    ]
    ok = _read(findings=[f], entangled="Warning hooks and duration (long videos) share files.")
    assert check_read(ok, brief, 1, collapses) == []


def test_parse_read_salvages_a_top_level_list_and_skips_non_dict_findings():
    r = parse_read(
        '[{"overview": "o", "findings": [{"title": "t"}, "junk", ["x"]], "next_tests": []}]'
    )
    assert r.overview == "o" and [f.title for f in r.findings] == ["t"]


async def test_write_read_gets_three_attempts():
    run = _run()
    bad = _json(_read(overview="CTR rose 99%."))
    good = _json(_read())
    writer = FakeAccountReadWriter([bad, bad, good])
    read, problems, _, _ = await write_read(run, writer)
    assert read is not None and problems == [] and len(writer.calls) == 3
