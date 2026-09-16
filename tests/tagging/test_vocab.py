import pytest

from cs.tagging.vocab import normalize


@pytest.mark.parametrize(
    "raw, slug",
    [
        ("Pharmacist Warning", "pharmacist_warning"),
        ("pharmacist-warning", "pharmacist_warning"),
        ("  Pharmacy--Coat  ", "pharmacy_coat"),
        ("Benadryl users!", "benadryl_users"),
        ("___", ""),
        ("x" * 60, "x" * 48),
        ("a" * 47 + "_b", "a" * 47),
    ],
)
def test_normalize(raw, slug):
    assert normalize(raw) == slug
