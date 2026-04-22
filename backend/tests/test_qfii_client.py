"""QFII 季度持股同步测试。"""

import asyncio
import sqlite3
import sys
from datetime import date
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import qfii_client


def _make_df(symbol: str) -> pd.DataFrame:
    """构造 stock_gdfx_holding_detail_em 单 symbol 的返回，模拟 UBS/摩根士丹利的 2025Q4 持仓。"""
    base = {
        "新进": {
            "序号": 1,
            "股东名称": "UBS AG",
            "股东类型": "QFII",
            "股票代码": "000411",
            "股票简称": "英特集团",
            "报告期": "2025-12-31",
            "期末持股-数量": 1978553,
            "期末持股-数量变化": None,
            "期末持股-数量变化比例": None,
            "期末持股-持股变动": "新进",
            "期末持股-流通市值": 25226550.75,
            "公告日": "2026-04-23",
            "股东排名": 9,
        },
        "增加": {
            "序号": 1,
            "股东名称": "MORGAN STANLEY",
            "股东类型": "QFII",
            "股票代码": "002218",
            "股票简称": "拓日新能",
            "报告期": "2025-12-31",
            "期末持股-数量": 5762088,
            "期末持股-数量变化": 800000,
            "期末持股-数量变化比例": 16.12,
            "期末持股-持股变动": "增加",
            "期末持股-流通市值": 25007461.92,
            "公告日": "2026-04-20",
            "股东排名": 10,
        },
    }
    return pd.DataFrame([base.get(symbol, base["新进"])])


def test_enumerate_quarter_ends_basic():
    out = qfii_client.enumerate_quarter_ends("2024-06-30", "2025-12-31")
    assert out == [
        "2024-06-30", "2024-09-30", "2024-12-31",
        "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
    ]


def test_latest_plannable_lag_honored():
    # 2026-04-22 时：距 Q4 已有 4 个月，Q4 可同步
    assert qfii_client.latest_plannable_report_date(today=date(2026, 4, 22)) == "2025-12-31"
    # 2026-01-10 时：距 Q4 仅 10 天 < 30，Q4 还不可同步，回退到 Q3
    assert qfii_client.latest_plannable_report_date(today=date(2026, 1, 10)) == "2025-09-30"


def test_normalize_rows_parses_required_columns():
    df = pd.concat([_make_df("新进"), _make_df("增加")], ignore_index=True)
    rows = qfii_client._normalize_rows(df)
    assert len(rows) == 2
    first = rows[0]
    assert first["stock_code"] == "000411"
    assert first["holder_name"] == "UBS AG"
    assert first["report_date"] == "2025-12-31"
    assert first["change_type"] == "新进"
    assert first["hold_shares"] == 1978553
    assert first["notice_date"] == "2026-04-23"


def test_normalize_rows_raises_on_missing_column():
    df = pd.DataFrame([{"股东名称": "UBS"}])
    try:
        qfii_client._normalize_rows(df)
    except RuntimeError as exc:
        assert "qfii_columns_missing" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_sync_qfii_quarter_upserts_all_symbols(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    qfii_client.ensure_tables(conn)

    calls = []

    async def _fake_fetch(report_date: str, retries: int = 3):
        calls.append(report_date)
        return pd.concat(
            [_make_df(s) for s in qfii_client.QFII_SYMBOLS[:2]],
            ignore_index=True,
        )

    monkeypatch.setattr(qfii_client, "fetch_qfii_quarter", _fake_fetch)

    result = asyncio.run(qfii_client.sync_qfii_quarter(conn, "2025-12-31"))

    assert result["status"] == "ok"
    assert result["written_rows"] == 2
    assert calls == ["2025-12-31"]

    rows = conn.execute(
        "SELECT stock_code, holder_name, change_type, notice_date, source "
        "FROM raw_qfii_holding_quarterly ORDER BY stock_code"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["stock_code"] == "000411"
    assert rows[0]["change_type"] == "新进"
    assert rows[0]["source"] == qfii_client.QFII_SOURCE
    assert rows[0]["notice_date"] == "2026-04-23"


def test_sync_qfii_quarter_is_idempotent(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    qfii_client.ensure_tables(conn)

    async def _fake_fetch(report_date: str, retries: int = 3):
        return _make_df("新进")

    monkeypatch.setattr(qfii_client, "fetch_qfii_quarter", _fake_fetch)

    asyncio.run(qfii_client.sync_qfii_quarter(conn, "2025-12-31"))
    asyncio.run(qfii_client.sync_qfii_quarter(conn, "2025-12-31"))

    count = conn.execute("SELECT COUNT(*) FROM raw_qfii_holding_quarterly").fetchone()[0]
    assert count == 1  # 重复同步不产生重复行


def test_sync_qfii_quarter_handles_source_failure(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    qfii_client.ensure_tables(conn)

    async def _fake_fetch(report_date: str, retries: int = 3):
        raise RuntimeError("qfii_source_failed:2025-12-31:新进:boom")

    monkeypatch.setattr(qfii_client, "fetch_qfii_quarter", _fake_fetch)

    result = asyncio.run(qfii_client.sync_qfii_quarter(conn, "2025-12-31"))
    assert result["status"] == "source_unavailable"
    assert result["written_rows"] == 0
    assert conn.execute("SELECT COUNT(*) FROM raw_qfii_holding_quarterly").fetchone()[0] == 0


def test_backfill_iterates_quarters(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    qfii_client.ensure_tables(conn)

    async def _fake_fetch(report_date: str, retries: int = 3):
        # 每个季度返回一个不同的 holder 以避免主键冲突
        df = _make_df("新进").copy()
        df.loc[0, "报告期"] = report_date
        df.loc[0, "股东名称"] = f"UBS AG {report_date}"
        return df

    monkeypatch.setattr(qfii_client, "fetch_qfii_quarter", _fake_fetch)

    result = asyncio.run(
        qfii_client.backfill_qfii_history(conn, "2025-03-31", "2025-12-31")
    )
    assert result["status"] == "ok"
    assert result["written_rows"] == 4  # Q1 + Q2 + Q3 + Q4
    quarters = conn.execute(
        "SELECT DISTINCT report_date FROM raw_qfii_holding_quarterly ORDER BY report_date"
    ).fetchall()
    assert [r["report_date"] for r in quarters] == [
        "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
    ]


def test_updater_registers_sync_qfii():
    from routers import updater
    step = next((s for s in updater.STEPS if s["id"] == "sync_qfii"), None)
    assert step is not None
    assert step["group"] == "data"
    assert updater.RUNNERS["sync_qfii"] is updater._step_sync_qfii
