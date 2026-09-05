from decimal import Decimal

from cs.core.metrics import derived_metrics
from cs.insights.patterns import metrics_from_sums


def test_derived_metrics_formula_and_nulls():
    assert derived_metrics(Decimal("100"), 10000, 200, Decimal("10"), Decimal("500")) == {
        "ctr": 0.02,
        "cpa": 10.0,
        "roas": 5.0,
    }
    assert derived_metrics(Decimal("0"), 0, 0, Decimal("0"), Decimal("0")) == {
        "ctr": None,
        "cpa": None,
        "roas": None,
    }


def test_insights_reexports_the_same_function():
    assert metrics_from_sums is derived_metrics
