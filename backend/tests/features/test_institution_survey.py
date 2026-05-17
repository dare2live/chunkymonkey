"""Tests for institution_survey features."""
from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from services.features.institution_survey import (
    build_institution_survey_features,
    feature_names,
)


@pytest.fixture
def synthetic_db(tmp_path):
    db = tmp_path / "test_smartmoney.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""
        CREATE TABLE mart_stock_survey_features (
            stock_code TEXT, as_of_date TEXT,
            survey_count_30d INTEGER, survey_count_60d INTEGER,
            survey_inst_30d INTEGER, survey_inst_60d INTEGER,
            survey_bin TEXT, built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.executemany(
        "INSERT INTO mart_stock_survey_features VALUES (?,?,?,?,?,?,?, CURRENT_TIMESTAMP)",
        [
            ("600000", "2025-06-15", 5, 8, 4, 6, "high"),
            ("600000", "2025-06-16", 6, 9, 5, 7, "high"),
            # 600036 no survey activity
            ("600036", "2025-06-15", 0, 0, 0, 0, "low"),
        ],
    )
    con.close()
    return db


class TestInstitutionSurvey:
    def test_basic_join(self, synthetic_db):
        signals = pd.DataFrame({
            "stock_code": ["600000", "600036"],
            "signal_date": ["2025-06-15", "2025-06-15"],
        })
        out = build_institution_survey_features(signals, synthetic_db)
        row1 = out[out["stock_code"] == "600000"].iloc[0]
        assert row1["survey_count_30d"] == 5
        assert row1["is_survey_active"] == 1
        # 600036: 0 survey
        row2 = out[out["stock_code"] == "600036"].iloc[0]
        assert row2["survey_count_30d"] == 0
        assert row2["is_survey_active"] == 0

    def test_missing_stock_zero_fill(self, synthetic_db):
        """Pre-coverage date (2024-01) → all NULL/0 fill."""
        signals = pd.DataFrame({
            "stock_code": ["600000"],
            "signal_date": ["2024-01-15"],  # 早于 coverage 范围
        })
        out = build_institution_survey_features(signals, synthetic_db)
        row = out.iloc[0]
        assert row["survey_count_30d"] == 0
        assert row["is_survey_active"] == 0

    def test_derived_inst_ratio(self, synthetic_db):
        signals = pd.DataFrame({
            "stock_code": ["600000"],
            "signal_date": ["2025-06-15"],
        })
        out = build_institution_survey_features(signals, synthetic_db)
        row = out.iloc[0]
        # 4 inst / 5 total = 0.8
        assert abs(row["is_inst_survey_30d"] - 0.8) < 1e-4

    def test_feature_names_count(self):
        names = feature_names()
        # 4 raw + 3 derived = 7
        assert len(names) == 7
        for n in ["survey_count_30d", "is_inst_survey_30d", "is_survey_active"]:
            assert n in names
