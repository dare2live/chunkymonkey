"""
market_db.py — 独立行情数据库 (market.duckdb)

职责：K 线存储、同步状态、导入批次管理。
与业务库 smartmoney.duckdb 完全解耦，业务层只通过本模块读写行情数据。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from services.data_processing_monitor import record_data_processing_tool_run
from services.duck_adapter import connect as _duck_connect, DuckConn
from services.kline_source import KLINE_VALUE_EPSILON, clean_price_rows
from services.market_read import (
    CANONICAL_KLINE_QFQ_RELATION,
    CANONICAL_KLINE_QFQ_VIEW_DDL,
    DEFAULT_KLINE_DAILY_QFQ_COLUMNS,
    KLINE_DAILY_QFQ_POLICY,
    PRICE_KLINE_TDXHUB_DDL,
    canonical_kline_daily_qfq_sql,
    get_all_sync_states,
    get_all_xdxr_sync_states,
    get_canonical_kline_qfq_relation,
    get_kline,
    get_kline_range,
    get_xdxr_events,
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
# Write Functions
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # Phase ψ.5 allowlist: INSERT timestamp helper


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
    output_table: str = "price_kline_tdxhub",
    batch_id: str = None,
    raise_on_miss: bool = True,
    max_date_override: str | None = None,
) -> list[dict]:
    """Filter rows by latest_completed_trade_date (write-side PIT lint).

    Shared helper (Codex review 2026-05-19 CRITICAL): 下沉到共享函数, 让所有 K-line writer
    (write_batch in build_price_kline_tdxhub and upsert_price_kline_tdxhub_rows
    via _clean_kline_rows_for_write) 都走同一 lint.

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


def _clean_kline_rows_for_write(
    conn,
    rows: list[dict],
    *,
    source: str,
    output_table: str,
    batch_id: str = None,
    max_date_override: str | None = None,
) -> list[dict]:
    cleaned_rows, stats = clean_price_rows(
        rows,
        source=source,
        require_volume_amount=True,
        tool_name=f"{output_table}_write_cleaner",
    )
    if stats.rejected_rows:
        record_data_processing_tool_run(
            conn,
            stats=stats,
            run_id=f"{stats.tool_name}_{batch_id or 'adhoc'}_{uuid4().hex[:12]}",
            input_table="source_payload",
            output_table=output_table,
            batch_id=batch_id,
            metadata={
                "epsilon": KLINE_VALUE_EPSILON,
                "contract": "finite_positive_ohlcv_amount",
            },
        )
    # CLAUDE.md Rule 3 反例 lint: reject future dates (tdxhub server 盘中可能返回当日 partial K-line).
    # Codex review 2026-05-19 CRITICAL: 下沉到 filter_kline_rows_by_calendar 共享 helper.
    cleaned_rows = filter_kline_rows_by_calendar(
        cleaned_rows, output_table=output_table, batch_id=batch_id,
        max_date_override=max_date_override,
    )
    return cleaned_rows


def upsert_price_rows(conn, rows: list[dict], source: str,
                       batch_id: str = None) -> int:
    """
    批量写入/更新 K 线数据。
    rows: [{code, date, freq, adjust, open, high, low, close, volume, amount}]
    返回实际写入行数。

    governance v1 (configs/data_governance.yaml): price_kline 主表 retired
    except hs300_benchmark_allowlist. 非 allowlist source 写入直接 raise.
    """
    if not rows:
        return 0
    # from yaml: configs/data_governance.yaml schema_contracts.price_kline.allowed_sources
    PRICE_KLINE_ALLOWED_SOURCES = {"akshare_csindex_hs300"}
    if source not in PRICE_KLINE_ALLOWED_SOURCES:
        raise ValueError(
            f"governance v1 reject: source={source!r} not in price_kline.allowed_sources "
            f"{sorted(PRICE_KLINE_ALLOWED_SOURCES)}. price_kline 主表 retired, "
            f"stock K-line 走 upsert_price_kline_tdxhub_rows (price_kline_tdxhub)."
        )
    rows = _clean_kline_rows_for_write(
        conn,
        rows,
        source=source,
        output_table="price_kline",
        batch_id=batch_id,
    )
    if not rows:
        return 0
    now = _now_iso()
    conn.executemany(
        "INSERT OR REPLACE INTO price_kline "
        "(code, date, freq, adjust, open, high, low, close, volume, amount, "
        " source, batch_id, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                r["code"], r["date"], r.get("freq", "daily"),
                r.get("adjust", "qfq"),
                r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                r.get("volume"), r.get("amount"),
                source, batch_id, now,
            )
            for r in rows
        ],
    )
    conn.commit()
    return len(rows)


def upsert_price_kline_tdxhub_rows(conn, rows: list[dict],
                                   source: str = "tdxhub",
                                   batch_id: str = None) -> int:
    """
    批量写入/更新 tdxhub 主 K 线表。

    rows: [{code, date, freq, adjust, open, high, low, close, volume, amount, factor?}]
    返回实际写入行数。
    """
    if not rows:
        return 0
    rows = _clean_kline_rows_for_write(
        conn,
        rows,
        source=source or "tdxhub",
        output_table="price_kline_tdxhub",
        batch_id=batch_id,
    )
    if not rows:
        return 0
    now = _now_iso()
    conn.executemany(
        """
        DELETE FROM price_kline_tdxhub
         WHERE code = ? AND date = ? AND freq = ? AND adjust = ?
        """,
        [
            (
                r["code"],
                r["date"],
                r.get("freq", "daily"),
                r.get("adjust", "qfq"),
            )
            for r in rows
        ],
    )
    conn.executemany(
        "INSERT INTO price_kline_tdxhub "
        "(code, date, freq, adjust, open, high, low, close, volume, amount, "
        " factor, source, batch_id, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                r["code"], r["date"], r.get("freq", "daily"),
                r.get("adjust", "qfq"),
                r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                r.get("volume"), r.get("amount"), r.get("factor"),
                source or "tdxhub", batch_id, now,
            )
            for r in rows
        ],
    )
    conn.commit()
    return len(rows)


def replace_xdxr_rows(conn, code: str, rows: list[dict], source: str,
                      batch_id: str = None) -> int:
    """按股票全量替换 xdxr 事件，保持单一真相源。"""
    now = _now_iso()
    conn.execute("DELETE FROM price_xdxr WHERE code=?", (code,))
    if rows:
        conn.executemany(
            "INSERT INTO price_xdxr "
            "(code, date, category, name, fenhong, peigujia, songzhuangu, "
            " peigu, suogu, panqianliutong, panhouliutong, qianzongguben, "
            " houzongguben, fenshu, xingquanjia, source, batch_id, ingested_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    code,
                    row["date"],
                    row["category"],
                    row.get("name"),
                    row.get("fenhong"),
                    row.get("peigujia"),
                    row.get("songzhuangu"),
                    row.get("peigu"),
                    row.get("suogu"),
                    row.get("panqianliutong"),
                    row.get("panhouliutong"),
                    row.get("qianzongguben"),
                    row.get("houzongguben"),
                    row.get("fenshu"),
                    row.get("xingquanjia"),
                    source,
                    batch_id,
                    now,
                )
                for row in rows
            ],
        )
    conn.commit()
    return len(rows)


def update_sync_state(conn, code: str, freq: str, *,
                       source: str = None,
                       min_date: str = None,
                       max_date: str = None,
                       row_count: int = None,
                       error: str = None):
    """更新同步状态（UPSERT 语义）"""
    now = _now_iso()
    conn.execute(
        "INSERT INTO market_sync_state "
        "(dataset, code, freq, adjust, source, min_date, max_date, "
        " row_count, last_success_at, last_attempt_at, last_error) "
        "VALUES ('price_kline',?,?,'qfq',?,?,?,?,?,?,?) "
        "ON CONFLICT(dataset, code, freq, adjust) DO UPDATE SET "
        " source=COALESCE(excluded.source, source), "
        " min_date=COALESCE(excluded.min_date, min_date), "
        " max_date=COALESCE(excluded.max_date, max_date), "
        " row_count=COALESCE(excluded.row_count, row_count), "
        " last_success_at=CASE WHEN excluded.last_error IS NULL "
        "   THEN excluded.last_success_at ELSE last_success_at END, "
        " last_attempt_at=excluded.last_attempt_at, "
        " last_error=excluded.last_error",
        (
            code, freq, source, min_date, max_date, row_count,
            now if error is None else None,  # last_success_at
            now,                              # last_attempt_at
            error,                            # last_error
        ),
    )
    conn.commit()


def update_xdxr_sync_state(conn, code: str, *,
                           source: str = None,
                           min_date: str = None,
                           max_date: str = None,
                           row_count: int = None,
                           error: str = None):
    """更新 xdxr 同步状态（UPSERT 语义）。"""
    now = _now_iso()
    conn.execute(
        "INSERT INTO market_sync_state "
        "(dataset, code, freq, adjust, source, min_date, max_date, "
        " row_count, last_success_at, last_attempt_at, last_error) "
        "VALUES ('price_xdxr',?,'event','none',?,?,?,?,?,?,?) "
        "ON CONFLICT(dataset, code, freq, adjust) DO UPDATE SET "
        " source=COALESCE(excluded.source, source), "
        " min_date=COALESCE(excluded.min_date, min_date), "
        " max_date=COALESCE(excluded.max_date, max_date), "
        " row_count=COALESCE(excluded.row_count, row_count), "
        " last_success_at=CASE WHEN excluded.last_error IS NULL "
        "   THEN excluded.last_success_at ELSE last_success_at END, "
        " last_attempt_at=excluded.last_attempt_at, "
        " last_error=excluded.last_error",
        (
            code,
            source,
            min_date,
            max_date,
            row_count,
            now if error is None else None,
            now,
            error,
        ),
    )
    conn.commit()


def start_import_batch(conn, source_type: str, source_name: str,
                        freq: str, adjust: str = "qfq") -> str:
    """创建导入批次，返回 batch_id"""
    now = _now_iso()
    batch_id = f"{source_type}_{now.replace(' ', '_').replace(':', '')}"
    conn.execute(
        "INSERT INTO price_import_batch "
        "(batch_id, source_type, source_name, freq, adjust, started_at, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (batch_id, source_type, source_name, freq, adjust, now, "running"),
    )
    conn.commit()
    return batch_id


def finish_import_batch(conn, batch_id: str, *,
                         rows_imported: int = 0,
                         min_date: str = None,
                         max_date: str = None,
                         status: str = "completed",
                         error: str = None,
                         detail: str = None):
    """完成导入批次"""
    now = _now_iso()
    conn.execute(
        "UPDATE price_import_batch SET "
        " rows_imported=?, min_date=?, max_date=?, "
        " finished_at=?, status=?, error=?, detail=? "
        "WHERE batch_id=?",
        (rows_imported, min_date, max_date, now, status, error, detail,
         batch_id),
    )
    conn.commit()
