import sys
from pathlib import Path

import duckdb


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.holder_availability import (  # noqa: E402
    backfill_future_holder_period_fetched_at_availability,
    backfill_future_holder_period_page_update_availability,
    backfill_holder_period_availability,
    backfill_holder_period_availability_rows,
    derive_holder_availability_dates,
    regulatory_notice_date_for_report_date,
)


def test_regulatory_notice_date_for_standard_report_periods():
    assert regulatory_notice_date_for_report_date("20251231") == "20260430"
    assert regulatory_notice_date_for_report_date("20260331") == "20260430"
    assert regulatory_notice_date_for_report_date("20260630") == "20260831"
    assert regulatory_notice_date_for_report_date("20260930") == "20261031"


def test_derive_holder_availability_uses_next_trading_day_after_notice():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
        conn.executemany(
            "INSERT INTO dim_trading_calendar VALUES (?, ?)",
            [
                ("2026-04-30", 1),
                ("2026-05-01", 0),
                ("2026-05-04", 1),
            ],
        )

        notice, effective, source = derive_holder_availability_dates(
            conn,
            report_date="20260331",
        )

        assert notice == "20260430"
        assert effective == "20260504"
        assert source == "regulatory_deadline"
    finally:
        conn.close()


def test_derive_holder_availability_uses_observed_page_update_before_regulatory_deadline():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
        conn.executemany(
            "INSERT INTO dim_trading_calendar VALUES (?, ?)",
            [
                ("2026-05-05", 1),
                ("2026-05-06", 1),
            ],
        )

        notice, effective, source = derive_holder_availability_dates(
            conn,
            report_date="20260421",
            page_update_date="2026-05-05",
        )

        assert notice == "20260505"
        assert effective == "20260506"
        assert source == "page_update_date"
    finally:
        conn.close()


def test_derive_holder_availability_uses_fetched_at_for_future_regulatory_deadline():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
        conn.executemany(
            "INSERT INTO dim_trading_calendar VALUES (?, ?)",
            [
                ("2026-05-05", 1),
                ("2026-05-06", 1),
            ],
        )

        notice, effective, source = derive_holder_availability_dates(
            conn,
            report_date="20260421",
            fetched_at="2026-05-05T12:00:00",
        )

        assert notice == "20260505"
        assert effective == "20260506"
        assert source == "fetched_at_observed"
    finally:
        conn.close()


def test_derive_holder_availability_does_not_use_current_fetch_for_historical_period():
    notice, _effective, source = derive_holder_availability_dates(
        None,
        report_date="20201231",
        fetched_at="2026-05-05T12:00:00",
    )

    assert notice == "20210430"
    assert source == "regulatory_deadline"


def test_derive_holder_availability_falls_back_when_calendar_does_not_cover_date():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
        conn.execute("INSERT INTO dim_trading_calendar VALUES ('2023-01-03', 1)")

        notice, effective, source = derive_holder_availability_dates(
            conn,
            report_date="20201231",
        )

        assert notice == "20210430"
        assert effective == "20210501"
        assert source == "regulatory_deadline"
    finally:
        conn.close()


def test_backfill_holder_period_availability_updates_missing_rows():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
        conn.execute("INSERT INTO dim_trading_calendar VALUES ('2026-05-04', 1)")
        conn.execute(
            """
            CREATE TABLE fact_top10_holder_period (
                stock_code TEXT,
                report_date TEXT,
                notice_date TEXT,
                effective_date TEXT,
                availability_source TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO fact_top10_holder_period VALUES ('600519', '20260331', NULL, NULL, NULL)"
        )

        result = backfill_holder_period_availability(conn)

        row = conn.execute("SELECT notice_date, effective_date, availability_source FROM fact_top10_holder_period").fetchone()
        assert row == ("20260430", "20260504", "regulatory_deadline")
        assert result == {"updated_report_dates": 1, "remaining_missing_rows": 0}
    finally:
        conn.close()


def test_backfill_holder_period_availability_can_recompute_regulatory_rows():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
        conn.execute("INSERT INTO dim_trading_calendar VALUES ('2023-01-03', 1)")
        conn.execute(
            """
            CREATE TABLE fact_top10_holder_period (
                stock_code TEXT,
                report_date TEXT,
                notice_date TEXT,
                effective_date TEXT,
                availability_source TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO fact_top10_holder_period VALUES ('600519', '20201231', '20210430', '20230103', 'regulatory_deadline')"
        )

        result = backfill_holder_period_availability(conn)
        assert result["updated_report_dates"] == 0
        result = backfill_holder_period_availability_rows(conn, overwrite_regulatory=True)

        row = conn.execute("SELECT notice_date, effective_date FROM fact_top10_holder_period").fetchone()
        assert row == ("20210430", "20210501")
        assert result == {"updated_report_dates": 1, "remaining_missing_rows": 0}
    finally:
        conn.close()


def test_backfill_future_holder_period_fetched_at_availability_updates_only_future_regulatory_rows():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
        conn.executemany(
            "INSERT INTO dim_trading_calendar VALUES (?, ?)",
            [
                ("2026-05-05", 1),
                ("2026-05-06", 1),
            ],
        )
        conn.execute(
            """
            CREATE TABLE fact_top10_holder_period (
                stock_code TEXT,
                report_date TEXT,
                notice_date TEXT,
                effective_date TEXT,
                availability_source TEXT,
                fetched_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO fact_top10_holder_period VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("000001", "20260421", "29990720", "29990721", "regulatory_deadline", "2026-05-05T12:00:00"),
                ("000002", "20201231", "20210430", "20210501", "regulatory_deadline", "2026-05-05T12:00:00"),
                ("000003", "20260421", "29990720", "29990721", "source_notice", "2026-05-05T12:00:00"),
            ],
        )

        result = backfill_future_holder_period_fetched_at_availability(conn)
        rows = conn.execute(
            """
            SELECT stock_code, notice_date, effective_date, availability_source
              FROM fact_top10_holder_period
             ORDER BY stock_code
            """
        ).fetchall()

        assert result == {"status": "ok", "updated_rows": 1, "remaining_candidate_rows": 0}
        assert rows == [
            ("000001", "20260505", "20260506", "fetched_at_observed"),
            ("000002", "20210430", "20210501", "regulatory_deadline"),
            ("000003", "29990720", "29990721", "source_notice"),
        ]
    finally:
        conn.close()


def test_backfill_future_holder_period_page_update_availability_updates_only_future_regulatory_rows():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
        conn.executemany(
            "INSERT INTO dim_trading_calendar VALUES (?, ?)",
            [
                ("2026-05-05", 1),
                ("2026-05-06", 1),
            ],
        )
        conn.execute(
            """
            CREATE TABLE fact_top10_holder_period (
                stock_code TEXT,
                report_date TEXT,
                notice_date TEXT,
                effective_date TEXT,
                availability_source TEXT,
                page_update_date TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO fact_top10_holder_period VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("000001", "20260421", "29990720", "29990721", "regulatory_deadline", "2026-05-05"),
                ("000002", "20260421", "20260430", "20260504", "regulatory_deadline", "2026-05-05"),
                ("000003", "20260421", "29990720", "29990721", "source_notice", "2026-05-05"),
            ],
        )

        result = backfill_future_holder_period_page_update_availability(conn)
        rows = conn.execute(
            """
            SELECT stock_code, notice_date, effective_date, availability_source
              FROM fact_top10_holder_period
             ORDER BY stock_code
            """
        ).fetchall()

        assert result == {"status": "ok", "updated_rows": 1, "remaining_candidate_rows": 0}
        assert rows == [
            ("000001", "20260505", "20260506", "page_update_date"),
            ("000002", "20260430", "20260504", "regulatory_deadline"),
            ("000003", "29990720", "29990721", "source_notice"),
        ]
    finally:
        conn.close()
