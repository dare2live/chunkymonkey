"""龙虎榜日度同步测试。"""

import asyncio
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import lhb_client


def _lhb_df():
    return pd.DataFrame([
        {
            "序号": 1, "代码": "000657", "名称": "中钨高新",
            "上榜日": "2026-04-20", "解读": "3家机构买入",
            "收盘价": 58.54, "涨跌幅": 4.3122,
            "龙虎榜净买额": 196107900, "龙虎榜买入额": 3420197000, "龙虎榜卖出额": 3224089000,
            "龙虎榜成交额": 6644285000, "市场总成交额": 25414170000,
            "净买额占总成交比": 0.77, "成交额占总成交比": 26.14,
            "换手率": 9.92, "流通市值": 85054250000,
            "上榜原因": "连续三个交易日内，涨幅偏离值累计达到20%的证券",
            "上榜后1日": -1.25, "上榜后2日": -0.70, "上榜后5日": None, "上榜后10日": None,
        },
        {
            "序号": 2, "代码": "000657", "名称": "中钨高新",
            "上榜日": "2026-04-20", "解读": "成交额大",
            "收盘价": 58.54, "涨跌幅": 4.3122,
            "龙虎榜净买额": 100_000_000, "龙虎榜买入额": 500_000_000, "龙虎榜卖出额": 400_000_000,
            "龙虎榜成交额": 900_000_000, "市场总成交额": 25_414_170_000,
            "净买额占总成交比": 0.40, "成交额占总成交比": 3.54,
            "换手率": 9.92, "流通市值": 85_054_250_000,
            "上榜原因": "日换手率达到20%的前5只证券",
            "上榜后1日": None, "上榜后2日": None, "上榜后5日": None, "上榜后10日": None,
        },
    ])


def test_normalize_rows_splits_by_rank_reason():
    rows = lhb_client._normalize_rows(_lhb_df())
    assert len(rows) == 2
    reasons = {r["rank_reason"] for r in rows}
    assert reasons == {
        "连续三个交易日内，涨幅偏离值累计达到20%的证券",
        "日换手率达到20%的前5只证券",
    }
    first = next(r for r in rows if "涨幅偏离值" in r["rank_reason"])
    assert first["stock_code"] == "000657"
    assert first["trade_date"] == "2026-04-20"
    assert first["net_buy"] == 196107900


def test_normalize_rows_raises_on_missing_column():
    try:
        lhb_client._normalize_rows(pd.DataFrame([{"代码": "000001"}]))
    except RuntimeError as exc:
        assert "lhb_columns_missing" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_sync_lhb_range_upserts_rows(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    lhb_client.ensure_tables(conn)

    async def _fake_fetch(s, e, retries: int = 3):
        assert s == "20260418"
        assert e == "20260421"
        return _lhb_df()

    monkeypatch.setattr(lhb_client, "fetch_lhb_range", _fake_fetch)

    result = asyncio.run(lhb_client.sync_lhb_range(conn, "2026-04-18", "2026-04-21"))
    assert result["status"] == "ok"
    assert result["written_rows"] == 2

    rows = conn.execute(
        "SELECT stock_code, rank_reason, net_buy, post_1d, source FROM raw_lhb_daily ORDER BY rank_reason"
    ).fetchall()
    assert len(rows) == 2
    assert all(r["source"] == lhb_client.LHB_SOURCE for r in rows)


def test_sync_lhb_is_idempotent(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    lhb_client.ensure_tables(conn)

    async def _fake_fetch(s, e, retries: int = 3):
        return _lhb_df()

    monkeypatch.setattr(lhb_client, "fetch_lhb_range", _fake_fetch)
    asyncio.run(lhb_client.sync_lhb_range(conn, "2026-04-18", "2026-04-21"))
    asyncio.run(lhb_client.sync_lhb_range(conn, "2026-04-18", "2026-04-21"))

    assert conn.execute("SELECT COUNT(*) FROM raw_lhb_daily").fetchone()[0] == 2


def test_sync_lhb_range_handles_source_failure(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    lhb_client.ensure_tables(conn)

    async def _fake_fetch(s, e, retries: int = 3):
        raise RuntimeError("lhb_source_failed:boom")

    monkeypatch.setattr(lhb_client, "fetch_lhb_range", _fake_fetch)

    result = asyncio.run(lhb_client.sync_lhb_range(conn, "2026-04-18", "2026-04-21"))
    assert result["status"] == "source_unavailable"
    assert result["written_rows"] == 0


def test_monthly_windows_split_correctly():
    windows = lhb_client._iter_monthly_windows("2025-11-15", "2026-02-03")
    assert windows == [
        ("2025-11-15", "2025-11-30"),
        ("2025-12-01", "2025-12-31"),
        ("2026-01-01", "2026-01-31"),
        ("2026-02-01", "2026-02-03"),
    ]


def test_backfill_iterates_months(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    lhb_client.ensure_tables(conn)

    calls = []

    async def _fake_fetch(s, e, retries: int = 3):
        calls.append((s, e))
        df = _lhb_df().copy()
        # 让每个月的 trade_date 落在当月，避免不同月份主键冲突
        df.loc[:, "上榜日"] = f"{s[:4]}-{s[4:6]}-15"
        # 让 reason 在不同月也不冲突（同原因但不同日期会自然区分）
        return df

    monkeypatch.setattr(lhb_client, "fetch_lhb_range", _fake_fetch)

    result = asyncio.run(lhb_client.backfill_lhb_history(conn, "2026-01-01", "2026-03-31"))
    assert result["status"] == "ok"
    assert len(calls) == 3  # Jan + Feb + Mar
    assert result["written_rows"] == 6  # 3 months × 2 reasons


def test_updater_registers_sync_lhb():
    from routers import updater
    step = next((s for s in updater.STEPS if s["id"] == "sync_lhb"), None)
    assert step is not None and step["group"] == "data"
    assert updater.RUNNERS["sync_lhb"] is updater._step_sync_lhb
