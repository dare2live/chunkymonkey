from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pytest


DB_PATH = Path(__file__).resolve().parents[3] / "data" / "smartmoney.duckdb"
TABLE_NAME = "mart_p0b_oos_predictions"
START_DATE = "2024-01-01"
END_DATE = "2024-12-31"


def _column_map(conn: duckdb.DuckDBPyConnection, table_name: str) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT column_name, data_type
          FROM information_schema.columns
         WHERE table_schema = 'main'
           AND table_name = ?
        """,
        [table_name],
    ).fetchall()
    return {str(name): str(dtype) for name, dtype in rows}


def _source_date_column(columns: dict[str, str]) -> str | None:
    # 生产库当前 P0b 表使用 signal_date DATE；若后续改回 trade_date，本测试自动跟随。
    for column in ("trade_date", "signal_date", "date"):
        if column in columns:
            return column
    return None


def _timed_count(conn: duckdb.DuckDBPyConnection, sql: str) -> tuple[float, int]:
    started = time.perf_counter()
    row_count = int(conn.execute(sql).fetchone()[0])
    return time.perf_counter() - started, row_count


@pytest.mark.perf
@pytest.mark.slow
def test_perf_p1_trade_date_dt_range_filter() -> None:
    if not DB_PATH.exists():
        pytest.skip(f"missing real DB: {DB_PATH}")

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        columns = _column_map(conn, TABLE_NAME)
        if not columns:
            pytest.skip(f"missing table: {TABLE_NAME}")
        if "trade_date_dt" not in columns:
            pytest.skip(f"missing Phase A column: {TABLE_NAME}.trade_date_dt")

        source_column = _source_date_column(columns)
        if source_column is None:
            pytest.skip(f"missing date source column in {TABLE_NAME}")

        total_rows = int(conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0])
        if total_rows == 0:
            pytest.skip(f"empty table: {TABLE_NAME}")

        q1 = f"""
            SELECT COUNT(*)
              FROM {TABLE_NAME}
             WHERE {source_column} BETWEEN '{START_DATE}' AND '{END_DATE}'
        """
        q2 = f"""
            SELECT COUNT(*)
              FROM {TABLE_NAME}
             WHERE trade_date_dt BETWEEN '{START_DATE}'::DATE AND '{END_DATE}'::DATE
        """

        q1_elapsed_s, q1_rows = _timed_count(conn, q1)
        q2_elapsed_s, q2_rows = _timed_count(conn, q2)

        print(
            "perf_p1_trade_date",
            f"table={TABLE_NAME}",
            f"source_column={source_column}",
            f"q1_elapsed_s={q1_elapsed_s:.6f}",
            f"q1_rows={q1_rows}",
            f"q2_elapsed_s={q2_elapsed_s:.6f}",
            f"q2_rows={q2_rows}",
        )
        assert q1_rows == q2_rows
    finally:
        conn.close()
