import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
import services.holdings as holdings


def test_get_inst_current_holdings_adds_industry_aliases_and_other_institutions():
    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE mart_current_relationship (
            institution_id TEXT,
            display_name TEXT,
            inst_type TEXT,
            stock_code TEXT,
            stock_name TEXT,
            report_date TEXT,
            notice_date TEXT,
            hold_amount REAL,
            hold_market_cap REAL,
            hold_ratio REAL,
            tdx_l1 TEXT,
            tdx_l2 TEXT,
            tdx_l3 TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT,
            tdx_l3_name TEXT,
            event_type TEXT,
            change_pct REAL,
            report_season TEXT,
            inst_ref_cost REAL,
            inst_cost_method TEXT,
            premium_pct REAL,
            premium_bucket TEXT,
            follow_gate TEXT,
            follow_gate_reason TEXT,
            gain_10d REAL,
            gain_30d REAL,
            gain_60d REAL,
            gain_120d REAL
        );
        """
    )
    try:
        conn.executemany(
            "INSERT INTO mart_current_relationship VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "inst_a", "机构甲", "fund", "600001", "样本一", "2026-03-31", "2026-04-15",
                    10.0, 100.0, 1.2,
                    "T10", "T1001", "T100101", "电子", "半导体", "芯片设计",
                    "increase", 5.0, "2026Q1",
                    12.3, "event", 4.5, "折价", "follow", "样本理由", 1.0, 2.0, 3.0, 4.0,
                ),
                (
                    "inst_b", "机构乙", "qfii", "600001", "样本一", "2026-03-31", "2026-04-16",
                    8.0, 80.0, 0.9,
                    "T10", "T1001", "T100101", "电子", "半导体", "芯片设计",
                    "new_entry", 3.0, "2026Q1",
                    11.8, "event", 3.2, "平价", "observe", "另一个理由", 0.5, 1.5, 2.5, 3.5,
                ),
            ],
        )
        conn.commit()

        rows = holdings.get_inst_current_holdings(conn, "inst_a")

        assert len(rows) == 1
        assert rows[0]["tdx_l2"] == "T1001"
        assert rows[0]["other_institutions"][0]["id"] == "inst_b"
    finally:
        conn.close()