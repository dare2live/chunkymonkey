from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import run_multidim_walkforward
from scripts import train_multidim_model
from services.duck_adapter import connect as duck_connect
from services.ml_lifecycle import registry as lifecycle_registry


pytestmark = [pytest.mark.integration, pytest.mark.pipeline]


def _seed_e2e_db(db_path: Path) -> None:
    conn = duck_connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                regime_flag TEXT,
                forward_ret_20d DOUBLE,
                ret_20d DOUBLE,
                ret_20d_rank DOUBLE
            );
            CREATE TABLE mart_model_selection_run (
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
            );
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                deployed_at TIMESTAMP,
                retired_at TIMESTAMP,
                promoted_from TEXT,
                ic_holdout DOUBLE,
                ic_walkforward_avg DOUBLE,
                ic_walkforward_std DOUBLE,
                drift_score DOUBLE,
                deploy_decision_notes TEXT,
                training_config TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            INSERT INTO mart_model_selection_run VALUES (
                'selection_e2e', 'production_registry', 'unit_selected_features',
                'forward_ret_20d', 1.0, '["ret_20d", "ret_20d_rank"]',
                '[]', 1, '{}', '2026-05-06'
            )
            """
        )
        rows = []
        start = date(2026, 1, 1)
        for day_idx in range(24):
            day = (start + timedelta(days=day_idx)).isoformat()
            for stock_idx in range(8):
                ret_20d = float(stock_idx) / 10.0 + day_idx * 0.01
                ret_20d_rank = float(stock_idx + 1) / 8.0
                label = 0.04 * ret_20d + 0.02 * ret_20d_rank + (day_idx % 3) * 0.001
                rows.append(
                    (
                        f"000{stock_idx + 1:03d}",
                        day,
                        "up" if day_idx % 3 == 0 else "flat",
                        label,
                        ret_20d,
                        ret_20d_rank,
                    )
                )
        conn.executemany("INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def test_model_selection_train_and_walkforward_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "model_selection_e2e.duckdb"
    model_dir = tmp_path / "models"
    _seed_e2e_db(db_path)

    def get_test_conn():
        return duck_connect(str(db_path))

    monkeypatch.setattr(train_multidim_model, "get_conn", get_test_conn)
    monkeypatch.setattr(run_multidim_walkforward, "get_conn", get_test_conn)
    monkeypatch.setattr(lifecycle_registry, "get_conn", get_test_conn)
    def model_dir_factory() -> Path:
        model_dir.mkdir(parents=True, exist_ok=True)
        return model_dir

    monkeypatch.setattr(train_multidim_model, "_model_dir", model_dir_factory)

    train_argv = [
        "train_multidim_model.py",
        "--start",
        "2026-01-01",
        "--end",
        "2026-01-24",
        "--feature-group",
        "model_selection_run",
        "--model-selection-run-id",
        "selection_e2e",
        "--model-id-prefix",
        "e2e",
        "--fixed-params-json",
        json.dumps(
            {
                "learning_rate": 0.05,
                "num_leaves": 7,
                "min_data_in_leaf": 2,
                "feature_fraction": 1.0,
                "bagging_fraction": 1.0,
                "bagging_freq": 0,
                "lambda_l1": 0.0,
                "lambda_l2": 0.0,
                "max_depth": 3,
            }
        ),
        "--num-round",
        "5",
        "--num-threads",
        "1",
    ]
    monkeypatch.setattr(sys, "argv", train_argv)
    train_multidim_model.main()

    conn = duck_connect(str(db_path))
    try:
        model_row = conn.execute(
            """
            SELECT model_id, n_features, feature_cols_json, feature_schema_version
              FROM mart_multidim_model
             WHERE model_id LIKE 'e2e_model_selection_run_%'
             ORDER BY created_at DESC
             LIMIT 1
            """
        ).fetchone()
        assert model_row is not None
        model_id = model_row["model_id"]
        assert model_row["n_features"] == 2
        assert json.loads(model_row["feature_cols_json"]) == ["ret_20d", "ret_20d_rank"]
        assert "model_selection_selection_e2e" in model_row["feature_schema_version"]

        lifecycle = conn.execute(
            "SELECT status, training_config FROM mart_model_lifecycle WHERE model_id = ?",
            [model_id],
        ).fetchone()
        assert lifecycle["status"] == "challenger"
        assert json.loads(lifecycle["training_config"])["model_selection_run_id"] == "selection_e2e"

        train_manifest = conn.execute(
            """
            SELECT pipeline_name, input_tables_json, output_tables_json, perf_summary_json
              FROM mart_pipeline_run_manifest
             WHERE run_id = ?
            """,
            [model_id],
        ).fetchone()
        assert train_manifest["pipeline_name"] == "train_multidim_model"
        assert "mart_model_selection_run" in train_manifest["input_tables_json"]
        assert "mart_multidim_prediction" in train_manifest["output_tables_json"]
        assert json.loads(train_manifest["perf_summary_json"])["model_search_mode"] == "fixed_params"
    finally:
        conn.close()

    walk_argv = [
        "run_multidim_walkforward.py",
        "--model-id",
        model_id,
        "--start",
        "2026-01-01",
        "--end",
        "2026-01-24",
        "--feature-group",
        "model_selection_run",
        "--model-selection-run-id",
        "selection_e2e",
        "--train-days",
        "8",
        "--valid-days",
        "4",
        "--test-days",
        "4",
        "--step-days",
        "4",
        "--max-folds",
        "2",
        "--walkforward-num-round",
        "5",
        "--prediction-mode",
        "topk",
        "--prediction-top-k",
        "2",
    ]
    monkeypatch.setattr(sys, "argv", walk_argv)
    run_multidim_walkforward.main()

    conn = duck_connect(str(db_path))
    try:
        fold_count = conn.execute(
            "SELECT COUNT(*) FROM mart_model_walkforward_fold WHERE model_id = ?",
            [model_id],
        ).fetchone()[0]
        prediction_count = conn.execute(
            "SELECT COUNT(*) FROM mart_model_walkforward_prediction"
        ).fetchone()[0]
        wf_manifest = conn.execute(
            """
            SELECT run_id, perf_summary_json, input_tables_json, output_tables_json
              FROM mart_pipeline_run_manifest
             WHERE pipeline_name = 'run_multidim_walkforward'
             ORDER BY created_at DESC
             LIMIT 1
            """
        ).fetchone()
        perf = json.loads(wf_manifest["perf_summary_json"])

        assert fold_count == 2
        assert prediction_count == 16
        assert "mart_model_selection_run" in wf_manifest["input_tables_json"]
        assert "mart_model_walkforward_prediction" in wf_manifest["output_tables_json"]
        assert perf["model_selection_run_id"] == "selection_e2e"
        assert perf["prediction_mode"] == "topk"
        assert perf["prediction_rows_written"] == 16
        assert len(perf["fold_metrics"]) == 2
    finally:
        conn.close()
