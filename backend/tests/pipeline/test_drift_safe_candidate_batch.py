from __future__ import annotations

import json

from conftest import duck_mem
from scripts import run_drift_safe_candidate_batch as subject


def _seed_batch_inputs(conn) -> None:
    subject.ensure_tables(conn)
    conn.execute(
        """
        CREATE OR REPLACE TABLE mart_drift_safe_candidate_summary (
            run_id TEXT,
            candidate_ids_json TEXT,
            built_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE OR REPLACE TABLE mart_model_selection_run (
            run_id TEXT,
            feature_set_id TEXT,
            method TEXT,
            label_name TEXT,
            objective_score DOUBLE,
            selected_features_json TEXT,
            rejected_features_json TEXT,
            trials INTEGER,
            notes TEXT,
            built_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO mart_drift_safe_candidate_summary VALUES (
            'summary_1', '["candidate_a", "candidate_b"]', '2026-05-06'
        )
        """
    )
    rows = [
        ("candidate_a", "production_registry", "drift_safe_candidate_generator", "forward_ret_20d", 1.0, '["f1"]', "{}", 0, "{}", "2026-05-06"),
        ("candidate_b", "production_registry", "drift_safe_candidate_generator", "forward_ret_20d", 1.0, '["f2"]', "{}", 0, "{}", "2026-05-06"),
    ]
    conn.executemany("INSERT INTO mart_model_selection_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


def test_run_drift_safe_candidate_batch_records_summary_and_manifest():
    conn = duck_mem()
    try:
        _seed_batch_inputs(conn)
        calls = []

        def fake_evaluator(conn, **kwargs):
            calls.append(kwargs)
            candidate_id = kwargs["model_selection_run_id"]
            passed = candidate_id == "candidate_b"
            return {
                "run_id": kwargs["run_id"],
                "model_selection_run_id": candidate_id,
                "model_family": kwargs["model_family"],
                "trials": kwargs["trials"],
                "study_total_trials": 1,
                "best_trial_number": 0,
                "best_score": 0.20 if passed else 0.10,
                "best_status": "pass" if passed else "fail",
                "best_rejection_reason": None if passed else "holdout_rank_ic",
                "best_topk_size": 100,
                "best_params": {},
                "best_metrics": {
                    "holdout_rank_ic": 0.08 if passed else 0.01,
                    "holdout_long_short_spread": 0.02 if passed else -0.01,
                    "walkforward_avg_rank_ic": 0.03,
                    "walkforward_std_rank_ic": 0.02,
                    "walkforward_worst_topk_drawdown": -0.10,
                    "walkforward_worst_feature_drift_psi": 0.12,
                },
            }

        result = subject.run_drift_safe_candidate_batch(
            conn,
            candidate_summary_run_id="summary_1",
            batch_run_id="batch_1",
            model_families=["lightgbm"],
            topk_size_choices=[50, 100],
            evaluator=fake_evaluator,
        )

        rows = conn.execute(
            """
            SELECT candidate_id, status, objective_score, best_topk_size,
                   rejection_reason
              FROM mart_drift_safe_candidate_batch_eval
             WHERE batch_run_id = 'batch_1'
             ORDER BY candidate_id
            """
        ).fetchall()
        summary = conn.execute(
            """
            SELECT evaluated_count, pass_count, best_candidate_id,
                   best_stability_run_id, best_status, best_objective_score
              FROM mart_drift_safe_candidate_batch_summary
             WHERE batch_run_id = 'batch_1'
            """
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'batch_1'"
        ).fetchone()

        assert result["evaluated_count"] == 2
        assert result["pass_count"] == 1
        assert result["best_candidate_id"] == "candidate_b"
        assert len(calls) == 2
        assert calls[0]["topk_size_choices"] == [50, 100]
        assert [(row["candidate_id"], row["status"]) for row in rows] == [
            ("candidate_a", "fail"),
            ("candidate_b", "pass"),
        ]
        assert rows[1]["best_topk_size"] == 100
        assert summary["evaluated_count"] == 2
        assert summary["pass_count"] == 1
        assert summary["best_candidate_id"] == "candidate_b"
        assert summary["best_status"] == "pass"
        assert summary["best_objective_score"] == 0.20
        perf = json.loads(manifest["perf_summary_json"])
        assert perf["candidate_summary_run_id"] == "summary_1"
        assert perf["pass_count"] == 1
    finally:
        conn.close()


def test_load_candidate_ids_uses_latest_summary_by_default():
    conn = duck_mem()
    try:
        subject.ensure_tables(conn)
        conn.execute(
            """
            CREATE OR REPLACE TABLE mart_drift_safe_candidate_summary (
                run_id TEXT,
                candidate_ids_json TEXT,
                built_at TEXT
            )
            """
        )
        conn.execute("INSERT INTO mart_drift_safe_candidate_summary VALUES ('old', '[\"a\"]', '2026-05-05')")
        conn.execute("INSERT INTO mart_drift_safe_candidate_summary VALUES ('new', '[\"b\"]', '2026-05-06')")

        summary_run_id, candidate_ids = subject.load_candidate_ids(conn, candidate_summary_run_id=None)

        assert summary_run_id == "new"
        assert candidate_ids == ["b"]
    finally:
        conn.close()


def test_run_drift_safe_candidate_batch_infers_candidate_feature_set_ids():
    conn = duck_mem()
    try:
        _seed_batch_inputs(conn)
        conn.execute("UPDATE mart_model_selection_run SET feature_set_id = 'candidate_panel_a' WHERE run_id = 'candidate_a'")
        conn.execute("UPDATE mart_model_selection_run SET feature_set_id = 'candidate_panel_b' WHERE run_id = 'candidate_b'")
        calls = []

        def fake_evaluator(conn, **kwargs):
            calls.append(kwargs)
            return {
                "run_id": kwargs["run_id"],
                "model_selection_run_id": kwargs["model_selection_run_id"],
                "model_family": kwargs["model_family"],
                "trials": kwargs["trials"],
                "study_total_trials": 1,
                "best_trial_number": 0,
                "best_score": 0.10,
                "best_status": "fail",
                "best_rejection_reason": "unit",
                "best_topk_size": 50,
                "best_params": {},
                "best_metrics": {},
            }

        subject.run_drift_safe_candidate_batch(
            conn,
            candidate_summary_run_id="summary_1",
            batch_run_id="batch_feature_sets",
            feature_table="fact_feature_panel_candidate",
            evaluator=fake_evaluator,
        )

        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'batch_feature_sets'"
        ).fetchone()
        summary = conn.execute(
            """
            SELECT config_json
              FROM mart_drift_safe_candidate_batch_summary
             WHERE batch_run_id = 'batch_feature_sets'
            """
        ).fetchone()

        assert [call["feature_set_id"] for call in calls] == ["candidate_panel_a", "candidate_panel_b"]
        perf = json.loads(manifest["perf_summary_json"])
        config = json.loads(summary["config_json"])
        assert perf["candidate_feature_set_ids"] == {
            "candidate_a": "candidate_panel_a",
            "candidate_b": "candidate_panel_b",
        }
        assert config["candidate_feature_set_ids"] == perf["candidate_feature_set_ids"]
    finally:
        conn.close()


def test_run_drift_safe_candidate_batch_explicit_feature_set_id_overrides_inference():
    conn = duck_mem()
    try:
        _seed_batch_inputs(conn)
        conn.execute("UPDATE mart_model_selection_run SET feature_set_id = 'candidate_panel_a' WHERE run_id = 'candidate_a'")
        calls = []

        def fake_evaluator(conn, **kwargs):
            calls.append(kwargs)
            return {
                "run_id": kwargs["run_id"],
                "model_selection_run_id": kwargs["model_selection_run_id"],
                "model_family": kwargs["model_family"],
                "trials": kwargs["trials"],
                "study_total_trials": 1,
                "best_trial_number": 0,
                "best_score": 0.10,
                "best_status": "fail",
                "best_rejection_reason": "unit",
                "best_topk_size": 50,
                "best_params": {},
                "best_metrics": {},
            }

        subject.run_drift_safe_candidate_batch(
            conn,
            candidate_summary_run_id="summary_1",
            batch_run_id="batch_explicit_feature_set",
            feature_table="fact_feature_panel_candidate",
            feature_set_id="explicit_panel",
            evaluator=fake_evaluator,
        )

        assert [call["feature_set_id"] for call in calls] == ["explicit_panel", "explicit_panel"]
    finally:
        conn.close()
