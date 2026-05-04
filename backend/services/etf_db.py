"""
etf_db.py — ETF 独立 DuckDB 数据库

职责：ETF 资产池、ETF 行情、ETF 快照与同步状态。
ETF 运行时只通过本模块读写，不再复用股票侧业务库与行情库。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from services.duck_adapter import connect as _duck_connect, DuckConn


logger = logging.getLogger("cm-api")

_DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"
# Phase 7: DuckDB 主库
_DB_PATH = _DB_DIR / "etf.duckdb"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_etf_conn(timeout: int = 30) -> DuckConn:
    """获取 ETF DuckDB 连接, 确保 schema 存在."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = _duck_connect(str(_DB_PATH), timeout=timeout)
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: DuckConn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS etf_asset_universe (
            code        TEXT PRIMARY KEY,
            name        TEXT,
            market      TEXT,
            category    TEXT,
            is_active   INTEGER DEFAULT 1,
            updated_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_etf_asset_active
            ON etf_asset_universe(is_active, category);

        CREATE TABLE IF NOT EXISTS etf_price_kline (
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
        CREATE INDEX IF NOT EXISTS idx_epk_code_freq
            ON etf_price_kline(code, freq);
        CREATE INDEX IF NOT EXISTS idx_epk_date
            ON etf_price_kline(date);

        CREATE TABLE IF NOT EXISTS etf_sync_state (
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

        CREATE TABLE IF NOT EXISTS etf_import_batch (
            batch_id        TEXT PRIMARY KEY,
            dataset         TEXT,
            source          TEXT,
            rows_imported   INTEGER DEFAULT 0,
            min_date        TEXT,
            max_date        TEXT,
            started_at      TEXT,
            finished_at     TEXT,
            status          TEXT DEFAULT 'running',
            error           TEXT,
            detail          TEXT
        );

        CREATE TABLE IF NOT EXISTS mart_etf_snapshot_latest (
            code            TEXT PRIMARY KEY,
            snapshot_id     TEXT NOT NULL,
            category        TEXT,
            factor_rank     INTEGER,
            factor_score    REAL,
            rotation_score  REAL,
            strategy_type   TEXT,
            payload_json    TEXT NOT NULL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_metf_snapshot
            ON mart_etf_snapshot_latest(snapshot_id);

        CREATE TABLE IF NOT EXISTS mart_etf_snapshot_state (
            state_key               TEXT PRIMARY KEY,
            snapshot_id             TEXT,
            schema_version          INTEGER DEFAULT 1,
            computed_at             TEXT,
            etf_count               INTEGER DEFAULT 0,
            history_start           TEXT,
            history_end             TEXT,
            overview_json           TEXT,
            factor_snapshot_json    TEXT,
            mining_snapshot_json    TEXT,
            source_status_json      TEXT
        );

        """
    )
    conn.commit()


def upsert_price_rows(conn: DuckConn, rows: list[dict], source: str,
                      batch_id: str | None = None) -> int:
    if not rows:
        return 0
    now = _now_iso()
    conn.executemany(
        "INSERT OR REPLACE INTO etf_price_kline "
        "(code, date, freq, adjust, open, high, low, close, volume, amount, "
        " source, batch_id, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                row["code"], row["date"], row.get("freq", "daily"), row.get("adjust", "qfq"),
                row.get("open"), row.get("high"), row.get("low"), row.get("close"),
                row.get("volume"), row.get("amount"), source, batch_id, now,
            )
            for row in rows
        ],
    )
    conn.commit()
    return len(rows)


def update_sync_state(conn: DuckConn, code: str, freq: str, *,
                      source: str | None = None,
                      min_date: str | None = None,
                      max_date: str | None = None,
                      row_count: int | None = None,
                      error: str | None = None) -> None:
    now = _now_iso()
    conn.execute(
        "INSERT INTO etf_sync_state "
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
            code,
            freq,
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
