"""Tests for capital_flow features."""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from services.features.capital_flow import build_capital_flow_features, feature_names


@pytest.fixture
def synthetic_db(tmp_path):
    """Build a tiny smartmoney.duckdb with fact_capital_flow_pit_daily."""
    db = tmp_path / "test_smartmoney.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE fact_capital_flow_pit_daily (
            stock_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            lhb_count_30d INTEGER,
            lhb_net_buy_pct_30d DOUBLE,
            lhb_inst_buy_30d INTEGER,
            lhb_count_90d INTEGER,
            lhb_inst_buy_90d INTEGER,
            exec_buy_60d INTEGER,
            exec_sell_60d INTEGER,
            exec_buy_pct_60d DOUBLE,
            exec_sell_pct_60d DOUBLE,
            exec_net_signal DOUBLE,
            holder_count_change_q_pct DOUBLE,
            holder_count_q_report_date TEXT,
            built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, trade_date)
        )
    """)
    rows = [
        # (stock, date, lhb30, lhb_pct30, lhb_inst30, lhb90, lhb_inst90,
        #  ex_buy, ex_sell, ex_buy_pct, ex_sell_pct, ex_net, holder_pct, holder_date)
        ("600000", "2024-06-15", 3, 12.5, 2, 5, 3, 1, 0, 0.5, 0.0, 1.0, -5.2, "2024-03-31"),
        ("600000", "2024-06-16", 4, 15.0, 3, 6, 4, 1, 1, 0.5, 0.3, 0.0,  None, None),
        ("600036", "2024-06-15", 0, None, 0, 0, 0, 0, 2, 0.0, 0.8, -1.0,  3.1, "2024-03-31"),
    ]
    con.executemany(
        """INSERT INTO fact_capital_flow_pit_daily VALUES
           (?,?,?,?,?,?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)""",
        rows,
    )
    con.close()
    return db


class TestCapitalFlow:
    def test_basic_join(self, synthetic_db):
        signals = pd.DataFrame({
            "stock_code": ["600000", "600000", "600036", "600999"],  # 600999 not in cap table
            "signal_date": ["2024-06-15", "2024-06-16", "2024-06-15", "2024-06-15"],
        })
        out = build_capital_flow_features(signals, db_path=synthetic_db)
        assert len(out) == 4
        # 600000 / 2024-06-15: lhb_count_30d=3
        row = out[(out["stock_code"] == "600000") & (out["signal_date"] == "2024-06-15")].iloc[0]
        assert row["lhb_count_30d"] == 3
        assert abs(row["lhb_net_buy_pct_30d"] - 12.5) < 1e-3

    def test_missing_stock_filled_zero(self, synthetic_db):
        """Signal stocks not in capital_flow → all 0 / neutral defaults."""
        signals = pd.DataFrame({
            "stock_code": ["600999"],
            "signal_date": ["2024-06-15"],
        })
        out = build_capital_flow_features(signals, db_path=synthetic_db)
        row = out.iloc[0]
        assert row["lhb_count_30d"] == 0
        assert row["exec_buy_60d"] == 0
        # 派生 ratio NaN -> default
        assert row["cf_lhb_inst_ratio_30d"] == 0.0
        assert row["cf_exec_buy_sell_ratio"] == 0.5  # neutral

    def test_derived_ratios(self, synthetic_db):
        signals = pd.DataFrame({
            "stock_code": ["600000"],
            "signal_date": ["2024-06-15"],
        })
        out = build_capital_flow_features(signals, db_path=synthetic_db)
        row = out.iloc[0]
        # lhb_inst_buy_30d=2 / lhb_count_30d=3
        assert abs(row["cf_lhb_inst_ratio_30d"] - 2/3) < 1e-4
        # exec_buy=1 / (buy=1+sell=0) = 1.0
        assert abs(row["cf_exec_buy_sell_ratio"] - 1.0) < 1e-4
        # holder_count_change_q_pct=-5.2 -> concentration=+5.2 (筹码集中信号)
        assert abs(row["cf_holder_concentration"] - 5.2) < 1e-3

    def test_feature_names_count(self):
        names = feature_names()
        # 11 raw + 4 derived = 15
        assert len(names) == 15
        for n in ["lhb_count_30d", "cf_lhb_inst_ratio_30d", "cf_holder_concentration"]:
            assert n in names
