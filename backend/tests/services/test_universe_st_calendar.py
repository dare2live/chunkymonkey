"""load_st_calendar 换源回归: canonical_stock_st_daily 取代冻结的 raw_tushare_stock_st.

背景: raw_tushare_stock_st 冻结在 20260716 后不再更新, 是主升浪 GT 正负样本标注
(rally_gt.py) 唯一的 ST 判据来源。calendar_identity_recon.py 已把它列入
BANNED_ST_BASELINE 并声明 ACCEPTED_ST_TABLE = canonical_stock_st_daily, 但
load_st_calendar 此前一直硬编码读旧表。本文件锁定换源后的行为, 防止回退。
"""
from __future__ import annotations

import pytest

from conftest import duck_mem

from services.universe import UniverseDataError, load_st_calendar


def test_reads_canonical_table_not_raw() -> None:
    """只建 canonical_stock_st_daily (不建 raw 表), 函数必须正常返回而非因表不存在报错."""

    conn = duck_mem()
    conn.execute(
        """
        CREATE TABLE canonical_stock_st_daily (
            trade_date DATE,
            ts_code VARCHAR
        )
        """
    )
    conn.execute(
        "INSERT INTO canonical_stock_st_daily VALUES (DATE '2026-08-28', '000010.SZ')"
    )

    cal = load_st_calendar(conn)

    assert cal == {"000010": {"20260828"}}


def test_date_column_compacted_to_8_digit_string() -> None:
    """canonical 的 trade_date 是 DATE 类型, 必须用 strftime 转紧凑 8 位, 不依赖隐式转换."""

    conn = duck_mem()
    conn.execute(
        """
        CREATE TABLE canonical_stock_st_daily (
            trade_date DATE,
            ts_code VARCHAR
        )
        """
    )
    conn.execute(
        "INSERT INTO canonical_stock_st_daily VALUES (DATE '2026-08-28', '600000.SH')"
    )

    cal = load_st_calendar(conn)

    dates = cal["600000"]
    assert dates == {"20260828"}
    assert next(iter(dates)) == "20260828"
    assert "-" not in next(iter(dates))


def test_code_derived_from_ts_code_prefix() -> None:
    """code 取 ts_code 前 6 位 (如 '000010.SZ' -> '000010'), 返回结构是 {6位code: set[str]}."""

    conn = duck_mem()
    conn.execute(
        """
        CREATE TABLE canonical_stock_st_daily (
            trade_date DATE,
            ts_code VARCHAR
        )
        """
    )
    conn.execute(
        "INSERT INTO canonical_stock_st_daily VALUES (DATE '2026-08-28', '000010.SZ')"
    )

    cal = load_st_calendar(conn)

    assert set(cal.keys()) == {"000010"}
    assert all(len(code) == 6 for code in cal)


def test_missing_table_raises_universe_data_error_naming_canonical() -> None:
    """表不存在时抛 UniverseDataError, 消息里必须是新表名 canonical_stock_st_daily."""

    conn = duck_mem()

    with pytest.raises(UniverseDataError) as exc_info:
        load_st_calendar(conn)

    assert "canonical_stock_st_daily" in str(exc_info.value)
    assert "raw_tushare_stock_st" not in str(exc_info.value)


def test_multiple_rows_same_code_aggregate_into_one_set() -> None:
    """同一只票多个 ST 日期必须聚合进同一个 set, 而非互相覆盖."""

    conn = duck_mem()
    conn.execute(
        """
        CREATE TABLE canonical_stock_st_daily (
            trade_date DATE,
            ts_code VARCHAR
        )
        """
    )
    conn.executemany(
        "INSERT INTO canonical_stock_st_daily VALUES (?, ?)",
        [
            ("2026-01-05", "000010.SZ"),
            ("2026-01-06", "000010.SZ"),
            ("2026-02-10", "000010.SZ"),
            ("2026-01-05", "600000.SH"),
        ],
    )

    cal = load_st_calendar(conn)

    assert cal["000010"] == {"20260105", "20260106", "20260210"}
    assert cal["600000"] == {"20260105"}
