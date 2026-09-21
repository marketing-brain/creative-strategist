"""The Explore block reports absence. These pin what counts as an absence worth reporting."""

from cs.insights.next_plan import explore
from tests.insights.account_read.test_analyzers import _unit


def _rows(units):
    rows, reason = explore.build(units, "video")
    assert reason is None
    return {(r.dimension, r.value): r for r in rows}


def test_closed_multi_dimensions_are_in_scope():
    """hook_devices, proof_devices, angles and registers carry explicit value lists, and an
    empty one of those is the most interesting kind of gap."""
    rows = _rows([_unit("a", [("angles", "savings")])])
    assert ("proof_devices", "guarantee") in rows
    assert ("hook_devices", "curiosity_gap") in rows
    assert rows[("proof_devices", "guarantee")].status == "never tried"


def test_values_naming_an_absence_are_not_proposed():
    """ "Never tried no offer" is not a brief."""
    rows = _rows([_unit("a", [("angles", "savings")])])
    assert not [k for k in rows if k[1] in ("none", "other")]


def test_demographic_dimensions_are_not_proposed():
    rows = _rows([_unit("a", [("angles", "savings")])])
    assert not [k for k in rows if k[0] in ("messenger_age_band", "messenger_gender_presentation")]


def test_a_well_covered_value_drops_out_but_a_thin_one_is_flagged():
    covered = [_unit(f"u{i}", [("angles", "savings")]) for i in range(6)]
    thin = [_unit(f"v{i}", [("angles", "novelty")]) for i in range(2)]
    rows = _rows(covered + thin)
    assert ("angles", "savings") not in rows  # at or above MIN_CELL, testable already
    assert rows[("angles", "novelty")].status == "too few files to test"
    assert rows[("angles", "novelty")].n_units == 2


def test_language_is_not_proposed_as_something_to_try():
    """Explore proposes values purely because they are absent. A US brand that has never shot
    a Hindi ad has not left a creative opportunity on the table -- that is a market decision."""
    rows = _rows([_unit("a", [("angles", "savings")])])
    assert not [k for k in rows if k[0] in ("spoken_language", "subtitle_language")]
