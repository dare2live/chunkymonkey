#!/usr/bin/env python3
"""Build dim_listing_status from the local ever-listed stock master."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
SMART_DB = REPO_ROOT / "data" / "smartmoney.duckdb"

log = logging.getLogger("build_dim_listing_status")

DIM_LISTING_STATUS_COLUMNS = (
    ("ts_code", "TEXT"),
    ("listed_date", "DATE"),
    ("delisted_date", "DATE"),
    ("listing_status", "TEXT"),
    ("status_reason", "TEXT"),
    ("flag_from_date", "TEXT"),
    ("detected_at", "TIMESTAMP"),
)


def _columns(conn: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = ?
        """,
        [table_name],
    ).fetchall()
    return {str(r[0]) for r in rows}


def _table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = ?
        """,
        [table_name],
    ).fetchone()
    return bool(row and row[0])


def _missing_dim_listing_status_columns(existing_cols: set[str]) -> list[tuple[str, str]]:
    return [(col, dtype) for col, dtype in DIM_LISTING_STATUS_COLUMNS if col not in existing_cols]


def _add_dim_listing_status_column(conn: duckdb.DuckDBPyConnection, col: str, dtype: str) -> None:
    conn.execute(f"ALTER TABLE dim_listing_status ADD COLUMN {col} {dtype}")


def ensure_dim_listing_status_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dim_listing_status (
            stock_code TEXT PRIMARY KEY,
            ts_code TEXT,
            listed_date DATE,
            delisted_date DATE,
            listing_status TEXT NOT NULL DEFAULT 'unknown',
            status_reason TEXT,
            flag_from_date TEXT,
            detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cols = _columns(conn, "dim_listing_status")
    for col, dtype in _missing_dim_listing_status_columns(cols):
        _add_dim_listing_status_column(conn, col, dtype)


def _source_projection(conn: duckdb.DuckDBPyConnection) -> str:
    if not _table_exists(conn, "dim_all_ever_listed"):
        raise RuntimeError("dim_all_ever_listed not found; cannot build PIT listing status")

    cols = _columns(conn, "dim_all_ever_listed")
    code_expr = "stock_code" if "stock_code" in cols else "ts_code"
    listed_expr = (
        "TRY_CAST(listed_date AS DATE)"
        if "listed_date" in cols
        else "TRY_CAST(first_seen_date AS DATE)"
        if "first_seen_date" in cols
        else "NULL::DATE"
    )
    delisted_expr = "TRY_CAST(delisted_date AS DATE)" if "delisted_date" in cols else "NULL::DATE"
    is_active_expr = (
        "COALESCE(is_active, 1)"
        if "is_active" in cols
        else "CASE WHEN delisted_date IS NULL THEN 1 ELSE 0 END"
        if "delisted_date" in cols
        else "1"
    )
    source_expr = "source" if "source" in cols else "'dim_all_ever_listed'"

    return f"""
        SELECT
            {code_expr} AS stock_code,
            {code_expr} AS ts_code,
            COALESCE({listed_expr}, DATE '1900-01-01') AS listed_date,
            {delisted_expr} AS delisted_date,
            CASE WHEN {is_active_expr} = 1 THEN 'listed' ELSE 'delisted' END AS listing_status,
            {source_expr} AS status_reason,
            CAST(COALESCE({delisted_expr}, {listed_expr}) AS VARCHAR) AS flag_from_date,
            CURRENT_TIMESTAMP AS detected_at
        FROM dim_all_ever_listed
        WHERE {code_expr} IS NOT NULL
    """


def build_dim_listing_status(db_path: str | Path | None = None) -> dict[str, int]:
    # §9 拆库: dim_all_ever_listed(源) + dim_listing_status(目标) 均迁 reference 库 → 默认连 reference RW
    #   (db_path 显式传=测试/特殊; None=生产走 reference)。
    if db_path is None:
        from services.data_access import resolver
        conn = resolver.connect_rw("reference")
    else:
        from services.duck_adapter import connect as duck_connect
        conn = duck_connect(str(db_path), read_only=False)  # 显式路径(测试/特殊): sanctioned adapter, RW
    try:
        ensure_dim_listing_status_schema(conn)
        projection = _source_projection(conn)
        source_count = conn.execute("SELECT COUNT(*) FROM dim_all_ever_listed").fetchone()[0]
        conn.execute("DELETE FROM dim_listing_status")
        conn.execute(
            f"""
            INSERT INTO dim_listing_status (
                stock_code, ts_code, listed_date, delisted_date,
                listing_status, status_reason, flag_from_date, detected_at
            )
            {projection}
            """
        )
        row_count = conn.execute("SELECT COUNT(*) FROM dim_listing_status").fetchone()[0]
        delisted_count = conn.execute(
            "SELECT COUNT(*) FROM dim_listing_status WHERE delisted_date IS NOT NULL"
        ).fetchone()[0]
        return {
            "source_rows": int(source_count),
            "rows": int(row_count),
            "delisted_rows": int(delisted_count),
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build dim_listing_status from dim_all_ever_listed")
    parser.add_argument("--db", default=None, help="DB path (default=None → reference 库, §9 拆库)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = build_dim_listing_status(args.db)
    log.info(
        "dim_listing_status rows=%s source_rows=%s delisted_rows=%s",
        f"{result['rows']:,}",
        f"{result['source_rows']:,}",
        f"{result['delisted_rows']:,}",
    )
    if result["rows"] <= 0:
        log.error("dim_listing_status is empty after build")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
