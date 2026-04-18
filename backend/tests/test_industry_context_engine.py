import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.industry_context_engine as industry_context_engine


def test_build_stock_industry_context_uses_shared_industry_alias_map(monkeypatch):
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
        """
    )
    try:
        conn.execute(
            "INSERT INTO mart_sector_momentum VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("电子", 78.0, "recovering", 1, 5.0, 8.0, 10.0, 12.0, 1.0, 2.0, 3.0, 4.0, 74.0, 1, 1, 2, "leader", 0),
        )
        conn.commit()

        monkeypatch.setattr(
            industry_context_engine,
            "load_industry_map",
            lambda _conn: {
                "600001": {"industry_level1": "电子", "industry_level2": "半导体"},
            },
        )

        inserted = industry_context_engine.build_stock_industry_context(conn, snapshot_date="2026-04-18")

        row = conn.execute(
            "SELECT sw_level1, sw_level2, industry_tailwind_score FROM dim_stock_industry_context_latest WHERE stock_code = '600001'"
        ).fetchone()
        assert inserted == 1
        assert row["sw_level1"] == "电子"
        assert row["sw_level2"] == "半导体"
        assert row["industry_tailwind_score"] is not None
    finally:
        conn.close()