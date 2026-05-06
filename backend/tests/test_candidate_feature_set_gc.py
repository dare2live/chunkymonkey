from __future__ import annotations

import json

from conftest import duck_mem
from services.candidate_feature_set_gc import (
    execute_obsolete_candidate_feature_set_delete,
    plan_obsolete_candidate_feature_set_delete,
)


def test_candidate_feature_set_gc_deletes_rows_and_stale_model_outputs() -> None:
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                signal DOUBLE,
                built_at TEXT
            );
            INSERT INTO fact_feature_panel_candidate VALUES
                ('old_set', '000001', '2026-01-02', 1.0, '2026-05-06T00:00:00'),
                ('old_set', '000002', '2026-01-02', 2.0, '2026-05-06T00:00:00');

            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT,
                training_config TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('stale_model', 'challenger',
                 '{"feature_table":"fact_feature_panel_candidate","feature_set_id":"old_set"}'),
                ('prod_model', 'champion',
                 '{"feature_table":"fact_feature_panel","feature_set_id":null}');

            CREATE TABLE mart_daily_recommendation (
                snapshot_date TEXT,
                stock_code TEXT,
                model_id TEXT,
                is_primary BOOLEAN
            );
            INSERT INTO mart_daily_recommendation VALUES
                ('2026-01-02', '000001', 'stale_model', FALSE),
                ('2026-01-02', '000001', 'prod_model', TRUE);

            CREATE TABLE mart_model_selection_run (
                run_id TEXT,
                feature_set_id TEXT,
                selected_features_json TEXT
            );
            INSERT INTO mart_model_selection_run VALUES
                ('old_selection', 'old_set', '["signal"]');
            """
        )

        plan = plan_obsolete_candidate_feature_set_delete(conn)
        assert plan["feature_set_count"] == 1
        assert plan["stale_model_ids"] == ["stale_model"]
        assert plan["production_blockers"] == []

        result = execute_obsolete_candidate_feature_set_delete(
            conn,
            run_id="delete_candidate_unit",
            approve=True,
        )

        assert result["deleted_rows"] == 5
        assert conn.execute("SELECT COUNT(*) AS n FROM fact_feature_panel_candidate").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_model_lifecycle").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_daily_recommendation").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_model_selection_run").fetchone()["n"] == 0
        deletion_rows = conn.execute(
            "SELECT table_name, deleted_rows, verification_json FROM mart_data_deletion_record"
        ).fetchall()
        assert {row["table_name"] for row in deletion_rows} == {
            "fact_feature_panel_candidate",
            "mart_daily_recommendation",
            "mart_model_lifecycle",
            "mart_model_selection_run",
        }
        assert all(json.loads(row["verification_json"])["delete_policy"] for row in deletion_rows)


def test_candidate_feature_set_gc_blocks_production_references() -> None:
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                signal DOUBLE
            );
            INSERT INTO fact_feature_panel_candidate VALUES ('old_set', '000001', '2026-01-02', 1.0);
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT,
                training_config TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('bad_model', 'champion',
                 '{"feature_table":"fact_feature_panel_candidate","feature_set_id":"old_set"}');
            """
        )

        plan = plan_obsolete_candidate_feature_set_delete(conn)

        assert plan["production_blockers"] == [
            {
                "type": "production_model_status",
                "model_id": "bad_model",
                "status": "champion",
            }
        ]
