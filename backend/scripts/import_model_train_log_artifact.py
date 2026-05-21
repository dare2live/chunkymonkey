"""Import one fact_model_train_log JSON artifact into the local DuckDB."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ml_ranking.ddl import FACT_MODEL_TRAIN_LOG_TABLE, create_fact_model_train_log_ddl


TRAIN_LOG_COLUMNS = [
    "model_id",
    "run_id",
    "model_version",
    "feature_version",
    "label_version",
    "train_start",
    "train_end",
    "n_train_rows",
    "n_features",
    "is_rank_ic",
    "is_rank_ic_ir",
    "is_ndcg5",
    "is_ndcg10",
    "is_ndcg20",
    "oos_rank_ic_avg",
    "oos_rank_ic_ir",
    "seed",
    "n_trials",
    "n_windows",
    "optuna_best_value",
    "walk_forward_mode",
    "metrics_json",
    "built_at",
]


def import_train_log_artifact(
    *,
    local_db: str,
    artifact_json: str,
    model_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    artifact_path = Path(artifact_json)
    if not artifact_path.exists():
        raise FileNotFoundError(artifact_json)
    record = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("train-log artifact must contain one JSON object")

    artifact_model_id = str(record.get("model_id") or "")
    if not artifact_model_id:
        raise ValueError("train-log artifact missing model_id")
    if model_id and artifact_model_id != model_id:
        raise ValueError(f"artifact model_id mismatch: {artifact_model_id} != {model_id}")
    run_id = str(record.get("run_id") or "")
    if not run_id:
        raise ValueError("train-log artifact missing run_id")

    normalized = {column: _normalize_value(record.get(column)) for column in TRAIN_LOG_COLUMNS}
    conn = duckdb.connect(local_db)
    try:
        create_fact_model_train_log_ddl(conn)
        local_before = conn.execute(
            f"SELECT COUNT(*) FROM {FACT_MODEL_TRAIN_LOG_TABLE} WHERE model_id = ? AND run_id = ?",
            [artifact_model_id, run_id],
        ).fetchone()[0]
        result = {
            "artifact_json": str(artifact_path),
            "model_id": artifact_model_id,
            "run_id": run_id,
            "dry_run": dry_run,
            "local_before": int(local_before),
            "status": "dry_run" if dry_run else "imported",
        }
        if dry_run:
            return result

        columns_sql = ", ".join(TRAIN_LOG_COLUMNS)
        placeholders = ", ".join("?" for _ in TRAIN_LOG_COLUMNS)
        conn.execute("BEGIN")
        try:
            conn.execute(
                f"DELETE FROM {FACT_MODEL_TRAIN_LOG_TABLE} WHERE model_id = ? AND run_id = ?",
                [artifact_model_id, run_id],
            )
            conn.execute(
                f"INSERT INTO {FACT_MODEL_TRAIN_LOG_TABLE} ({columns_sql}) VALUES ({placeholders})",
                [normalized[column] for column in TRAIN_LOG_COLUMNS],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        local_after = conn.execute(
            f"SELECT COUNT(*) FROM {FACT_MODEL_TRAIN_LOG_TABLE} WHERE model_id = ? AND run_id = ?",
            [artifact_model_id, run_id],
        ).fetchone()[0]
        result["local_after"] = int(local_after)
        return result
    finally:
        conn.close()


def _normalize_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-db", default="data/smartmoney.duckdb")
    parser.add_argument("--artifact-json", required=True)
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = import_train_log_artifact(
        local_db=args.local_db,
        artifact_json=args.artifact_json,
        model_id=args.model_id,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
