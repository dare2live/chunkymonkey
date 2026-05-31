import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.build_dim_listing_status import (  # noqa: E402
    DIM_LISTING_STATUS_COLUMNS,
    build_dim_listing_status,
    ensure_dim_listing_status_schema,
)


def test_ensure_dim_listing_status_schema_backfills_legacy_columns():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE dim_listing_status (stock_code TEXT PRIMARY KEY)")

        ensure_dim_listing_status_schema(conn)

        cols = {
            row[0]
            for row in conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'dim_listing_status'
                """
            ).fetchall()
        }
        assert {"stock_code", *(col for col, _dtype in DIM_LISTING_STATUS_COLUMNS)} <= cols
    finally:
        conn.close()


def test_build_dim_listing_status_uses_ever_listed_source(tmp_path):
    db_path = tmp_path / "smartmoney.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE dim_listing_status (stock_code TEXT PRIMARY KEY)")
        conn.execute(
            """
            CREATE TABLE dim_all_ever_listed (
                stock_code TEXT,
                listed_date TEXT,
                delisted_date TEXT,
                is_active INTEGER,
                source TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO dim_all_ever_listed VALUES (?, ?, ?, ?, ?)",
            [
                ("000001", "1991-04-03", None, 1, "fixture"),
                ("000002", "1991-01-29", "2026-01-05", 0, "fixture"),
            ],
        )
    finally:
        conn.close()

    result = build_dim_listing_status(db_path)

    conn = duckdb.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT stock_code, listing_status, status_reason, flag_from_date
            FROM dim_listing_status
            ORDER BY stock_code
            """
        ).fetchall()
    finally:
        conn.close()

    assert result == {"source_rows": 2, "rows": 2, "delisted_rows": 1}
    assert rows == [
        ("000001", "listed", "fixture", "1991-04-03"),
        ("000002", "delisted", "fixture", "2026-01-05"),
    ]


def test_build_dim_listing_status_handles_minimal_ever_listed_schema(tmp_path):
    db_path = tmp_path / "smartmoney_minimal.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE dim_all_ever_listed (
                stock_code TEXT,
                first_seen_date TEXT
            )
            """
        )
        conn.execute("INSERT INTO dim_all_ever_listed VALUES ('000003', '1991-07-03')")
    finally:
        conn.close()

    result = build_dim_listing_status(db_path)

    conn = duckdb.connect(str(db_path))
    try:
        row = conn.execute(
            """
            SELECT stock_code, CAST(listed_date AS VARCHAR), delisted_date,
                   listing_status, status_reason, flag_from_date
            FROM dim_listing_status
            """
        ).fetchone()
    finally:
        conn.close()

    assert result == {"source_rows": 1, "rows": 1, "delisted_rows": 0}
    assert row == ("000003", "1991-07-03", None, "listed", "dim_all_ever_listed", "1991-07-03")
