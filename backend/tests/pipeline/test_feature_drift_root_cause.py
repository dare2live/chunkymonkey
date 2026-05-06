from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import build_feature_drift_root_cause as subject


pytestmark = pytest.mark.pipeline


def _seed_stability_tables(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE mart_model_stability_search_trial (
            run_id TEXT,
            trial_number INTEGER,
            model_family TEXT,
            objective_value DOUBLE,
            status TEXT,
            fold_metrics_json TEXT
        );
        CREATE TABLE mart_model_stability_search_summary (
            run_id TEXT,
            best_trial_number INTEGER,
            config_json TEXT
        );
        """
    )
    fold_metrics = [
        {
            "fold_id": 1,
            "test_start": "2026-01-01",
            "test_end": "2026-01-20",
            "feature_drift_psi_by_feature": {
                "stable": 0.05,
                "repeat_offender": 0.31,
                "severe_feature": 0.83,
            },
        },
        {
            "fold_id": 2,
            "test_start": "2026-02-01",
            "test_end": "2026-02-20",
            "feature_drift_psi_by_feature": {
                "repeat_offender": 0.42,
                "severe_feature": 0.91,
            },
        },
    ]
    conn.execute(
        "INSERT INTO mart_model_stability_search_trial VALUES (?, ?, ?, ?, ?, ?)",
        (
            "source_run_a",
            7,
            "lightgbm",
            0.12,
            "fail",
            json.dumps(fold_metrics, ensure_ascii=False),
        ),
    )
    config = {
        "model_family": "lightgbm",
        "best_metrics": {
            "holdout_feature_drift_psi_by_feature": {
                "repeat_offender": 0.36,
                "holdout_only": 0.28,
                "stable": 0.01,
            }
        },
    }
    conn.execute(
        "INSERT INTO mart_model_stability_search_summary VALUES (?, ?, ?)",
        ("source_run_a", 7, json.dumps(config, ensure_ascii=False)),
    )


def test_build_feature_drift_root_cause_persists_offenders_summary_and_manifest():
    with duck_mem() as conn:
        _seed_stability_tables(conn)

        result = subject.build_feature_drift_root_cause(
            conn,
            run_id="drift_root_unit",
            source_run_ids=["source_run_a"],
            psi_threshold=0.25,
        )

        assert result["detail_rows"] == 6
        assert result["summary_rows"] == 3
        assert result["severe_rows"] == 2
        assert result["top_features"][0]["feature_name"] == "severe_feature"

        detail = conn.execute(
            """
            SELECT scope, fold_id, feature_name, severity, psi_value
              FROM mart_feature_drift_root_cause
             WHERE run_id = 'drift_root_unit'
             ORDER BY psi_value DESC
            """
        ).fetchall()
        assert detail[0]["feature_name"] == "severe_feature"
        assert detail[0]["severity"] == "severe"
        assert {row["scope"] for row in detail} == {"holdout_best", "walkforward_fold"}

        summary = conn.execute(
            """
            SELECT offender_count, severe_count, recommendation, fold_ids_json
              FROM mart_feature_drift_root_cause_summary
             WHERE run_id = 'drift_root_unit'
               AND feature_name = 'repeat_offender'
            """
        ).fetchone()
        assert summary["offender_count"] == 3
        assert summary["severe_count"] == 0
        assert summary["recommendation"] == "winsorize_bucket_or_regime_split"
        assert json.loads(summary["fold_ids_json"]) == [1, 2]

        manifest = conn.execute(
            """
            SELECT pipeline_name, status, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = 'drift_root_unit'
            """
        ).fetchone()
        assert manifest["pipeline_name"] == "build_feature_drift_root_cause"
        assert manifest["status"] == "success"
        assert json.loads(manifest["perf_summary_json"])["summary_rows"] == 3

        versions = {
            row["table_name"]: row["actual_version"]
            for row in conn.execute(
                """
                SELECT table_name, actual_version
                  FROM dim_schema_version
                 WHERE table_name LIKE 'mart_feature_drift_root_cause%'
                """
            ).fetchall()
        }
        assert versions == {
            "mart_feature_drift_root_cause": "v1",
            "mart_feature_drift_root_cause_summary": "v1",
        }
