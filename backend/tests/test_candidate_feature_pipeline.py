import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from scripts.build_candidate_feature_panel import build_candidate_feature_panel
from scripts.mark_deprecated_data_assets import mark_deprecated_assets
from scripts.run_feature_group_ablation import run_group_ablation
from scripts.run_optuna_feature_elimination import run_optuna_feature_elimination
from scripts.run_walkforward_feature_eval import run_walkforward_feature_eval
from scripts.validate_tdx_feature_pit import validate_tdx_feature_pit


def _create_candidate_inputs(conn):
    conn.execute(
        """
        CREATE TABLE fact_feature_panel (
            stock_code TEXT,
            date TEXT,
            close REAL,
            forward_ret_20d REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_holder_count_period (
            stock_code TEXT,
            report_date TEXT,
            holder_count_change_pct REAL,
            avg_float_shares_change_pct REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_top10_holder_period (
            stock_code TEXT,
            report_date TEXT,
            holder_set TEXT,
            is_exit_row BOOLEAN,
            is_secondary_class BOOLEAN,
            hold_ratio_total REAL,
            hold_ratio_float REAL,
            hold_ratio REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_common_major_holder_stock (
            stock_code TEXT,
            report_date TEXT,
            peer_stock_code TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE fact_fund_holding_tdx_f10 (
            stock_code TEXT,
            report_date TEXT,
            shares REAL,
            float_a_ratio REAL,
            market_value REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE raw_gpcw_detail (
            stock_code TEXT,
            report_date TEXT,
            inst_total_shares REAL,
            national_team_shares_wan REAL,
            qfii_shares REAL,
            fund_shares REAL,
            social_security_shares REAL,
            contract_liabilities REAL,
            revenue REAL,
            operating_cashflow REAL,
            net_profit REAL,
            accounts_receivable REAL,
            inventory REAL,
            forecast_profit_yoy_low REAL,
            forecast_profit_yoy_high REAL,
            express_net_profit REAL
        )
        """
    )
    panel_rows = []
    for date in ("2026-04-01", "2026-04-02"):
        panel_rows.extend([
            ("000001", date, 10.0, 0.03),
            ("000002", date, 20.0, 0.01),
            ("000003", date, 30.0, -0.02),
        ])
    conn.executemany("INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?)", panel_rows)
    conn.executemany(
        "INSERT INTO fact_holder_count_period VALUES (?, ?, ?, ?)",
        [
            ("000001", "2025-12-31", -2.0, 4.0),
            ("000001", "2026-03-31", -5.0, 5.0),
            ("000002", "2025-12-31", 4.0, -2.0),
            ("000002", "2026-03-31", 2.0, -1.0),
            ("000003", "2025-12-31", 7.0, -3.0),
            ("000003", "2026-03-31", 8.0, -4.0),
        ],
    )
    conn.executemany(
        "INSERT INTO fact_top10_holder_period VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("000001", "2025-12-31", "all", False, False, 50.0, None, 50.0),
            ("000001", "2026-03-31", "all", False, False, 55.0, None, 55.0),
            ("000002", "2025-12-31", "all", False, False, 40.0, None, 40.0),
            ("000002", "2026-03-31", "all", False, False, 38.0, None, 38.0),
            ("000003", "2025-12-31", "all", False, False, 30.0, None, 30.0),
            ("000003", "2026-03-31", "all", False, False, 29.0, None, 29.0),
        ],
    )
    conn.executemany(
        "INSERT INTO fact_common_major_holder_stock VALUES (?, ?, ?)",
        [
            ("000001", "2026-03-31", "000001"),
            ("000001", "2026-03-31", "000002"),
            ("000001", "2026-03-31", "000003"),
            ("000002", "2026-03-31", "000001"),
            ("000003", "2026-03-31", "000002"),
        ],
    )
    conn.executemany(
        "INSERT INTO fact_fund_holding_tdx_f10 VALUES (?, ?, ?, ?, ?)",
        [
            ("000001", "2026-03-31", 1000.0, 3.5, 120.5),
            ("000002", "2026-03-31", 500.0, 1.0, 60.0),
            ("000003", "2026-03-31", 200.0, 0.5, 20.0),
        ],
    )
    conn.executemany(
        "INSERT INTO raw_gpcw_detail VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("000001", "2025-12-31", 100, 10, 5, 30, 7, 20, 200, 80, 40, 50, 20, 10, 20, 40),
            ("000001", "2026-03-31", 120, 12, 6, 36, 8, 30, 240, 100, 50, 60, 30, 20, 40, 50),
            ("000002", "2025-12-31", 100, 10, 5, 30, 5, 10, 200, 40, 40, 20, 10, 0, 10, 20),
            ("000002", "2026-03-31", 90, 9, 4, 27, 4, 8, 200, 30, 35, 18, 8, 0, 10, 18),
            ("000003", "2025-12-31", 100, 10, 5, 30, 4, 5, 100, 20, 40, 10, 5, -10, 0, 10),
            ("000003", "2026-03-31", 80, 8, 3, 24, 3, 4, 100, 10, 30, 8, 4, -20, -10, 8),
        ],
    )


def test_candidate_feature_panel_ablation_and_elimination_records():
    conn = duck_mem()
    try:
        _create_candidate_inputs(conn)

        built = build_candidate_feature_panel(conn, start_date="2026-04-01")
        sample = conn.execute(
            """
            SELECT common_holder_network_count, fund_holding_shares_tdx_f10,
                   fund_holding_float_a_ratio_tdx_f10, fund_holding_market_value_tdx_f10,
                   holder_count_change_pct_tdx, holder_count_acceleration_tdx,
                   top10_concentration_change,
                   tdx_inst_total_shares_qoq, contract_liabilities_to_revenue,
                   ocf_to_profit_tdx, social_security_shares_qoq,
                   receivables_to_revenue, inventory_to_revenue,
                   forecast_profit_yoy_mid, forecast_range_width,
                   express_net_profit_yoy
            FROM fact_feature_panel_candidate
            WHERE stock_code = '000001' AND date = '2026-04-01'
            """
        ).fetchone()
        walkforward = run_walkforward_feature_eval(conn, folds=2, run_id="test_walkforward")
        ablation = run_group_ablation(conn)
        elimination = run_optuna_feature_elimination(conn, trials=0, min_abs_ic=0.0)
        elimination_sql = run_optuna_feature_elimination(
            conn,
            trials=0,
            min_abs_ic=0.0,
            run_id="test_sql_elimination",
            method="sql",
        )
        pit = validate_tdx_feature_pit(conn, audit_run_id="test_pit")
        score_count = conn.execute(
            "SELECT COUNT(*) FROM mart_feature_candidate_score"
        ).fetchone()[0]
        walkforward_count = conn.execute(
            "SELECT COUNT(*) FROM mart_candidate_walkforward_eval WHERE run_id = 'test_walkforward'"
        ).fetchone()[0]
        pit_rows = conn.execute(
            "SELECT COUNT(*) AS n, SUM(violation_rows) AS violations FROM mart_feature_pit_audit"
        ).fetchone()

        assert built["rows"] == 6
        assert sample["common_holder_network_count"] == 2
        assert sample["fund_holding_shares_tdx_f10"] == 1000.0
        assert sample["fund_holding_float_a_ratio_tdx_f10"] == pytest.approx(3.5)
        assert sample["fund_holding_market_value_tdx_f10"] == pytest.approx(120.5)
        assert sample["holder_count_change_pct_tdx"] == -5.0
        assert sample["holder_count_acceleration_tdx"] == -3.0
        assert sample["top10_concentration_change"] == 5.0
        assert sample["tdx_inst_total_shares_qoq"] == pytest.approx(0.2)
        assert sample["contract_liabilities_to_revenue"] == pytest.approx(0.125)
        assert sample["ocf_to_profit_tdx"] == 2.0
        assert sample["social_security_shares_qoq"] == pytest.approx(1 / 7)
        assert sample["receivables_to_revenue"] == pytest.approx(0.25)
        assert sample["inventory_to_revenue"] == pytest.approx(0.125)
        assert sample["forecast_profit_yoy_mid"] == 30.0
        assert sample["forecast_range_width"] == 20.0
        assert sample["express_net_profit_yoy"] == pytest.approx(0.25)
        assert walkforward["feature_set_id"] == "tdx_f10_gpcw_v1"
        assert walkforward["rows"] == (
            walkforward["folds"] * walkforward["features"] * len(walkforward["labels"])
        )
        assert walkforward_count == walkforward["rows"]
        assert ablation["feature_set_id"] == "tdx_f10_gpcw_v1"
        assert len(ablation["groups"]) == 5
        assert elimination["promote_to_champion"] is False
        assert elimination_sql["method"] == "sql_walkforward_deterministic"
        assert score_count >= 1
        assert pit["status"] == "passed"
        assert pit_rows["n"] >= 1
        assert pit_rows["violations"] == 0
    finally:
        conn.close()


def test_mark_deprecated_assets_updates_metadata_without_dropping_tables():
    conn = duck_mem()
    try:
        retired_table = "market" + "_raw" + "_holdings"
        conn.execute(
            """
            CREATE TABLE dim_data_asset (
                table_name TEXT PRIMARY KEY,
                deprecation_status TEXT DEFAULT 'active',
                deprecated_at TEXT,
                deprecated_reason TEXT,
                replacement_table TEXT,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO dim_data_asset (table_name, deprecation_status) VALUES (?, 'active')",
            (retired_table,),
        )

        result = mark_deprecated_assets(conn)
        row = conn.execute(
            "SELECT deprecation_status, replacement_table FROM dim_data_asset WHERE table_name = ?",
            (retired_table,),
        ).fetchone()
        record_count = conn.execute(
            "SELECT COUNT(*) FROM mart_data_deprecation_record WHERE table_name = ?",
            (retired_table,),
        ).fetchone()[0]

        assert result["deprecated"][0]["table_name"] == retired_table
        assert row["deprecation_status"] == "deprecated"
        assert row["replacement_table"] == "fact_top10_holder_period"
        assert record_count == 1
    finally:
        conn.close()
