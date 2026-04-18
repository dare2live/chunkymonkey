import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import backtest_engine, scoring


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_build_inst_industry_performance_uses_event_snapshot_without_dim_table():
    conn = _make_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_institution_event (
                institution_id TEXT,
                stock_code TEXT,
                report_date TEXT,
                event_type TEXT,
                gain_10d REAL,
                gain_30d REAL,
                gain_60d REAL,
                gain_120d REAL,
                max_drawdown_30d REAL,
                max_drawdown_60d REAL,
                inst_ref_cost REAL,
                premium_pct REAL
            );
            CREATE TABLE fact_institution_event_industry_snapshot (
                institution_id TEXT,
                stock_code TEXT,
                report_date TEXT,
                sw_level1 TEXT,
                sw_level2 TEXT,
                sw_level3 TEXT
            );
            CREATE TABLE inst_institutions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                name TEXT,
                type TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO inst_institutions VALUES (?, ?, ?, ?)",
            ("inst_a", "机构A", "机构A", "公募"),
        )
        conn.execute(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("inst_a", "600001", "2026-03-31", "new_entry", 3.0, 8.0, 12.0, 20.0, -4.0, -6.0, 18.5, 4.2),
        )
        conn.execute(
            "INSERT INTO fact_institution_event_industry_snapshot VALUES (?, ?, ?, ?, ?, ?)",
            ("inst_a", "600001", "2026-03-31", "旧一级", "旧二级", "旧三级"),
        )
        conn.commit()

        result = backtest_engine.build_inst_industry_performance(conn)

        assert result == {"rows": 3}
        rows = conn.execute(
            "SELECT industry_level, industry_name FROM research_inst_industry_performance ORDER BY industry_level"
        ).fetchall()
        assert [(row["industry_level"], row["industry_name"]) for row in rows] == [
            ("L1", "旧一级"),
            ("L2", "旧二级"),
            ("L3", "旧三级"),
        ]
    finally:
        conn.close()


def test_build_holding_chains_uses_event_snapshot_without_dim_table():
    conn = _make_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_institution_event (
                institution_id TEXT,
                stock_code TEXT,
                report_date TEXT,
                notice_date TEXT,
                event_type TEXT,
                inst_ref_cost REAL,
                price_entry REAL,
                premium_pct REAL,
                gain_30d REAL,
                gain_60d REAL,
                gain_120d REAL,
                max_drawdown_30d REAL
            );
            CREATE TABLE fact_institution_event_industry_snapshot (
                institution_id TEXT,
                stock_code TEXT,
                report_date TEXT,
                sw_level1 TEXT,
                sw_level2 TEXT,
                sw_level3 TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("inst_a", "600001", "2026-03-31", "2026-04-10", "new_entry", 10.0, 11.5, 4.0, 8.0, 12.0, 20.0, -4.0),
                ("inst_a", "600001", "2026-06-30", "2026-07-10", "exit", 12.0, 13.0, 6.0, 6.0, 9.0, 14.0, -3.0),
            ],
        )
        conn.executemany(
            "INSERT INTO fact_institution_event_industry_snapshot VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("inst_a", "600001", "2026-03-31", "旧一级", "旧二级", "旧三级"),
                ("inst_a", "600001", "2026-06-30", "旧一级", "旧二级", "旧三级"),
            ],
        )
        conn.commit()

        result = backtest_engine.build_holding_chains(conn)

        assert result == {"total_chains": 1, "closed": 1}
        row = conn.execute(
            "SELECT industry_l1, industry_l2, industry_l3 FROM research_holding_chains"
        ).fetchone()
        assert (row["industry_l1"], row["industry_l2"], row["industry_l3"]) == ("旧一级", "旧二级", "旧三级")
    finally:
        conn.close()


def test_build_cross_factor_analysis_uses_event_snapshot_without_dim_table():
    conn = _make_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_institution_event (
                institution_id TEXT,
                stock_code TEXT,
                report_date TEXT,
                event_type TEXT,
                gain_30d REAL,
                gain_60d REAL,
                gain_120d REAL,
                max_drawdown_30d REAL,
                change_pct REAL,
                premium_pct REAL
            );
            CREATE TABLE fact_institution_event_industry_snapshot (
                institution_id TEXT,
                stock_code TEXT,
                report_date TEXT,
                sw_level1 TEXT,
                sw_level2 TEXT,
                sw_level3 TEXT
            );
            CREATE TABLE inst_institutions (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                name TEXT,
                type TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO inst_institutions VALUES (?, ?, ?, ?)",
            ("inst_a", "机构A", "机构A", "公募"),
        )
        events = [
            ("inst_a", f"6000{i:02d}", "2026-03-31", "new_entry", 8.0, 11.0, 15.0, -4.0, None, 4.0)
            for i in range(10)
        ]
        snapshots = [
            ("inst_a", f"6000{i:02d}", "2026-03-31", "旧一级", "旧二级", "旧三级")
            for i in range(10)
        ]
        conn.executemany(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            events,
        )
        conn.executemany(
            "INSERT INTO fact_institution_event_industry_snapshot VALUES (?, ?, ?, ?, ?, ?)",
            snapshots,
        )
        conn.commit()

        result = backtest_engine.build_cross_factor_analysis(conn)

        assert result["rows"] >= 1
        row = conn.execute(
            "SELECT factor_b_value FROM research_cross_factor WHERE factor_b = 'industry_l1' LIMIT 1"
        ).fetchone()
        assert row["factor_b_value"] == "旧一级"
    finally:
        conn.close()


def test_load_crowding_fit_lookup_skilled_l3_uses_event_snapshot_without_dim_table():
    conn = _make_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_institution_event (
                institution_id TEXT,
                stock_code TEXT,
                report_date TEXT,
                event_type TEXT,
                premium_pct REAL,
                gain_30d REAL,
                max_drawdown_30d REAL
            );
            CREATE TABLE fact_institution_event_industry_snapshot (
                institution_id TEXT,
                stock_code TEXT,
                report_date TEXT,
                sw_level1 TEXT,
                sw_level2 TEXT,
                sw_level3 TEXT
            );
            CREATE TABLE research_inst_industry_performance (
                institution_id TEXT,
                industry_level TEXT,
                industry_name TEXT,
                buy_event_count INTEGER,
                win_rate_30d REAL
            );
            """
        )
        conn.execute(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("inst_a", "600001", "2026-03-31", "new_entry", 4.0, 8.0, -3.0),
        )
        conn.execute(
            "INSERT INTO fact_institution_event_industry_snapshot VALUES (?, ?, ?, ?, ?, ?)",
            ("inst_a", "600001", "2026-03-31", "旧一级", "旧二级", "旧三级"),
        )
        conn.execute(
            "INSERT INTO research_inst_industry_performance VALUES (?, ?, ?, ?, ?)",
            ("inst_a", "L3", "旧三级", 5, 65.0),
        )
        conn.commit()

        lookup = scoring._load_crowding_fit_lookup(conn)

        assert lookup["skilled_l3"][("new_entry", "solo", "0_10")] == {
            "n": 1,
            "avg30": 8.0,
            "wr30": 100.0,
            "dd30": -3.0,
        }
    finally:
        conn.close()