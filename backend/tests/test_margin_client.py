"""融资融券日度同步测试。"""

import asyncio
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import margin_client


def _sh_df():
    return pd.DataFrame([{
        "信用交易日期": "20260421",
        "标的证券代码": "600519",
        "标的证券简称": "贵州茅台",
        "融资余额": 1_200_000_000,
        "融资买入额": 80_000_000,
        "融资偿还额": 60_000_000,
        "融券余量": 1000,
        "融券卖出量": 500,
        "融券偿还量": 200,
    }])


def _sz_df():
    return pd.DataFrame([{
        "证券代码": "000001",
        "证券简称": "平安银行",
        "融资买入额": 84_840_010,
        "融资余额": 5_396_379_449,
        "融券卖出量": 88_400,
        "融券余量": 1_241_560,
        "融券余额": 13_756_485,
        "融资融券余额": 5_410_135_934,
    }])


def test_normalize_sh_maps_fields():
    rows = margin_client._normalize_sh(_sh_df(), "2026-04-21")
    assert len(rows) == 1
    r = rows[0]
    assert r["market"] == "SH"
    assert r["stock_code"] == "600519"
    assert r["rz_balance"] == 1_200_000_000
    assert r["rz_repay"] == 60_000_000
    assert r["rq_shares"] == 1000
    assert r["rq_balance"] is None
    assert r["rzrq_balance"] is None
    assert r["source"] == margin_client.MARGIN_SOURCE_SH


def test_normalize_sz_maps_fields_and_computes_rzrq_when_missing():
    df = _sz_df().copy()
    df.loc[0, "融资融券余额"] = None
    rows = margin_client._normalize_sz(df, "2026-04-21")
    assert len(rows) == 1
    r = rows[0]
    assert r["market"] == "SZ"
    assert r["stock_code"] == "000001"
    assert r["rzrq_balance"] == 5_396_379_449 + 13_756_485
    assert r["source"] == margin_client.MARGIN_SOURCE_SZ


def test_sync_margin_day_upserts_both_markets(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    margin_client.ensure_tables(conn)

    async def _fake_fetch(yyyymmdd: str, retries: int = 3):
        assert yyyymmdd == "20260421"
        return {"sh": _sh_df(), "sz": _sz_df()}

    monkeypatch.setattr(margin_client, "fetch_margin_day", _fake_fetch)

    result = asyncio.run(margin_client.sync_margin_day(conn, "2026-04-21"))
    assert result["status"] == "ok"
    assert result["written_rows"] == 2
    assert result["sh_rows"] == 1
    assert result["sz_rows"] == 1

    rows = conn.execute(
        "SELECT stock_code, market, rz_balance FROM raw_margin_daily ORDER BY stock_code"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["stock_code"] == "000001"
    assert rows[0]["market"] == "SZ"
    assert rows[1]["stock_code"] == "600519"
    assert rows[1]["market"] == "SH"


def test_sync_margin_day_is_idempotent(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    margin_client.ensure_tables(conn)

    async def _fake_fetch(yyyymmdd: str, retries: int = 3):
        return {"sh": _sh_df(), "sz": _sz_df()}

    monkeypatch.setattr(margin_client, "fetch_margin_day", _fake_fetch)
    asyncio.run(margin_client.sync_margin_day(conn, "2026-04-21"))
    asyncio.run(margin_client.sync_margin_day(conn, "2026-04-21"))

    assert conn.execute("SELECT COUNT(*) FROM raw_margin_daily").fetchone()[0] == 2


def test_sync_margin_day_survives_single_market_failure(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    margin_client.ensure_tables(conn)

    async def _fake_fetch(yyyymmdd: str, retries: int = 3):
        # SH 失败（返回 None），SZ 正常
        return {"sh": None, "sz": _sz_df()}

    monkeypatch.setattr(margin_client, "fetch_margin_day", _fake_fetch)
    result = asyncio.run(margin_client.sync_margin_day(conn, "2026-04-21"))
    # 仍有 SZ 写入，status 为 ok
    assert result["status"] == "ok"
    assert result["written_rows"] == 1
    assert result["sh_rows"] == 0
    assert result["sz_rows"] == 1


def test_backfill_margin_skips_existing_and_honors_calendar(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    margin_client.ensure_tables(conn)
    conn.executescript(
        """
        CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER);
        INSERT INTO dim_trading_calendar VALUES
            ('2026-04-20', 1),
            ('2026-04-21', 1),
            ('2026-04-22', 1);
        """
    )
    # 预先写入 04-20 的数据（模拟已有）
    conn.execute(
        "INSERT INTO raw_margin_daily (trade_date, stock_code, market, rz_balance, source) VALUES (?, ?, ?, ?, ?)",
        ("2026-04-20", "000001", "SZ", 1.0, "test"),
    )
    conn.commit()

    calls = []

    async def _fake_fetch(yyyymmdd: str, retries: int = 3):
        calls.append(yyyymmdd)
        return {"sh": _sh_df(), "sz": _sz_df()}

    monkeypatch.setattr(margin_client, "fetch_margin_day", _fake_fetch)

    result = asyncio.run(margin_client.backfill_margin_history(
        conn, "2026-04-20", "2026-04-22", skip_existing=True,
    ))
    assert result["status"] == "ok"
    assert result["days"] == 3
    assert result["days_run"] == 2
    assert result["days_skipped"] == 1
    assert calls == ["20260421", "20260422"]
    assert result["written_rows"] == 4  # 2 days × (1 SH + 1 SZ)


def test_updater_registers_sync_margin():
    from routers import updater
    step = next((s for s in updater.STEPS if s["id"] == "sync_margin"), None)
    assert step is not None and step["group"] == "data"
    assert updater.RUNNERS["sync_margin"] is updater._step_sync_margin


def _conn_with_calendar() -> sqlite3.Connection:
    """创建带 dim_trading_calendar 的 in-memory conn，供 fallback 链路测试使用。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    margin_client.ensure_tables(conn)
    conn.executescript(
        """
        CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER);
        INSERT INTO dim_trading_calendar VALUES
            ('2026-04-17', 1),
            ('2026-04-20', 1),
            ('2026-04-21', 1),
            ('2026-04-22', 1);
        """
    )
    conn.commit()
    return conn


def test_previous_trading_day_walks_calendar():
    conn = _conn_with_calendar()
    assert margin_client._previous_trading_day(conn, "2026-04-22") == "2026-04-21"
    assert margin_client._previous_trading_day(conn, "2026-04-20") == "2026-04-17"
    assert margin_client._previous_trading_day(conn, "2026-04-17") is None


def test_fallback_disabled_by_default(monkeypatch):
    conn = _conn_with_calendar()

    async def _fake_fetch(yyyymmdd: str, retries: int = 3):
        return {"sh": None, "sz": None}  # 双边失败

    monkeypatch.setattr(margin_client, "fetch_margin_day", _fake_fetch)
    result = asyncio.run(margin_client.sync_margin_day(conn, "2026-04-22"))
    assert result["status"] == "empty"
    assert result["written_rows"] == 0
    assert result["fallback_used"] is False


def test_fallback_walks_back_until_data_found(monkeypatch):
    conn = _conn_with_calendar()
    call_log = []

    async def _fake_fetch(yyyymmdd: str, retries: int = 3):
        call_log.append(yyyymmdd)
        # T (04-22) 和 T-1 (04-21) 源未披露，T-2 (04-20) 有数据
        if yyyymmdd == "20260420":
            return {"sh": _sh_df(), "sz": _sz_df()}
        return {"sh": None, "sz": None}

    monkeypatch.setattr(margin_client, "fetch_margin_day", _fake_fetch)

    result = asyncio.run(
        margin_client.sync_margin_day(conn, "2026-04-22", fallback_days=2)
    )
    assert result["fallback_used"] is True
    assert result["requested_date"] == "2026-04-22"
    assert result["trade_date"] == "2026-04-20"
    assert result["written_rows"] == 2  # 1 SH + 1 SZ 在 04-20 成功
    assert call_log == ["20260422", "20260421", "20260420"]


def test_fallback_gives_up_after_exhausting_budget(monkeypatch):
    conn = _conn_with_calendar()
    call_log = []

    async def _fake_fetch(yyyymmdd: str, retries: int = 3):
        call_log.append(yyyymmdd)
        return {"sh": None, "sz": None}  # 整条链全空

    monkeypatch.setattr(margin_client, "fetch_margin_day", _fake_fetch)

    result = asyncio.run(
        margin_client.sync_margin_day(conn, "2026-04-22", fallback_days=2)
    )
    # fallback 链：T → T-1 → T-2 （3 次调用），依然空，status=empty
    assert result["status"] == "empty"
    assert result["written_rows"] == 0
    # 注意最外层结果反映的是最深那一次的 trade_date
    assert result["trade_date"] == "2026-04-20"
    assert result["fallback_used"] is True
    assert call_log == ["20260422", "20260421", "20260420"]
