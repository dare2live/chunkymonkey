from __future__ import annotations

import pytest

from conftest import duck_mem
from scripts import audit_registry_feature_pit as subject


pytestmark = pytest.mark.pipeline


def _seed_model(conn, features: list[str]) -> None:
    conn.execute(
        """
        CREATE TABLE mart_multidim_model (
            model_id TEXT,
            feature_cols_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO mart_multidim_model VALUES ('model_1', ?)",
        ("[" + ",".join(f'"{feature}"' for feature in features) + "]",),
    )


def test_audit_registry_feature_pit_detects_fundamental_lag_violation():
    conn = duck_mem()
    try:
        _seed_model(conn, ["ret_20d", "yjyg_lower_pct"])
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE,
                yjyg_lower_pct DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('000001', '2025-07-01', 0.10, 8.0),
            ('000002', '2025-04-01', 0.20, 9.0)
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_fundamental_quarterly (
                stock_code TEXT,
                report_date TEXT,
                shareholder_count DOUBLE,
                inst_count DOUBLE,
                fund_count DOUBLE,
                qfii_count DOUBLE,
                yjyg_lower_pct DOUBLE,
                yjyg_upper_pct DOUBLE,
                roe DOUBLE,
                eps_basic DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_fundamental_quarterly VALUES
            ('000001', '20250331', 100, 10, 5, 1, 8.0, 9.0, 0.1, 0.2),
            ('000002', '20250331', 100, 10, 5, 1, 9.0, 10.0, 0.1, 0.2)
            """
        )

        result = subject.audit_registry_feature_pit(
            conn,
            model_id="model_1",
            feature_table="fact_feature_panel",
            audit_run_id="pit_unit",
        )
        rows = conn.execute(
            """
            SELECT feature_name, checked_rows, violation_rows, status
            FROM mart_feature_pit_audit
            WHERE audit_run_id = 'pit_unit'
            ORDER BY feature_name
            """
        ).fetchall()

        by_feature = {row["feature_name"]: dict(row) for row in rows}
        assert result["status"] == "failed"
        assert by_feature["ret_20d"]["violation_rows"] == 0
        assert by_feature["yjyg_lower_pct"]["checked_rows"] == 2
        assert by_feature["yjyg_lower_pct"]["violation_rows"] == 1
        assert by_feature["yjyg_lower_pct"]["status"] == "failed"
    finally:
        conn.close()


def test_audit_registry_feature_pit_records_missing_panel_column_as_violation():
    conn = duck_mem()
    try:
        _seed_model(conn, ["ret_20d", "missing_signal"])
        conn.execute("CREATE TABLE fact_feature_panel (stock_code TEXT, date TEXT, ret_20d DOUBLE)")
        conn.execute("INSERT INTO fact_feature_panel VALUES ('000001', '2025-07-01', 0.10)")

        result = subject.audit_registry_feature_pit(
            conn,
            model_id="model_1",
            feature_table="fact_feature_panel",
            audit_run_id="pit_missing_col",
        )
        row = conn.execute(
            """
            SELECT status, violation_rows
            FROM mart_feature_pit_audit
            WHERE audit_run_id = 'pit_missing_col' AND feature_name = 'missing_signal'
            """
        ).fetchone()

        assert result["status"] == "failed"
        assert row["status"] == "missing_panel_column"
        assert row["violation_rows"] == 1
    finally:
        conn.close()


def test_audit_registry_feature_pit_detects_future_event_count_leakage():
    conn = duck_mem()
    try:
        _seed_model(conn, ["exec_buy_count_90d", "days_since_exec_buy"])
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                exec_buy_count_90d INTEGER,
                days_since_exec_buy INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('000001', '2026-01-01', 1, 0),
            ('000001', '2026-01-03', 1, 1)
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_executive_trade_event (
                stock_code TEXT,
                notice_date TEXT,
                direction TEXT,
                total_change_pct_total DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_executive_trade_event VALUES
            ('000001', '2026-01-02', 'buy', 1.5)
            """
        )

        result = subject.audit_registry_feature_pit(
            conn,
            model_id="model_1",
            feature_table="fact_feature_panel",
            audit_run_id="pit_event_unit",
        )
        rows = conn.execute(
            """
            SELECT feature_name, checked_rows, violation_rows, status
            FROM mart_feature_pit_audit
            WHERE audit_run_id = 'pit_event_unit'
            ORDER BY feature_name
            """
        ).fetchall()
        by_feature = {row["feature_name"]: dict(row) for row in rows}

        assert result["status"] == "failed"
        assert by_feature["exec_buy_count_90d"]["checked_rows"] == 2
        assert by_feature["exec_buy_count_90d"]["violation_rows"] == 1
        assert by_feature["exec_buy_count_90d"]["status"] == "failed"
        assert by_feature["days_since_exec_buy"]["checked_rows"] == 2
        assert by_feature["days_since_exec_buy"]["violation_rows"] == 1
        assert by_feature["days_since_exec_buy"]["status"] == "failed"
    finally:
        conn.close()


def test_audit_registry_feature_pit_validates_tdx_shareholder_plan_features():
    conn = duck_mem()
    try:
        _seed_model(
            conn,
            [
                "shareholder_plan_increase_count_180d",
                "shareholder_plan_increase_amount_max_180d",
                "days_since_shareholder_plan_increase",
            ],
        )
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                shareholder_plan_increase_count_180d INTEGER,
                shareholder_plan_increase_amount_max_180d DOUBLE,
                days_since_shareholder_plan_increase INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('000001', '2026-01-03', 0, 0.0, -1),
            ('000001', '2026-01-04', 1, 3300000000.0, 0),
            ('000001', '2026-01-05', 1, 3300000000.0, 1)
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_shareholder_plan_tdx_f10 (
                stock_code TEXT,
                source_available_date TEXT,
                direction TEXT,
                progress TEXT,
                target_amount_min BIGINT,
                target_amount_max BIGINT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_shareholder_plan_tdx_f10 VALUES
            ('000001', '2026-01-04', '增持计划', '完成', 3000000000, 3300000000)
            """
        )

        result = subject.audit_registry_feature_pit(
            conn,
            model_id="model_1",
            feature_table="fact_feature_panel",
            audit_run_id="pit_plan_unit",
        )
        rows = conn.execute(
            """
            SELECT feature_name, checked_rows, violation_rows, status, source_table
              FROM mart_feature_pit_audit
             WHERE audit_run_id = 'pit_plan_unit'
             ORDER BY feature_name
            """
        ).fetchall()
        by_feature = {row["feature_name"]: dict(row) for row in rows}

        assert result["status"] == "passed"
        assert by_feature["shareholder_plan_increase_count_180d"]["source_table"] == "fact_shareholder_plan_tdx_f10"
        assert by_feature["shareholder_plan_increase_count_180d"]["violation_rows"] == 0
        assert by_feature["shareholder_plan_increase_amount_max_180d"]["violation_rows"] == 0
        assert by_feature["days_since_shareholder_plan_increase"]["violation_rows"] == 0
    finally:
        conn.close()


def test_audit_registry_feature_pit_accepts_explicit_feature_list_without_model_row():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                shareholder_plan_decrease_count_180d INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel VALUES
            ('000001', '2026-01-03', 0),
            ('000001', '2026-01-04', 1)
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_shareholder_plan_tdx_f10 (
                stock_code TEXT,
                source_available_date TEXT,
                direction TEXT,
                progress TEXT,
                target_amount_min BIGINT,
                target_amount_max BIGINT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_shareholder_plan_tdx_f10 VALUES
            ('000001', '2026-01-04', '减持计划', '进行中', NULL, 1000000)
            """
        )

        result = subject.audit_registry_feature_pit(
            conn,
            feature_names=["shareholder_plan_decrease_count_180d"],
            feature_table="fact_feature_panel",
            audit_run_id="pit_explicit_features_unit",
            audit_scope="explicit_feature_list",
        )
        summary = conn.execute(
            """
            SELECT audit_scope, failed_columns
              FROM mart_feature_pit_coverage_summary
             WHERE audit_run_id = 'pit_explicit_features_unit'
            """
        ).fetchone()

        assert result["model_id"] == "explicit_feature_list"
        assert result["status"] == "passed"
        assert summary["audit_scope"] == "explicit_feature_list"
        assert summary["failed_columns"] == 0
    finally:
        conn.close()


def test_audit_high_critical_feature_pit_blocks_unknown_candidate_fields():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE,
                yjyg_lower_pct DOUBLE,
                unknown_f10_value DOUBLE,
                forward_ret_60d DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_feature_panel_candidate VALUES
            ('set_1', '000001', '2025-07-01', 0.10, 8.0, 1.5, 0.03),
            ('set_1', '000002', '2025-04-01', 0.20, 9.0, 2.5, 0.04)
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_fundamental_quarterly (
                stock_code TEXT,
                report_date TEXT,
                shareholder_count DOUBLE,
                inst_count DOUBLE,
                fund_count DOUBLE,
                qfii_count DOUBLE,
                yjyg_lower_pct DOUBLE,
                yjyg_upper_pct DOUBLE,
                roe DOUBLE,
                eps_basic DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_fundamental_quarterly VALUES
            ('000001', '20250331', 100, 10, 5, 1, 8.0, 9.0, 0.1, 0.2),
            ('000002', '20250331', 100, 10, 5, 1, 9.0, 10.0, 0.1, 0.2)
            """
        )

        result = subject.audit_high_critical_feature_pit(
            conn,
            feature_table="fact_feature_panel_candidate",
            feature_set_id="set_1",
            audit_run_id="pit_all_unit",
        )
        rows = conn.execute(
            """
            SELECT feature_name, pit_risk_level, status
              FROM mart_feature_pit_audit
             WHERE audit_run_id = 'pit_all_unit'
             ORDER BY feature_name
            """
        ).fetchall()
        summary = conn.execute(
            """
            SELECT critical_risk_columns, unknown_blocking_columns, high_risk_columns
              FROM mart_feature_pit_coverage_summary
             WHERE audit_run_id = 'pit_all_unit'
            """
        ).fetchone()
        by_feature = {row["feature_name"]: dict(row) for row in rows}

        assert result["status"] == "failed"
        assert by_feature["unknown_f10_value"]["pit_risk_level"] == "critical"
        assert by_feature["unknown_f10_value"]["status"] == "unknown_blocking"
        assert by_feature["yjyg_lower_pct"]["pit_risk_level"] == "high"
        assert summary["critical_risk_columns"] >= 1
        assert summary["unknown_blocking_columns"] >= 1
        assert summary["high_risk_columns"] >= 1
    finally:
        conn.close()


def test_audit_high_critical_feature_pit_blocks_zero_coverage_high_risk_fields():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                yjyg_lower_pct DOUBLE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_fundamental_quarterly (
                stock_code TEXT,
                report_date TEXT,
                shareholder_count DOUBLE,
                inst_count DOUBLE,
                fund_count DOUBLE,
                qfii_count DOUBLE,
                yjyg_lower_pct DOUBLE,
                yjyg_upper_pct DOUBLE,
                roe DOUBLE,
                eps_basic DOUBLE
            )
            """
        )

        result = subject.audit_high_critical_feature_pit(
            conn,
            feature_table="fact_feature_panel_candidate",
            feature_set_id="empty_set",
            audit_run_id="pit_zero_coverage_unit",
        )
        row = conn.execute(
            """
            SELECT status, violation_rows, pit_risk_level
              FROM mart_feature_pit_audit
             WHERE audit_run_id = 'pit_zero_coverage_unit'
               AND feature_name = 'yjyg_lower_pct'
            """
        ).fetchone()

        assert result["status"] == "failed"
        assert row["pit_risk_level"] == "high"
        assert row["status"] == "zero_coverage_blocking"
        assert row["violation_rows"] == 1
    finally:
        conn.close()
