import asyncio
import sqlite3
import sys
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import updater
from services import northbound_client


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fact_northbound_daily (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            hold_shares REAL,
            hold_market_cap REAL,
            hold_ratio REAL,
            change_shares REAL,
            trade_date TEXT NOT NULL,
            updated_at TEXT,
            PRIMARY KEY (stock_code, trade_date)
        );
        """
    )
    return conn


def test_sync_northbound_daily_upserts_rows_and_derives_change_shares(monkeypatch):
    conn = _make_conn()
    try:
        conn.execute(
            "INSERT INTO fact_northbound_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("600001", "样本一", 100.0, 500.0, 1.2, 8.0, "2025-05-06", "2025-05-06T10:00:00"),
        )
        conn.commit()

        async def _fake_fetch(start_date, end_date, *, retries=2, timeout=45):
            assert start_date == "2025-05-07"
            assert end_date == "2025-05-08"
            return pd.DataFrame(
                [
                    {
                        "持股日期": "2025-05-07",
                        "股票代码": "600001",
                        "股票简称": "样本一",
                        "持股数量": 112.0,
                        "持股市值": 560.0,
                        "持股数量占发行股百分比": 1.35,
                    },
                    {
                        "持股日期": "2025-05-08",
                        "股票代码": "600001",
                        "股票简称": "样本一",
                        "持股数量": 120.0,
                        "持股市值": 590.0,
                        "持股数量占发行股百分比": 1.42,
                    },
                    {
                        "持股日期": "2025-05-08",
                        "股票代码": "600002",
                        "股票简称": "样本二",
                        "持股数量": 50.0,
                        "持股市值": 210.0,
                        "持股数量占发行股百分比": 0.88,
                    },
                    {
                        "持股日期": "2025-05-08",
                        "股票代码": "900001",
                        "股票简称": "应过滤",
                        "持股数量": 999.0,
                        "持股市值": 999.0,
                        "持股数量占发行股百分比": 9.99,
                    },
                ]
            )

        monkeypatch.setattr(northbound_client, "fetch_northbound_statistics", _fake_fetch)

        result = asyncio.run(
            northbound_client.sync_northbound_daily(
                conn,
                start_date="2025-05-07",
                end_date="2025-05-08",
                active_codes={"600001", "600002"},
            )
        )

        assert result["status"] == "success"
        assert result["written_rows"] == 3
        assert result["trade_dates"] == ["2025-05-07", "2025-05-08"]

        rows = conn.execute(
            "SELECT stock_code, trade_date, hold_shares, change_shares FROM fact_northbound_daily ORDER BY stock_code, trade_date"
        ).fetchall()
        assert len(rows) == 4
        assert rows[1][0] == "600001"
        assert rows[1][1] == "2025-05-07"
        assert rows[1][3] == 12.0
        assert rows[2][0] == "600001"
        assert rows[2][1] == "2025-05-08"
        assert rows[2][3] == 8.0
        assert rows[3][0] == "600002"
        assert rows[3][3] is None
    finally:
        conn.close()


def test_sync_northbound_daily_returns_empty_when_source_has_no_rows(monkeypatch):
    conn = _make_conn()
    try:
        async def _fake_fetch(start_date, end_date, *, retries=2, timeout=45):
            return pd.DataFrame(columns=[
                "持股日期",
                "股票代码",
                "股票简称",
                "持股数量",
                "持股市值",
                "持股数量占发行股百分比",
            ])

        monkeypatch.setattr(northbound_client, "fetch_northbound_statistics", _fake_fetch)

        result = asyncio.run(
            northbound_client.sync_northbound_daily(
                conn,
                start_date="2025-05-07",
                end_date="2025-05-08",
                active_codes={"600001"},
            )
        )

        assert result["status"] == "empty"
        assert result["written_rows"] == 0
        assert conn.execute("SELECT COUNT(*) FROM fact_northbound_daily").fetchone()[0] == 0
    finally:
        conn.close()


def test_sync_northbound_daily_returns_source_unavailable_when_fetch_fails(monkeypatch):
    conn = _make_conn()
    try:
        async def _fake_fetch(start_date, end_date, *, retries=2, timeout=45):
            raise RuntimeError("northbound_source_failed: boom")

        monkeypatch.setattr(northbound_client, "fetch_northbound_statistics", _fake_fetch)

        result = asyncio.run(
            northbound_client.sync_northbound_daily(
                conn,
                start_date="2025-05-07",
                end_date="2025-05-08",
                active_codes={"600001"},
            )
        )

        assert result["status"] == "source_unavailable"
        assert result["written_rows"] == 0
        assert "northbound_source_failed" in result["error"]
        assert conn.execute("SELECT COUNT(*) FROM fact_northbound_daily").fetchone()[0] == 0
    finally:
        conn.close()


def test_updater_registers_sync_northbound_step():
    step = next(item for item in updater.STEPS if item["id"] == "sync_northbound")

    assert step["group"] == "data"
    assert updater.RUNNERS["sync_northbound"] is updater._step_sync_northbound
