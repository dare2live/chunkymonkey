#!/usr/bin/env python3
"""Weekly LambdaMART v6 retrain and prediction materialization.

This wrapper reuses the executable v6 Optuna walk-forward implementation in
run_p0b_lambdamart_v6.py, then retrains the best trial across each OOS window
and stores predictions in a dedicated v6 table for paper_sim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import shutil
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect
from services.ml_ranking.ddl import (
    FACT_MODEL_TRAIN_LOG_TABLE,
    FACT_MODEL_TRAIN_LOG_WINDOW_TABLE,
    LAMBDAMART_V6_PREDICTIONS_TABLE,
    create_fact_model_train_log_ddl,
    create_fact_model_train_log_window_ddl,
    create_lambdamart_v6_predictions_ddl,
)
from services.schema_versions import ensure_schema_version_table, record_actual_version

from scripts.run_p0b_lambdamart_v6 import (
    RankPanel,
    WindowSpec,
    build_walk_forward_windows,
    evaluate_predictions,
    load_rank_panel,
    run_optuna,
    load_warm_start_params,
    _fit_lambdamart_window_model,
    _prediction_frame,
    _run_lambdamart_window,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("retrain_lambdamart_v6")

LABEL_COLUMNS = ("fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d")
MODEL_VERSION = "v6.lambdamart"
DEFAULT_FEATURE_VERSION = "p0a_v4"
DEFAULT_LABEL_VERSION = "horizon_governance_v1"
_PREEMPT_SYNC_IN_PROGRESS = False


def _storage_sqlite_path(study_storage: str | None) -> Path | None:
    if not study_storage or not study_storage.startswith("sqlite:///"):
        return None
    return Path(study_storage.removeprefix("sqlite:///"))


def _write_sigterm_marker(
    *,
    model_id: str,
    checkpoint_path: str | None,
    study_storage: str | None,
    signum: int,
) -> Path:
    marker_dir = Path(checkpoint_path).parent if checkpoint_path else Path("data/reports/optuna")
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker_path = marker_dir / f"{model_id}.sigterm.json"
    payload = {
        "model_id": model_id,
        "signal": signum,
        "pid": os.getpid(),
        "checkpoint_path": checkpoint_path,
        "study_storage": study_storage,
        "received_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    tmp = marker_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(marker_path)
    return marker_path


def _sync_preempt_artifacts(
    *,
    model_id: str,
    checkpoint_path: str | None,
    study_storage: str | None,
    gcs_sync_uri: str | None,
    signum: int | None = None,
) -> None:
    """Best-effort copy of preemption artifacts to GCS within the short shutdown window."""

    global _PREEMPT_SYNC_IN_PROGRESS
    if not gcs_sync_uri or _PREEMPT_SYNC_IN_PROGRESS:
        return
    _PREEMPT_SYNC_IN_PROGRESS = True
    try:
        artifact_paths: list[Path] = []
        if signum is not None:
            artifact_paths.append(
                _write_sigterm_marker(
                    model_id=model_id,
                    checkpoint_path=checkpoint_path,
                    study_storage=study_storage,
                    signum=signum,
                )
            )
        if checkpoint_path:
            artifact_paths.append(Path(checkpoint_path))
        sqlite_path = _storage_sqlite_path(study_storage)
        if sqlite_path:
            artifact_paths.extend(
                [
                    sqlite_path,
                    sqlite_path.with_name(sqlite_path.name + "-wal"),
                    sqlite_path.with_name(sqlite_path.name + "-shm"),
                ]
            )

        if shutil.which("gcloud"):
            copier = ["gcloud", "storage", "cp"]
        elif shutil.which("gsutil"):
            copier = ["gsutil", "cp"]
        else:
            copier = None
        if copier is None:
            log.warning("GCS sync skipped: neither gcloud nor gsutil is available")
            return
        dest_dir = gcs_sync_uri.rstrip("/")
        for artifact_path in artifact_paths:
            if not artifact_path.exists() or not artifact_path.is_file():
                continue
            dest = f"{dest_dir}/{artifact_path.name}"
            try:
                subprocess.run([*copier, str(artifact_path), dest], check=False, timeout=8)
            except subprocess.TimeoutExpired:
                log.warning("GCS sync timed out for %s", artifact_path)
    finally:
        _PREEMPT_SYNC_IN_PROGRESS = False


def _install_sigterm_handler(
    *,
    model_id: str,
    checkpoint_path: str | None,
    study_storage: str | None,
    gcs_sync_uri: str | None,
) -> None:
    def _handle_sigterm(signum: int, _frame: Any) -> None:
        log.warning("received SIGTERM; syncing checkpoint artifacts before exit")
        _sync_preempt_artifacts(
            model_id=model_id,
            checkpoint_path=checkpoint_path,
            study_storage=study_storage,
            gcs_sync_uri=gcs_sync_uri,
            signum=signum,
        )
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, _handle_sigterm)


def make_model_id(model_date: str | None = None) -> str:
    """Return the production model_id for a daily LambdaMART v6 refresh."""

    if model_date is None:
        model_date = datetime.now().strftime("%Y%m%d")
    clean = model_date.replace("-", "")
    if len(clean) != 8 or not clean.isdigit():
        raise ValueError(f"model_date must be YYYYMMDD or YYYY-MM-DD, got {model_date!r}")
    return f"lambdamart_v6_{clean}"


def complete_lambdamart_params(
    best_params: dict[str, Any],
    *,
    seed: int,
    n_estimators: int,
) -> dict[str, Any]:
    """Add fixed LightGBM parameters omitted from Optuna's best_params."""

    n_jobs = int(os.environ.get("OMP_NUM_THREADS", "8"))
    params = dict(best_params)
    params.update(
        n_estimators=n_estimators,
        random_state=seed,
        verbose=-1,
        n_jobs=n_jobs,
        num_threads=n_jobs,
    )
    return params


def load_checkpoint_best_payload(checkpoint_path: str | Path) -> dict[str, Any]:
    """Load and validate a per-trial best checkpoint JSON."""

    path = Path(checkpoint_path)
    if not path.exists():
        raise FileNotFoundError(f"checkpoint not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    params = payload.get("best_params")
    if not isinstance(params, dict) or not params:
        raise ValueError(f"checkpoint missing non-empty best_params: {path}")
    return payload


def load_checkpoint_best_params(checkpoint_path: str | Path) -> dict[str, Any]:
    """Load best params from a per-trial checkpoint JSON."""

    return dict(load_checkpoint_best_payload(checkpoint_path)["best_params"])


def _stable_json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _date_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)[:10]


def train_log_window_key(window: WindowSpec) -> str:
    return "|".join(
        [
            str(window.train_start),
            str(window.train_end),
            str(window.test_start),
            str(window.test_end),
        ]
    )


def _window_identity(window: WindowSpec) -> dict[str, str]:
    return {
        "train_start": str(window.train_start),
        "train_end": str(window.train_end),
        "test_start": str(window.test_start),
        "test_end": str(window.test_end),
    }


def make_train_log_params_hash(
    *,
    params: dict[str, Any],
    label_col: str,
    feature_version: str,
    label_version: str,
    seed: int | None,
    windows: list[WindowSpec],
) -> str:
    """Stable hash for deciding whether a completed replay window is reusable."""

    payload = {
        "model_version": MODEL_VERSION,
        "label_col": label_col,
        "feature_version": feature_version,
        "label_version": label_version,
        "seed": seed,
        "params": params,
        "windows": [_window_identity(window) for window in windows],
    }
    return hashlib.sha256(_stable_json_dumps(payload).encode("utf-8")).hexdigest()


def build_train_log_window_record(
    *,
    model_id: str,
    replay_id: str,
    params_hash: str,
    window_idx: int,
    window: WindowSpec,
    n_train_rows: int,
    n_test_rows: int,
    n_features: int,
    train_metrics: dict[str, Any],
    oos_metrics: dict[str, Any],
    feature_version: str,
    label_version: str,
    built_at: str,
) -> dict[str, Any]:
    metric_record = {
        "window_idx": int(window_idx),
        "train_start": str(window.train_start),
        "train_end": str(window.train_end),
        "test_start": str(window.test_start),
        "test_end": str(window.test_end),
        "n_train_rows": int(n_train_rows),
        "n_test_rows": int(n_test_rows),
        "train_metrics": train_metrics,
        "oos_metrics": oos_metrics,
    }
    return {
        "model_id": model_id,
        "replay_id": replay_id,
        "params_hash": params_hash,
        "window_idx": int(window_idx),
        "window_key": train_log_window_key(window),
        "model_version": MODEL_VERSION,
        "feature_version": feature_version,
        "label_version": label_version,
        "walk_forward_mode": "expanding_monthly",
        "train_start": str(window.train_start),
        "train_end": str(window.train_end),
        "test_start": str(window.test_start),
        "test_end": str(window.test_end),
        "n_train_rows": int(n_train_rows),
        "n_test_rows": int(n_test_rows),
        "n_features": int(n_features),
        "train_metrics_json": _stable_json_dumps(train_metrics),
        "oos_metrics_json": _stable_json_dumps(oos_metrics),
        "metrics_json": _stable_json_dumps(metric_record),
        "checkpoint_status": "complete",
        "built_at": built_at,
    }


def _window_metric_from_checkpoint_row(row: Any, expected_idx: int, window: WindowSpec) -> dict[str, Any] | None:
    if row["checkpoint_status"] != "complete":
        return None
    if row["window_idx"] != expected_idx:
        return None
    if row["window_key"] != train_log_window_key(window):
        return None
    if _date_str(row["train_start"]) != str(window.train_start):
        return None
    if _date_str(row["train_end"]) != str(window.train_end):
        return None
    if _date_str(row["test_start"]) != str(window.test_start):
        return None
    if _date_str(row["test_end"]) != str(window.test_end):
        return None
    n_train_rows = int(row["n_train_rows"] or 0)
    n_test_rows = int(row["n_test_rows"] or 0)
    if n_train_rows <= 0 or n_test_rows <= 0:
        return None
    if n_train_rows != len(window.train_idx) or n_test_rows != len(window.test_idx):
        return None
    try:
        metric_record = json.loads(row["metrics_json"])
        train_metrics = json.loads(row["train_metrics_json"])
        oos_metrics = json.loads(row["oos_metrics_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(metric_record, dict) or not isinstance(train_metrics, dict) or not isinstance(oos_metrics, dict):
        return None
    if metric_record.get("window_idx") != expected_idx:
        return None
    if int(metric_record.get("n_train_rows") or 0) != n_train_rows:
        return None
    if int(metric_record.get("n_test_rows") or 0) != n_test_rows:
        return None
    return {
        "window_idx": expected_idx,
        "train_start": str(window.train_start),
        "train_end": str(window.train_end),
        "test_start": str(window.test_start),
        "test_end": str(window.test_end),
        "n_train_rows": n_train_rows,
        "n_test_rows": n_test_rows,
        "train_metrics": train_metrics,
        "oos_metrics": oos_metrics,
    }


def load_verified_train_log_windows(
    conn,
    *,
    model_id: str,
    replay_id: str,
    params_hash: str,
    windows: list[WindowSpec],
) -> dict[str, dict[str, Any]]:
    """Return only completed window checkpoints that exactly match this replay."""

    create_fact_model_train_log_window_ddl(conn)
    rows = conn.execute(
        f"""
        SELECT *
          FROM {FACT_MODEL_TRAIN_LOG_WINDOW_TABLE}
         WHERE model_id = ?
           AND replay_id = ?
           AND params_hash = ?
           AND checkpoint_status = 'complete'
        """,
        [model_id, replay_id, params_hash],
    ).fetchall()
    rows_by_key = {str(row["window_key"]): row for row in rows}
    verified: dict[str, dict[str, Any]] = {}
    for idx, window in enumerate(windows):
        key = train_log_window_key(window)
        row = rows_by_key.get(key)
        if row is None:
            continue
        metric_record = _window_metric_from_checkpoint_row(row, idx, window)
        if metric_record is not None:
            verified[key] = metric_record
    return verified


def persist_train_log_window(conn, record: dict[str, Any]) -> int:
    """Persist one verified replay window and commit immediately for preemption safety."""

    create_fact_model_train_log_window_ddl(conn)
    columns = [
        "model_id",
        "replay_id",
        "params_hash",
        "window_idx",
        "window_key",
        "model_version",
        "feature_version",
        "label_version",
        "walk_forward_mode",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "n_train_rows",
        "n_test_rows",
        "n_features",
        "train_metrics_json",
        "oos_metrics_json",
        "metrics_json",
        "checkpoint_status",
        "built_at",
    ]
    conn.execute(
        f"""
        DELETE FROM {FACT_MODEL_TRAIN_LOG_WINDOW_TABLE}
         WHERE model_id = ? AND replay_id = ? AND window_key = ?
        """,
        [record["model_id"], record["replay_id"], record["window_key"]],
    )
    conn.execute(
        f"""
        INSERT INTO {FACT_MODEL_TRAIN_LOG_WINDOW_TABLE}
        ({", ".join(columns)})
        VALUES ({", ".join("?" for _ in columns)})
        """,
        [record.get(column) for column in columns],
    )
    conn.commit()
    return 1


def _assert_complete_train_log_window_metrics(
    windows: list[WindowSpec],
    window_metrics: list[dict[str, Any]],
) -> None:
    if len(window_metrics) != len(windows):
        raise ValueError(f"incomplete train-log replay: {len(window_metrics)}/{len(windows)} windows verified")
    for idx, (window, metric) in enumerate(zip(windows, window_metrics, strict=True)):
        if metric.get("window_idx") != idx:
            raise ValueError(f"train-log replay window index mismatch at {idx}")
        if metric.get("train_start") != str(window.train_start) or metric.get("train_end") != str(window.train_end):
            raise ValueError(f"train-log replay train boundary mismatch at window {idx}")
        if metric.get("test_start") != str(window.test_start) or metric.get("test_end") != str(window.test_end):
            raise ValueError(f"train-log replay test boundary mismatch at window {idx}")
        if int(metric.get("n_train_rows") or 0) != len(window.train_idx):
            raise ValueError(f"train-log replay train row mismatch at window {idx}")
        if int(metric.get("n_test_rows") or 0) != len(window.test_idx):
            raise ValueError(f"train-log replay test row mismatch at window {idx}")
        if not isinstance(metric.get("train_metrics"), dict) or not isinstance(metric.get("oos_metrics"), dict):
            raise ValueError(f"train-log replay metrics missing at window {idx}")


def _prediction_output_frame(
    pred_df: pd.DataFrame,
    *,
    label_col: str,
    model_id: str,
    feature_version: str,
    label_version: str,
    window: WindowSpec,
    built_at: str,
) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "stock_code": pred_df["stock_code"].astype(str),
            "signal_date": pd.to_datetime(pred_df["signal_date"]).dt.strftime("%Y-%m-%d"),
            "score": pred_df["score"].astype(float),
            "model_id": model_id,
            "model_version": MODEL_VERSION,
            "feature_version": feature_version,
            "label_version": label_version,
            "walk_forward_mode": "expanding_monthly",
            "train_start": window.train_start,
            "train_end": window.train_end,
            "test_start": window.test_start,
            "test_end": window.test_end,
            "is_final_holdout": False,
            "built_at": built_at,
        }
    )
    for column in LABEL_COLUMNS:
        out[column] = pred_df[label_col].astype(float) if column == label_col else None
    return out[
        [
            "stock_code",
            "signal_date",
            "score",
            "fwd_cost_after_5d",
            "fwd_cost_after_10d",
            "fwd_cost_after_20d",
            "model_id",
            "model_version",
            "feature_version",
            "label_version",
            "walk_forward_mode",
            "train_start",
            "train_end",
            "test_start",
            "test_end",
            "is_final_holdout",
            "built_at",
        ]
    ]


def materialize_best_predictions(
    *,
    panel: RankPanel,
    windows: list[WindowSpec],
    params: dict[str, Any],
    label_col: str,
    model_id: str,
    feature_version: str,
    label_version: str,
    built_at: str | None = None,
) -> pd.DataFrame:
    """Run the best LambdaMART trial across OOS windows and return rows to persist."""

    built_at = built_at or datetime.now(UTC).isoformat(timespec="seconds")
    frames: list[pd.DataFrame] = []
    for i, window in enumerate(windows):
        log.info(
            "materialize window %d/%d: train %s..%s -> test %s..%s",
            i + 1,
            len(windows),
            window.train_start,
            window.train_end,
            window.test_start,
            window.test_end,
        )
        pred_df = _run_lambdamart_window(panel, window, params, label_col=label_col)
        frames.append(
            _prediction_output_frame(
                pred_df,
                label_col=label_col,
                model_id=model_id,
                feature_version=feature_version,
                label_version=label_version,
                window=window,
                built_at=built_at,
            )
        )
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _finite_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _mean_or_none(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return float(sum(clean) / len(clean)) if clean else None


def _metric_ir(values: list[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return None
    if len(clean) == 1:
        return 0.0
    arr = pd.Series(clean, dtype="float64")
    std = float(arr.std(ddof=1))
    if std <= 1e-12:
        return None
    return float(arr.mean() / std * math.sqrt(len(clean)))


def build_train_log_record(
    *,
    model_id: str,
    feature_version: str,
    label_version: str,
    windows: list[WindowSpec],
    window_metrics: list[dict[str, Any]],
    built_at: str,
    seed: int | None,
    n_trials: int | None,
    optuna_best_value: float | None,
    replay_id: str | None = None,
    params_hash: str | None = None,
) -> dict[str, Any]:
    """Build one aggregate true train/OOS evidence row for Phase4 IS-OOS gate."""

    train_rank_ics = [
        _finite_or_none((item.get("train_metrics") or {}).get("rank_ic"))
        for item in window_metrics
    ]
    oos_rank_ics = [
        _finite_or_none((item.get("oos_metrics") or {}).get("rank_ic"))
        for item in window_metrics
    ]
    metrics_json = {
        "metric_family": "rank_ic",
        "is_aggregation": "window_mean_unweighted",
        "oos_aggregation": "window_mean_unweighted",
        "window_metrics": window_metrics,
    }
    if replay_id and params_hash:
        metrics_json.update(
            checkpoint_replay_id=replay_id,
            checkpoint_params_hash=params_hash,
            expected_windows=len(windows),
            verified_windows=len(window_metrics),
        )
    first_window = windows[0] if windows else None
    last_window = windows[-1] if windows else None
    run_id = f"{model_id}:train_log:{built_at}"
    return {
        "model_id": model_id,
        "run_id": run_id,
        "model_version": MODEL_VERSION,
        "feature_version": feature_version,
        "label_version": label_version,
        "train_start": first_window.train_start if first_window else None,
        "train_end": last_window.train_end if last_window else None,
        "n_train_rows": int(sum(item.get("n_train_rows") or 0 for item in window_metrics)),
        "n_features": 0,
        "is_rank_ic": _mean_or_none(train_rank_ics),
        "is_rank_ic_ir": _metric_ir(train_rank_ics),
        "is_ndcg5": _mean_or_none([
            _finite_or_none((item.get("train_metrics") or {}).get("ndcg5"))
            for item in window_metrics
        ]),
        "is_ndcg10": _mean_or_none([
            _finite_or_none((item.get("train_metrics") or {}).get("ndcg10"))
            for item in window_metrics
        ]),
        "is_ndcg20": _mean_or_none([
            _finite_or_none((item.get("train_metrics") or {}).get("ndcg20"))
            for item in window_metrics
        ]),
        "oos_rank_ic_avg": _mean_or_none(oos_rank_ics),
        "oos_rank_ic_ir": _metric_ir(oos_rank_ics),
        "seed": seed,
        "n_trials": n_trials,
        "n_windows": len(window_metrics),
        "optuna_best_value": optuna_best_value,
        "walk_forward_mode": "expanding_monthly",
        "metrics_json": json.dumps(metrics_json, ensure_ascii=False, sort_keys=True, default=str),
        "built_at": built_at,
    }


def materialize_best_predictions_with_train_log(
    *,
    panel: RankPanel,
    windows: list[WindowSpec],
    params: dict[str, Any],
    label_col: str,
    model_id: str,
    feature_version: str,
    label_version: str,
    built_at: str | None = None,
    seed: int | None = None,
    n_trials: int | None = None,
    optuna_best_value: float | None = None,
    include_predictions: bool = True,
    checkpoint_conn=None,
    resume_train_log: bool = False,
    replay_id: str | None = None,
    params_hash: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run best LambdaMART windows once and return train-log evidence plus optional OOS rows."""

    if resume_train_log and include_predictions:
        raise ValueError("train-log resume can only skip windows when include_predictions=False")
    if resume_train_log and checkpoint_conn is None:
        raise ValueError("resume_train_log requires checkpoint_conn")
    if checkpoint_conn is not None and (not replay_id or not params_hash):
        raise ValueError("train-log window checkpointing requires replay_id and params_hash")

    built_at = built_at or datetime.now(UTC).isoformat(timespec="seconds")
    frames: list[pd.DataFrame] = []
    window_metrics: list[dict[str, Any]] = []
    completed_windows: dict[str, dict[str, Any]] = {}
    if resume_train_log and checkpoint_conn is not None and replay_id and params_hash:
        completed_windows = load_verified_train_log_windows(
            checkpoint_conn,
            model_id=model_id,
            replay_id=replay_id,
            params_hash=params_hash,
            windows=windows,
        )
        if completed_windows:
            log.info(
                "train-log resume: verified %d/%d completed windows for replay_id=%s",
                len(completed_windows),
                len(windows),
                replay_id,
            )
    for i, window in enumerate(windows):
        window_key = train_log_window_key(window)
        if window_key in completed_windows:
            log.info(
                "train-log resume: skip verified window %d/%d: train %s..%s -> test %s..%s",
                i + 1,
                len(windows),
                window.train_start,
                window.train_end,
                window.test_start,
                window.test_end,
            )
            window_metrics.append(completed_windows[window_key])
            continue
        log.info(
            "materialize window %d/%d: train %s..%s -> test %s..%s",
            i + 1,
            len(windows),
            window.train_start,
            window.train_end,
            window.test_start,
            window.test_end,
        )
        model = _fit_lambdamart_window_model(panel, window, params)
        train_pred = model.predict(panel.X[window.train_idx])
        test_pred = model.predict(panel.X[window.test_idx])
        train_df = _prediction_frame(panel, window.train_idx, train_pred, label_col=label_col)
        pred_df = _prediction_frame(panel, window.test_idx, test_pred, label_col=label_col)
        train_metrics = evaluate_predictions(train_df, label_col=label_col)
        oos_metrics = evaluate_predictions(pred_df, label_col=label_col)
        window_metrics.append(
            {
                "window_idx": i,
                "train_start": window.train_start,
                "train_end": window.train_end,
                "test_start": window.test_start,
                "test_end": window.test_end,
                "n_train_rows": int(len(window.train_idx)),
                "n_test_rows": int(len(window.test_idx)),
                "train_metrics": train_metrics,
                "oos_metrics": oos_metrics,
            }
        )
        if checkpoint_conn is not None and replay_id and params_hash:
            persist_train_log_window(
                checkpoint_conn,
                build_train_log_window_record(
                    model_id=model_id,
                    replay_id=replay_id,
                    params_hash=params_hash,
                    window_idx=i,
                    window=window,
                    n_train_rows=int(len(window.train_idx)),
                    n_test_rows=int(len(window.test_idx)),
                    n_features=len(panel.feature_columns),
                    train_metrics=train_metrics,
                    oos_metrics=oos_metrics,
                    feature_version=feature_version,
                    label_version=label_version,
                    built_at=built_at,
                ),
            )
        if include_predictions:
            frames.append(
                _prediction_output_frame(
                    pred_df,
                    label_col=label_col,
                    model_id=model_id,
                    feature_version=feature_version,
                    label_version=label_version,
                    window=window,
                    built_at=built_at,
                )
            )
    _assert_complete_train_log_window_metrics(windows, window_metrics)
    predictions = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    train_log = build_train_log_record(
        model_id=model_id,
        feature_version=feature_version,
        label_version=label_version,
        windows=windows,
        window_metrics=window_metrics,
        built_at=built_at,
        seed=seed,
        n_trials=n_trials,
        optuna_best_value=optuna_best_value,
        replay_id=replay_id,
        params_hash=params_hash,
    )
    train_log["n_features"] = len(panel.feature_columns)
    return predictions, train_log


def persist_predictions(conn, predictions: pd.DataFrame, *, model_id: str) -> int:
    """Idempotently replace one model_id in mart_p0b_lambdamart_v6_predictions."""

    if predictions.empty:
        raise ValueError("no predictions to persist")
    create_lambdamart_v6_predictions_ddl(conn)
    temp_name = "_lambdamart_v6_predictions"
    conn._con.register(temp_name, predictions)
    try:
        conn.execute(
            f"DELETE FROM {LAMBDAMART_V6_PREDICTIONS_TABLE} WHERE model_id = ?",
            [model_id],
        )
        conn.execute(
            f"""
            INSERT INTO {LAMBDAMART_V6_PREDICTIONS_TABLE}
            (stock_code, signal_date, score,
             fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
             model_id, model_version, feature_version, label_version,
             walk_forward_mode,
             train_start, train_end, test_start, test_end,
             is_final_holdout, built_at)
            SELECT stock_code, CAST(signal_date AS DATE), score,
                   fwd_cost_after_5d, fwd_cost_after_10d, fwd_cost_after_20d,
                   model_id, model_version, feature_version, label_version,
                   walk_forward_mode,
                   CAST(train_start AS DATE), CAST(train_end AS DATE),
                   CAST(test_start AS DATE), CAST(test_end AS DATE),
                   is_final_holdout, built_at
              FROM {temp_name}
            """
        )
    finally:
        conn._con.unregister(temp_name)
    return int(len(predictions))


def persist_train_log(conn, record: dict[str, Any]) -> int:
    """Persist one aggregate train-log evidence row without deleting older evidence."""

    create_fact_model_train_log_ddl(conn)
    columns = [
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
    conn.execute(
        f"DELETE FROM {FACT_MODEL_TRAIN_LOG_TABLE} WHERE model_id = ? AND run_id = ?",
        [record["model_id"], record["run_id"]],
    )
    conn.execute(
        f"""
        INSERT INTO {FACT_MODEL_TRAIN_LOG_TABLE}
        ({", ".join(columns)})
        VALUES ({", ".join("?" for _ in columns)})
        """,
        [record.get(column) for column in columns],
    )
    return 1


def persist_materialization_outputs(
    conn,
    predictions: pd.DataFrame,
    train_log_record: dict[str, Any],
    *,
    model_id: str,
    train_log_only: bool = False,
) -> tuple[int, int]:
    """Persist materialized outputs, optionally leaving prediction rows untouched."""

    n_prediction_rows = 0
    if not train_log_only:
        n_prediction_rows = persist_predictions(conn, predictions, model_id=model_id)
        register_lambdamart_v6_asset(conn)
    n_train_log_rows = persist_train_log(conn, train_log_record)
    return n_prediction_rows, n_train_log_rows


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_schema = 'main' AND table_name = ?
         LIMIT 1
        """,
        [table_name],
    ).fetchone()
    return row is not None


def _columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_name = ?
        """,
        [table_name],
    ).fetchall()
    return {str(r[0]) for r in rows}


def _upsert_by_primary_key(conn, table_name: str, key: str, payload: dict[str, Any]) -> None:
    available = _columns(conn, table_name)
    cols = [c for c in payload if c in available]
    if key not in cols:
        raise RuntimeError(f"{table_name} is missing primary key column {key}")
    assignments = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != key)
    placeholders = ", ".join("?" for _ in cols)
    sql = f"""
        INSERT INTO {table_name} ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT ({key}) DO UPDATE SET {assignments}
    """
    conn.execute(sql, [payload[c] for c in cols])


def register_lambdamart_v6_asset(conn) -> None:
    """Register the v6 predictions table in governance metadata tables."""

    ensure_schema_version_table(conn, commit=False)
    record_actual_version(conn, LAMBDAMART_V6_PREDICTIONS_TABLE)

    if not _table_exists(conn, "dim_data_asset"):
        log.warning("dim_data_asset missing; schema version was recorded but asset registry was skipped")
        return

    now = datetime.now(UTC).isoformat(timespec="seconds")
    payload = {
        "table_name": LAMBDAMART_V6_PREDICTIONS_TABLE,
        "layer": "mart",
        "purpose": "LambdaMART v6 walk-forward OOS predictions for paper_sim ml_score selection",
        "writer_module": "backend/scripts/retrain_lambdamart_v6.py",
        "reader_modules": json.dumps(
            [
                "backend/services/paper_sim/ml_score_loader.py",
                "backend/scripts/run_paper_sim_lambdamart_v6_compare.py",
            ],
            ensure_ascii=False,
        ),
        "upstream_source": "derived from mart_p0a_feature_label_panel_v4 via run_p0b_lambdamart_v6",
        "source_tier": None,
        "fallback_chain": json.dumps([], ensure_ascii=False),
        "expected_freshness": "weekly",
        "sla_hours": 168,
        "consumed_by_views": json.dumps([], ensure_ascii=False),
        "asset_grain": "stock_code+signal_date+model_id",
        "asset_cadence": "weekly_model_refresh",
        "coverage_policy": "walk_forward_oos_signal_universe",
        "null_policy": "no_unclassified_nulls_predictions_nullable_only_failed_score",
        "pit_policy": "walk_forward_train_dates_strictly_before_signal_date",
        "intended_use": "paper_sim_selection_score",
        "model_eligibility": "not_model_input",
        "strategy_eligibility": "paper_sim_ml_score_candidate_source",
        "frontend_visibility": "governance_visible",
        "quality_gate_level": "blocking",
        "is_append_only": False,
        "deprecation_status": "active",
        "schema_version": "v1",
        "notes": "MSAF Phase 2.1 LambdaMART v6 dedicated prediction table",
        "auto_discovered": False,
        "last_updated_at": now,
    }
    _upsert_by_primary_key(conn, "dim_data_asset", "table_name", payload)


def _log_result_metrics(model_id: str, result) -> None:
    metrics = result.metrics
    log.info("model_id=%s", model_id)
    log.info("best_value=%.6f n_trials=%d n_windows=%d", result.best_value, result.n_trials, result.n_windows)
    for key in ("rank_ic", "ndcg5", "ndcg10", "ndcg20", "top5_spread", "top10_spread", "top5_turnover"):
        value = metrics.get(key)
        if value is None or not math.isfinite(float(value)):
            log.info("%s=nan", key)
        else:
            log.info("%s=%.6f", key, float(value))


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrain LambdaMART v6 and persist OOS predictions")
    parser.add_argument("--model-date", default=None, help="model id date, YYYYMMDD; default today")
    parser.add_argument("--model-id", default=None, help="override model id")
    parser.add_argument("--label", default="fwd_cost_after_20d")
    parser.add_argument("--n-trials", type=int, default=50)
    parser.add_argument("--full", action="store_true", help="use n_estimators=2000")
    parser.add_argument("--n-estimators", type=int, default=None)
    parser.add_argument("--start-date", default="2024-01-01")  # rule-compliance: ok evidence=alpha158-panel-起始日
    parser.add_argument("--end-date", default="2026-04-13")     # rule-compliance: ok evidence=panel-cutoff
    parser.add_argument("--min-train-months", type=int, default=12)
    parser.add_argument("--forward-months", type=int, default=1)
    parser.add_argument("--max-windows", type=int, default=None)
    parser.add_argument("--feature-panel", default="mart_p0a_feature_label_panel_v4")
    parser.add_argument("--feature-version", default=DEFAULT_FEATURE_VERSION)
    parser.add_argument("--label-version", default=DEFAULT_LABEL_VERSION)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--exclude-cols", default="")
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--turnover-limit", type=float, default=3.0)
    parser.add_argument("--turnover-penalty-weight", type=float, default=0.02)
    parser.add_argument("--window-rank-ic-std-penalty-weight", type=float, default=0.0,
                        help="Opt-in Optuna objective penalty on per-window RankIC std; default 0 preserves existing behavior")
    parser.add_argument("--window-rank-ic-negative-rate-penalty-weight", type=float, default=0.0,
                        help="Opt-in Optuna objective penalty on negative per-window RankIC rate; default 0 preserves existing behavior")
    # F1+F2 (Codex bocq8b60j 2026-05-20): Optuna SQLite persistent storage + checkpoint
    # 防 spot preempt 浪费 + interrupted 时 best params 可救回
    parser.add_argument("--study-storage", default=None,
                        help="Optuna storage URL e.g. sqlite:///data/reports/optuna/<model_id>.db (F1 resume on preempt)")
    parser.add_argument("--study-name", default=None,
                        help="Optuna study name, default = model_id (F1 resume)")
    parser.add_argument("--checkpoint-path", default=None,
                        help="JSON checkpoint path for best params (F2, atomic per-trial write)")
    parser.add_argument("--use-checkpoint-best", action="store_true",
                        help="Skip Optuna and materialize predictions from --checkpoint-path best_params")
    parser.add_argument("--train-log-only", action="store_true",
                        help="Compute true train/OOS evidence but do not replace prediction rows")
    parser.add_argument("--resume-train-log", dest="resume_train_log", action="store_true", default=True,
                        help="Reuse verified per-window train-log checkpoints when --train-log-only is set")
    parser.add_argument("--no-resume-train-log", dest="resume_train_log", action="store_false",
                        help="Ignore per-window train-log checkpoints and recompute every replay window")
    parser.add_argument("--train-log-replay-id", default=None,
                        help="Stable replay id for per-window train-log checkpoints; default derives from model_id and params hash")
    parser.add_argument("--warm-start-checkpoint", default=None,
                        help="Seed Optuna with best_params from a prior checkpoint JSON (Layer 4 warm-start)")
    parser.add_argument("--gcs-sync-uri", default=os.environ.get("RETRAIN_GCS_SYNC_URI"),
                        help="Optional gs:// directory for best-effort SIGTERM sync of best.json and Optuna SQLite")
    args = parser.parse_args()

    # Pre-train leakage audit (Option A integration, 2026-05-22 user push back)
    # 5 自动 check (schema PIT marker / panel JOIN PIT-strict / flat current-mapping PARTITION BY /
    # mapping fallback ratio / per-feature temporal variance). HIGH=block, MEDIUM=warn, set
    # SKIP_LEAKAGE_AUDIT=1 to override (use only when audit known-false-positive).
    if not os.environ.get("SKIP_LEAKAGE_AUDIT"):
        import subprocess  # local import to avoid top-level dep when audit skipped
        audit_cmd = [
            sys.executable,
            str(Path(__file__).parent / "audit_panel_leakage.py"),
            "--panel", args.feature_panel,
        ]
        log.info("pre-train leakage audit: %s", " ".join(audit_cmd))
        rc = subprocess.call(audit_cmd, env={**os.environ, "PYTHONPATH": "backend"})
        if rc == 1:
            log.error(
                "BLOCK: pre-train leakage audit returned HIGH-risk findings (exit 1). "
                "Review data/reports/leakage_audit/ and fix panel/source. "
                "Override (use cautiously): SKIP_LEAKAGE_AUDIT=1"
            )
            return 3
        elif rc == 2:
            log.warning("pre-train leakage audit returned MEDIUM-risk findings (exit 2); proceeding (use --strict in audit to block)")
        else:
            log.info("pre-train leakage audit: PASS (rc=0)")
    else:
        log.warning("SKIP_LEAKAGE_AUDIT=1 set — pre-train audit bypassed (only ok for known-false-positive)")

    model_id = args.model_id or make_model_id(args.model_date)
    db_path = args.db_path or str(DB_PATH)
    n_estimators = args.n_estimators if args.n_estimators is not None else (2000 if args.full else 300)
    study_storage = args.study_storage
    study_name = args.study_name or model_id
    checkpoint_path = args.checkpoint_path
    if study_storage and not checkpoint_path:
        checkpoint_path = str(Path("data/reports/optuna") / f"{model_id}.best.json")
    _install_sigterm_handler(
        model_id=model_id,
        checkpoint_path=checkpoint_path,
        study_storage=study_storage,
        gcs_sync_uri=args.gcs_sync_uri,
    )

    log.info(
        "start retrain model_id=%s label=%s n_trials=%d n_estimators=%d feature_panel=%s",
        model_id,
        args.label,
        args.n_trials,
        n_estimators,
        args.feature_panel,
    )
    panel = load_rank_panel(
        db_path=db_path,
        feature_panel=args.feature_panel,
        label_col=args.label,
        start_date=args.start_date,
        end_date=args.end_date,
        exclude_cols=args.exclude_cols,
    )
    windows = build_walk_forward_windows(
        panel,
        min_train_months=args.min_train_months,
        forward_months=args.forward_months,
        max_windows=args.max_windows,
    )
    if not windows:
        log.error("No walk-forward windows produced")
        return 1

    # Governance gate (CLAUDE.md Rule 6 + memory feedback_optuna_trials_no_shortcut):
    # n_trials < 50 拦下 — 防再犯 "为省时间缩 trials = 漏 best params" 错 (2026-05-20 反例 Trial 14→27 best +3.7%)
    from services.optimization.governance import enforce_pre_optimize
    enforce_pre_optimize(n_trials=args.n_trials, has_seed=True)

    warm_start_params = None
    if args.warm_start_checkpoint:
        warm_start_params = load_warm_start_params(args.warm_start_checkpoint)
        log.info("loaded warm-start params from %s", args.warm_start_checkpoint)

    if args.use_checkpoint_best:
        if not checkpoint_path:
            raise ValueError("--use-checkpoint-best requires --checkpoint-path")
        checkpoint_payload = load_checkpoint_best_payload(checkpoint_path)
        best_params = dict(checkpoint_payload["best_params"])
        optuna_best_value = _finite_or_none(checkpoint_payload.get("best_value"))
        log.info("using checkpoint best params from %s; Optuna optimize skipped", checkpoint_path)
    else:
        result = run_optuna(
            model_name="lambdamart",
            panel=panel,
            windows=windows,
            label_col=args.label,
            n_trials=args.n_trials,
            n_estimators=n_estimators,
            seed=args.seed,
            turnover_limit=args.turnover_limit,
            turnover_penalty_weight=args.turnover_penalty_weight,
            top_k=args.top_k,
            study_storage=study_storage,
            study_name=study_name,
            checkpoint_path=checkpoint_path,
            warm_start_params=warm_start_params,
            warm_start_source=args.warm_start_checkpoint,
            window_rank_ic_std_penalty_weight=args.window_rank_ic_std_penalty_weight,
            window_rank_ic_negative_rate_penalty_weight=args.window_rank_ic_negative_rate_penalty_weight,
        )
        _log_result_metrics(model_id, result)
        best_params = result.best_params
        optuna_best_value = float(result.best_value)

    params = complete_lambdamart_params(best_params, seed=args.seed, n_estimators=n_estimators)
    train_log_params_hash = make_train_log_params_hash(
        params=params,
        label_col=args.label,
        feature_version=args.feature_version,
        label_version=args.label_version,
        seed=args.seed,
        windows=windows,
    )
    train_log_replay_id = args.train_log_replay_id or f"{model_id}:train_log:{train_log_params_hash[:16]}"
    use_train_log_resume = bool(args.train_log_only and args.resume_train_log)
    if use_train_log_resume:
        log.info("train-log replay_id=%s params_hash=%s", train_log_replay_id, train_log_params_hash)

    if use_train_log_resume:
        conn = duck_connect(db_path)
        try:
            predictions, train_log_record = materialize_best_predictions_with_train_log(
                panel=panel,
                windows=windows,
                params=params,
                label_col=args.label,
                model_id=model_id,
                feature_version=args.feature_version,
                label_version=args.label_version,
                seed=args.seed,
                n_trials=args.n_trials,
                optuna_best_value=optuna_best_value,
                include_predictions=False,
                checkpoint_conn=conn,
                resume_train_log=True,
                replay_id=train_log_replay_id,
                params_hash=train_log_params_hash,
            )
            n_rows, _n_train_log_rows = persist_materialization_outputs(
                conn,
                predictions,
                train_log_record,
                model_id=model_id,
                train_log_only=True,
            )
            conn.commit()
        finally:
            conn.close()
    else:
        predictions, train_log_record = materialize_best_predictions_with_train_log(
            panel=panel,
            windows=windows,
            params=params,
            label_col=args.label,
            model_id=model_id,
            feature_version=args.feature_version,
            label_version=args.label_version,
            seed=args.seed,
            n_trials=args.n_trials,
            optuna_best_value=optuna_best_value,
            include_predictions=not args.train_log_only,
        )
        conn = duck_connect(db_path)
        try:
            n_rows, _n_train_log_rows = persist_materialization_outputs(
                conn,
                predictions,
                train_log_record,
                model_id=model_id,
                train_log_only=args.train_log_only,
            )
            conn.commit()
        finally:
            conn.close()

    if args.train_log_only:
        log.info("train-log-only: left %s prediction rows untouched", LAMBDAMART_V6_PREDICTIONS_TABLE)
    else:
        log.info("wrote %s predictions to %s", f"{n_rows:,}", LAMBDAMART_V6_PREDICTIONS_TABLE)
    log.info("wrote true train/OOS evidence to %s", FACT_MODEL_TRAIN_LOG_TABLE)
    _sync_preempt_artifacts(
        model_id=model_id,
        checkpoint_path=checkpoint_path,
        study_storage=study_storage,
        gcs_sync_uri=args.gcs_sync_uri,
    )
    print(f"MODEL_ID={model_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
