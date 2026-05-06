import asyncio
from pathlib import Path

import pytest

from fixtures.mini_market import insert_fallback_kline, insert_primary_kline, mini_market_conn
from services import akshare_client
from services.market_db import CANONICAL_KLINE_QFQ_RELATION
from services.source_policy import get_capability_policy


pytestmark = pytest.mark.contract


def _row(date: str) -> list[dict]:
    return [
        {
            "date": date,
            "open": 10.0,
            "high": 11.0,
            "low": 9.0,
            "close": 10.5,
            "volume": 1000.0,
            "amount": 10500.0,
        }
    ]


def test_stock_daily_qfq_policy_is_tdxhub_primary_with_canonical_relation():
    policy = get_capability_policy("kline_daily")

    assert policy.primary == "tdxhub"
    assert policy.fallback == ("akshare_multi_source",)
    assert policy.canonical_relation == "market.v_price_kline_qfq"
    assert CANONICAL_KLINE_QFQ_RELATION == "market.v_price_kline_qfq"
    assert policy.require_fallback_lineage is True


def test_canonical_qfq_uses_fallback_only_for_missing_primary_keys():
    conn = mini_market_conn()
    try:
        insert_primary_kline(
            conn,
            [
                ("000001", "2026-05-04", 10, 11, 9, 10.5, 1000, 10500, "tdxhub", "tdx-1", "2026-05-04"),
            ],
        )
        insert_fallback_kline(
            conn,
            [
                ("000001", "2026-05-04", 99, 99, 99, 99.0, 9999, 9999, "eastmoney", "fb-1", "2026-05-04"),
                ("000001", "2026-05-05", 11, 12, 10, 11.5, 1100, 12650, "eastmoney", "fb-2", "2026-05-05"),
            ],
        )

        rows = conn.execute(
            """
            SELECT code, date, close, source_name, source_tier, is_fallback
              FROM v_price_kline_qfq
             ORDER BY date
            """
        ).fetchall()
        duplicate_keys = conn.execute(
            """
            SELECT COUNT(*)
              FROM (
                SELECT code, date, freq, adjust, COUNT(*) AS n
                  FROM v_price_kline_qfq
                 GROUP BY code, date, freq, adjust
                HAVING COUNT(*) > 1
              )
            """
        ).fetchone()[0]

        assert [tuple(row) for row in rows] == [
            ("000001", "2026-05-04", 10.5, "tdxhub", 1, False),
            ("000001", "2026-05-05", 11.5, "eastmoney", 3, True),
        ]
        assert duplicate_keys == 0
    finally:
        conn.close()


def test_stock_daily_fetch_attempts_tdxhub_before_fallback_by_default(monkeypatch):
    calls = []

    async def fake_tdxhub(code, start_date, end_date):
        calls.append("tdxhub")
        return _row("2026-04-10"), "tdxhub", {"ok": True, "summary": "tdxhub healthy"}

    async def fake_fallback(*args, **kwargs):
        calls.append("fallback")
        return _row("2026-04-10"), "tx", {"ok": True}

    monkeypatch.setattr(akshare_client, "_fetch_daily_tdxhub_with_diagnostics", fake_tdxhub)
    monkeypatch.setattr(akshare_client, "_fetch_daily_akshare_fallbacks", fake_fallback)

    rows, source = asyncio.run(
        akshare_client._fetch_daily_with_fallback("000001", "20260401", "20260410")
    )

    assert rows == _row("2026-04-10")
    assert source == "tdxhub"
    assert calls == ["tdxhub"]


def test_core_feature_and_model_readers_resolve_canonical_kline_relation():
    repo = Path(__file__).resolve().parents[2]
    readers = [
        "scripts/build_feature_panel_duck.py",
        "scripts/run_daily_topk.py",
        "scripts/backtest_model_portfolio.py",
        "scripts/backtest_walkforward_portfolio.py",
        "scripts/build_alpha158_duck.py",
        "services/return_engine.py",
        "services/stock_turtle_engine.py",
        "services/screening_engine.py",
        "services/sector_momentum.py",
        "services/risk_factors.py",
    ]

    missing = []
    for relative_path in readers:
        text = (repo / relative_path).read_text(encoding="utf-8")
        if (
            "CANONICAL_KLINE_QFQ_RELATION" not in text
            and "KLINE_DAILY_QFQ_RELATION" not in text
            and "get_canonical_kline_qfq_relation" not in text
        ):
            missing.append(relative_path)

    assert missing == []
