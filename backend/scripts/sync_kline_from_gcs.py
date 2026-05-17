#!/usr/bin/env python3
"""Merge a VM-produced TDXHub K-line delta from GCS into local market.duckdb.

The VM fetch flow writes a compact DuckDB with:
  - price_kline_tdxhub
  - price_kline_tdxhub_adjustment_event
  - tdxhub_kline_delta_metadata

This script keeps the merge PIT-auditable by adding source_available_date to the
local primary table and recording the GCS sync run.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.market_db import (  # noqa: E402
    CANONICAL_KLINE_QFQ_VIEW_DDL,
    PRICE_KLINE_TDXHUB_DDL,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCAL_DB = REPO_ROOT / "data" / "market.duckdb"
LOT_SIZE_SHARES = 100.0
VWAP_RATIO_MIN = 0.5
VWAP_RATIO_MAX = 1.5


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _download_if_needed(uri: str, tmpdir: Path) -> Path:
    if not uri.startswith("gs://"):
        path = Path(uri).expanduser()
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    if shutil.which("gsutil") is None:
        raise RuntimeError("gsutil is required for gs:// input")
    local = tmpdir / Path(uri.rstrip("/").split("/")[-1]).name
    _run(["gsutil", "cp", uri, str(local)])
    return local


def _table_exists(conn: duckdb.DuckDBPyConnection, relation: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {relation} LIMIT 0")
        return True
    except Exception:
        return False


def _execute_script(conn: duckdb.DuckDBPyConnection, sql: str) -> None:
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)


def _columns(conn: duckdb.DuckDBPyConnection, relation: str) -> set[str]:
    if not _table_exists(conn, relation):
        return set()
    return {str(row[0]) for row in conn.execute(f"DESCRIBE {relation}").fetchall()}


def _ensure_column(conn: duckdb.DuckDBPyConnection, table: str, column: str, ddl_type: str) -> None:
    if column in _columns(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def _ensure_fallback_price_kline_table(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_kline (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            freq TEXT NOT NULL DEFAULT 'daily',
            adjust TEXT NOT NULL DEFAULT 'qfq',
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            source TEXT,
            batch_id TEXT,
            ingested_at TEXT,
            PRIMARY KEY (code, date, freq, adjust)
        )
        """
    )


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _weekday_dates(start_date: str, end_date: str) -> list[str]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    out: list[str] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur = date.fromordinal(cur.toordinal() + 1)
    return out


def _create_staging(
    conn: duckdb.DuckDBPyConnection,
    *,
    start_date: str,
    end_date: str,
    source_available_date: str,
    fallback_batch_id: str,
) -> dict:
    source_cols = _columns(conn, "src.price_kline_tdxhub")
    required = {
        "code",
        "date",
        "freq",
        "adjust",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    }
    missing = sorted(required - source_cols)
    if missing:
        raise RuntimeError(f"source price_kline_tdxhub missing columns: {missing}")

    factor_expr = "factor" if "factor" in source_cols else "1.0"
    source_expr = "COALESCE(NULLIF(source, ''), 'tdxhub_vm')" if "source" in source_cols else "'tdxhub_vm'"
    batch_expr = (
        f"COALESCE(NULLIF(batch_id, ''), {_quote_literal(fallback_batch_id)})"
        if "batch_id" in source_cols
        else _quote_literal(fallback_batch_id)
    )
    available_expr = (
        f"COALESCE(NULLIF(CAST(source_available_date AS VARCHAR), ''), {_quote_literal(source_available_date)})"
        if "source_available_date" in source_cols
        else _quote_literal(source_available_date)
    )

    conn.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE tmp_kline_gcs_delta AS
        SELECT
            CAST(code AS VARCHAR) AS code,
            CAST(date AS VARCHAR) AS date,
            COALESCE(NULLIF(CAST(freq AS VARCHAR), ''), 'daily') AS freq,
            COALESCE(NULLIF(CAST(adjust AS VARCHAR), ''), 'qfq') AS adjust,
            CAST(open AS DOUBLE) AS open,
            CAST(high AS DOUBLE) AS high,
            CAST(low AS DOUBLE) AS low,
            CAST(close AS DOUBLE) AS close,
            CAST(volume AS DOUBLE) AS volume,
            CAST(amount AS DOUBLE) AS amount,
            CAST({factor_expr} AS DOUBLE) AS factor,
            {source_expr} AS source,
            {batch_expr} AS batch_id,
            {available_expr} AS source_available_date
          FROM src.price_kline_tdxhub
         WHERE CAST(date AS DATE) >= CAST(? AS DATE)
           AND CAST(date AS DATE) <= CAST(? AS DATE)
           AND COALESCE(NULLIF(CAST(freq AS VARCHAR), ''), 'daily') = 'daily'
           AND COALESCE(NULLIF(CAST(adjust AS VARCHAR), ''), 'qfq') = 'qfq'
        """,
        [start_date, end_date],
    )
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE tmp_kline_gcs_delta_dedup AS
        SELECT *
          FROM tmp_kline_gcs_delta
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY code, date, freq, adjust
            ORDER BY source_available_date DESC, batch_id DESC
        ) = 1
        """
    )
    conn.execute("CREATE OR REPLACE TEMP TABLE tmp_kline_gcs_delta AS SELECT * FROM tmp_kline_gcs_delta_dedup")
    row = conn.execute(
        """
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT code) AS codes,
               COUNT(DISTINCT date) AS days,
               MIN(date) AS min_date,
               MAX(date) AS max_date
          FROM tmp_kline_gcs_delta
        """
    ).fetchone()
    return {
        "rows": int(row[0] or 0),
        "codes": int(row[1] or 0),
        "days": int(row[2] or 0),
        "min_date": row[3],
        "max_date": row[4],
    }


def _validate_staging(
    conn: duckdb.DuckDBPyConnection,
    *,
    min_codes_per_day: int,
    expected_dates: list[str],
) -> dict:
    invalid = conn.execute(
        f"""
        SELECT COUNT(*)
          FROM tmp_kline_gcs_delta
         WHERE code IS NULL OR TRIM(code) = ''
            OR date IS NULL OR TRY_CAST(date AS DATE) IS NULL
            OR TRY_CAST(source_available_date AS DATE) IS NULL
            OR CAST(date AS DATE) > CAST(source_available_date AS DATE)
            OR open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
            OR volume IS NULL OR amount IS NULL
            OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
            OR volume <= 0 OR amount <= 0
            OR high < GREATEST(open, low, close)
            OR low > LEAST(open, high, close)
            OR amount / NULLIF(volume * {LOT_SIZE_SHARES}, 0) / NULLIF(close, 0)
               NOT BETWEEN {VWAP_RATIO_MIN} AND {VWAP_RATIO_MAX}
        """
    ).fetchone()[0]
    if invalid:
        raise RuntimeError(f"invalid kline rows in delta: {invalid}")

    daily_rows = conn.execute(
        """
        SELECT date, COUNT(DISTINCT code) AS codes
          FROM tmp_kline_gcs_delta
         GROUP BY 1
         ORDER BY 1
        """
    ).fetchall()
    daily = {str(row[0]): int(row[1] or 0) for row in daily_rows}
    if min_codes_per_day > 0:
        weak = {day: codes for day, codes in daily.items() if codes < min_codes_per_day}
        if weak:
            raise RuntimeError(f"daily code coverage below {min_codes_per_day}: {weak}")
    if expected_dates:
        missing = [day for day in expected_dates if day not in daily]
        weak_expected = {
            day: daily[day]
            for day in expected_dates
            if day in daily and min_codes_per_day > 0 and daily[day] < min_codes_per_day
        }
        if missing or weak_expected:
            raise RuntimeError(
                "expected date coverage failed: "
                + json.dumps(
                    {"missing": missing, "weak": weak_expected},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    return {"daily_code_counts": daily}


def _merge_adjustment_events(conn: duckdb.DuckDBPyConnection) -> int:
    if not _table_exists(conn, "src.price_kline_tdxhub_adjustment_event"):
        return 0
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE tmp_kline_gcs_adjustment_event AS
        SELECT *
          FROM src.price_kline_tdxhub_adjustment_event
        """
    )
    cols = _columns(conn, "tmp_kline_gcs_adjustment_event")
    required = {"code", "event_date", "event_hash", "adjust_factor"}
    if not required <= cols:
        return 0
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_kline_tdxhub_adjustment_event (
            code TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_hash TEXT NOT NULL,
            adjust_factor REAL NOT NULL,
            prev_close REAL,
            source TEXT,
            batch_id TEXT,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (code, event_date, event_hash)
        )
        """
    )
    count = conn.execute("SELECT COUNT(*) FROM tmp_kline_gcs_adjustment_event").fetchone()[0]
    if count == 0:
        return 0
    conn.execute(
        """
        DELETE FROM price_kline_tdxhub_adjustment_event AS target
              USING tmp_kline_gcs_adjustment_event AS incoming
              WHERE target.code = incoming.code
                AND target.event_date = incoming.event_date
                AND target.event_hash = incoming.event_hash
        """
    )
    conn.execute(
        """
        INSERT INTO price_kline_tdxhub_adjustment_event
        SELECT * FROM tmp_kline_gcs_adjustment_event
        """
    )
    return int(count)


def _record_sync_run(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    gcs_uri: str,
    source_available_date: str,
    start_date: str,
    end_date: str,
    rows_written: int,
    metadata: dict,
) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mart_kline_gcs_sync_run (
            run_id TEXT PRIMARY KEY,
            gcs_uri TEXT NOT NULL,
            source_available_date TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            rows_written BIGINT NOT NULL,
            metadata_json TEXT,
            synced_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_kline_gcs_sync_run
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            gcs_uri,
            source_available_date,
            start_date,
            end_date,
            rows_written,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ],
    )


def sync_delta(args: argparse.Namespace) -> dict:
    tmp = tempfile.TemporaryDirectory(prefix="cm_kline_gcs_")
    try:
        source_path = _download_if_needed(args.gcs_uri, Path(tmp.name))
        local_db = Path(args.local_db).expanduser()
        local_db.parent.mkdir(parents=True, exist_ok=True)
        run_id = args.run_id or f"kline_gcs_sync_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
        source_available_date = args.source_available_date or date.today().isoformat()

        conn = duckdb.connect(str(local_db))
        try:
            conn.execute(f"ATTACH '{source_path}' AS src (READ_ONLY)")
            if not _table_exists(conn, "src.price_kline_tdxhub"):
                raise RuntimeError("source delta missing table: price_kline_tdxhub")
            _execute_script(conn, PRICE_KLINE_TDXHUB_DDL)
            _ensure_column(conn, "price_kline_tdxhub", "source_available_date", "TEXT")
            _ensure_fallback_price_kline_table(conn)
            staging = _create_staging(
                conn,
                start_date=args.start_date,
                end_date=args.end_date,
                source_available_date=source_available_date,
                fallback_batch_id=run_id,
            )
            expected_dates = []
            if args.expected_dates:
                expected_dates = [item.strip() for item in args.expected_dates.split(",") if item.strip()]
            if args.require_weekdays:
                expected_dates = sorted(set(expected_dates + _weekday_dates(args.start_date, args.end_date)))
            validation = _validate_staging(
                conn,
                min_codes_per_day=args.min_codes_per_day,
                expected_dates=expected_dates,
            )
            if args.dry_run:
                return {
                    "dry_run": True,
                    "local_db": str(local_db),
                    "source_path": str(source_path),
                    "staging": staging,
                    **validation,
                }

            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(
                    """
                    DELETE FROM price_kline_tdxhub AS target
                          USING tmp_kline_gcs_delta AS incoming
                          WHERE target.code = incoming.code
                            AND target.date = incoming.date
                            AND target.freq = incoming.freq
                            AND target.adjust = incoming.adjust
                    """
                )
                conn.execute(
                    """
                    INSERT INTO price_kline_tdxhub (
                        code, date, freq, adjust, open, high, low, close,
                        volume, amount, factor, source, batch_id, ingested_at,
                        source_available_date
                    )
                    SELECT code, date, freq, adjust, open, high, low, close,
                           volume, amount, factor, source, batch_id,
                           CURRENT_TIMESTAMP, source_available_date
                      FROM tmp_kline_gcs_delta
                    """
                )
                adjustment_events = _merge_adjustment_events(conn)
                _execute_script(conn, CANONICAL_KLINE_QFQ_VIEW_DDL)
                _record_sync_run(
                    conn,
                    run_id=run_id,
                    gcs_uri=args.gcs_uri,
                    source_available_date=source_available_date,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    rows_written=staging["rows"],
                    metadata={**staging, **validation, "adjustment_events": adjustment_events},
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

            return {
                "dry_run": False,
                "run_id": run_id,
                "local_db": str(local_db),
                "gcs_uri": args.gcs_uri,
                "source_available_date": source_available_date,
                "rows_written": staging["rows"],
                "adjustment_events": adjustment_events,
                **validation,
            }
        finally:
            conn.close()
    finally:
        if args.keep_temp:
            print(f"kept temp dir: {tmp.name}", file=sys.stderr)
        else:
            tmp.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gcs-uri", required=True, help="gs://.../kline_delta_*.duckdb or local delta path")
    parser.add_argument("--local-db", default=str(DEFAULT_LOCAL_DB), help="local market.duckdb path")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--source-available-date", default=None, help="actual fetch/availability date; default today")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--min-codes-per-day", type=int, default=0)
    parser.add_argument("--expected-dates", default="", help="comma-separated trading dates that must be present")
    parser.add_argument("--require-weekdays", action="store_true", help="require every weekday in the date range")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()

    result = sync_delta(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
