from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from services.market_perception.leader_follower_engine import compute_leader_follower_for_range


def _make_conn(snapshot: date):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
    days = [snapshot - timedelta(days=i) for i in range(45)]
    conn.executemany("INSERT INTO dim_trading_calendar VALUES (?, 1)", [(d.isoformat(),) for d in days])
    conn.execute(
        """
        CREATE TABLE fact_stock_kline_daily (
            stock_code TEXT,
            trade_date TEXT,
            close DOUBLE,
            amount DOUBLE
        )
        """
    )
    codes = ["000001", "000002", "000003", "000004"]
    for code_idx, code in enumerate(codes):
        rows = []
        ordered_days = sorted(days)
        for offset, day in enumerate(ordered_days):
            close = 10.0 + code_idx
            if code == "000001" and day >= snapshot - timedelta(days=5):
                close += 0.30 * (offset - len(ordered_days) + 6)
            if code == "000002" and day >= snapshot - timedelta(days=1):
                close += 0.80
            if code == "000003" and day >= snapshot - timedelta(days=5):
                close -= 0.20
            amount = 1000.0 + offset
            if code == "000001":
                amount += 200.0
            if code == "000002" and day >= snapshot - timedelta(days=1):
                amount += 500.0
            rows.append((code, day.isoformat(), close, amount))
        conn.executemany("INSERT INTO fact_stock_kline_daily VALUES (?, ?, ?, ?)", rows)
    conn.execute(
        """
        CREATE TABLE mart_stock_industry_pit (
            stock_code TEXT,
            effective_from TEXT,
            effective_to TEXT,
            tdx_l1_name TEXT,
            confidence_level TEXT
        )
        """
    )
    pit_rows = [
        ("000001", (snapshot - timedelta(days=30)).isoformat(), snapshot.isoformat(), "信息产业", "observed_snapshot"),
        ("000002", (snapshot - timedelta(days=30)).isoformat(), snapshot.isoformat(), "信息产业", "observed_snapshot"),
        ("000003", (snapshot - timedelta(days=30)).isoformat(), snapshot.isoformat(), "信息产业", "observed_snapshot"),
        ("000004", (snapshot - timedelta(days=30)).isoformat(), snapshot.isoformat(), "信息产业", "current_label_fallback"),
    ]
    conn.executemany("INSERT INTO mart_stock_industry_pit VALUES (?, ?, ?, ?, ?)", pit_rows)
    return conn


def test_leader_follower_links_prior_leader_to_lagging_responder():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)

    frame = compute_leader_follower_for_range(conn, snapshot, snapshot, top_n=2)

    assert not frame.empty
    top = frame.sort_values("diffusion_score", ascending=False).iloc[0]
    assert top["leader_stock_code"] == "000001"
    assert top["follower_stock_code"] == "000002"
    assert float(top["diffusion_score"]) > 0.0


def test_leader_follower_excludes_current_label_fallback_members():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)

    frame = compute_leader_follower_for_range(conn, snapshot, snapshot, top_n=5)

    assert "000004" not in set(frame["leader_stock_code"])
    assert "000004" not in set(frame["follower_stock_code"])


def test_leader_follower_today_excluded():
    today = date.today()
    conn = _make_conn(today)

    with pytest.raises(ValueError, match="today/future"):
        compute_leader_follower_for_range(conn, today, today)


def test_leader_follower_uses_trading_calendar_as_primary_date_gate():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)
    non_trading = snapshot - timedelta(days=1)
    conn.execute("UPDATE dim_trading_calendar SET is_trading = 0 WHERE trade_date = ?", [non_trading.isoformat()])
    conn.execute(
        "INSERT INTO fact_stock_kline_daily VALUES (?, ?, ?, ?)",
        ["000001", non_trading.isoformat(), 99.0, 9999.0],
    )

    frame = compute_leader_follower_for_range(conn, non_trading, snapshot, top_n=5)

    assert non_trading.isoformat() not in set(frame["snapshot_date"])
