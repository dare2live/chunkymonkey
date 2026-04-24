import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import industry_overview_read


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE mart_sector_momentum (
            sector_name TEXT,
            sector_code TEXT,
            trend_state TEXT,
            macd_cross INTEGER,
            momentum_score REAL,
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

        CREATE TABLE mart_current_relationship (
            institution_id TEXT,
            stock_code TEXT,
            tdx_l1 TEXT
        );

        CREATE TABLE mart_stock_trend (
            stock_code TEXT,
            stock_name TEXT,
            stock_archetype TEXT,
            priority_pool TEXT,
            composite_priority_score REAL,
            company_quality_score REAL,
            stage_score REAL,
            setup_tag TEXT,
            discovery_score REAL,
            price_20d_pct REAL
        );

        CREATE TABLE dim_stock_industry_context_latest (
            stock_code TEXT,
            tdx_l1 TEXT,
            industry_tailwind_score REAL,
            dual_confirm_recent_180d INTEGER
        );

        CREATE TABLE fact_setup_snapshot (
            snapshot_tdx_l1 TEXT,
            snapshot_date TEXT,
            priority_pool TEXT,
            matured_10d INTEGER,
            gain_10d REAL,
            matured_30d INTEGER,
            gain_30d REAL,
            matured_60d INTEGER,
            gain_60d REAL
        );

        CREATE TABLE fact_institution_event (
            event_type TEXT,
            stock_code TEXT,
            notice_date TEXT,
            report_date TEXT
        );

        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT,
            tdx_l1 TEXT,
            tdx_l2 TEXT,
            tdx_l3 TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT,
            tdx_l3_name TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO mart_sector_momentum VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("汽车", "AUTO", "bullish", 1, 88.0, 1.2, 6.5, 10.0, 18.0, 0.5, 2.1, 4.2, 8.3, 80.0, 1, 1, 1, "leader", 0),
            ("半导体", "SEMI", "recovering", 0, 72.0, 0.8, 4.0, 7.5, 12.0, 0.3, 1.2, 2.8, 5.0, 67.0, 2, 2, 2, "watch", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO mart_current_relationship VALUES (?, ?, ?)",
        [
            ("inst_a", "600001", "汽车"),
            ("inst_b", "600001", "汽车"),
            ("inst_c", "600002", "半导体"),
        ],
    )
    conn.executemany(
        "INSERT INTO mart_stock_trend VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("600001", "汽车股", "成长", "A池", 82.0, 85.0, 79.0, "setup", 76.0, 5.0),
            ("600002", "芯片股", "成长", "B池", 68.0, 73.0, 66.0, None, 64.0, 2.0),
        ],
    )
    conn.executemany(
        "INSERT INTO dim_stock_industry_context_latest VALUES (?, ?, ?, ?)",
        [
            ("600001", "汽车", 77.0, 2),
            ("600002", "半导体", 61.0, 0),
        ],
    )
    conn.executemany(
        "INSERT INTO fact_setup_snapshot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("汽车", "2026-04-01", "A池", 1, 3.0, 1, 6.0, 0, None),
            ("半导体", "2026-04-02", "B池", 1, -1.0, 0, None, 0, None),
        ],
    )
    conn.executemany(
        "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?)",
        [
            ("new_entry", "600001", "2026-04-10", "2026-03-31"),
            ("increase", "600001", "2026-04-11", "2026-03-31"),
            ("new_entry", "600002", "2026-04-12", "2026-03-31"),
        ],
    )
    conn.executemany(
        "INSERT INTO dim_stock_tdx_industry (stock_code, tdx_l1, tdx_l2, tdx_l3, tdx_l1_name, tdx_l2_name, tdx_l3_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("600001", "汽车", "汽车零部件", "零部件", "汽车", "汽车零部件", "零部件"),
            ("600002", "半导体", "半导体", "芯片设计", "半导体", "半导体", "芯片设计"),
        ],
    )
    conn.commit()
    return conn


def test_get_industry_overview_payload_keeps_summary_and_sorting():
    conn = _make_conn()
    try:
        payload = industry_overview_read.get_industry_overview_payload(conn, topn=2)

        assert payload["ok"] is True
        assert payload["count"] == 2
        assert payload["data"][0]["sector_name"] == "汽车"
        assert payload["data"][0]["top_stocks"][0]["stock_code"] == "600001"
        assert payload["summary"]["strongest_sector"] == "汽车"
    finally:
        conn.close()


def test_get_industry_overview_payload_falls_back_to_sector_momentum():
    conn = _make_conn()
    try:
        payload = industry_overview_read.get_industry_overview_payload(conn, topn=1)

        assert payload["ok"] is True
        assert payload["summary"]["strongest_sector"] == "汽车"
        assert payload["summary"]["strongest_sector_source"] == "sector_momentum"
        assert payload["summary"]["strongest_sector_note"] == "按行业动量排序"
        assert payload["data"][0]["sector_name"] == "汽车"
    finally:
        conn.close()