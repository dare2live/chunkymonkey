import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts import run_follow_backtest as subject


def _create_follow_inputs(conn):
    conn.execute(
        """
        CREATE TABLE fact_institution_event (
            institution_id TEXT,
            stock_code TEXT,
            notice_date TEXT,
            event_type TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE inst_institutions (
            id TEXT,
            name TEXT,
            type TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT,
            tdx_l1_name TEXT,
            tdx_l2_name TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO inst_institutions VALUES (?, ?, ?)",
        [
            ("inst_fund", "Fund A", "基金"),
            ("inst_qfii", "QFII A", "QFII"),
            ("inst_north", "North A", "北向"),
        ],
    )
    conn.executemany(
        "INSERT INTO dim_stock_tdx_industry VALUES (?, ?, ?)",
        [
            ("000001", "医药", "创新药"),
            ("000002", "医药", "医疗服务"),
            ("000003", "科技", "软件"),
            ("000004", "消费", "白酒"),
        ],
    )
    conn.executemany(
        "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?)",
        [
            ("inst_fund", "000001", "20260102", "new_entry"),
            ("inst_fund", "000002", "20260103", "increase"),
            ("inst_qfii", "000003", "20260104", "new_entry"),
            ("inst_north", "000004", "20260105", "new_entry"),
        ],
    )


def test_follow_backtest_loads_and_ranks_cohorts_as_records():
    conn = duck_mem()
    try:
        _create_follow_inputs(conn)

        events = subject.load_cohort_events(conn, "inst_type_L1", "基金|医药")
        cohorts = subject.list_top_cohorts(conn, "inst_type_L1", top=5, min_samples=1)

        assert events == [
            {"institution_id": "inst_fund", "stock_code": "000001", "notice_date": "20260102"},
            {"institution_id": "inst_fund", "stock_code": "000002", "notice_date": "20260103"},
        ]
        assert cohorts == [("基金|医药", 2), ("QFII|科技", 1)]
    finally:
        conn.close()


def test_follow_backtest_writes_results_without_table_registration(monkeypatch):
    conn = duck_mem()
    try:
        _create_follow_inputs(conn)
        subject.ensure_table(conn)

        def fake_simulate_events(events, params):
            return {
                "n_events": len(events),
                "n_filled": len(events),
                "avg_pnl": 0.03,
                "avg_hold_days": 10.0,
                "win_rate": 0.5,
                "annual_return": 0.8,
                "sharpe": 1.2,
                "avg_position_maxdd": -0.04,
                "p95_position_maxdd": -0.08,
                "exit_reason_counts": {"max_hold": len(events)},
            }

        monkeypatch.setattr(subject, "simulate_events", fake_simulate_events)
        rows = subject.run_backtest_for_cohort(
            conn,
            "inst_type_L1",
            "基金|医药",
            {
                "entry_lag": [1],
                "max_hold_days": [10],
                "stop_loss": [-0.08],
                "take_profit": [0.2],
            },
        )
        stored = conn.execute(
            """
            SELECT n_events, n_filled, event_date_min, event_date_max,
                   exit_reasons_json
            FROM fact_institution_follow_backtest
            """
        ).fetchone()

        assert len(rows) == 1
        assert rows[0]["n_events"] == 2
        assert stored["n_events"] == 2
        assert stored["n_filled"] == 2
        assert stored["event_date_min"] == "20260102"
        assert stored["event_date_max"] == "20260103"
        assert json.loads(stored["exit_reasons_json"]) == {"max_hold": 2}
    finally:
        conn.close()
