"""
market_db.py — 独立行情数据库 (market.duckdb)

职责：K 线存储、同步状态、导入批次管理。
与业务库 smartmoney.duckdb 完全解耦，业务层只通过本模块读写行情数据。
"""

from __future__ import annotations

from pathlib import Path

from services.duck_adapter import connect as _duck_connect, DuckConn
from services.market_read import (
    ANALYSIS_KLINE_QFQ_RELATION,
    ANALYSIS_KLINE_QFQ_VIEW_DDL,
    DEFAULT_KLINE_DAILY_QFQ_COLUMNS,
    KLINE_DAILY_QFQ_POLICY,
    analysis_kline_daily_qfq_sql,
    get_analysis_kline_qfq_relation,
)
from services.market_schema import ensure_market_schema

_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
# Phase 7: DuckDB 主库
_DB_PATH = _DB_DIR / "market.duckdb"


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_market_conn(timeout: int = 30) -> DuckConn:
    """获取 market DB 连接 (DuckDB via duck_adapter)."""
    return _duck_connect(str(_DB_PATH), timeout=timeout)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_market_db():
    """创建行情数据库表结构（仅建表，不做迁移）"""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_market_conn()
    try:
        ensure_market_schema(conn)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Write-side calendar lint (PIT 盘中污染防御, 通用 K-line writer helper)
# (旧写管线函数批3a 已删, 详 ledger; 保留 filter_kline_rows_by_calendar = 通用写侧日历 lint)
# ---------------------------------------------------------------------------

# Codex review 2026-05-19 a7ffbdb2 HIGH 1: calendar gate 统一到 services/calendar.py.
# market_db 不再 own K-line write calendar policy, 改 import shim 保留 KlineWriteLintError 别名
# (backward compat for callers + tests).
from services.calendar import (  # noqa: E402, F401
    CalendarMissError as KlineWriteLintError,
    latest_completed_for_kline_write as _latest_completed_trade_date_for_write,
)


def filter_kline_rows_by_calendar(
    rows: list[dict],
    *,
    output_table: str = "kline",
    batch_id: str = None,
    raise_on_miss: bool = True,
    max_date_override: str | None = None,
) -> list[dict]:
    """Filter rows by latest_completed_trade_date (write-side PIT lint).

    通用 K-line writer 写侧日历 lint (Codex review 2026-05-19 CRITICAL): 任何 K线写入前
    剔除 date > latest_completed_trade_date 的盘中 partial 行 —— 盘中未收盘的当日行不是
    完成交易日, 计入会让下游把半天数据当整天 (边界由 services.calendar 定, 不在此处硬编码)。

    max_date_override: batch sync 启动时锁定的 cutoff, 避免跨 15:05 阈值导致同批次不一致.

    rule-compliance: ok evidence=shared-defense-PIT-lint
    """
    if not rows:
        return rows
    last_closed = max_date_override or _latest_completed_trade_date_for_write(raise_on_miss=raise_on_miss)
    if last_closed is None:
        return rows  # bypass only if KLINE_WRITE_LINT_BYPASS=1 (raise_on_miss=False)
    before = len(rows)
    filtered = [r for r in rows if str(r.get("date", ""))[:10] <= last_closed]
    rejected = before - len(filtered)
    if rejected > 0:
        import logging
        logging.getLogger(__name__).warning(
            "kline write lint: rejected %d rows with date > %s (盘中污染防御, output_table=%s, batch_id=%s)",
            rejected, last_closed, output_table, batch_id,
        )
    return filtered
