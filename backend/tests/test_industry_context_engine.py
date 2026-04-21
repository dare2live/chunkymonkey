import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.industry_context_engine as industry_context_engine


def test_build_stock_industry_context_reads_from_sw_industry():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE mart_sector_momentum (
            sector_name TEXT,
            momentum_score REAL,
            trend_state TEXT,
            macd_cross INTEGER,
            return_1m REAL,
            return_3m REAL,
            return_6m REAL,
            return_12m REAL,
            excess_1m REAL,
            excess_3m REAL,
            excess_6m REAL,
            excess_12m REAL,
            rotation_score REAL,
            rotation_rank INTEGER,
            rotation_rank_1m INTEGER,
            rotation_rank_3m INTEGER,
            rotation_bucket TEXT,
            rotation_blacklisted INTEGER
        );

        CREATE TABLE dim_stock_sw_industry (
            stock_code TEXT PRIMARY KEY,
            sw_l1 TEXT,
            sw_l2 TEXT,
            sw_l3 TEXT,
            sw_l1_name TEXT,
            sw_l2_name TEXT,
            sw_l3_name TEXT
        );
        """
    )
    try:
        conn.execute(
            "INSERT INTO mart_sector_momentum VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("电子", 78.0, "recovering", 1, 5.0, 8.0, 10.0, 12.0, 1.0, 2.0, 3.0, 4.0, 74.0, 1, 1, 2, "leader", 0),
        )
        conn.execute(
            "INSERT INTO dim_stock_sw_industry (stock_code, sw_l1, sw_l2, sw_l1_name, sw_l2_name) VALUES (?, ?, ?, ?, ?)",
            ("600001", "T10", "T1001", "电子", "半导体"),
        )
        conn.commit()

        inserted = industry_context_engine.build_stock_industry_context(conn, snapshot_date="2026-04-18")

        row = conn.execute(
            "SELECT sw_l1, sw_l2, sw_l1_name, sw_l2_name, industry_tailwind_score FROM dim_stock_industry_context_latest WHERE stock_code = '600001'"
        ).fetchone()
        assert inserted == 1
        assert row["sw_l1"] == "T10"
        assert row["sw_l2"] == "T1001"
        assert row["sw_l1_name"] == "电子"
        assert row["sw_l2_name"] == "半导体"
        assert row["industry_tailwind_score"] is not None
    finally:
        conn.close()