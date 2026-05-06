import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from conftest import duck_mem
from routers.recommendation import _resolve_model_id
from scripts.build_tdx_keep_challenger_panel import build_panel
from scripts.evaluate_tdx_keep_promotion_gate import _rank_ic_gate, evaluate_gate
from scripts.run_daily_topk import load_latest_model_id
from services.ml_lifecycle.registry import select_default_model_id
from services.model_feature_schema import (
    TDX_KEEP_FEATURE_COLS,
    TDX_KEEP_OPTIONAL_WATCH_FEATURE_COLS,
    tdx_keep_challenger_feature_cols,
)


def _create_lifecycle_tables(conn):
    conn.execute(
        """
        CREATE TABLE mart_multidim_model (
            model_id TEXT PRIMARY KEY,
            created_at TEXT,
            holdout_rank_ic DOUBLE,
            holdout_long_short_spread DOUBLE,
            holdout_top_decile_avg DOUBLE,
            holdout_winrate_top DOUBLE,
            feature_schema_version TEXT,
            n_features INTEGER,
            feature_cols_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_model_lifecycle (
            model_id TEXT PRIMARY KEY,
            status TEXT,
            deployed_at TIMESTAMP,
            updated_at TIMESTAMP,
            ic_holdout DOUBLE,
            ic_walkforward_avg DOUBLE,
            ic_walkforward_std DOUBLE,
            drift_score DOUBLE,
            training_config TEXT,
            deploy_decision_notes TEXT
        )
        """
    )


def _create_passing_source_lineage_evidence(conn):
    conn.execute(
        """
        CREATE TABLE fact_top10_holder_period (
            stock_code TEXT,
            source_tier INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO fact_top10_holder_period VALUES
        ('000001', 1), ('000002', 1), ('000003', 1), ('000004', 1)
        """
    )
    conn.execute(
        """
        CREATE TABLE mart_tdx_gpcw_file_manifest (
            filename TEXT,
            source_tier INTEGER,
            status TEXT
        )
        """
    )
    conn.execute("INSERT INTO mart_tdx_gpcw_file_manifest VALUES ('gpcw20260331.dat', 1, 'success')")
    conn.execute(
        """
        CREATE TABLE mart_data_source_watermark (
            data_domain TEXT,
            source_name TEXT,
            source_tier INTEGER,
            fallback_active BOOLEAN,
            consecutive_failures INTEGER
        )
        """
    )
    conn.execute(
        """
        INSERT INTO mart_data_source_watermark VALUES
        ('holders_top10_float', 'tdxhub_holders', 1, FALSE, 0),
        ('financial_gpcw_8q', 'tdxhub_gpcw', 1, FALSE, 0)
        """
    )


def test_default_model_selection_uses_lifecycle_champion_not_latest_challenger():
    conn = duck_mem()
    try:
        _create_lifecycle_tables(conn)
        conn.execute(
            "INSERT INTO mart_multidim_model VALUES ('champion_model', '2026-04-01', 0.04, 0.02, 0.01, 0.55, 'm7', 2, '[]')"
        )
        conn.execute(
            "INSERT INTO mart_multidim_model VALUES ('tdx_keep_challenger_new', '2026-05-04', 0.08, 0.03, 0.02, 0.60, 'm8_tdx_keep_challenger_v1', 7, '[]')"
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('champion_model', 'champion', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('tdx_keep_challenger_new', 'challenger', NULL, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )

        assert select_default_model_id(conn) == ("champion_model", False)
        assert load_latest_model_id(conn) == "champion_model"
        assert _resolve_model_id(conn, None) == ("champion_model", False, "champion")
        assert _resolve_model_id(conn, "tdx_keep_challenger_new") == (
            "tdx_keep_challenger_new",
            False,
            "challenger",
        )
    finally:
        conn.close()


def test_tdx_keep_schema_excludes_auto_watch_pool_from_default_schema():
    cols = tdx_keep_challenger_feature_cols()
    for feature in TDX_KEEP_FEATURE_COLS:
        assert feature in cols
    for feature in TDX_KEEP_OPTIONAL_WATCH_FEATURE_COLS:
        assert feature not in cols


def test_rank_ic_gate_uses_absolute_candidate_gate_for_cross_horizon_comparison():
    ok, detail, reason = _rank_ic_gate(
        0.096,
        0.065,
        champion_label_name="forward_ret_20d",
        challenger_label_name="forward_ret_90d",
    )

    assert ok is True
    assert reason is None
    assert detail["horizon_mismatch"] is True
    assert detail["comparison_scope"] == "absolute_candidate_rank_ic_due_to_horizon_mismatch"
    assert detail["champion_holding_period"] == 20
    assert detail["challenger_holding_period"] == 90


def test_rank_ic_gate_keeps_uplift_rule_for_same_horizon_comparison():
    ok, detail, reason = _rank_ic_gate(
        0.096,
        0.065,
        champion_label_name="forward_ret_20d",
        challenger_label_name="forward_ret_20d",
    )

    assert ok is False
    assert "challenger rank_ic" in reason
    assert detail["horizon_mismatch"] is False
    assert detail["comparison_scope"] == "same_horizon_champion_uplift"


def test_build_tdx_keep_challenger_panel_keeps_champion_panel_untouched():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT, date TEXT, regime_flag TEXT,
                forward_ret_5d REAL, forward_ret_10d REAL, forward_ret_20d REAL, forward_ret_60d REAL,
                forward_ret_90d REAL,
                ret_1d REAL, ret_5d REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT, stock_code TEXT, date TEXT,
                forward_ret_5d REAL, forward_ret_10d REAL, forward_ret_20d REAL, forward_ret_60d REAL,
                forward_ret_90d REAL,
                forecast_profit_yoy_mid REAL,
                avg_float_shares_change_pct_tdx REAL,
                ocf_to_profit_tdx REAL,
                fund_shares_qoq REAL,
                forecast_range_width REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('000001','2026-04-01','up',0.01,0.02,0.03,0.04,0.05,0.1,0.2),
            ('000002','2026-04-01','up',0.01,0.02,0.03,0.04,0.05,0.3,0.4)
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel_candidate VALUES
            ('tdx_f10_gpcw_v1','000001','2026-04-01',0.01,0.02,0.03,0.04,0.05,10.0,20.0,1.5,0.2,30.0),
            ('tdx_f10_gpcw_v1','000002','2026-04-01',0.01,0.02,0.03,0.04,0.05,20.0,30.0,2.5,0.3,40.0)
            """
        )

        result = build_panel(conn, start_date="2026-01-01")
        rows = conn.execute(
            """
            SELECT ret_1d, forecast_profit_yoy_mid, ocf_to_profit_tdx, forward_ret_20d
            FROM fact_feature_panel_tdx_keep_challenger
            WHERE feature_set_id='tdx_keep_challenger_v1'
            ORDER BY stock_code
            """
        ).fetchall()
        original_count = conn.execute("SELECT COUNT(*) FROM fact_feature_panel").fetchone()[0]

        assert result["rows"]["n"] == 2
        assert result["tdx_keep_transform"] == "daily_cross_sectional_percent_rank"
        assert rows[0]["ret_1d"] == pytest.approx(0.1)
        assert rows[0]["forecast_profit_yoy_mid"] == pytest.approx(0.0)
        assert rows[0]["ocf_to_profit_tdx"] == pytest.approx(0.0)
        assert rows[0]["forward_ret_20d"] == pytest.approx(0.03)
        assert rows[1]["forecast_profit_yoy_mid"] == pytest.approx(1.0)
        assert rows[1]["ocf_to_profit_tdx"] == pytest.approx(1.0)
        assert original_count == 2
    finally:
        conn.close()


def test_promotion_gate_waits_when_shadow_or_drift_evidence_missing():
    conn = duck_mem()
    try:
        _create_lifecycle_tables(conn)
        conn.execute(
            "INSERT INTO mart_multidim_model VALUES ('champion_model', '2026-04-01', 0.04, 0.02, 0.01, 0.55, 'm7', 2, '[]')"
        )
        conn.execute(
            "INSERT INTO mart_multidim_model VALUES ('tdx_keep_challenger_new', '2026-05-04', 0.08, 0.03, 0.02, 0.60, 'm8_tdx_keep_challenger_v1', 7, '[]')"
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('champion_model', 'champion', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('tdx_keep_challenger_new', 'challenger', NULL, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute(
            """
            CREATE TABLE mart_feature_pit_audit (
                audit_run_id TEXT, violation_rows INTEGER
            )
            """
        )
        conn.execute("INSERT INTO mart_feature_pit_audit VALUES ('pit_tdx_f10_gpcw_v1', 0)")
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_tdx_keep_challenger (
                feature_set_id TEXT,
                forecast_profit_yoy_mid REAL,
                avg_float_shares_change_pct_tdx REAL,
                ocf_to_profit_tdx REAL,
                fund_shares_qoq REAL,
                forecast_range_width REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel_tdx_keep_challenger VALUES
            ('tdx_keep_challenger_v1', 1, 1, 1, 1, 1)
            """
        )

        result = evaluate_gate(conn, challenger_model_id="tdx_keep_challenger_new")

        assert result["promotion_status"] in {"WAIT", "FAIL"}
        assert result["gates"]["api_safety"]["status"] == "PASS"
        assert result["gates"]["shadow_topk"]["status"] == "WAIT"
    finally:
        conn.close()


def test_promotion_gate_passes_relative_rank_ic_and_incremental_drift_scope():
    conn = duck_mem()
    try:
        _create_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('champion_model', '2026-04-01', 0.0400, 0.0100, 0.0100, 0.55,
             'm7', 1, '["base_feature"]')
            """
        )
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('tdx_keep_challenger_new', '2026-05-04', 0.0421, 0.0105, 0.0110, 0.56,
             'm8_tdx_keep_challenger_v1', 2, '["base_feature", "forecast_profit_yoy_mid"]')
            """
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('champion_model', 'champion', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('tdx_keep_challenger_new', 'challenger', NULL, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute("CREATE TABLE mart_feature_pit_audit (audit_run_id TEXT, violation_rows INTEGER)")
        conn.execute("INSERT INTO mart_feature_pit_audit VALUES ('pit_tdx_f10_gpcw_v1', 0)")
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_tdx_keep_challenger (
                feature_set_id TEXT,
                forecast_profit_yoy_mid REAL,
                avg_float_shares_change_pct_tdx REAL,
                ocf_to_profit_tdx REAL,
                fund_shares_qoq REAL,
                forecast_range_width REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel_tdx_keep_challenger VALUES
            ('tdx_keep_challenger_v1', 1, 1, 1, 1, 1)
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_model_portfolio_summary (
                run_id TEXT, curve_id TEXT, curve_type TEXT, model_id TEXT,
                total_return DOUBLE, annualized_return DOUBLE, max_drawdown DOUBLE,
                sharpe DOUBLE, avg_turnover DOUBLE, cost_bps DOUBLE, built_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_model_portfolio_summary VALUES
            ('r1', 'c1', 'model_top20', 'champion_model', 0.1, 0.1, -0.10, 1.0, 0.1, 0, '2026-05-04'),
            ('r2', 'c2', 'model_top20', 'tdx_keep_challenger_new', 0.11, 0.11, -0.11, 1.1, 0.1, 0, '2026-05-04')
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_feature_drift (
                snapshot_at TIMESTAMP,
                model_id TEXT,
                feature TEXT,
                psi DOUBLE,
                n_train BIGINT,
                n_recent BIGINT,
                window_days INTEGER,
                severity TEXT,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_feature_drift VALUES
            (TIMESTAMP '2026-05-04 10:00:00', 'champion_model', 'base_feature', 0.30, 100, 20, 20, 'critical', NULL),
            (TIMESTAMP '2026-05-04 11:00:00', 'tdx_keep_challenger_new', 'base_feature', 0.31, 100, 20, 20, 'critical', NULL),
            (TIMESTAMP '2026-05-04 11:00:00', 'tdx_keep_challenger_new', 'forecast_profit_yoy_mid', 0.04, 100, 20, 20, 'ok', NULL)
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_daily_recommendation (
                snapshot_date TEXT, stock_code TEXT, model_id TEXT, run_mode TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO mart_daily_recommendation VALUES ('2026-05-04', '000001', 'tdx_keep_challenger_new', 'shadow')"
        )
        conn.execute(
            """
            CREATE TABLE mart_model_walkforward_fold (
                run_id TEXT,
                model_id TEXT,
                test_rank_ic DOUBLE,
                test_long_short_spread DOUBLE,
                quality_flag TEXT,
                built_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO mart_model_walkforward_fold VALUES ('wf_1', 'tdx_keep_challenger_new', ?, 0.02, 'ok', '2026-05-04')",
            [(0.030,), (0.031,), (0.029,), (0.032,)],
        )
        _create_passing_source_lineage_evidence(conn)

        result = evaluate_gate(conn, challenger_model_id="tdx_keep_challenger_new")

        assert result["promotion_status"] == "PASS"
        assert result["decision"] == "promote_ready"
        assert result["gates"]["rank_ic"]["status"] == "PASS"
        assert result["gates"]["drift"]["status"] == "PASS"
        assert result["gates"]["walk_forward"]["status"] == "PASS"
        assert result["gates"]["source_lineage"]["status"] == "PASS"
    finally:
        conn.close()


def test_promotion_gate_blocks_high_holder_fallback_ratio():
    conn = duck_mem()
    try:
        _create_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('champion_model', '2026-04-01', 0.0400, 0.0100, 0.0100, 0.55,
             'm7', 1, '["base_feature"]')
            """
        )
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('tdx_keep_challenger_new', '2026-05-04', 0.0500, 0.0200, 0.0110, 0.56,
             'm8_tdx_keep_challenger_v1', 2, '["base_feature", "forecast_profit_yoy_mid"]')
            """
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('champion_model', 'champion', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('tdx_keep_challenger_new', 'challenger', NULL, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute("CREATE TABLE mart_feature_pit_audit (audit_run_id TEXT, violation_rows INTEGER)")
        conn.execute("INSERT INTO mart_feature_pit_audit VALUES ('pit_tdx_f10_gpcw_v1', 0)")
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_tdx_keep_challenger (
                feature_set_id TEXT,
                forecast_profit_yoy_mid REAL,
                avg_float_shares_change_pct_tdx REAL,
                ocf_to_profit_tdx REAL,
                fund_shares_qoq REAL,
                forecast_range_width REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel_tdx_keep_challenger VALUES
            ('tdx_keep_challenger_v1', 1, 1, 1, 1, 1)
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_model_portfolio_summary (
                run_id TEXT, curve_id TEXT, curve_type TEXT, model_id TEXT,
                total_return DOUBLE, annualized_return DOUBLE, max_drawdown DOUBLE,
                sharpe DOUBLE, avg_turnover DOUBLE, cost_bps DOUBLE, built_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_model_portfolio_summary VALUES
            ('r1', 'c1', 'model_top20', 'champion_model', 0.1, 0.1, -0.10, 1.0, 0.1, 0, '2026-05-04'),
            ('r2', 'c2', 'model_top20', 'tdx_keep_challenger_new', 0.11, 0.11, -0.10, 1.1, 0.1, 0, '2026-05-04')
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_feature_drift (
                snapshot_at TIMESTAMP,
                model_id TEXT,
                feature TEXT,
                psi DOUBLE,
                n_train BIGINT,
                n_recent BIGINT,
                window_days INTEGER,
                severity TEXT,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_feature_drift VALUES
            (TIMESTAMP '2026-05-04 10:00:00', 'champion_model', 'base_feature', 0.01, 100, 20, 20, 'ok', NULL),
            (TIMESTAMP '2026-05-04 11:00:00', 'tdx_keep_challenger_new', 'base_feature', 0.01, 100, 20, 20, 'ok', NULL),
            (TIMESTAMP '2026-05-04 11:00:00', 'tdx_keep_challenger_new', 'forecast_profit_yoy_mid', 0.01, 100, 20, 20, 'ok', NULL)
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_daily_recommendation (
                snapshot_date TEXT, stock_code TEXT, model_id TEXT, run_mode TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO mart_daily_recommendation VALUES ('2026-05-04', '000001', 'tdx_keep_challenger_new', 'shadow')"
        )
        _create_passing_source_lineage_evidence(conn)
        conn.execute("INSERT INTO fact_top10_holder_period VALUES ('000099', 2)")

        result = evaluate_gate(conn, challenger_model_id="tdx_keep_challenger_new")

        assert result["promotion_status"] == "FAIL"
        assert result["decision"] == "reject"
        assert result["gates"]["source_lineage"]["status"] == "FAIL"
        assert any(item["gate"] == "source_lineage" for item in result["blockers"])
    finally:
        conn.close()


def test_promotion_gate_uses_model_feature_coverage_for_generic_panel():
    conn = duck_mem()
    try:
        _create_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('champion_model', '2026-04-01', 0.0400, 0.0100, 0.0100, 0.55,
             'm7', 1, '["ret_20d"]')
            """
        )
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('optuna_model_selection_new', '2026-05-04', 0.0500, 0.0200, 0.0110, 0.56,
             'model_selection_run', 2, '["ret_20d", "ma_ratio_60"]')
            """
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('champion_model', 'champion', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('optuna_model_selection_new', 'challenger', NULL, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute("CREATE TABLE mart_feature_pit_audit (audit_run_id TEXT, violation_rows INTEGER)")
        conn.execute("INSERT INTO mart_feature_pit_audit VALUES ('pit_registry_panel_v1', 0)")
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE,
                ma_ratio_60 DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('000001', '2026-04-01', 0.1, 1.1),
            ('000002', '2026-04-01', 0.2, 1.2)
            """
        )

        result = evaluate_gate(
            conn,
            challenger_model_id="optuna_model_selection_new",
            feature_table="fact_feature_panel",
            feature_set_id=None,
            pit_audit_run_id="pit_registry_panel_v1",
        )

        assert result["gates"]["coverage"]["status"] == "PASS"
        assert result["gates"]["coverage"]["feature_table"] == "fact_feature_panel"
        assert {row["feature"] for row in result["gates"]["coverage"]["features"]} == {
            "ret_20d",
            "ma_ratio_60",
        }
    finally:
        conn.close()


def test_promotion_gate_waits_when_requested_pit_audit_run_missing():
    conn = duck_mem()
    try:
        _create_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('champion_model', '2026-04-01', 0.0400, 0.0100, 0.0100, 0.55,
             'm7', 1, '["ret_20d"]')
            """
        )
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('optuna_model_selection_new', '2026-05-04', 0.0500, 0.0200, 0.0110, 0.56,
             'model_selection_run', 1, '["ret_20d"]')
            """
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('champion_model', 'champion', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('optuna_model_selection_new', 'challenger', NULL, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute("CREATE TABLE mart_feature_pit_audit (audit_run_id TEXT, violation_rows INTEGER)")
        conn.execute("CREATE TABLE fact_feature_panel (stock_code TEXT, date TEXT, ret_20d DOUBLE)")
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-04-01', 0.1)")

        result = evaluate_gate(
            conn,
            challenger_model_id="optuna_model_selection_new",
            feature_table="fact_feature_panel",
            feature_set_id=None,
            pit_audit_run_id="missing_audit_run",
        )

        assert result["gates"]["PIT"]["status"] == "WAIT"
        assert any(item["gate"] == "PIT" for item in result["blockers"])
    finally:
        conn.close()


def test_promotion_gate_blocks_missing_generic_model_feature_column():
    conn = duck_mem()
    try:
        _create_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('champion_model', '2026-04-01', 0.0400, 0.0100, 0.0100, 0.55,
             'm7', 1, '["ret_20d"]')
            """
        )
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('optuna_model_selection_new', '2026-05-04', 0.0500, 0.0200, 0.0110, 0.56,
             'model_selection_run', 2, '["ret_20d", "missing_signal"]')
            """
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('champion_model', 'champion', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('optuna_model_selection_new', 'challenger', NULL, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute("CREATE TABLE mart_feature_pit_audit (audit_run_id TEXT, violation_rows INTEGER)")
        conn.execute("INSERT INTO mart_feature_pit_audit VALUES ('pit_registry_panel_v1', 0)")
        conn.execute("CREATE TABLE fact_feature_panel (stock_code TEXT, date TEXT, ret_20d DOUBLE)")
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-04-01', 0.1)")

        result = evaluate_gate(
            conn,
            challenger_model_id="optuna_model_selection_new",
            feature_table="fact_feature_panel",
            feature_set_id=None,
            pit_audit_run_id="pit_registry_panel_v1",
        )

        assert result["gates"]["coverage"]["status"] == "FAIL"
        assert any(row["status"] == "missing_column" for row in result["gates"]["coverage"]["features"])
        assert any(item["gate"] == "coverage" for item in result["blockers"])
    finally:
        conn.close()


def test_promotion_gate_waits_for_insufficient_walkforward_evidence():
    conn = duck_mem()
    try:
        _create_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('champion_model', '2026-04-01', 0.0400, 0.0100, 0.0100, 0.55,
             'm7', 1, '["ret_20d"]')
            """
        )
        conn.execute(
            """
            INSERT INTO mart_multidim_model VALUES
            ('optuna_model_selection_new', '2026-05-04', 0.0500, 0.0200, 0.0110, 0.56,
             'model_selection_run', 1, '["ret_20d"]')
            """
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('champion_model', 'champion', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute(
            "INSERT INTO mart_model_lifecycle VALUES ('optuna_model_selection_new', 'challenger', NULL, CURRENT_TIMESTAMP, NULL, NULL, NULL, NULL, '{}', NULL)"
        )
        conn.execute("CREATE TABLE mart_feature_pit_audit (audit_run_id TEXT, violation_rows INTEGER)")
        conn.execute("INSERT INTO mart_feature_pit_audit VALUES ('pit_registry_panel_v1', 0)")
        conn.execute("CREATE TABLE fact_feature_panel (stock_code TEXT, date TEXT, ret_20d DOUBLE)")
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2026-04-01', 0.1)")
        conn.execute(
            """
            CREATE TABLE mart_model_walkforward_fold (
                run_id TEXT,
                model_id TEXT,
                test_rank_ic DOUBLE,
                test_long_short_spread DOUBLE,
                quality_flag TEXT,
                built_at TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO mart_model_walkforward_fold VALUES ('wf_1', 'optuna_model_selection_new', ?, 0.02, 'ok', '2026-05-04')",
            [(0.030,), (0.031,)],
        )

        result = evaluate_gate(
            conn,
            challenger_model_id="optuna_model_selection_new",
            feature_table="fact_feature_panel",
            feature_set_id=None,
            pit_audit_run_id="pit_registry_panel_v1",
        )

        assert result["gates"]["walk_forward"]["status"] == "WAIT"
        assert any(item["gate"] == "walk_forward" for item in result["blockers"])
    finally:
        conn.close()
