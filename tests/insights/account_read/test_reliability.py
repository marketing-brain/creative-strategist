from cs.insights.account_read.facets import extract
from cs.insights.account_read.reliability import MIN_AGREEMENT, MIN_DIM_FILES, agreement


def run(**dims):
    """One tag run as the extractor sees it: dimension -> value or list of values."""
    rows = []
    for d, v in dims.items():
        for x in v if isinstance(v, list) else [v]:
            kind = (
                "numeric" if isinstance(x, float) else "multi" if isinstance(v, list) else "single"
            )
            rows.append(
                {
                    "dimension": d,
                    "kind": kind,
                    "value": str(x),
                    "raw_value": None,
                    "t_start_s": None,
                    "ordinal": None,
                }
            )
    return extract(rows, duration_s=60.0)


def files(n, *runs):
    return [list(runs) for _ in range(n)]


def test_a_value_every_run_agrees_on_scores_one():
    r = agreement(files(MIN_DIM_FILES, run(messenger="expert"), run(messenger="expert")))
    assert r.values[("messenger", "expert")].agreement == 1.0
    assert r.reliable("messenger", "expert")


def test_a_value_the_runs_flip_on_is_unreliable():
    # The two runs of the file never agree: the label is a coin flip.
    r = agreement(files(MIN_DIM_FILES, run(setting="home"), run(setting="studio_or_neutral")))
    assert r.values[("setting", "home")].agreement == 0.0
    assert not r.reliable("setting", "home")


def test_agreement_is_both_over_either_not_over_pairs():
    # Three runs, two of which name the trait: one pair of three agrees, and the pair that
    # names it in neither run is not evidence either way.
    r = agreement(files(MIN_DIM_FILES, run(claims=["a"]), run(claims=["a"]), run(claims=["b"])))
    assert r.values[("claims", "a")].agreement == round(1 / 3, 3)
    assert r.values[("claims", "b")].agreement == 0.0


def test_a_value_never_seen_on_a_retagged_file_falls_back_to_its_dimension():
    r = agreement(files(MIN_DIM_FILES, run(claims=["common"]), run(claims=["common"])))
    assert r.score("claims", "never_seen") is r.dimensions["claims"]
    assert r.reliable("claims", "never_seen")  # the dimension is steady, so it passes


def test_a_thin_value_keeps_its_own_score_rather_than_its_dimensions():
    """A dimension's mean is a bad stand-in for a value that was tried and missed: on org 3 it
    carried `messenger_age_band = 40_to_60` (0.50 on four files) into a test to run."""
    seen = files(MIN_DIM_FILES, run(claims=["common"]), run(claims=["common"]))
    seen[0] = [run(claims=["common", "rare"]), run(claims=["common"])]
    r = agreement(seen)
    assert r.dimensions["claims"].agreement >= MIN_AGREEMENT
    assert r.values[("claims", "rare")].files == 1
    assert r.score("claims", "rare") is r.values[("claims", "rare")]
    assert not r.reliable("claims", "rare")


def test_unmeasured_attributes_pass():
    """Unmeasured means the value was never seen on a re-tagged file and its dimension has too
    few to stand in. An account whose files were each tagged once has nothing to judge by."""
    r = agreement(files(MIN_DIM_FILES - 1, run(messenger="a"), run(messenger="b")))
    assert r.dimensions["messenger"].files < MIN_DIM_FILES
    assert r.score("messenger", "never_seen") is None
    assert r.reliable("messenger", "never_seen")
    assert r.reliable("never_seen", "at_all")
    assert agreement([]).reliable("anything", "at_all")


def test_a_number_read_within_tolerance_agrees():
    steady = agreement(files(MIN_DIM_FILES, run(duration_s=60.0), run(duration_s=62.0)))
    assert steady.dimensions["duration_s"].agreement == 1.0
    jumpy = agreement(files(MIN_DIM_FILES, run(duration_s=60.0), run(duration_s=20.0)))
    assert jumpy.dimensions["duration_s"].agreement == 0.0
