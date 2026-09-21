"""Rulings the Make block must not lose. Each of these was a defect the first real-data run
showed (org 3, `scripts/next_plan_probe.py`), not a hypothetical."""

from decimal import Decimal

from cs.insights.next_plan import make
from cs.insights.next_plan.stake import stake, wrong_side
from tests.insights.account_read.test_analyzers import _test, _unit

KINDS = {"duration_s": "single"}  # everything else falls through to "multi"


def _units_with(specs: list[tuple[str, list, float, int]]):
    """(sha, facets, duration_s, spend) -> units."""
    out = []
    for sha, facets, duration, spend in specs:
        u = _unit(sha, facets, spend=spend)
        u.duration_s = duration
        out.append(u)
    return out


def _good(dim, val, shas, *, metric="link_ctr", p=0.01):
    return _test(dim, val, metric=metric, change=0.5, p=p, grade="suggestive", cell_shas=shas)


def test_stake_is_the_spend_on_the_side_the_evidence_argues_against():
    units = _units_with(
        [
            ("a", [("angles", "savings")], 30.0, 100),
            ("b", [("angles", "savings")], 30.0, 100),
            ("c", [], 30.0, 700),
        ]
    )
    t = _good("angles", "savings", ["a", "b"])
    assert [u.sha256 for u in wrong_side(t, units, KINDS)] == ["c"]
    s = stake(t, units, KINDS, account_spend=900.0)
    assert s.amount == 700.0
    assert round(s.share, 4) == round(700 / 900, 4)
    assert s.window == "window"


def test_a_demographic_attribute_never_becomes_an_ingredient():
    """Observing the pattern is fine; prescribing it is not. See next_plan.policy."""
    units = _units_with(
        [
            (f"u{i}", [("messenger_gender_presentation", "man"), ("angles", "savings")], 30.0, 100)
            for i in range(5)
        ]
        + [(f"v{i}", [("registers", "authority")], 30.0, 100) for i in range(5)]
    )
    tests = [
        _good("messenger_gender_presentation", "man", [f"u{i}" for i in range(5)], p=0.001),
        _good("angles", "savings", [f"u{i}" for i in range(5)]),
        _good("registers", "authority", [f"v{i}" for i in range(5)]),
    ]
    briefs, reason = make.build(units, tests, [], 1000.0, KINDS)
    assert reason is None
    named = {tuple(a) for b in briefs for a in b.attributes}
    assert ("messenger_gender_presentation", "man") not in named
    assert named  # the non-demographic ingredients still produce briefs


def test_every_brief_introduces_an_ingredient_no_earlier_brief_used():
    """Without this the list is one idea re-cut: a pair, then that pair plus a third."""
    units = _units_with([(f"u{i}", [], 30.0, 100) for i in range(8)])
    for i, facet in enumerate(
        [("angles", "savings"), ("registers", "authority"), ("proof_devices", "guarantee")]
    ):
        units[i].facets.add(facet)
    tests = [
        _good("angles", "savings", ["u0"], p=0.001),
        _good("registers", "authority", ["u1"], p=0.002),
        _good("proof_devices", "guarantee", ["u2"], p=0.003),
    ]
    briefs, _ = make.build(units, tests, [], 800.0, KINDS)
    assert len(briefs) >= 2
    seen: set[tuple[str, str]] = set()
    for b in briefs:
        attrs = {tuple(a) for a in b.attributes}
        assert attrs - seen, f"{b.title} repeats ingredients already covered"
        seen |= attrs


def test_the_shape_obeys_a_duration_ingredient_instead_of_contradicting_it():
    """A combination naming "under 30s" printed a 200s median, dragged there by the long files
    that carried its other ingredient."""
    short = _units_with([(f"s{i}", [("duration_s", "low")], 10.0, 100) for i in range(4)])
    long_ = _units_with([(f"l{i}", [("registers", "authority")], 200.0, 100) for i in range(4)])
    units = short + long_
    tests = [
        _good("duration_s", "low", [u.sha256 for u in short]),
        _good("registers", "authority", [u.sha256 for u in long_], p=0.02),
    ]
    briefs, _ = make.build(units, tests, [], 800.0, KINDS, bin_edges={"duration_s": (30.0, 60.0)})
    combo = next(b for b in briefs if ["duration_s", "low"] in b.attributes)
    assert combo.shape.duration_s is not None
    assert combo.shape.duration_s <= 30.0
    assert combo.shape.duration_range is not None
    assert combo.shape.duration_range[1] <= 30.0


def test_a_brief_says_when_nothing_it_cites_is_graded_on_purchases():
    units = _units_with([(f"u{i}", [("angles", "savings")], 30.0, 100) for i in range(4)])
    units += _units_with([(f"v{i}", [("registers", "authority")], 30.0, 100) for i in range(4)])
    tests = [
        _good("angles", "savings", ["u0", "u1", "u2", "u3"]),
        _good("registers", "authority", ["v0", "v1", "v2", "v3"], p=0.02),
    ]
    briefs, _ = make.build(units, tests, [], 800.0, KINDS)
    assert briefs
    for b in briefs:
        assert "attention test, not a CPA test" in b.caveat
        assert b.downstream


def test_the_binding_ingredient_is_the_one_with_the_least_addressable_spend():
    units = _units_with([(f"u{i}", [("angles", "savings")], 30.0, 100) for i in range(7)])
    units += _units_with([(f"v{i}", [("registers", "authority")], 30.0, 100) for i in range(3)])
    # savings is on 7 of 10 files, so little spend is left to move; authority is on 3.
    tests = [
        _good("angles", "savings", [f"u{i}" for i in range(7)]),
        _good("registers", "authority", [f"v{i}" for i in range(3)], p=0.02),
    ]
    briefs, _ = make.build(units, tests, [], float(sum(u.spend for u in units)), KINDS)
    assert briefs[0].binding == "angles = savings"
    assert briefs[0].stake.amount == float(Decimal(300))


def test_ingredients_are_listed_not_chained_with_and():
    """Three ingredients joined with "and" twice read as one run-on clause on the page."""
    assert make._listed(["A"]) == "A"
    assert make._listed(["A", "B"]) == "A and B"
    assert make._listed(["A", "B", "C"]) == "A, B and C"


def test_the_hold_line_names_every_ingredient_once():
    units = _units_with([(f"u{i}", [("angles", "savings")], 30.0, 100) for i in range(4)])
    units += _units_with([(f"v{i}", [("registers", "authority")], 30.0, 100) for i in range(4)])
    tests = [
        _good("angles", "savings", ["u0", "u1", "u2", "u3"]),
        _good("registers", "authority", ["v0", "v1", "v2", "v3"], p=0.02),
    ]
    briefs, _ = make.build(units, tests, [], 800.0, KINDS)
    # Only the clause listing the ingredients; the first sentence has its own "and".
    clause = briefs[0].hold.split("changes are ", 1)[1]
    assert clause.count(" and ") == 1, clause
    assert clause.startswith("angles = savings and registers = authority")
