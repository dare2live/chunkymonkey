"""Tests for sector_momentum features."""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from services.features.sector_momentum import (
    build_sector_momentum_features,
    feature_names,
)


@pytest.fixture
def synthetic_db(tmp_path):
    """Build smartmoney.duckdb subset: mart_stock_industry_pit + fact_sector_momentum_daily."""
    db = tmp_path / "test_smartmoney.duckdb"
    con = duckdb.connect(str(db))
    # PIT industry mapping
    con.execute("""
        CREATE TABLE mart_stock_industry_pit (
            stock_code TEXT, effective_from TEXT, effective_to TEXT,
            tdx_l1_name TEXT, confidence_level TEXT
        )
    """)
    con.executemany(
        "INSERT INTO mart_stock_industry_pit VALUES (?,?,?,?,?)",
        [
            # 600000 has clean PIT history: 金融 in entire training period
            ("600000", "2020-01-01", "2099-12-31", "金融", "observed_snapshot"),
            # 600036 switched 科技 → 金融 mid-2024
            ("600036", "2020-01-01", "2024-07-01", "科技", "observed_snapshot"),
            ("600036", "2024-07-01", "2099-12-31", "金融", "observed_snapshot"),
            # 600999 only has fallback (no PIT)
            ("600999", "1900-01-01", "2099-12-31", "其他", "current_label_fallback"),
        ],
    )
    # Sector momentum daily
    con.execute("""
        CREATE TABLE fact_sector_momentum_daily (
            sector_name TEXT, date TEXT,
            ret_5d DOUBLE, ret_20d DOUBLE, ret_60d DOUBLE, ret_120d DOUBLE,
            excess_20d DOUBLE, excess_60d DOUBLE,
            price_vs_ma20 DOUBLE, price_vs_ma60 DOUBLE, vol_60d DOUBLE
        )
    """)
    con.executemany(
        "INSERT INTO fact_sector_momentum_daily VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("金融", "2024-06-15", 0.01, 0.05, 0.10, 0.20, 0.03, 0.07, 0.02, 0.05, 0.15),
            ("金融", "2024-08-15", 0.02, 0.06, 0.12, 0.22, 0.04, 0.08, 0.03, 0.06, 0.16),
            ("科技", "2024-06-15", -0.02, -0.05, 0.20, 0.30, -0.03, 0.15, -0.01, 0.10, 0.25),
        ],
    )
    con.close()
    return db


class TestSectorMomentum:
    def test_basic_pit_join(self, synthetic_db):
        """600000 always 金融, 600036 在 2024-06-15 还是 科技."""
        signals = pd.DataFrame({
            "stock_code": ["600000", "600036"],
            "signal_date": ["2024-06-15", "2024-06-15"],
        })
        out = build_sector_momentum_features(signals, synthetic_db)
        # 600000 → 金融 → ret_60d=0.10
        row1 = out[out["stock_code"] == "600000"].iloc[0]
        assert row1["sector_name"] == "金融"
        assert abs(row1["ret_60d"] - 0.10) < 1e-4
        # 600036 在 2024-06-15 还在 科技 (effective_from <= signal_date < effective_to)
        row2 = out[out["stock_code"] == "600036"].iloc[0]
        assert row2["sector_name"] == "科技"
        assert abs(row2["ret_60d"] - 0.20) < 1e-4

    def test_pit_history_switch(self, synthetic_db):
        """600036 在 2024-08-15 已切金融 (effective_to=2024-07-01)."""
        signals = pd.DataFrame({
            "stock_code": ["600036"],
            "signal_date": ["2024-08-15"],
        })
        out = build_sector_momentum_features(signals, synthetic_db)
        row = out.iloc[0]
        assert row["sector_name"] == "金融"
        assert abs(row["ret_60d"] - 0.12) < 1e-4

    def test_fallback_excluded_default(self, synthetic_db):
        """600999 只有 fallback row, default (include_fallback=False) 全 NULL."""
        signals = pd.DataFrame({
            "stock_code": ["600999"],
            "signal_date": ["2024-06-15"],
        })
        out = build_sector_momentum_features(signals, synthetic_db)
        row = out.iloc[0]
        assert pd.isna(row["sector_name"])
        assert pd.isna(row["ret_60d"])
        # 派生 score: NaN excess → 0
        assert row["sec_mom_score"] == 0.0

    def test_fallback_included_optin(self, synthetic_db):
        """include_fallback=True opt-in 拿 fallback sector (不推荐生产)."""
        signals = pd.DataFrame({
            "stock_code": ["600999"],
            "signal_date": ["2024-06-15"],
        })
        out = build_sector_momentum_features(signals, synthetic_db, include_fallback=True)
        row = out.iloc[0]
        assert row["sector_name"] == "其他"

    def test_feature_names_count(self):
        names = feature_names()
        # 9 raw + 2 derived
        assert len(names) == 11
        for n in ["ret_60d", "excess_60d", "sec_mom_score", "sec_mom_rank_60d"]:
            assert n in names
