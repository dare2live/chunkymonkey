from __future__ import annotations

import json

from conftest import duck_mem
from services.pipeline_manifest import record_pipeline_run
from services.pipeline_run_gc import (
    execute_obsolete_pipeline_performance_run_delete,
    plan_obsolete_pipeline_performance_run_delete,
)


def test_pipeline_gc_deletes_obsolete_slow_runs_but_blocks_champion_training() -> None:
    with duck_mem() as conn:
        conn.execute("CREATE TABLE mart_model_lifecycle (model_id TEXT, status TEXT)")
        conn.executemany(
            "INSERT INTO mart_model_lifecycle VALUES (?, ?)",
            [("champion_m", "champion"), ("old_m", "challenger")],
        )
        record_pipeline_run(
            conn,
            run_id="old_eval",
            pipeline_name="evaluate_champion_candidate",
            status="failed",
            started_at="2026-05-06T00:00:00",
            duration_s=60.0,
            model_id="old_m",
            perf_summary={"config": {}},
        )
        record_pipeline_run(
            conn,
            run_id="champion_train",
            pipeline_name="train_multidim_model",
            status="success",
            started_at="2026-05-06T00:01:00",
            duration_s=1200.0,
            model_id="champion_m",
            perf_summary={"n_train": 1},
        )

        plan = plan_obsolete_pipeline_performance_run_delete(conn)

        assert [row["run_id"] for row in plan["offenders"]] == ["old_eval"]
        assert [row["run_id"] for row in plan["blocked"]] == ["champion_train"]


def test_pipeline_gc_accepts_slow_runs_with_timings_key() -> None:
    with duck_mem() as conn:
        record_pipeline_run(
            conn,
            run_id="slow_train_with_timings",
            pipeline_name="train_multidim_model",
            status="success",
            started_at="2026-05-06T00:00:00",
            duration_s=90.0,
            model_id="challenger_m",
            perf_summary={"timings": {"load_panel_s": 10.0, "train_s": 80.0}},
        )

        plan = plan_obsolete_pipeline_performance_run_delete(conn)

        assert plan["offender_count"] == 0
        assert plan["blocked_count"] == 0


def test_pipeline_gc_executes_direct_delete_with_ledger() -> None:
    with duck_mem() as conn:
        record_pipeline_run(
            conn,
            run_id="old_eval",
            pipeline_name="evaluate_champion_candidate",
            status="failed",
            started_at="2026-05-06T00:00:00",
            duration_s=60.0,
            model_id="old_m",
            perf_summary={"config": {}},
        )

        result = execute_obsolete_pipeline_performance_run_delete(
            conn,
            run_id="delete_pipeline_unit",
            approve=True,
        )

        assert result["deleted_rows"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM mart_pipeline_run_manifest WHERE run_id = 'old_eval'"
        ).fetchone()["n"] == 0
        ledger = conn.execute(
            "SELECT verification_json FROM mart_data_deletion_record WHERE deletion_run_id = 'delete_pipeline_unit'"
        ).fetchone()
        assert json.loads(ledger["verification_json"])["run_id"] == "old_eval"
