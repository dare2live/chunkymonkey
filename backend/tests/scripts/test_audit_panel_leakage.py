from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_panel_leakage.py"
SPEC = importlib.util.spec_from_file_location("audit_panel_leakage", SCRIPT_PATH)
audit_panel_leakage = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_panel_leakage
SPEC.loader.exec_module(audit_panel_leakage)


def test_pit_marker_check_flags_missing_markers_with_batched_schema() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE fact_trade_signal (stock_code TEXT, score DOUBLE)")
        conn.execute("CREATE TABLE mart_safe_panel (stock_code TEXT, built_at TIMESTAMP)")
        conn.execute("CREATE TABLE dim_stock_tdx_industry (stock_code TEXT, tdx_l1 TEXT)")

        findings = audit_panel_leakage.audit_check_1_pit_markers(conn)
    finally:
        conn.close()

    by_table = {finding["table"]: finding for finding in findings}
    assert by_table["fact_trade_signal"]["risk"] == "HIGH"
    assert by_table["dim_stock_tdx_industry"]["risk"] == "HIGH"
    assert "mart_safe_panel" not in by_table


def test_flat_mapping_partition_detects_non_pit_mapping_table(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    sql_path = tmp_path / "build_panel.sql"
    sql_path.write_text(
        """
        WITH src AS (
            SELECT stock_code, tdx_l1, signal_date
            FROM dim_stock_tdx_industry
        )
        SELECT avg(score) OVER (PARTITION BY tdx_l1 ORDER BY signal_date) AS sector_score
        FROM src
        """,
        encoding="utf-8",
    )
    try:
        conn.execute("CREATE TABLE dim_stock_tdx_industry (stock_code TEXT, tdx_l1 TEXT)")

        findings = audit_panel_leakage.audit_check_3_flat_mapping_partition(sql_path, conn)
    finally:
        conn.close()

    assert any(
        finding["check"] == "3_flat_mapping_partition"
        and finding["source_table"] == "dim_stock_tdx_industry"
        and finding["risk"] == "HIGH"
        for finding in findings
    )


def test_null_year_gradient_batches_feature_null_rates() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE panel (
                stock_code TEXT,
                signal_date DATE,
                feature_leaky DOUBLE,
                fwd_label DOUBLE,
                y_label DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO panel
            SELECT '000001', DATE '2024-01-01' + CAST(i AS INTEGER), NULL, 1.0, 1.0
              FROM range(10) AS t(i)
            UNION ALL
            SELECT '000001', DATE '2025-01-01' + CAST(i AS INTEGER), 1.0, 1.0, 1.0
              FROM range(10) AS t(i)
            """
        )

        findings = audit_panel_leakage.audit_check_6_null_year_gradient(conn, "panel")
    finally:
        conn.close()

    assert any(
        finding["feature"] == "feature_leaky"
        and finding["risk"] == "HIGH"
        and finding["yearly_null_pct"] == {2024: 100.0, 2025: 0.0}
        for finding in findings
    )
    assert all(finding.get("feature") not in {"fwd_label", "y_label"} for finding in findings)


def test_forward_index_grep_uses_combined_pattern_and_skips_comments(tmp_path: Path) -> None:
    path = tmp_path / "feature.py"
    path.write_text(
        """
        # df["x"].shift(-1) is only documentation here
        def build(df):
            return df["x"].shift(-1)
        """,
        encoding="utf-8",
    )

    findings = audit_panel_leakage._forward_index_findings_for_file(path)

    assert len(findings) == 1
    assert findings[0]["check"] == "7_forward_index"
    assert findings[0]["risk"] == "HIGH"
