"""Phase ψ.γ.dict.2 — 字段字典 runtime enforcer 单测.

防回退:
- pk/pit-key NULL → reject
- enum 值不匹配 → reject
- sign positive/negative 违反 → reject
- outlier_cap 单/双边超限 → reject
- type 粗校 → reject
- 表不在字典 → reject (除非 skip_missing_table)
- 批量模式收集 violations 不 raise
"""
from __future__ import annotations

import math

import pytest

from services.data_governance import (
    DictionaryViolation,
    enforce_dictionary,
    enforce_dictionary_batch,
    get_field_dictionary,
    load_field_dictionary,
    reload_field_dictionary,
)
from services.data_governance.config import FieldSpec, TableSpec


# ━━━━━ Dictionary loading ━━━━━

def test_load_field_dictionary_smoke():
    """字典能加载, 含 market/smartmoney DB."""
    fd = load_field_dictionary()
    assert "market.duckdb.v_price_kline_qfq" in fd.tables
    assert "smartmoney.duckdb.fact_signal_context" in fd.tables


def test_lookup_short_name():
    fd = load_field_dictionary()
    spec = fd.lookup_table("fact_signal_context")
    assert spec is not None
    assert spec.name == "fact_signal_context"
    assert spec.db == "smartmoney.duckdb"


def test_lookup_full_name():
    fd = load_field_dictionary()
    spec = fd.lookup_table("smartmoney.duckdb.fact_signal_context")
    assert spec is not None


def test_lookup_missing_returns_none():
    fd = load_field_dictionary()
    assert fd.lookup_table("nonexistent_table_xyz") is None


def test_lookup_table_has_pit_key():
    fd = load_field_dictionary()
    spec = fd.lookup_table("fact_signal_context")
    assert spec.pit_key == "date"


# ━━━━━ enforce_dictionary — happy path ━━━━━

_VALID_SIGNAL_CONTEXT_ROW = {
    "date":           "2026-05-14",
    "stock_code":     "600036",
    "vol_r20":        0.5,
    "amt_r20":        0.6,
    "amount_20d_avg": 12345678.0,
    "price_pos_60d":  0.45,
    "price_pos_120d": 0.40,
    "drawdown_60d":   -0.15,
    "technical_stage": "2",
    "built_at":       "2026-05-14 10:00:00",
}


def test_enforce_valid_row_passes():
    enforce_dictionary(_VALID_SIGNAL_CONTEXT_ROW, "fact_signal_context")   # no raise


# ━━━━━ NOT NULL on pk/pit-key roles ━━━━━

def test_enforce_rejects_null_pit_key():
    row = dict(_VALID_SIGNAL_CONTEXT_ROW)
    row["date"] = None
    with pytest.raises(DictionaryViolation, match="date.*NULL"):
        enforce_dictionary(row, "fact_signal_context")


def test_enforce_rejects_nan_pit_key():
    row = dict(_VALID_SIGNAL_CONTEXT_ROW)
    row["date"] = float("nan")   # 假装是数值表里 NaN
    # date 字段类型是 DATE, NaN 触发"NULL violation" 而非 type 错
    with pytest.raises(DictionaryViolation):
        enforce_dictionary(row, "fact_signal_context")


# ━━━━━ Enum check ━━━━━

def test_enforce_rejects_invalid_enum():
    row = dict(_VALID_SIGNAL_CONTEXT_ROW)
    row["technical_stage"] = "99"   # 不在 ["1","1.5","2","3","4"]
    with pytest.raises(DictionaryViolation, match="enum"):
        enforce_dictionary(row, "fact_signal_context")


def test_enforce_accepts_valid_enum_choices():
    for stage in ("1", "1.5", "2", "3", "4"):
        row = dict(_VALID_SIGNAL_CONTEXT_ROW)
        row["technical_stage"] = stage
        enforce_dictionary(row, "fact_signal_context")   # no raise


# ━━━━━ Outlier cap ━━━━━

_VALID_STAGE_OPTIMAL_ROW = {
    "stock_code": "600036",
    "formula_id": "reversal_1m_deep",
    "formula_variant": "default",
    "stage_filter": "2",
    "optimal_hp": 15,
    "optimal_stop_pct": -0.08,
    "optimal_target_pct": 0.15,
    "optimal_trailing_pct": 0.03,
    "oos_sharpe": 1.2,
    "oos_win_rate": 0.55,
    "oos_avg_ret": 0.03,
    "oos_n_traded": 10,
    "sharpe": 1.5,
    "win_rate": 0.65,
}


def test_enforce_rejects_sign_negative_violation():
    """optimal_stop_pct: sign=negative — 正数应 reject."""
    row = dict(_VALID_STAGE_OPTIMAL_ROW)
    row["optimal_stop_pct"] = 0.08
    with pytest.raises(DictionaryViolation, match="sign=negative"):
        enforce_dictionary(row, "mart_per_stock_stage_strategy_optimal")


def test_enforce_rejects_sign_positive_violation():
    row = dict(_VALID_STAGE_OPTIMAL_ROW)
    row["optimal_target_pct"] = -0.15
    with pytest.raises(DictionaryViolation, match="sign=positive"):
        enforce_dictionary(row, "mart_per_stock_stage_strategy_optimal")


def test_enforce_rejects_enum_int_violation():
    """optimal_hp: enum [5,10,15,20,30,60,90]."""
    row = dict(_VALID_STAGE_OPTIMAL_ROW)
    row["optimal_hp"] = 7
    with pytest.raises(DictionaryViolation, match="enum"):
        enforce_dictionary(row, "mart_per_stock_stage_strategy_optimal")


# ━━━━━ Type check ━━━━━

def test_enforce_rejects_wrong_type_double_as_string():
    row = dict(_VALID_SIGNAL_CONTEXT_ROW)
    row["vol_r20"] = "not_a_number"
    with pytest.raises(DictionaryViolation, match="DOUBLE"):
        enforce_dictionary(row, "fact_signal_context")


def test_enforce_rejects_wrong_type_text_as_int():
    row = dict(_VALID_SIGNAL_CONTEXT_ROW)
    row["stock_code"] = 600036   # int not str
    # stock_code 在字典里 type TEXT
    with pytest.raises(DictionaryViolation, match="TEXT"):
        enforce_dictionary(row, "fact_signal_context")


def test_enforce_rejects_bool_not_integer():
    """bool 是 int 的 subclass 但应被识别为非 INTEGER."""
    row = dict(_VALID_STAGE_OPTIMAL_ROW)
    row["optimal_hp"] = True   # bool
    with pytest.raises(DictionaryViolation):
        enforce_dictionary(row, "mart_per_stock_stage_strategy_optimal")


# ━━━━━ Date format ━━━━━

def test_enforce_rejects_bad_date_format():
    row = dict(_VALID_SIGNAL_CONTEXT_ROW)
    row["date"] = "2026/05/14"   # 分隔符错
    with pytest.raises(DictionaryViolation, match="DATE"):
        enforce_dictionary(row, "fact_signal_context")


def test_enforce_accepts_datetime_date_object():
    import datetime
    row = dict(_VALID_SIGNAL_CONTEXT_ROW)
    row["date"] = datetime.date(2026, 5, 14)
    enforce_dictionary(row, "fact_signal_context")   # no raise


# ━━━━━ Missing table handling ━━━━━

def test_enforce_rejects_missing_table_by_default():
    with pytest.raises(DictionaryViolation, match="不在 field_dictionary"):
        enforce_dictionary({"x": 1}, "totally_fake_table_xyz")


def test_enforce_skip_missing_table_opt():
    out = enforce_dictionary({"x": 1}, "totally_fake_table_xyz", skip_missing_table=True)
    assert out == []


# ━━━━━ strict=False mode ━━━━━

def test_enforce_non_strict_returns_violations_list():
    row = dict(_VALID_SIGNAL_CONTEXT_ROW)
    row["date"] = None
    row["technical_stage"] = "99"
    violations = enforce_dictionary(
        row, "fact_signal_context", strict=False
    )
    assert len(violations) >= 2
    assert any("date" in v for v in violations)
    assert any("technical_stage" in v for v in violations)


# ━━━━━ Batch mode ━━━━━

def test_batch_mode_collects_violations_no_raise():
    rows = [
        dict(_VALID_SIGNAL_CONTEXT_ROW),
        {**_VALID_SIGNAL_CONTEXT_ROW, "date": None},   # bad
        {**_VALID_SIGNAL_CONTEXT_ROW, "technical_stage": "99"},  # bad
        dict(_VALID_SIGNAL_CONTEXT_ROW),
    ]
    result = enforce_dictionary_batch(rows, "fact_signal_context")
    assert result["total"] == 4
    assert result["passed"] == 2
    assert result["failed"] == 2
    assert {r["row_idx"] for r in result["failed_rows"]} == {1, 2}


def test_batch_mode_strict_per_row_raises_on_first_bad():
    rows = [
        dict(_VALID_SIGNAL_CONTEXT_ROW),
        {**_VALID_SIGNAL_CONTEXT_ROW, "date": None},
    ]
    with pytest.raises(DictionaryViolation):
        enforce_dictionary_batch(rows, "fact_signal_context", strict_per_row=True)


# ━━━━━ Singleton + reload ━━━━━

def test_get_field_dictionary_singleton():
    a = get_field_dictionary()
    b = get_field_dictionary()
    assert a is b


def test_reload_field_dictionary_returns_fresh():
    a = get_field_dictionary()
    b = reload_field_dictionary()
    # 内容应等价但 identity 不同 (deep load 后单例 reset)
    assert a.tables.keys() == b.tables.keys()
