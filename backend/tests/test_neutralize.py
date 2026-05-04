import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.neutralize import (  # noqa: E402
    neutralize,
    neutralize_by_group,
    neutralize_by_quintile,
    standardize,
)


def test_neutralize_by_group_returns_keyed_demeaned_scores():
    result = neutralize_by_group(
        {"000001": 1.0, "000002": 3.0, "000003": 10.0},
        {"000001": "bank", "000002": "bank", "000003": "tech"},
    )

    assert result == {
        "000001": -1.0,
        "000002": 1.0,
        "000003": 0.0,
    }


def test_neutralize_by_quintile_supports_positional_inputs():
    result = neutralize_by_quintile(
        [1.0, 3.0, 10.0, 14.0],
        [100.0, 200.0, 1000.0, 1200.0],
        n_bins=2,
    )

    assert result == [-1.0, 1.0, -2.0, 2.0]


def test_neutralize_applies_group_then_market_cap():
    result = neutralize(
        {"000001": 1.0, "000002": 3.0, "000003": 10.0, "000004": 14.0},
        industry={"000001": "bank", "000002": "bank", "000003": "tech", "000004": "tech"},
        market_cap={"000001": 100.0, "000002": 200.0, "000003": 1000.0, "000004": 1200.0},
        market_cap_bins=2,
    )

    assert result == {
        "000001": -1.0,
        "000002": 1.0,
        "000003": -2.0,
        "000004": 2.0,
    }


def test_standardize_uses_sample_standard_deviation_and_preserves_missing_values():
    result = standardize({"a": 1.0, "b": 2.0, "c": 3.0, "d": None})

    assert result["a"] == pytest.approx(-1.0)
    assert result["b"] == pytest.approx(0.0)
    assert result["c"] == pytest.approx(1.0)
    assert result["d"] is None
    assert math.isclose(sum(value for value in result.values() if value is not None), 0.0)
