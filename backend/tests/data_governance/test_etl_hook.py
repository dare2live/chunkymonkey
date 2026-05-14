"""Phase ψ.γ.dict.2 — ETL hook 单测."""
from __future__ import annotations

import pytest

from services.data_governance import (
    DictionaryViolation,
    validate_rows_before_insert,
)


_RISK_FACTORS_COLS = [
    "stock_code", "calc_date",
    "vol_30d", "vol_60d", "vol_120d",
    "max_dd_60d", "max_dd_120d",
    "sharpe_30d", "sharpe_60d",
    "skew_60d", "kurt_60d",
    "mom_30d", "mom_120d",
    "n_bars",
]


def _make_valid_row(date="2026-05-14", code="600036"):
    return (
        code, date,
        0.30,    # vol_30d
        0.28,    # vol_60d
        0.32,    # vol_120d
        -0.05,   # max_dd_60d
        -0.10,   # max_dd_120d
        0.5,     # sharpe_30d
        0.6,     # sharpe_60d
        -0.1,    # skew_60d
        3.5,     # kurt_60d
        0.02,    # mom_30d
        0.10,    # mom_120d
        252,     # n_bars
    )


def test_empty_rows_skipped():
    result = validate_rows_before_insert([], _RISK_FACTORS_COLS, "fact_risk_factors")
    assert result["total"] == 0


def test_valid_rows_pass():
    rows = [_make_valid_row(date=f"2026-05-{d:02d}") for d in range(1, 11)]
    result = validate_rows_before_insert(
        rows, _RISK_FACTORS_COLS, "fact_risk_factors"
    )
    assert result["total"] == 10
    assert result["failed"] == 0
    assert result["rate"] == 0.0


def test_violation_below_threshold_passes():
    """violation rate < max_violation_rate → no raise, return result."""
    good = [_make_valid_row(date=f"2026-05-{d:02d}") for d in range(1, 11)]
    # 1 row bad (1/11 ≈ 9% — but with high max_violation_rate=0.20, 应允许)
    bad = list(_make_valid_row())
    bad[0] = None   # stock_code NULL (pk)
    rows = good + [tuple(bad)]
    result = validate_rows_before_insert(
        rows, _RISK_FACTORS_COLS, "fact_risk_factors",
        max_violation_rate=0.20,
    )
    assert result["failed"] == 1
    assert result["passed"] == 10
    assert 0.05 < result["rate"] < 0.15


def test_violation_above_threshold_raises():
    """violation rate > max_violation_rate → raise RuntimeError."""
    bad_row = list(_make_valid_row())
    bad_row[0] = None
    rows = [tuple(bad_row), _make_valid_row()]  # 50% bad
    with pytest.raises(RuntimeError, match="违反率"):
        validate_rows_before_insert(
            rows, _RISK_FACTORS_COLS, "fact_risk_factors",
            max_violation_rate=0.01,
        )


def test_outlier_cap_vol_60d():
    """vol_60d 字典 outlier_cap=2.0 → 4.0 应 reject."""
    bad = list(_make_valid_row())
    bad[3] = 4.0   # vol_60d
    rows = [tuple(bad)]
    with pytest.raises(RuntimeError, match="违反率"):
        validate_rows_before_insert(
            rows, _RISK_FACTORS_COLS, "fact_risk_factors",
            max_violation_rate=0.0,    # 任何违反就 raise
        )


def test_missing_table_raises_by_default():
    """skip_missing_table=False (默认), 表不在字典 raise."""
    with pytest.raises(DictionaryViolation):
        validate_rows_before_insert(
            [(1, 2, 3)], ["a", "b", "c"], "totally_fake_xyz",
        )


def test_skip_missing_table_opt():
    """skip_missing_table=True → pass."""
    result = validate_rows_before_insert(
        [(1, 2, 3)], ["a", "b", "c"], "totally_fake_xyz",
        skip_missing_table=True,
    )
    assert result["total"] == 1
    assert result["failed"] == 0


def test_partial_columns_ok_for_subset():
    """字典只覆盖部分字段, columns 多出来 → 多余字段 zip 进 dict 但字典 lookup 忽略."""
    # fact_risk_factors 字典里 sharpe_30d / n_bars 未列, 也不应 reject
    rows = [_make_valid_row()]
    result = validate_rows_before_insert(
        rows, _RISK_FACTORS_COLS, "fact_risk_factors"
    )
    assert result["failed"] == 0
