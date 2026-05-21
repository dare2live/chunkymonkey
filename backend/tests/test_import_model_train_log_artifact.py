from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from scripts.import_model_train_log_artifact import import_train_log_artifact


def _record() -> dict:
    return {
        "model_id": "m1",
        "run_id": "r1",
        "model_version": "v",
        "feature_version": "fv",
        "label_version": "lv",
        "train_start": "2023-01-01",
        "train_end": "2023-12-31",
        "n_train_rows": 10,
        "n_features": 3,
        "is_rank_ic": 0.1,
        "is_rank_ic_ir": 1.0,
        "is_ndcg5": 0.5,
        "is_ndcg10": 0.6,
        "is_ndcg20": 0.7,
        "oos_rank_ic_avg": 0.04,
        "oos_rank_ic_ir": 1.2,
        "seed": 42,
        "n_trials": 5,
        "n_windows": 2,
        "optuna_best_value": 0.3,
        "walk_forward_mode": "expanding_monthly",
        "metrics_json": {"windows": [1, 2]},
        "built_at": "2026-05-21T00:00:00Z",
    }


def test_import_train_log_artifact_replaces_same_model_run(tmp_path: Path):
    db_path = tmp_path / "local.duckdb"
    artifact = tmp_path / "train_log.json"
    artifact.write_text(json.dumps(_record()), encoding="utf-8")

    first = import_train_log_artifact(local_db=str(db_path), artifact_json=str(artifact), model_id="m1")
    second = import_train_log_artifact(local_db=str(db_path), artifact_json=str(artifact), model_id="m1")

    assert first["local_before"] == 0
    assert first["local_after"] == 1
    assert second["local_before"] == 1
    assert second["local_after"] == 1
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        row = conn.execute(
            "SELECT model_id, run_id, oos_rank_ic_avg, metrics_json FROM fact_model_train_log"
        ).fetchone()
        assert row[0] == "m1"
        assert row[1] == "r1"
        assert row[2] == 0.04
        assert json.loads(row[3]) == {"windows": [1, 2]}
    finally:
        conn.close()


def test_import_train_log_artifact_dry_run_does_not_write(tmp_path: Path):
    db_path = tmp_path / "local.duckdb"
    artifact = tmp_path / "train_log.json"
    artifact.write_text(json.dumps(_record()), encoding="utf-8")

    result = import_train_log_artifact(local_db=str(db_path), artifact_json=str(artifact), dry_run=True)

    assert result["status"] == "dry_run"
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='fact_model_train_log'"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fact_model_train_log").fetchone()[0] == 0
    finally:
        conn.close()


def test_import_train_log_artifact_rejects_model_mismatch(tmp_path: Path):
    db_path = tmp_path / "local.duckdb"
    artifact = tmp_path / "train_log.json"
    artifact.write_text(json.dumps(_record()), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact model_id mismatch"):
        import_train_log_artifact(local_db=str(db_path), artifact_json=str(artifact), model_id="other")
