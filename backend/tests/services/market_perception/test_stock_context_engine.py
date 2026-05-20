from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from services.market_perception.stock_context_engine import compute_stock_context_for_range


def _make_conn(snapshot: date):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
    conn.executemany(
        "INSERT INTO dim_trading_calendar VALUES (?, 1)",
        [((snapshot - timedelta(days=i)).isoformat(),) for i in range(5)],
    )
    conn.execute(
        """
        CREATE TABLE mart_market_perception_under_reaction_daily (
            snapshot_date DATE,
            stock_code TEXT,
            under_reaction_score DOUBLE,
            fund_anomaly_score DOUBLE,
            theme_name TEXT,
            theme_score DOUBLE,
            lifecycle_stage TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO mart_market_perception_under_reaction_daily VALUES (?, '000001', 0.60, 0.70, '信息产业', 0.80, '高潮')",
        [snapshot.isoformat()],
    )
    conn.execute("CREATE TABLE mart_market_perception_daily (snapshot_date DATE, regime_score DOUBLE)")
    conn.execute("INSERT INTO mart_market_perception_daily VALUES (?, 0.20)", [snapshot.isoformat()])
    conn.execute("CREATE TABLE mart_market_perception_emotion_daily (snapshot_date DATE, emotion_score DOUBLE, emotion_state TEXT)")
    conn.execute("INSERT INTO mart_market_perception_emotion_daily VALUES (?, 0.30, '赚钱效应扩张')", [snapshot.isoformat()])
    conn.execute(
        """
        CREATE TABLE mart_market_perception_leader_follower_daily (
            snapshot_date DATE,
            follower_stock_code TEXT,
            leader_stock_code TEXT,
            diffusion_score DOUBLE
        )
        """
    )
    conn.execute("INSERT INTO mart_market_perception_leader_follower_daily VALUES (?, '000001', '000002', 0.50)", [snapshot.isoformat()])
    conn.execute(
        """
        CREATE TABLE mart_market_perception_style_daily (
            snapshot_date DATE,
            style_rotation_score DOUBLE,
            style_bias TEXT,
            crowding_risk_score DOUBLE,
            overheat_reversal_risk DOUBLE
        )
        """
    )
    conn.execute("INSERT INTO mart_market_perception_style_daily VALUES (?, 0.10, '小盘/趋势', 0.20, 0.05)", [snapshot.isoformat()])
    return conn


def test_stock_context_aggregates_existing_engines():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)

    frame = compute_stock_context_for_range(conn, snapshot, snapshot, limit=10)
    row = frame.iloc[0]

    assert row["stock_code"] == "000001"
    assert row["leader_stock_code"] == "000002"
    assert float(row["context_score"]) > 0.25
    assert float(row["data_completeness_score"]) == 1.0


def test_stock_context_keeps_missing_global_context_as_missing_not_forward_filled():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)
    conn.execute("DELETE FROM mart_market_perception_daily")
    conn.execute("DELETE FROM mart_market_perception_emotion_daily")

    frame = compute_stock_context_for_range(conn, snapshot, snapshot, limit=10)
    row = frame.iloc[0]

    assert "market_regime_score" in row["missing_context_fields"]
    assert "emotion_score" in row["missing_context_fields"]
    assert float(row["data_completeness_score"]) < 1.0


def test_stock_context_uses_trading_calendar_gate():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)
    non_trading = snapshot - timedelta(days=1)
    conn.execute("UPDATE dim_trading_calendar SET is_trading = 0 WHERE trade_date = ?", [non_trading.isoformat()])
    conn.execute(
        "INSERT INTO mart_market_perception_under_reaction_daily VALUES (?, '000003', 0.9, 0.9, '材料', 0.5, '启动')",
        [non_trading.isoformat()],
    )

    frame = compute_stock_context_for_range(conn, non_trading, snapshot, limit=10)

    assert non_trading.isoformat() not in set(frame["snapshot_date"])


def test_stock_context_today_excluded():
    today = date.today()
    conn = _make_conn(today)

    with pytest.raises(ValueError, match="today/future"):
        compute_stock_context_for_range(conn, today, today)
