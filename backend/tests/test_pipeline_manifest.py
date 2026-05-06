from conftest import duck_mem
from scripts.benchmark_model_pipeline import benchmark_plan
from services.pipeline_manifest import (
    ensure_pipeline_manifest_schema,
    record_pipeline_run,
    table_row_counts,
)
from services.workbench_read import build_workbench_pipelines


def test_record_pipeline_run_writes_auditable_manifest():
    conn = duck_mem()
    try:
        conn.execute("CREATE TABLE fact_feature_panel (date TEXT, value INTEGER)")
        conn.execute("INSERT INTO fact_feature_panel VALUES ('2026-05-05', 1)")
        ensure_pipeline_manifest_schema(conn)

        record_pipeline_run(
            conn,
            run_id="run_1",
            pipeline_name="unit_pipeline",
            status="success",
            started_at="2026-05-05T00:00:00",
            ended_at="2026-05-05T00:00:01",
            duration_s=1.0,
            commit_sha="abc123",
            command="unit",
            cwd="/tmp/repo",
            input_tables=["fact_feature_panel"],
            output_tables=["mart_multidim_model"],
            model_id="model_1",
            feature_group="base_dense_v2",
            label_name="forward_ret_20d",
            holding_period=20,
            perf_summary={"rows": 1},
        )

        row = conn.execute(
            "SELECT run_id, input_row_counts_json, perf_summary_json "
            "FROM mart_pipeline_run_manifest WHERE run_id = 'run_1'"
        ).fetchone()

        assert row["run_id"] == "run_1"
        assert '"fact_feature_panel": 1' in row["input_row_counts_json"]
        assert '"rows": 1' in row["perf_summary_json"]
    finally:
        conn.close()


def test_table_row_counts_marks_missing_tables_as_none():
    conn = duck_mem()
    try:
        assert table_row_counts(conn, ["missing_table"]) == {"missing_table": None}
    finally:
        conn.close()


def test_workbench_pipelines_marks_warn_daily_run_as_blocker():
    conn = duck_mem()
    try:
        ensure_pipeline_manifest_schema(conn)
        record_pipeline_run(
            conn,
            run_id="cron_daily_unit",
            pipeline_name="cron_daily",
            status="warn",
            started_at="2026-05-05T00:00:00",
            ended_at="2026-05-05T00:01:30",
            duration_s=90.0,
            perf_summary={"phases": [{"phase": "sync", "status": "warn", "phase_elapsed_s": 90.0}]},
            blockers=[{"kind": "phase_warn", "phase": "sync"}],
        )

        result = build_workbench_pipelines(conn, limit=10)

        assert result["recent"][0]["run_id"] == "cron_daily_unit"
        assert result["recent"][0]["status"] == "warn"
        assert result["blockers"][0]["blocker_count"] == 1
        assert result["blockers"][0]["perf_summary"]["phases"][0]["phase"] == "sync"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def test_benchmark_plan_is_structural_and_uses_bulk_pipeline_scripts():
    plan = benchmark_plan("full", model_id="model_1")
    commands = [" ".join(step["command"]) for step in plan]

    assert [step["name"] for step in plan] == [
        "train_full_base_dense",
        "walkforward_full_2fold",
        "holding_topk_grid",
    ]
    assert any("train_multidim_model.py" in cmd and "--feature-group base_dense_v2" in cmd for cmd in commands)
    assert any("run_multidim_walkforward.py" in cmd and "--prediction-mode topk" in cmd for cmd in commands)
    assert any("evaluate_holding_topk.py" in cmd for cmd in commands)
