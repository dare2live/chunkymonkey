"""
market_db.py — 独立行情数据库 (market.duckdb)

职责：K 线存储、同步状态、导入批次管理。
与业务库 smartmoney.duckdb 完全解耦，业务层只通过本模块读写行情数据。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from uuid import uuid4

from services.data_processing_monitor import record_data_processing_tool_run
from services.duck_adapter import connect as _duck_connect, DuckConn
from services.kline_source import KLINE_VALUE_EPSILON, clean_price_rows
from services.source_policy import get_capability_policy

_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
# Phase 7: DuckDB 主库
_DB_PATH = _DB_DIR / "market.duckdb"
KLINE_DAILY_QFQ_POLICY = get_capability_policy("kline_daily")
CANONICAL_KLINE_QFQ_RELATION = KLINE_DAILY_QFQ_POLICY.canonical_relation or "market.v_price_kline_qfq"
DEFAULT_KLINE_DAILY_QFQ_COLUMNS = (
    "code", "date", "open", "high", "low", "close", "volume", "amount", "factor",
)


def get_canonical_kline_qfq_relation(schema: Optional[str] = None) -> str:
    """Resolve the canonical daily qfq K-line relation for a connection.

    Cross-database analytical jobs attach `market.duckdb` as `market` and use
    `market.v_price_kline_qfq`. Direct market connections use
    `v_price_kline_qfq`.
    """
    name = CANONICAL_KLINE_QFQ_RELATION.rsplit(".", 1)[-1]
    return f"{schema}.{name}" if schema else name


def canonical_kline_daily_qfq_sql(
    *,
    relation: str | None = None,
    columns: Iterable[str] = DEFAULT_KLINE_DAILY_QFQ_COLUMNS,
    include_source_lineage: bool = False,
) -> str:
    """Return the canonical daily qfq K-line SELECT used by analytical jobs."""
    relation = relation or CANONICAL_KLINE_QFQ_RELATION
    allowed = {
        "code", "date", "open", "high", "low", "close", "volume", "amount", "factor",
        "freq", "adjust",
    }
    selected = []
    for column in columns:
        if column not in allowed:
            raise ValueError(f"unsupported canonical kline column: {column}")
        selected.append(column)
    if include_source_lineage:
        selected.extend([
            "COALESCE(source_name, 'unknown') AS source_name",
            "COALESCE(source_tier, 99)::SMALLINT AS source_tier",
            "COALESCE(is_fallback, FALSE) AS is_fallback",
        ])
    select_sql = ", ".join(selected)
    return (
        f"SELECT {select_sql}\n"
        f"FROM {relation}\n"
        "WHERE freq='daily' AND adjust='qfq'"
    )


PRICE_KLINE_TDXHUB_DDL = """
CREATE TABLE IF NOT EXISTS price_kline_tdxhub (
    code          TEXT NOT NULL,
    date          TEXT NOT NULL,
    freq          TEXT NOT NULL DEFAULT 'daily',
    adjust        TEXT NOT NULL DEFAULT 'qfq',
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    volume        REAL,
    amount        REAL,
    factor        REAL,
    source        TEXT DEFAULT 'tdxhub',
    batch_id      TEXT,
    ingested_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, date, freq, adjust)
);
CREATE INDEX IF NOT EXISTS idx_pkt_code ON price_kline_tdxhub(code);
CREATE INDEX IF NOT EXISTS idx_pkt_date ON price_kline_tdxhub(date);

CREATE TABLE IF NOT EXISTS price_kline_tdxhub_adjustment_event (
    code          TEXT NOT NULL,
    event_date    TEXT NOT NULL,
    event_hash    TEXT NOT NULL,
    adjust_factor REAL NOT NULL,
    prev_close    REAL,
    source        TEXT,
    batch_id      TEXT,
    applied_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (code, event_date, event_hash)
);
CREATE INDEX IF NOT EXISTS idx_pkt_adj_code_date
    ON price_kline_tdxhub_adjustment_event(code, event_date);
"""

CANONICAL_KLINE_QFQ_VIEW_DDL = """
CREATE OR REPLACE VIEW v_price_kline_qfq AS
WITH primary_rows AS (
    SELECT
        code,
        date,
        freq,
        adjust,
        open,
        high,
        low,
        close,
        volume,
        amount,
        COALESCE(factor, 1.0) AS factor,
        COALESCE(NULLIF(source, ''), 'tdxhub') AS source_name,
        1::SMALLINT AS source_tier,
        FALSE AS is_fallback,
        batch_id,
        ingested_at
    FROM price_kline_tdxhub
    WHERE freq = 'daily' AND adjust = 'qfq'
      AND open IS NOT NULL AND open > 0
      AND high IS NOT NULL AND high > 0
      AND low IS NOT NULL AND low > 0
      AND close IS NOT NULL AND close > 0
      AND volume IS NOT NULL AND volume >= 1e-6
      AND amount IS NOT NULL AND amount >= 1e-6
      AND high >= open AND high >= close AND high >= low
      AND low <= open AND low <= close AND low <= high
),
fallback_rows AS (
    SELECT
        f.code,
        f.date,
        f.freq,
        f.adjust,
        f.open,
        f.high,
        f.low,
        f.close,
        f.volume,
        f.amount,
        1.0 AS factor,
        COALESCE(NULLIF(f.source, ''), 'akshare_multi_source') AS source_name,
        3::SMALLINT AS source_tier,
        TRUE AS is_fallback,
        f.batch_id,
        f.ingested_at
    FROM price_kline f
    WHERE f.freq = 'daily'
      AND f.adjust = 'qfq'
      AND f.open IS NOT NULL AND f.open > 0
      AND f.high IS NOT NULL AND f.high > 0
      AND f.low IS NOT NULL AND f.low > 0
      AND f.close IS NOT NULL AND f.close > 0
      AND f.volume IS NOT NULL AND f.volume >= 1e-6
      AND f.amount IS NOT NULL AND f.amount >= 1e-6
      AND f.high >= f.open AND f.high >= f.close AND f.high >= f.low
      AND f.low <= f.open AND f.low <= f.close AND f.low <= f.high
      AND NOT EXISTS (
          SELECT 1
          FROM primary_rows p
          WHERE p.code = f.code
            AND p.date = f.date
            AND p.freq = f.freq
            AND p.adjust = f.adjust
      )
)
SELECT * FROM primary_rows
UNION ALL
SELECT * FROM fallback_rows
"""

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
        conn.executescript("""
        -- K 线数据主表
        CREATE TABLE IF NOT EXISTS price_kline (
            code        TEXT    NOT NULL,
            date        TEXT    NOT NULL,
            freq        TEXT    NOT NULL DEFAULT 'daily',
            adjust      TEXT    NOT NULL DEFAULT 'qfq',
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      REAL,
            amount      REAL,
            source      TEXT,
            batch_id    TEXT,
            ingested_at TEXT,
            PRIMARY KEY (code, date, freq, adjust)
        );
        CREATE INDEX IF NOT EXISTS idx_pk_code_freq
            ON price_kline(code, freq);
        CREATE INDEX IF NOT EXISTS idx_pk_date
            ON price_kline(date);

        -- 除权除息 / 股本变动事件（TDX xdxr）
        CREATE TABLE IF NOT EXISTS price_xdxr (
            code            TEXT NOT NULL,
            date            TEXT NOT NULL,
            category        INTEGER NOT NULL,
            name            TEXT,
            fenhong         REAL,
            peigujia        REAL,
            songzhuangu     REAL,
            peigu           REAL,
            suogu           REAL,
            panqianliutong  REAL,
            panhouliutong   REAL,
            qianzongguben   REAL,
            houzongguben    REAL,
            fenshu          REAL,
            xingquanjia     REAL,
            source          TEXT,
            batch_id        TEXT,
            ingested_at     TEXT,
            PRIMARY KEY (code, date, category)
        );
        CREATE INDEX IF NOT EXISTS idx_xdxr_code_date
            ON price_xdxr(code, date);

        -- 同步状态表（覆盖状态交给审计层推导，不在此表堆字段）
        CREATE TABLE IF NOT EXISTS market_sync_state (
            dataset         TEXT NOT NULL DEFAULT 'price_kline',
            code            TEXT NOT NULL,
            freq            TEXT NOT NULL DEFAULT 'daily',
            adjust          TEXT NOT NULL DEFAULT 'qfq',
            source          TEXT,
            min_date        TEXT,
            max_date        TEXT,
            row_count       INTEGER DEFAULT 0,
            last_success_at TEXT,
            last_attempt_at TEXT,
            last_error      TEXT,
            PRIMARY KEY (dataset, code, freq, adjust)
        );

        -- 导入批次记录
        CREATE TABLE IF NOT EXISTS price_import_batch (
            batch_id        TEXT PRIMARY KEY,
            source_type     TEXT,
            source_name     TEXT,
            freq            TEXT,
            adjust          TEXT,
            rows_imported   INTEGER DEFAULT 0,
            min_date        TEXT,
            max_date        TEXT,
            started_at      TEXT,
            finished_at     TEXT,
            status          TEXT DEFAULT 'running',
            error           TEXT,
            detail          TEXT
        );
        """)
        conn.executescript(PRICE_KLINE_TDXHUB_DDL)
        conn.executescript(CANONICAL_KLINE_QFQ_VIEW_DDL)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Read Functions
# ---------------------------------------------------------------------------

_PRICE_FIELDS = {"open", "high", "low", "close", "volume", "amount", "factor"}


def _quote_price_field(field: str) -> str:
    if field not in _PRICE_FIELDS:
        raise ValueError(f"unsupported price field: {field}")
    return f'"{field}"'


def _relation_has_column(conn, relation: str, column: str) -> bool:
    try:
        rows = conn.execute(f"DESCRIBE {relation}").fetchall()
    except Exception:
        return False
    for row in rows:
        try:
            name = row["column_name"]
        except Exception:
            name = row[0]
        if str(name).lower() == column.lower():
            return True
    return False


def get_kline(conn, code: str, date: str, freq: str = "daily",
              field: str = "open") -> Optional[float]:
    """单点价格查询：取指定日期的指定字段值"""
    col = _quote_price_field(field)
    relation = get_canonical_kline_qfq_relation() if freq == "daily" else "price_kline"
    row = conn.execute(
        f"SELECT {col} FROM {relation} "
        "WHERE code=? AND date=? AND freq=? AND adjust='qfq'",
        (code, date, freq)
    ).fetchone()
    if row:
        return row[0]
    # daily 回退到 monthly close
    if freq == "daily":
        row = conn.execute(
            "SELECT \"close\" FROM price_kline "
            "WHERE code=? AND date<=? AND freq='monthly' AND adjust='qfq' "
            "ORDER BY date DESC LIMIT 1",
            (code, date)
        ).fetchone()
        return row[0] if row else None
    return None


def get_kline_range(conn, code: str, start: str, end: str,
                    freq: str = "daily") -> "list[dict]":
    """区间查询：返回 [{date, open, high, low, close, volume, amount, factor}]"""
    relation = get_canonical_kline_qfq_relation() if freq == "daily" else "price_kline"
    has_factor = freq == "daily" and _relation_has_column(conn, relation, "factor")
    factor_expr = "COALESCE(factor, 1.0) AS factor" if has_factor else "1.0 AS factor"
    rows = conn.execute(
        f"SELECT date, open, high, low, close, volume, amount, {factor_expr} "
        f"FROM {relation} "
        "WHERE code=? AND freq=? AND adjust='qfq' AND date>=? AND date<=? "
        "ORDER BY date",
        (code, freq, start, end)
    ).fetchall()
    return [dict(r) for r in rows]


def get_xdxr_events(conn, code: str, start: Optional[str] = None,
                    end: Optional[str] = None) -> "list[dict]":
    """查询某只股票的除权除息 / 股本变动事件。"""
    where = ["code=?"]
    params: list = [code]
    if start:
        where.append("date>=?")
        params.append(start)
    if end:
        where.append("date<=?")
        params.append(end)

    rows = conn.execute(
        "SELECT code, date, category, name, fenhong, peigujia, songzhuangu, "
        " peigu, suogu, panqianliutong, panhouliutong, qianzongguben, "
        " houzongguben, fenshu, xingquanjia, source "
        f"FROM price_xdxr WHERE {' AND '.join(where)} "
        "ORDER BY date, category",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_sync_states(conn, freq: str = "daily") -> "list[dict]":
    """查询所有股票的同步状态"""
    rows = conn.execute(
        "SELECT * FROM market_sync_state "
        "WHERE dataset='price_kline' AND freq=? AND adjust='qfq'",
        (freq,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_xdxr_sync_states(conn) -> "list[dict]":
    """查询所有股票的 xdxr 同步状态。"""
    rows = conn.execute(
        "SELECT * FROM market_sync_state "
        "WHERE dataset='price_xdxr' AND freq='event' AND adjust='none'"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Write Functions
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # Phase ψ.5 allowlist: INSERT timestamp helper


class KlineWriteLintError(RuntimeError):
    """Raised when calendar lookup fails or write contains 盘中 future-dated rows.

    CLAUDE.md Rule 3 反例: fail-closed by default (Codex review HIGH 1 verdict).
    Emergency bypass: env var KLINE_WRITE_LINT_BYPASS=1 (audit any uses).
    """


def _latest_completed_trade_date_for_write(*, raise_on_miss: bool = True) -> Optional[str]:
    """Read latest_completed_trade_date from smartmoney.duckdb (calendar location).

    Used as defense-in-depth lint at K-line write time to reject 盘中 contamination
    (CLAUDE.md Rule 3 反例: tdxhub server 可能返回当日 partial K-line, write-side 必须 enforce).

    fail-closed (Codex review 2026-05-19 HIGH 1): calendar 不可访问时 raise KlineWriteLintError,
    不 silent skip. Emergency bypass via env KLINE_WRITE_LINT_BYPASS=1.

    rule-compliance: ok evidence=defense-in-depth-PIT-lint-fail-closed
    """
    import os
    if os.environ.get("KLINE_WRITE_LINT_BYPASS") == "1":
        import logging
        logging.getLogger(__name__).warning(
            "kline write lint: BYPASS via KLINE_WRITE_LINT_BYPASS=1 (audit this bypass!)"
        )
        return None
    try:
        from services.db import get_conn as _get_smart_conn
        from services.utils import latest_completed_trade_date as _latest_completed
        smart_conn = _get_smart_conn()
        try:
            return _latest_completed(smart_conn, close_hour=16)  # rule-compliance: ok evidence=A-share-close-15:00-plus-1h-buffer
        finally:
            smart_conn.close()
    except Exception as e:
        if raise_on_miss:
            raise KlineWriteLintError(
                f"latest_completed_trade_date lookup failed: {e}. "
                "fail-closed (CLAUDE.md Rule 3 + Codex 2026-05-19 HIGH 1). "
                "Set KLINE_WRITE_LINT_BYPASS=1 to bypass (audit any uses)."
            ) from e
        return None


def filter_kline_rows_by_calendar(
    rows: list[dict],
    *,
    output_table: str = "price_kline_tdxhub",
    batch_id: str = None,
    raise_on_miss: bool = True,
) -> list[dict]:
    """Filter rows by latest_completed_trade_date (write-side PIT lint).

    Shared helper (Codex review 2026-05-19 CRITICAL): 下沉到共享函数, 让所有 K-line writer
    (write_batch in build_price_kline_tdxhub, sync_kline_from_gcs, upsert_price_kline_tdxhub_rows
    via _clean_kline_rows_for_write) 都走同一 lint.

    rule-compliance: ok evidence=shared-defense-PIT-lint
    """
    if not rows:
        return rows
    last_closed = _latest_completed_trade_date_for_write(raise_on_miss=raise_on_miss)
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
