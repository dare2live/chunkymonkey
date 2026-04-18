import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.institution_aux_read as institution_aux_read


def test_load_holdings_rows_filters_and_orders():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE inst_holdings (
            institution_id TEXT,
            stock_code TEXT,
            report_date TEXT,
            hold_market_cap REAL,
            sw_level1 TEXT,
            sw_level2 TEXT,
            sw_level3 TEXT
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO inst_holdings VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("inst_a", "600001", "2026-03-31", 100.0, "电子", "半导体", "芯片设计"),
                ("inst_a", "600002", "2025-12-31", 90.0, "汽车", "零部件", "车身附件"),
                ("inst_b", "600001", "2026-03-31", 80.0, "电子", "半导体", "芯片设计"),
            ],
        )
        conn.commit()

        rows = institution_aux_read.load_holdings_rows(conn, institution_id="inst_a")

        assert [row["stock_code"] for row in rows] == ["600001", "600002"]
        assert rows[0]["sw_level2"] == "半导体"
        assert rows[0]["industry_level2"] == "半导体"
    finally:
        conn.close()


def test_load_event_rows_returns_filtered_rows_and_total():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE fact_institution_event (
            institution_id TEXT,
            stock_code TEXT,
            event_type TEXT,
            notice_date TEXT,
            report_date TEXT,
            sw_level1 TEXT,
            sw_level2 TEXT,
            sw_level3 TEXT
        );
        CREATE TABLE inst_institutions (
            id TEXT PRIMARY KEY,
            display_name TEXT
        );
        """
    )
    try:
        conn.execute("INSERT INTO inst_institutions VALUES (?, ?)", ("inst_a", "机构甲"))
        conn.executemany(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("inst_a", "600001", "increase", "2026-04-10", "2026-03-31", "电子", "半导体", "芯片设计"),
                ("inst_a", "600001", "new_entry", "2026-04-08", "2026-03-31", "电子", "半导体", "芯片设计"),
                ("inst_b", "600002", "decrease", "2026-04-09", "2026-03-31", "汽车", "零部件", "车身附件"),
            ],
        )
        conn.commit()

        payload = institution_aux_read.load_event_rows(conn, institution_id="inst_a", limit=1)

        assert payload["total"] == 2
        assert len(payload["data"]) == 1
        assert payload["data"][0]["inst_display_name"] == "机构甲"
        assert payload["data"][0]["event_type"] == "increase"
        assert payload["data"][0]["industry_level1"] == "电子"
        assert payload["data"][0]["sw_level1"] == "电子"
    finally:
        conn.close()


def test_load_industry_stat_rows_filters_institution():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE mart_institution_industry_stat (
            institution_id TEXT,
            industry_name TEXT,
            sample_events INTEGER
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO mart_institution_industry_stat VALUES (?, ?, ?)",
            [
                ("inst_a", "半导体", 12),
                ("inst_b", "银行", 18),
                ("inst_a", "电子", 20),
            ],
        )
        conn.commit()

        rows = institution_aux_read.load_industry_stat_rows(conn, institution_id="inst_a")

        assert [row["industry_name"] for row in rows] == ["电子", "半导体"]
    finally:
        conn.close()


def test_load_exclusion_categories_orders_by_category():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE exclusion_categories (
            category TEXT,
            label TEXT,
            enabled INTEGER
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO exclusion_categories VALUES (?, ?, ?)",
            [
                ("ZETA", "后排", 1),
                ("ALPHA", "前排", 1),
            ],
        )
        conn.commit()

        rows = institution_aux_read.load_exclusion_categories(conn)

        assert [row["category"] for row in rows] == ["ALPHA", "ZETA"]
    finally:
        conn.close()