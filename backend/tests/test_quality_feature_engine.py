import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.quality_feature_engine import build_quality_features, ensure_tables


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE dim_financial_latest (
            stock_code TEXT PRIMARY KEY,
            latest_report_date TEXT,
            roe REAL,
            debt_ratio REAL,
            current_ratio REAL,
            gross_margin REAL,
            ocf_to_profit REAL,
            contract_to_revenue REAL
        );

        CREATE TABLE dim_financial_indicator_latest (
            stock_code TEXT PRIMARY KEY,
            latest_report_date TEXT,
            roe_ak REAL,
            roa_ak REAL,
            gross_margin_ak REAL,
            net_margin_ak REAL,
            current_ratio_ak REAL,
            quick_ratio_ak REAL,
            debt_ratio_ak REAL,
            asset_turnover_ak REAL,
            inventory_turnover_ak REAL,
            receivables_turnover_ak REAL,
            revenue_growth_yoy_ak REAL,
            net_profit_growth_yoy_ak REAL
        );
        """
    )
    return conn


def test_build_quality_features_uses_shared_industry_alias_map(monkeypatch):
    conn = _make_conn()
    try:
        ensure_tables(conn)
        monkeypatch.setattr(
            "services.quality_feature_engine.load_industry_map",
            lambda _conn: {
                f"60{idx:04d}": {"industry_level1": "电子", "industry_level2": "芯片"}
                for idx in range(15)
            },
        )

        for idx in range(15):
            code = f"60{idx:04d}"
            conn.execute(
                "INSERT INTO dim_financial_latest VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (code, "2026-03-31", 0.10 + idx * 0.005, 0.40, 1.5, 0.30, 1.1, 0.15),
            )
            conn.execute(
                "INSERT INTO dim_financial_indicator_latest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, "2026-03-31", 0.12, 0.08 + idx * 0.002, 0.28, 0.12, 1.6, 1.2, 0.45, 0.9, 2.0, 3.0, 12.0, 10.0),
            )
        conn.commit()

        inserted = build_quality_features(conn, snapshot_date="2026-04-18")

        row = conn.execute(
            "SELECT sw_level1, sw_level2, roe_rank, roa_rank, quality_score_v1 FROM dim_stock_quality_latest WHERE stock_code = ?",
            ("600000",),
        ).fetchone()
        assert inserted == 15
        assert row["sw_level1"] == "电子"
        assert row["sw_level2"] == "芯片"
        assert row["roe_rank"] is not None
        assert row["roa_rank"] is not None
        assert row["quality_score_v1"] is not None
    finally:
        conn.close()