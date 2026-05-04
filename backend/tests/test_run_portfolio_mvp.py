import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import run_portfolio_mvp as subject


def _days(start: date, count: int) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


def test_event_source_loaders_return_records():
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE dim_stock_tdx_industry (
                stock_code TEXT,
                tdx_l1_name TEXT,
                tdx_l2_name TEXT
            )
            """
        )
        con.execute(
            """
            CREATE TABLE fact_lhb_event (
                trade_date TEXT,
                stock_code TEXT,
                net_buy DOUBLE,
                inst_buy_seats INTEGER,
                is_inst_net_buy INTEGER
            )
            """
        )
        con.execute(
            """
            CREATE TABLE fact_executive_trade_event (
                notice_date TEXT,
                stock_code TEXT,
                n_shareholders INTEGER,
                total_change_pct_total DOUBLE,
                max_change_pct_total DOUBLE,
                any_individual INTEGER,
                any_corporate INTEGER,
                direction TEXT
            )
            """
        )
        con.executemany(
            "INSERT INTO dim_stock_tdx_industry VALUES (?, ?, ?)",
            [("000001", "finance", "bank")],
        )
        con.executemany(
            "INSERT INTO fact_lhb_event VALUES (?, ?, ?, ?, ?)",
            [("2024-01-03", "000001", 20_000_000.0, 3, 1)],
        )
        con.executemany(
            "INSERT INTO fact_executive_trade_event VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [("2024-01-04", "000001", 2, 1.2, 1.2, 1, 0, "buy")],
        )

        lhb_events = subject.load_lhb_events_as_events(con, "20240101", "20240131", min_inst_seats=1)
        exec_events = subject.load_exec_trade_events_as_events(con, "20240101", "20240131", min_pct_total=1.0)

        assert lhb_events[0]["institution_id"] == "LHB_000001"
        assert lhb_events[0]["notice_date"] == "20240103"
        assert lhb_events[0]["stable_score"] > 30
        assert exec_events[0]["institution_id"] == "EXEC_000001"
        assert exec_events[0]["notice_date"] == "20240104"
        assert exec_events[0]["stable_score"] == 1.2
    finally:
        con.close()


def test_simulate_portfolio_and_evaluate_records():
    trading_days = _days(date(2024, 1, 1), 8)
    prices = subject._build_price_history([
        {
            "code": "000001",
            "date": day,
            "open": 10.0 + idx,
            "high": 10.5 + idx,
            "low": 9.5 + idx,
            "close": 10.0 + idx,
        }
        for idx, day in enumerate(trading_days)
    ])
    events = [{
        "institution_id": "INST1",
        "stock_code": "000001",
        "notice_date": "20240101",
        "l2": "bank",
        "stable_score": 10.0,
        "entry_lag": 0,
        "max_hold_days": 2,
        "stop_loss": -0.9,
        "take_profit": 9.0,
    }]

    result = subject.simulate_portfolio(
        events,
        prices,
        trading_days,
        initial_capital=10_000.0,
        top_n=1,
        policy_filter=lambda row: row["stable_score"] > 0,
    )
    metrics = subject.evaluate(result)

    assert len(result["equity_curve"]) == len(trading_days)
    assert len(result["trades"]) == 1
    assert result["trades"][0]["exit_reason"] == "max_hold"
    assert metrics["n_trades"] == 1
    assert metrics["win_rate"] == 1.0
    assert metrics["final_equity"] > 10_000.0


def test_benchmark_buy_hold_hs300_records():
    trading_days = _days(date(2024, 1, 1), 3)
    prices = subject._build_price_history([
        {"code": "510300", "date": "2024-01-01", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
        {"code": "510300", "date": "2024-01-02", "open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1},
        {"code": "510300", "date": "2024-01-03", "open": 1.2, "high": 1.2, "low": 1.2, "close": 1.2},
    ])

    result = subject.benchmark_buy_hold_hs300(prices["510300"], trading_days, initial_capital=100.0)

    assert result["trades"] == []
    assert result["final_equity"] == 120.0
