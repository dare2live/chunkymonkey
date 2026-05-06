import asyncio
from datetime import datetime

from conftest import duck_mem
from routers import updater


def test_sync_calendar_is_first_and_registered():
    assert updater.STEPS[0]["id"] == "sync_calendar"
    assert updater.RUNNERS["sync_calendar"] is updater._step_sync_calendar
    assert updater.HARD_DEPS["sync_market_data"][0] == "sync_calendar"


def test_ensure_calendar_step_for_data_fetch_inserts_once():
    assert updater._ensure_calendar_step_for_data_fetch(["sync_market_data"]) == [
        "sync_calendar",
        "sync_market_data",
    ]
    assert updater._ensure_calendar_step_for_data_fetch(["sync_calendar", "sync_raw"]) == [
        "sync_calendar",
        "sync_raw",
    ]
    assert updater._ensure_calendar_step_for_data_fetch(["calc_returns"]) == ["calc_returns"]


def test_trading_calendar_status_uses_existing_future_covered_calendar():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER);
            INSERT INTO dim_trading_calendar
            SELECT STRFTIME(DATE '2023-01-02' + CAST(i AS INTEGER), '%Y-%m-%d'), 1
              FROM range(1500) t(i);
            """
        )

        status = updater._trading_calendar_status(conn, now=datetime(2026, 5, 5, 22, 0))

        assert status["needs_refresh"] is False
        assert status["latest_completed_trade_date"] == "2026-05-05"
    finally:
        conn.close()


def test_trading_calendar_status_requests_refresh_when_coverage_is_short():
    conn = duck_mem()
    try:
        conn.executescript(
            """
            CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER);
            INSERT INTO dim_trading_calendar VALUES ('2026-04-30', 1);
            """
        )

        status = updater._trading_calendar_status(conn, now=datetime(2026, 5, 5, 22, 0))

        assert status["needs_refresh"] is True
        assert "rows<" in status["reason"]
        assert "max_date<" in status["reason"]
    finally:
        conn.close()


def test_step_sync_calendar_refreshes_when_needed(monkeypatch):
    conn = duck_mem()
    try:
        async def _fake_fetch_trading_calendar():
            return [
                (datetime(2023, 1, 2) + updater.timedelta(days=i)).strftime("%Y-%m-%d")
                for i in range(1500)
            ]

        import services.akshare_client as akshare_client

        monkeypatch.setattr(akshare_client, "fetch_trading_calendar", _fake_fetch_trading_calendar)

        result = asyncio.run(updater._step_sync_calendar(conn))

        assert result["status"] == "completed"
        assert result["count"] == 1500
        assert result["calendar"]["needs_refresh"] is False
    finally:
        conn.close()
