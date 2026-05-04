import pytest

from scripts.run_daily_topk import (
    _percentile_linear,
    _rank_percentiles,
    _top_by_regime,
)


def test_rank_percentiles_matches_average_tie_semantics():
    assert _rank_percentiles([0.2, 0.4, 0.4, 0.1]) == pytest.approx([
        0.5,
        0.875,
        0.875,
        0.25,
    ])


def test_percentile_linear_interpolates_without_numpy():
    assert _percentile_linear([10, 20, 30, 40], 25) == pytest.approx(17.5)
    assert _percentile_linear([10, 20, 30, 40], 50) == pytest.approx(25.0)


def test_top_by_regime_keeps_each_group_limit_in_score_order():
    rows = [
        {"stock_code": "000001", "regime_flag": "up"},
        {"stock_code": "000002", "regime_flag": "down"},
        {"stock_code": "000003", "regime_flag": "up"},
        {"stock_code": "000004", "regime_flag": "up"},
        {"stock_code": "000005", "regime_flag": "down"},
    ]

    selected = _top_by_regime(rows, 2)

    assert [row["stock_code"] for row in selected] == [
        "000001",
        "000002",
        "000003",
        "000005",
    ]
