from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import duck_mem
from scripts import plan_research_schedule as subject
from services.pipeline_manifest import ensure_pipeline_manifest_schema


pytestmark = pytest.mark.pipeline


def _write_config(path: Path) -> None:
    path.write_text(
        """
version: 1
defaults:
  python: python3
  backend_dir: backend
research_schedule:
  - task_id: completed_model
    task_type: model_stability
    priority: 10
    enabled: true
    evidence:
      table: mart_model_stability_search_summary
      key_column: run_id
      run_id: done_run
    command:
      script: run_optuna_model_stability_search.py
      args:
        --run-id: done_run
        --no-resume: true

  - task_id: planned_large_model
    task_type: model_stability
    priority: 20
    enabled: true
    depends_on:
      - completed_model
    evidence:
      table: mart_model_stability_search_summary
      key_column: run_id
      run_id: missing_run
    command:
      script: run_optuna_model_stability_search.py
      args:
        --run-id: missing_run
        --trials: 80
    resources:
      feature_table: fact_feature_panel

  - task_id: blocked_champion_eval
    task_type: champion_candidate_eval
    priority: 30
    enabled: true
    depends_on:
      - planned_large_model
    evidence:
      table: mart_champion_candidate_evaluation
      key_column: evaluation_run_id
      run_id: candidate_eval_missing
    command:
      script: evaluate_champion_candidate.py
      args:
        --model-id: model_x

  - task_id: deferred_large_batch
    task_type: drift_safe_batch
    priority: 35
    enabled: true
    deferred: true
    defer_reason: wait for drift mitigation
    evidence:
      table: mart_drift_safe_candidate_batch_summary
      key_column: batch_run_id
      run_id: deferred_missing
    command:
      script: run_drift_safe_candidate_batch.py
      args:
        --batch-run-id: deferred_missing

  - task_id: disabled_template
    task_type: champion_candidate_eval
    priority: 40
    enabled: false
    evidence:
      table: mart_champion_candidate_evaluation
      key_column: evaluation_run_id
      run_id: disabled_missing
    command:
      script: evaluate_champion_candidate.py
      args:
        --model-id: REPLACE
""",
        encoding="utf-8",
    )


def test_plan_research_schedule_persists_statuses_commands_and_manifest(tmp_path):
    config_path = tmp_path / "model_search.yaml"
    _write_config(config_path)

    with duck_mem() as conn:
        conn.execute("CREATE TABLE mart_model_stability_search_summary (run_id TEXT, built_at TEXT)")
        conn.execute(
            "INSERT INTO mart_model_stability_search_summary VALUES ('done_run', '2026-05-06T00:00:00')"
        )
        result = subject.plan_research_schedule(
            conn,
            config_path=config_path,
            run_id="research_schedule_unit",
        )

        assert result["status_counts"] == {
            "completed": 1,
            "planned": 1,
            "blocked": 1,
            "deferred": 1,
            "disabled": 1,
        }

        rows = conn.execute(
            """
            SELECT task_id, status, evidence_found, command_json, command_text, reason
              FROM mart_research_schedule_plan
             WHERE run_id = 'research_schedule_unit'
             ORDER BY priority
            """
        ).fetchall()
        assert [row["status"] for row in rows] == ["completed", "planned", "blocked", "deferred", "disabled"]
        assert rows[0]["evidence_found"] is True
        assert rows[1]["evidence_found"] is False
        assert "missing_run" in rows[1]["command_text"]
        assert "--no-resume" in rows[0]["command_text"]

        command = json.loads(rows[1]["command_json"])
        assert command["argv"][:2] == ["python3", "backend/scripts/run_optuna_model_stability_search.py"]
        assert "--trials" in command["argv"]
        assert "waiting for dependencies: planned_large_model" == rows[2]["reason"]
        assert rows[3]["reason"] == "wait for drift mitigation"

        manifest = conn.execute(
            """
            SELECT pipeline_name, status, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'research_schedule_unit'
            """
        ).fetchone()
        assert manifest["pipeline_name"] == "plan_research_schedule"
        assert manifest["status"] == "success"
        perf = json.loads(manifest["perf_summary_json"])
        assert perf["status_counts"]["planned"] == 1
        assert perf["ranker_policy_deferred"] == 0
        assert perf["ranker_policy"]["max_runtime_ratio_vs_regression"] == 2.0

        version = conn.execute(
            """
            SELECT actual_version
              FROM dim_schema_version
             WHERE table_name = 'mart_research_schedule_plan'
            """
        ).fetchone()
        assert version["actual_version"] == "v1"


def test_check_evidence_can_require_accepted_statuses():
    with duck_mem() as conn:
        conn.execute("CREATE TABLE evidence_table (run_id TEXT, status TEXT, built_at TEXT)")
        conn.execute("INSERT INTO evidence_table VALUES ('run_a', 'fail', '2026-05-06')")

        evidence = subject.check_evidence(
            conn,
            {
                "table": "evidence_table",
                "key_column": "run_id",
                "run_id": "run_a",
                "status_column": "status",
                "accepted_statuses": ["pass"],
            },
        )

        assert evidence["found"] is False
        assert evidence["status"] == "fail"
        assert "not accepted" in evidence["reason"]


def test_ranker_large_task_is_deferred_when_perf_is_slow_and_gate_failed(tmp_path):
    config_path = tmp_path / "model_search.yaml"
    config_path.write_text(
        """
version: 1
research_schedule:
  - task_id: completed_model
    task_type: model_stability
    priority: 10
    enabled: true
    evidence:
      table: mart_model_stability_search_summary
      key_column: run_id
      run_id: done_run
    command:
      script: run_optuna_model_stability_search.py

  - task_id: ranker_large
    task_type: model_stability
    priority: 20
    enabled: true
    depends_on:
      - completed_model
    evidence:
      table: mart_model_stability_search_summary
      key_column: run_id
      run_id: missing_ranker_large
    command:
      script: run_optuna_model_stability_search.py
      args:
        --model-family: lightgbm_ranker
        --trials: 60
    resources:
      model_family: lightgbm_ranker
""",
        encoding="utf-8",
    )
    with duck_mem() as conn:
        conn.execute(
            """
            CREATE TABLE mart_model_stability_search_summary (
                run_id TEXT,
                trials INTEGER,
                study_total_trials INTEGER,
                config_json TEXT,
                built_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_model_stability_search_summary VALUES
                ('done_run', 1, 1, '{}', '2026-05-06T00:00:00'),
                ('lgbm_large', 80, 80,
                 '{"model_family":"lightgbm","best_status":"fail","best_rejection_reason":"drawdown"}',
                 '2026-05-06T01:00:00'),
                ('ranker_failed', 60, 60,
                 '{"model_family":"lightgbm_ranker","best_status":"fail","best_rejection_reason":"walkforward stability / drawdown / psi"}',
                 '2026-05-06T02:00:00')
            """
        )
        ensure_pipeline_manifest_schema(conn)
        conn.execute(
            """
            INSERT INTO mart_pipeline_run_manifest
                (run_id, pipeline_name, status, started_at, duration_s, perf_summary_json)
            VALUES
                ('lgbm_large', 'run_optuna_model_stability_search', 'success',
                 '2026-05-06 01:00:00', 1000.0, '{"timing":{"train_s":500}}'),
                ('ranker_failed', 'run_optuna_model_stability_search', 'success',
                 '2026-05-06 02:00:00', 3000.0, '{"ranker_cache":{"entries":2,"hits":10,"misses":2}}')
            """
        )

        result = subject.plan_research_schedule(
            conn,
            config_path=config_path,
            run_id="ranker_policy_unit",
        )

        assert result["status_counts"] == {"completed": 1, "deferred": 1}
        assert result["ranker_policy_deferred"] == 1
        row = conn.execute(
            """
            SELECT status, reason
              FROM mart_research_schedule_plan
             WHERE run_id = 'ranker_policy_unit'
               AND task_id = 'ranker_large'
            """
        ).fetchone()
        assert row["status"] == "deferred"
        assert "runtime per trial" in row["reason"]


def test_ranker_policy_can_be_configured_to_allow_non_gate_slow_profile(tmp_path):
    config_path = tmp_path / "model_search.yaml"
    config_path.write_text(
        """
version: 1
ranker_policy:
  max_runtime_ratio_vs_regression: 10.0
  gate_failure_tokens:
    - psi
research_schedule:
  - task_id: completed_model
    task_type: model_stability
    priority: 10
    enabled: true
    evidence:
      table: mart_model_stability_search_summary
      key_column: run_id
      run_id: done_run
    command:
      script: run_optuna_model_stability_search.py

  - task_id: ranker_large
    task_type: model_stability
    priority: 20
    enabled: true
    depends_on:
      - completed_model
    evidence:
      table: mart_model_stability_search_summary
      key_column: run_id
      run_id: missing_ranker_large
    command:
      script: run_optuna_model_stability_search.py
      args:
        --model-family: lightgbm_ranker
        --trials: 60
    resources:
      model_family: lightgbm_ranker
""",
        encoding="utf-8",
    )
    with duck_mem() as conn:
        conn.execute(
            """
            CREATE TABLE mart_model_stability_search_summary (
                run_id TEXT,
                trials INTEGER,
                study_total_trials INTEGER,
                config_json TEXT,
                built_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_model_stability_search_summary VALUES
                ('done_run', 1, 1, '{}', '2026-05-06T00:00:00'),
                ('lgbm_large', 80, 80,
                 '{"model_family":"lightgbm","best_status":"fail","best_rejection_reason":"drawdown"}',
                 '2026-05-06T01:00:00'),
                ('ranker_failed', 60, 60,
                 '{"model_family":"lightgbm_ranker","best_status":"fail","best_rejection_reason":"weak score"}',
                 '2026-05-06T02:00:00')
            """
        )
        ensure_pipeline_manifest_schema(conn)
        conn.execute(
            """
            INSERT INTO mart_pipeline_run_manifest
                (run_id, pipeline_name, status, started_at, duration_s, perf_summary_json)
            VALUES
                ('lgbm_large', 'run_optuna_model_stability_search', 'success',
                 '2026-05-06 01:00:00', 1000.0, '{"timing":{"train_s":500}}'),
                ('ranker_failed', 'run_optuna_model_stability_search', 'success',
                 '2026-05-06 02:00:00', 3000.0, '{"ranker_cache":{"entries":2,"hits":10,"misses":2}}')
            """
        )

        result = subject.plan_research_schedule(
            conn,
            config_path=config_path,
            run_id="ranker_policy_config_unit",
        )

        assert result["status_counts"] == {"completed": 1, "planned": 1}
        assert result["ranker_policy_deferred"] == 0
        row = conn.execute(
            """
            SELECT status, reason
              FROM mart_research_schedule_plan
             WHERE run_id = 'ranker_policy_config_unit'
               AND task_id = 'ranker_large'
            """
        ).fetchone()
        assert row["status"] == "planned"
        manifest = conn.execute(
            """
            SELECT perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'ranker_policy_config_unit'
            """
        ).fetchone()
        perf = json.loads(manifest["perf_summary_json"])
        assert perf["ranker_policy"]["max_runtime_ratio_vs_regression"] == 10.0
        assert perf["ranker_policy"]["gate_failure_tokens"] == ["psi"]
