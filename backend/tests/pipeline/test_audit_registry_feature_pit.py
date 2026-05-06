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
