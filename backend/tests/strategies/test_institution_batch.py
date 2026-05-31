"""Unit tests for the institution composite batch builder."""
from __future__ import annotations

import pandas as pd

from conftest import duck_mem
from scripts.build_institution_score_daily import (
    CLASS_SPECS,
    _source_available,
    compose_signal_date_scores,
    normalize_per_signal_date,
)
from services.strategies.institution_follow.lhb_alpha import LHBAlpha


def test_normalize_per_signal_date() -> None:
    df = pd.DataFrame({"stock_code": ["AAA", "BBB", "CCC"], "score": [10.0, 20.0, 30.0]})

    out = normalize_per_signal_date(df, "score", "score_norm")

    assert out["score_norm"].between(0.0, 1.0).all()
    assert out["score_norm"].tolist() == [0.0, 0.5, 1.0]


def test_composite_with_missing_class() -> None:
    universe = ["AAA", "BBB"]
    frames = {
        "lhb": pd.DataFrame({"stock_code": universe, "lhb_score": [0.0, 10.0]}),
        "capital_flow": pd.DataFrame({"stock_code": universe, "capital_flow_score": [10.0, 20.0]}),
        "survey": pd.DataFrame({"stock_code": universe, "survey_score": [5.0, 15.0]}),
        "northbound": pd.DataFrame(columns=["stock_code", "northbound_score"]),
    }

    out = compose_signal_date_scores("2024-07-01", universe, frames, built_at="test")

    assert out["n_classes_eligible"].tolist() == [3, 3]
    assert out["composite_score"].tolist() == [0.0, 1.0]
    assert out["northbound_score_norm"].isna().all()


def test_capital_flow_source_is_pit_only() -> None:
    capital_flow = next(spec for spec in CLASS_SPECS if spec.name == "capital_flow")

    assert capital_flow.source_tables == ("fact_capital_flow_pit_daily",)


def test_raw_fund_flow_does_not_make_capital_flow_source_available() -> None:
    capital_flow = next(spec for spec in CLASS_SPECS if spec.name == "capital_flow")
    conn = duck_mem()
    try:
        conn.execute("""
            CREATE TABLE raw_fund_flow_daily (
                trade_date VARCHAR,
                stock_code VARCHAR,
                main_net_amount DOUBLE
            )
        """)
        conn.execute(
            "INSERT INTO raw_fund_flow_daily VALUES (?, ?, ?)",
            ["2024-07-01", "AAA", 100.0],
        )

        assert not _source_available(conn, capital_flow)
    finally:
        conn.close()


def test_pit_strict() -> None:
    conn = duck_mem()
    try:
        conn.execute("""
            CREATE TABLE fact_lhb_event (
                trade_date VARCHAR,
                stock_code VARCHAR,
                net_buy DOUBLE,
                net_buy_pct DOUBLE,
                is_inst_net_buy INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO fact_lhb_event VALUES (?, ?, ?, ?, ?)",
            ["2024-07-01", "AAA", 100000000.0, 9.0, 1],
        )

        alpha = LHBAlpha(conn=conn, price_table="v_price_kline_qfq")
        features = alpha.get_features("2024-07-01", ["AAA"])

        row = features.iloc[0]
        assert row["lhb_event_count_30d"] == 0
        assert row["lhb_inst_net_buy_amount_30d"] == 0
        assert row["lhb_score"] == 0
    finally:
        conn.close()
