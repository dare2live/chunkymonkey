from __future__ import annotations

from conftest import duck_mem
from services.recommendation_output_gc import (
    execute_obsolete_recommendation_output_delete,
    plan_obsolete_recommendation_output_delete,
)


def test_recommendation_output_gc_deletes_retired_model_outputs() -> None:
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_model_lifecycle (model_id TEXT, status TEXT);
            INSERT INTO mart_model_lifecycle VALUES
                ('old_m', 'retired'),
                ('champion_m', 'champion');
            CREATE TABLE mart_daily_recommendation (
                snapshot_date TEXT,
                stock_code TEXT,
                model_id TEXT,
                is_primary BOOLEAN,
                run_mode TEXT
            );
            INSERT INTO mart_daily_recommendation VALUES
                ('2026-05-06', '000001', 'old_m', TRUE, 'champion'),
                ('2026-05-06', '000002', 'champion_m', TRUE, 'champion');
            CREATE TABLE mart_daily_topk_view_cache (
                snapshot_date TEXT,
                stock_code TEXT,
                model_id TEXT,
                is_primary BOOLEAN,
                run_mode TEXT
            );
            INSERT INTO mart_daily_topk_view_cache VALUES
                ('2026-05-06', '000001', 'old_m', TRUE, 'champion');
            """
        )

        plan = plan_obsolete_recommendation_output_delete(conn)
        assert plan["candidate_count"] == 1
        assert plan["candidates"][0]["table_counts"] == {
            "mart_daily_recommendation": 1,
            "mart_daily_topk_view_cache": 1,
        }

        result = execute_obsolete_recommendation_output_delete(
            conn,
            run_id="delete_recommendation_unit",
            approve=True,
        )

        assert result["deleted_rows"] == 2
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM mart_daily_recommendation WHERE model_id = 'old_m'"
        ).fetchone()["n"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM mart_daily_recommendation WHERE model_id = 'champion_m'"
        ).fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM mart_data_deletion_record").fetchone()["n"] == 2
