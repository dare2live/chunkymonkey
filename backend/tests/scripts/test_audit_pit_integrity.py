from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_pit_integrity.py"
SPEC = importlib.util.spec_from_file_location("audit_pit_integrity", SCRIPT_PATH)
audit_pit_integrity = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_pit_integrity
SPEC.loader.exec_module(audit_pit_integrity)


def _by_name(results):
    return {result.name: result for result in results}


def test_batch_write_anomaly_preserves_critical_fail_and_legacy_warn() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_per_stock_stage_strategy_optimal_pit (
                cutoff_date DATE,
                stock_code TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_per_stock_stage_strategy_optimal_pit VALUES
            (DATE '2026-01-01', '000001'),
            (DATE '2026-01-01', '000002')
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_per_stock_stage_strategy_optimal (
                built_at TIMESTAMP,
                stock_code TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_per_stock_stage_strategy_optimal VALUES
            (TIMESTAMP '2026-01-01 09:30:00', '000001'),
            (TIMESTAMP '2026-01-01 09:30:00', '000002')
            """
        )

        results = _by_name(audit_pit_integrity.check_batch_write_anomaly(conn))
    finally:
        conn.close()

    assert results["mart_per_stock_stage_strategy_optimal_pit"].status == "FAIL"
    assert results["mart_per_stock_stage_strategy_optimal_pit"].rows == 2
    assert results["mart_per_stock_stage_strategy_optimal"].status == "WARN"
    assert results["mart_per_stock_stage_strategy_optimal"].rows == 2


def test_oos_validity_preserves_tier_severity() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_per_stock_stage_strategy_optimal_pit (
                oos_period_start DATE,
                oos_period_end DATE,
                train_end_date DATE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_per_stock_stage_strategy_optimal_pit VALUES
            (DATE '2026-01-01', DATE '2026-01-31', DATE '2025-12-31'),
            (DATE '2026-02-01', DATE '2026-01-31', DATE '2026-02-15')
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_per_stock_stage_strategy_optimal (
                oos_period_start DATE,
                oos_period_end DATE,
                train_end_date DATE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_per_stock_stage_strategy_optimal VALUES
            (DATE '2026-02-01', DATE '2026-01-31', DATE '2026-02-15')
            """
        )

        results = _by_name(audit_pit_integrity.check_oos_validity(conn))
    finally:
        conn.close()

    assert results["mart_per_stock_stage_strategy_optimal_pit"].status == "FAIL"
    assert results["mart_per_stock_stage_strategy_optimal_pit"].rows == 1
    assert results["mart_per_stock_stage_strategy_optimal"].status == "WARN"
    assert results["mart_per_stock_stage_strategy_optimal"].rows == 1


def test_forward_leak_spot_check_flattens_cross_date_source_scan() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE fact_risk_factors (calc_date DATE)")
        conn.execute("CREATE TABLE fact_financial_pit_daily (trade_date DATE)")
        conn.execute("CREATE TABLE fact_capital_flow_pit_daily (trade_date DATE)")
        conn.execute("CREATE TABLE fact_signal_context (date DATE)")
        conn.execute("CREATE TABLE fact_technical_trigger (date DATE)")
        for table, column in audit_pit_integrity.FORWARD_LEAK_SOURCES:
            conn.execute(f"INSERT INTO {table} ({column}) VALUES (DATE '2024-01-01')")

        results = audit_pit_integrity.check_forward_leak_spot_check(conn)
    finally:
        conn.close()

    assert len(results) == 25
    assert {result.status for result in results} == {"PASS"}
    assert all("0 rows" in result.detail for result in results)
