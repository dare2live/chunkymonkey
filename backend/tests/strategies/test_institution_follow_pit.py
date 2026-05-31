"""PIT injection tests for Scheme 7 institution-follow alpha modules."""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pandas as pd
import pytest

from services.strategies.institution_follow.capital_flow_alpha import CapitalFlowAlpha
from services.strategies.institution_follow.lhb_alpha import LHBAlpha
from services.strategies.institution_follow.northbound_alpha import NorthboundAlpha
from services.strategies.institution_follow.survey_alpha import SurveyAlpha


def _assert_same(before: pd.DataFrame, after: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(
        before.sort_index(axis=1).reset_index(drop=True),
        after.sort_index(axis=1).reset_index(drop=True),
        check_dtype=False,
        check_exact=False,
        atol=1e-12,
        rtol=1e-12,
    )


def _dates(start: str, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


@pytest.fixture
def conn():
    con = duckdb.connect(":memory:")
    try:
        yield con
    finally:
        con.close()


def test_lhb_alpha_ignores_future_event_rows(conn):
    conn.execute("""
        CREATE TABLE fact_lhb_event (
            trade_date VARCHAR,
            stock_code VARCHAR,
            net_buy DOUBLE,
            net_buy_pct DOUBLE,
            is_inst_net_buy INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE v_price_kline_qfq (
            code VARCHAR,
            date VARCHAR,
            freq VARCHAR,
            adjust VARCHAR,
            close DOUBLE
        )
    """)
    rows = []
    for i, d in enumerate(_dates("2024-05-01", 80)):
        rows.append(("000300", d, "daily", "qfq", 1000.0 + i))
        rows.append(("600000", d, "daily", "qfq", 10.0 + i * 0.05))
    conn.executemany("INSERT INTO v_price_kline_qfq VALUES (?, ?, ?, ?, ?)", rows)
    conn.executemany(
        "INSERT INTO fact_lhb_event VALUES (?, ?, ?, ?, ?)",
        [
            ("2024-05-06", "600000", 20_000_000.0, 3.0, 1),
            ("2024-06-03", "600000", 10_000_000.0, 1.5, 1),
        ],
    )

    alpha = LHBAlpha(conn=conn, price_table="v_price_kline_qfq")
    before = alpha.get_features("2024-06-15", ["600000"])
    conn.execute(
        "INSERT INTO fact_lhb_event VALUES (?, ?, ?, ?, ?)",
        ["2024-07-01", "600000", 999_000_000.0, 99.0, 1],
    )
    after = alpha.get_features("2024-06-15", ["600000"])

    _assert_same(before, after)


def test_capital_flow_alpha_ignores_deprecated_raw_flow_without_pit(conn):
    conn.execute("""
        CREATE TABLE raw_fund_flow_daily (
            trade_date VARCHAR,
            stock_code VARCHAR,
            main_net_amount DOUBLE,
            main_net_pct DOUBLE,
            super_large_net_amount DOUBLE,
            large_net_amount DOUBLE,
            small_net_amount DOUBLE
        )
    """)
    conn.executemany(
        "INSERT INTO raw_fund_flow_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("2024-06-10", "600000", 5_000_000.0, 1.0, 2_000_000.0, 3_000_000.0, -4_000_000.0),
            ("2024-06-14", "600000", 7_000_000.0, 2.0, 1_000_000.0, 2_000_000.0, -2_000_000.0),
        ],
    )

    alpha = CapitalFlowAlpha(conn=conn)
    before = alpha.get_features("2024-06-15", ["600000"])
    conn.execute(
        "INSERT INTO raw_fund_flow_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["2024-06-20", "600000", 900_000_000.0, 90.0, 500_000_000.0, 400_000_000.0, -1.0],
    )
    after = alpha.get_features("2024-06-15", ["600000"])

    _assert_same(before, after)
    assert before.iloc[0]["capital_flow_score"] == 0.0
    assert before.iloc[0]["main_inflow_5d"] == 0.0


def test_capital_flow_alpha_prefers_pit_proxy_over_deprecated_raw_flow(conn):
    conn.execute("""
        CREATE TABLE raw_fund_flow_daily (
            trade_date VARCHAR,
            stock_code VARCHAR,
            main_net_amount DOUBLE,
            main_net_pct DOUBLE,
            super_large_net_amount DOUBLE,
            large_net_amount DOUBLE,
            small_net_amount DOUBLE
        )
    """)
    conn.execute("""
        CREATE TABLE fact_capital_flow_pit_daily (
            stock_code VARCHAR,
            trade_date VARCHAR,
            lhb_net_buy_pct_30d DOUBLE,
            exec_buy_pct_60d DOUBLE,
            exec_sell_pct_60d DOUBLE,
            exec_net_signal DOUBLE
        )
    """)
    conn.execute(
        "INSERT INTO raw_fund_flow_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["2024-06-14", "600000", 900_000_000.0, 90.0, 500_000_000.0, 400_000_000.0, -1.0],
    )
    conn.execute(
        "INSERT INTO fact_capital_flow_pit_daily VALUES (?, ?, ?, ?, ?, ?)",
        ["600000", "2024-06-14", 3.0, 0.8, 0.2, 2.0],
    )

    features = CapitalFlowAlpha(conn=conn).get_features("2024-06-15", ["600000"])

    row = features.iloc[0]
    assert row["capital_main_net_amount_5d"] == pytest.approx(2.0)
    assert row["capital_main_net_pct_5d"] == pytest.approx(3.0)
    assert row["capital_inst_net_amount_5d"] == pytest.approx(0.6)


def test_survey_alpha_ignores_future_disclosure_rows(conn):
    conn.execute("""
        CREATE TABLE raw_institution_surveys (
            stock_code VARCHAR,
            survey_date VARCHAR,
            notice_date VARCHAR,
            inst_count BIGINT
        )
    """)
    conn.executemany(
        "INSERT INTO raw_institution_surveys VALUES (?, ?, ?, ?)",
        [
            ("600000", "2024-06-01", "2024-06-02", 10),
            ("600000", "2024-06-05", "2024-06-06", 20),
        ],
    )

    alpha = SurveyAlpha(conn=conn)
    before = alpha.get_features("2024-06-15", ["600000"])
    conn.execute(
        "INSERT INTO raw_institution_surveys VALUES (?, ?, ?, ?)",
        ["600000", "2024-06-10", "2024-06-20", 999],
    )
    after = alpha.get_features("2024-06-15", ["600000"])

    _assert_same(before, after)


def test_northbound_alpha_ignores_future_snapshot_rows(conn):
    conn.execute("""
        CREATE TABLE fact_hsgt_daily (
            snapshot_date VARCHAR,
            stock_code VARCHAR,
            hold_market_value DOUBLE,
            hold_pct_of_float DOUBLE
        )
    """)
    conn.executemany(
        "INSERT INTO fact_hsgt_daily VALUES (?, ?, ?, ?)",
        [
            ("20240501", "600000", 100_000_000.0, 1.5),
            ("20240601", "600000", 120_000_000.0, 1.8),
        ],
    )

    alpha = NorthboundAlpha(conn=conn)
    before = alpha.get_features("2024-06-15", ["600000"])
    conn.execute(
        "INSERT INTO fact_hsgt_daily VALUES (?, ?, ?, ?)",
        ["20240701", "600000", 900_000_000.0, 9.9],
    )
    after = alpha.get_features("2024-06-15", ["600000"])

    _assert_same(before, after)
