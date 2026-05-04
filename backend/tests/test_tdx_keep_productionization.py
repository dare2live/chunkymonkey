import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from conftest import duck_mem
from routers.recommendation import _resolve_model_id
from scripts.build_tdx_keep_challenger_panel import build_panel
from scripts.evaluate_tdx_keep_promotion_gate import evaluate_gate
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


def test_build_tdx_keep_challenger_panel_keeps_champion_panel_untouched():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT, date TEXT, regime_flag TEXT,
                forward_ret_5d REAL, forward_ret_10d REAL, forward_ret_20d REAL, forward_ret_60d REAL,
                ret_1d REAL, ret_5d REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT, stock_code TEXT, date TEXT,
                forward_ret_5d REAL, forward_ret_10d REAL, forward_ret_20d REAL, forward_ret_60d REAL,
                forecast_profit_yoy_mid REAL,
                avg_float_shares_change_pct_tdx REAL,
                ocf_to_profit_tdx REAL,
                fund_shares_qoq REAL,
                forecast_range_width REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO fact_feature_panel VALUES ('000001','2026-04-01','up',0.01,0.02,0.03,0.04,0.1,0.2)"
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel_candidate VALUES
            ('tdx_f10_gpcw_v1','000001','2026-04-01',0.01,0.02,0.03,0.04,10.0,20.0,1.5,0.2,30.0)
            """
        )

        result = build_panel(conn, start_date="2026-01-01")
        row = conn.execute(
            """
            SELECT ret_1d, forecast_profit_yoy_mid, ocf_to_profit_tdx, forward_ret_20d
            FROM fact_feature_panel_tdx_keep_challenger
            WHERE feature_set_id='tdx_keep_challenger_v1'
            """
        ).fetchone()
        original_count = conn.execute("SELECT COUNT(*) FROM fact_feature_panel").fetchone()[0]

        assert result["rows"]["n"] == 1
        assert row["ret_1d"] == pytest.approx(0.1)
        assert row["forecast_profit_yoy_mid"] == pytest.approx(10.0)
        assert row["ocf_to_profit_tdx"] == pytest.approx(1.5)
        assert row["forward_ret_20d"] == pytest.approx(0.03)
        assert original_count == 1
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
