import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from routers import data_health as data_health_router
from scripts.data_health_snapshot import compute_health_for_table


def test_raw_source_freshness_uses_writer_time_and_trading_calendar():
    conn = duck_mem()
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT, is_trading INTEGER)")
        conn.execute(
            """
            INSERT INTO dim_trading_calendar VALUES
            ('2026-04-28', 1), ('2026-04-29', 1), ('2026-04-30', 1),
            ('2026-05-01', 0), ('2026-05-04', 0), ('2026-05-05', 0)
            """
        )
        conn.execute(
            """
            CREATE TABLE raw_lhb_daily (
                trade_date TEXT,
                stock_code TEXT,
                ingested_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO raw_lhb_daily VALUES ('2026-04-28', '000001', TIMESTAMP '2026-04-28 21:19:11')"
        )

        health = compute_health_for_table(
            conn,
            {
                "table_name": "raw_lhb_daily",
                "layer": "raw",
                "writer_module": "services.lhb_client",
                "upstream_source": "aif10:RPT_DAILYBILLBOARD_DETAILSNEW",
                "expected_freshness": "t+1",
                "sla_hours": 48,
            },
            datetime(2026, 5, 4, 12, 0, 0),
        )

        assert health["severity"] == "green"
        assert health["freshness_hours"] == 48
        assert health["last_writer_at"].startswith("2026-04-28")
    finally:
        conn.close()


def test_on_demand_table_without_date_column_is_green():
    conn = duck_mem()
    try:
        conn.execute("CREATE TABLE research_cache (id INTEGER, value TEXT)")
        conn.execute("INSERT INTO research_cache VALUES (1, 'ok')")

        health = compute_health_for_table(
            conn,
            {
                "table_name": "research_cache",
                "layer": "research",
                "writer_module": None,
                "upstream_source": None,
                "expected_freshness": "on-demand",
                "sla_hours": 720,
            },
            datetime(2026, 5, 4, 12, 0, 0),
        )

        assert health["severity"] == "green"
        assert health["issue_summary"] is None
    finally:
        conn.close()


def test_event_fact_freshness_uses_writer_time():
    conn = duck_mem()
    try:
        conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT, is_trading INTEGER)")
        conn.execute(
            """
            INSERT INTO dim_trading_calendar VALUES
            ('2026-04-28', 1), ('2026-04-29', 1), ('2026-04-30', 1),
            ('2026-05-01', 0), ('2026-05-04', 0)
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_shareholder_trade (
                stock_code TEXT,
                change_date TEXT,
                fetched_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO fact_shareholder_trade VALUES ('000001', '20260420', '2026-04-30T08:15:28+00:00')"
        )

        health = compute_health_for_table(
            conn,
            {
                "table_name": "fact_shareholder_trade",
                "layer": "fact",
                "writer_module": "scripts/ingest_holders_tdxhub.py",
                "upstream_source": "tdxhub.holders",
                "expected_freshness": "event",
                "sla_hours": 48,
            },
            datetime(2026, 5, 4, 12, 0, 0),
        )

        assert health["severity"] == "green"
        assert health["freshness_hours"] == 0
        assert health["last_writer_at"].startswith("2026-04-30")
    finally:
        conn.close()


def test_sources_overview_excludes_derived_and_deprecated(monkeypatch):
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE dim_data_asset (
                table_name TEXT,
                upstream_source TEXT,
                source_tier INTEGER,
                deprecation_status TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_data_health (
                table_name TEXT,
                snapshot_at TEXT,
                row_count INTEGER,
                severity TEXT,
                freshness_hours DOUBLE
            )
            """
        )
        conn.executemany(
            "INSERT INTO dim_data_asset VALUES (?, ?, ?, ?)",
            [
                ("raw_lhb_daily", "aif10:RPT_DAILYBILLBOARD_DETAILSNEW", 2, "active"),
                ("fact_feature_panel", "derived: kline + 财务", 99, "active"),
                ("legacy_hsgt", "akshare:stale_hsgt", 3, "deprecated"),
            ],
        )
        conn.executemany(
            "INSERT INTO mart_data_health VALUES (?, '2026-05-04T12:00:00', ?, ?, ?)",
            [
                ("raw_lhb_daily", 10, "green", 0),
                ("fact_feature_panel", 10, "red", 120),
                ("legacy_hsgt", 10, "red", 999),
            ],
        )
        monkeypatch.setattr(data_health_router, "get_conn", lambda: conn)

        result = data_health_router.get_sources_overview()

        assert result["sources"] == [
            {
                "upstream_source": "aif10:RPT_DAILYBILLBOARD_DETAILSNEW",
                "source_tier": 2,
                "asset_count": 1,
                "total_rows": 10,
                "red_count": 0,
                "yellow_count": 0,
                "green_count": 1,
                "max_freshness_h": 0.0,
            }
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass
