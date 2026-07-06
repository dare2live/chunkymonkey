"""calendar_builder 单测 (R1 件1, 2026-07-03).

锁: (1) raw_tushare_trade_cal → dim_trading_calendar 增量正确性 (watermark 语义, 只延伸不回填
前史); (2) compact→ISO 转换 + SSE/is_open=1 过滤; (3) raw 缺/空 fail loud。
日历 horizon 门 (前瞻余量 red-green) 2026-07-06 迁至
backend/tests/scripts/test_check_continuity_integrity.py (孤儿 data_quality.py 整体退役,
horizon 逻辑真正接进 check_continuity_integrity.py 的 calendar_horizon 检测类型)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import duck_mem
from services import calendar_builder as cb

_RAW_DDL = ("CREATE TABLE raw_tushare_trade_cal "
            "(exchange TEXT, cal_date TEXT, is_open BIGINT, pretrade_date TEXT)")
_DIM_DDL = ("CREATE TABLE dim_trading_calendar "
            "(trade_date VARCHAR NOT NULL, is_trading BIGINT, PRIMARY KEY(trade_date))")


def _conn(*, with_dim: bool = True):
    c = duck_mem()
    c.execute(_RAW_DDL)
    if with_dim:
        c.execute(_DIM_DDL)
    return c


def test_bootstrap_full_copy_iso_and_filters():
    """dim 空 → 全量拷贝 raw SSE is_open=1; compact→ISO; SZSE 行 / is_open=0 行不入。"""
    c = _conn()
    try:
        c.executemany("INSERT INTO raw_tushare_trade_cal VALUES (?, ?, ?, NULL)", [
            ("SSE", "20260702", 1), ("SSE", "20260703", 1),
            ("SSE", "20260704", 0),               # 非交易日不入 (只存交易日语义)
            ("SZSE", "20260702", 1),              # 非 SSE 不入 (统一上交所口径)
            ("SSE", "20260706", 1),
        ])
        out = cb.build_latest(conn=c)
        assert out["inserted"] == 3 and out["watermark_before"] is None
        rows = c.execute(
            "SELECT trade_date, is_trading FROM dim_trading_calendar ORDER BY trade_date").fetchall()
        assert [r[0] for r in rows] == ["2026-07-02", "2026-07-03", "2026-07-06"]  # ISO 转换
        assert all(r[1] == 1 for r in rows)
        assert out["dim_max"] == "2026-07-06" and out["raw_max_trading"] == "2026-07-06"
    finally:
        c.close()


def test_incremental_watermark_no_prehistory_backfill():
    """增量 = watermark > MAX(dim): 只延伸未来; raw 更早前史 (dim 起点之前) 不回填;
    重跑幂等 0 插入。"""
    c = _conn()
    try:
        c.executemany("INSERT INTO dim_trading_calendar VALUES (?, 1)",
                      [("2026-07-02",), ("2026-07-03",)])
        c.executemany("INSERT INTO raw_tushare_trade_cal VALUES (?, ?, ?, NULL)", [
            ("SSE", "19901219", 1),               # 前史: raw 有但 dim 契约起点后 → 不回填
            ("SSE", "20260702", 1), ("SSE", "20260703", 1),
            ("SSE", "20260706", 1), ("SSE", "20260707", 1),   # 新日: 延伸
        ])
        out = cb.build_latest(conn=c)
        assert out["inserted"] == 2 and out["watermark_before"] == "2026-07-03"
        assert out["dim_max"] == "2026-07-07" and out["dim_rows"] == 4
        assert c.execute(
            "SELECT COUNT(*) FROM dim_trading_calendar WHERE trade_date < '2026-07-02'"
        ).fetchone()[0] == 0, "watermark 语义: 前史不回填 (回填走人工 rebuild)"
        out2 = cb.build_latest(conn=c)   # 幂等重跑
        assert out2["inserted"] == 0 and out2["dim_rows"] == 4
    finally:
        c.close()


def test_raw_missing_or_empty_fails_loud():
    """raw 表缺 / 无 SSE 交易日行 → raise, 拒绝静默 no-op 假装刷新。"""
    c = duck_mem()
    c.execute(_DIM_DDL)
    try:
        # 表缺: _raw_rel 会尝试 ATTACH 生产 tushare_raw — 内存测试禁碰真库, 建空表代替
        c.execute(_RAW_DDL)
        with pytest.raises(RuntimeError, match="无 SSE is_open=1 行"):
            cb.build_latest(conn=c)
        c.execute("INSERT INTO raw_tushare_trade_cal VALUES ('SZSE', '20260702', 1, NULL)")
        with pytest.raises(RuntimeError):   # 只有非 SSE 行同样算空
            cb.build_latest(conn=c)
    finally:
        c.close()
