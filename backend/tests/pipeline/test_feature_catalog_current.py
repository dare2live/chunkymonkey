from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_feature_catalog_current as subject


pytestmark = pytest.mark.pipeline


def test_build_feature_catalog_current_records_pit_join_plan_and_exclusions():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                stock_code TEXT,
                date TEXT,
                ret_20d DOUBLE,
                ret_20d_xs_rank DOUBLE,
                shareholder_count_qoq DOUBLE,
                shareholder_plan_increase_count_180d INTEGER,
                forward_ret_60d DOUBLE,
                unknown_alpha DOUBLE,
                all_null_feature DOUBLE
            );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('000001', '2026-01-02', 0.1, 0.8, 0.05, 1, 0.2, 1.0, NULL),
                ('000002', '2026-01-02', 0.2, 0.9, NULL, 0, 0.3, 2.0, NULL);
            """
        )

        result = subject.build_feature_catalog_current(
            conn,
            run_id="feature_catalog_unit",
            feature_tables=["fact_feature_panel_candidate", "missing_feature_table"],
        )

        ret = conn.execute(
            """
            SELECT feature_family, registry_status, pit_risk_level,
                   source_available_date_column, allowed_in_production_research
              FROM mart_feature_catalog_current
             WHERE run_id = 'feature_catalog_unit'
               AND feature_table = 'fact_feature_panel_candidate'
               AND feature_name = 'ret_20d'
            """
        ).fetchone()
        transformed = conn.execute(
            """
            SELECT registry_status, pit_risk_level, allowed_in_production_research
              FROM mart_feature_catalog_current
             WHERE run_id = 'feature_catalog_unit'
               AND feature_name = 'ret_20d_xs_rank'
            """
        ).fetchone()
        fundamental_plan = conn.execute(
            """
            SELECT source_event_date_column, source_available_date_column,
                   lag_policy_days, join_policy
              FROM mart_feature_pit_join_plan
             WHERE run_id = 'feature_catalog_unit'
               AND feature_name = 'shareholder_count_qoq'
            """
        ).fetchone()
        unknown_reason = conn.execute(
            """
            SELECT reason_code, production_blocking
              FROM mart_feature_exclusion_reason
             WHERE run_id = 'feature_catalog_unit'
               AND feature_name = 'unknown_alpha'
             ORDER BY reason_code
            """
        ).fetchall()
        plan_join = conn.execute(
            """
            SELECT source_event_date_column, source_available_date_column,
                   join_policy, production_blocking
              FROM mart_feature_pit_join_plan
             WHERE run_id = 'feature_catalog_unit'
               AND feature_name = 'shareholder_plan_increase_count_180d'
            """
        ).fetchone()
        label_reason = conn.execute(
            """
            SELECT reason_code, production_blocking
              FROM mart_feature_exclusion_reason
             WHERE run_id = 'feature_catalog_unit'
               AND feature_name = 'forward_ret_60d'
            """
        ).fetchone()
        missing_table = conn.execute(
            """
            SELECT reason_code, production_blocking
              FROM mart_feature_exclusion_reason
             WHERE run_id = 'feature_catalog_unit'
               AND feature_table = 'missing_feature_table'
               AND feature_name = '*'
            """
        ).fetchone()
        manifest = conn.execute(
            """
            SELECT perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'feature_catalog_unit'
            """
        ).fetchone()

        assert result["catalog_rows"] == 9
        assert result["join_plan_rows"] == 9
        assert result["missing_tables"] == ["missing_feature_table"]
        assert ret["feature_family"] == "price_volume"
        assert ret["registry_status"] == "registered"
        assert ret["pit_risk_level"] == "low"
        assert ret["source_available_date_column"] == "date"
        assert ret["allowed_in_production_research"] is True
        assert transformed["registry_status"] == "transformed_from_registered:ret_20d"
        assert transformed["pit_risk_level"] == "low"
        assert transformed["allowed_in_production_research"] is True
        assert fundamental_plan["source_event_date_column"] == "report_date"
        assert fundamental_plan["source_available_date_column"] == "report_date_plus_90d"
        assert fundamental_plan["lag_policy_days"] == 90
        assert fundamental_plan["join_policy"] == "asof_source_available_date"
        assert plan_join["source_event_date_column"] == "source_notice_date"
        assert plan_join["source_available_date_column"] == "source_available_date"
        assert plan_join["join_policy"] == "asof_source_available_date"
        assert plan_join["production_blocking"] is False
        assert ("unknown_blocking", True) in [
            (row["reason_code"], row["production_blocking"]) for row in unknown_reason
        ]
        assert ("critical_pit_risk", True) in [
            (row["reason_code"], row["production_blocking"]) for row in unknown_reason
        ]
        assert label_reason["reason_code"] == "label_column"
        assert label_reason["production_blocking"] is True
        assert missing_table["reason_code"] == "table_missing"
        assert missing_table["production_blocking"] is True
        assert json.loads(manifest["perf_summary_json"])["catalog_rows"] == 9
