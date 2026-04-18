import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.stock_validation import _load_turtle_validation


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE mart_stock_trend (
            stock_code TEXT PRIMARY KEY,
            priority_pool TEXT,
            price_20d_pct REAL
        );

        CREATE TABLE dim_stock_turtle_latest (
            stock_code TEXT PRIMARY KEY,
            sw_level1 TEXT,
            turtle_setup_state TEXT,
            preferred_system TEXT,
            turtle_execution_score_v1 REAL,
            turtle_breakout_score REAL,
            turtle_risk_score REAL,
            stage_score_v1 REAL,
            forecast_score_v1 REAL
        );

        CREATE TABLE dim_stock_industry (
            stock_code TEXT,
            sw_level1 TEXT
        );
        """
    )
    return conn


def test_load_turtle_validation_summarizes_breakout_watch_and_exit_states():
    conn = _make_conn()
    try:
        conn.executemany(
            "INSERT INTO mart_stock_trend (stock_code, priority_pool, price_20d_pct) VALUES (?, ?, ?)",
            [
                ("600001", "A池", 18.0),
                ("600002", "B池", 5.0),
                ("000001", "D池", -12.0),
                ("300001", "C池", 2.0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO dim_stock_turtle_latest (
                stock_code, sw_level1, turtle_setup_state, preferred_system,
                turtle_execution_score_v1, turtle_breakout_score, turtle_risk_score,
                stage_score_v1, forecast_score_v1
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("600001", "电子", "S2突破触发", "S2", 76.0, 82.0, 68.0, 78.0, 74.0),
                ("600002", "电子", "S1待突破", "S1", 63.0, 66.0, 61.0, 70.0, 65.0),
                ("000001", "消费", "20日退出触发", "S1", 35.0, 28.0, 32.0, 34.0, 36.0),
            ],
        )
        conn.commit()

        report = _load_turtle_validation(conn)

        assert report["summary"]["total_stock_count"] == 4
        assert report["summary"]["covered_stock_count"] == 3
        assert report["summary"]["coverage_ratio"] == 75.0
        assert report["summary"]["breakout_trigger_count"] == 1
        assert report["summary"]["watchlist_count"] == 1
        assert report["summary"]["exit_trigger_count"] == 1

        state_map = {item["turtle_setup_state"]: item for item in report["state_distribution"]}
        assert state_map["S2突破触发"]["a_pool_count"] == 1
        assert state_map["20日退出触发"]["d_pool_count"] == 1
        assert state_map["S2突破触发"]["avg_price_20d_pct"] == 18.0
        assert state_map["20日退出触发"]["avg_price_20d_pct"] == -12.0

        assert any("更强的股票区分出来" in hint for hint in report["hints"])
        assert any("风险过滤价值" in hint for hint in report["hints"])
    finally:
        conn.close()


def test_load_turtle_validation_sector_filter_falls_back_to_dim_stock_industry():
    conn = _make_conn()
    try:
        conn.execute(
            "INSERT INTO mart_stock_trend (stock_code, priority_pool, price_20d_pct) VALUES (?, ?, ?)",
            ("600001", "A池", 18.0),
        )
        conn.execute(
            "INSERT INTO dim_stock_turtle_latest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("600001", "", "S2突破触发", "S2", 76.0, 82.0, 68.0, 78.0, 74.0),
        )
        conn.execute("INSERT INTO dim_stock_industry VALUES (?, ?)", ("600001", "电子"))
        conn.commit()

        report = _load_turtle_validation(conn, sector="电子")

        assert report["summary"]["covered_stock_count"] == 1
        assert report["summary"]["breakout_trigger_count"] == 1
    finally:
        conn.close()