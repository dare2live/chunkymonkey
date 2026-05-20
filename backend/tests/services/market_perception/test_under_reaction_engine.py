from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from services.market_perception.under_reaction_engine import compute_under_reaction_for_range


def _make_conn(snapshot: date):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE dim_trading_calendar (trade_date TEXT PRIMARY KEY, is_trading INTEGER)")
    conn.execute(
        """
        CREATE TABLE fact_capital_flow_pit_daily (
            stock_code TEXT,
            trade_date TEXT,
            lhb_count_30d INTEGER,
            lhb_net_buy_pct_30d DOUBLE,
            lhb_inst_buy_30d INTEGER,
            exec_net_signal DOUBLE,
            holder_count_change_q_pct DOUBLE
        )
        """
    )
    days = [snapshot - timedelta(days=i) for i in range(35)]
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
        for offset, day in enumerate(sorted(days)):
            close = 10 + code_idx
            if code == "000002" and day >= snapshot - timedelta(days=5):
                close += 5
            amount = 1000 + offset * (5 if code == "000001" else 1)
            rows.append((code, day.isoformat(), close, amount))
        conn.executemany("INSERT INTO fact_stock_kline_daily VALUES (?, ?, ?, ?)", rows)
    flow_rows = [
        ("000001", snapshot.isoformat(), 5, 80.0, 4, 1.0, -10.0),
        ("000002", snapshot.isoformat(), 5, 90.0, 4, 1.0, -10.0),
        ("000003", snapshot.isoformat(), 0, 0.0, 0, 0.0, 0.0),
        ("000004", (snapshot + timedelta(days=1)).isoformat(), 20, 99.0, 20, 1.0, -30.0),
    ]
    conn.executemany("INSERT INTO fact_capital_flow_pit_daily VALUES (?, ?, ?, ?, ?, ?, ?)", flow_rows)
    return conn


def test_under_reaction_prefers_fund_flow_without_price_reaction():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)

    frame = compute_under_reaction_for_range(conn, snapshot, snapshot, top_n=4)
    ordered = frame.sort_values("under_reaction_score", ascending=False)

    assert ordered.iloc[0]["stock_code"] == "000001"
    assert float(ordered.iloc[0]["under_reaction_score"]) > float(ordered[ordered["stock_code"] == "000002"].iloc[0]["under_reaction_score"])


def test_under_reaction_no_lookahead_capital_flow():
    snapshot = date(2026, 5, 18)
    conn = _make_conn(snapshot)

    frame = compute_under_reaction_for_range(conn, snapshot, snapshot, top_n=10)

    assert "000004" not in set(frame["stock_code"])


def test_under_reaction_today_excluded():
    today = date.today()
    conn = _make_conn(today)

    with pytest.raises(ValueError, match="today/future"):
        compute_under_reaction_for_range(conn, today, today)
