import sys
from pathlib import Path

import duckdb


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.schema_marts import ensure_mart_schema


def test_mart_data_lineage_view_aliases_mart_lineage_for_coverage_metric():
    conn = duckdb.connect(":memory:")
    try:
        ensure_mart_schema(conn)
        conn.execute(
            """
            INSERT INTO mart_lineage (
                lineage_id, output_table, input_tables, sql_text, sql_hash,
                last_status
            )
            VALUES (
                'mart_example/build_v1', 'mart_example', '["raw_example"]',
                'select 1', 'abc123', 'pending'
            )
            """
        )

        row = conn.execute(
            """
            SELECT lineage_id, mart_table, output_table, input_tables, last_status
              FROM mart_data_lineage
             WHERE lineage_id = 'mart_example/build_v1'
            """
        ).fetchone()

        assert row == (
            "mart_example/build_v1",
            "mart_example",
            "mart_example",
            '["raw_example"]',
            "pending",
        )
    finally:
        conn.close()


def test_ensure_mart_schema_creates_strategy_result_registry_lineage_columns():
    conn = duckdb.connect(":memory:")
    try:
        ensure_mart_schema(conn)

        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info('mart_strategy_result_registry')").fetchall()
        }

        assert {
            "result_id",
            "source_table",
            "source_pk",
            "result_type",
            "parent_result_id",
            "baseline_result_id",
            "sim_config_hash",
            "param_diff_json",
            "params_json",
            "lineage_url",
            "source_artifact_uri",
            "decision",
            "production_status",
            "evidence_json",
        }.issubset(cols)
    finally:
        conn.close()
