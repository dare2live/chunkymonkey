#!/usr/bin/env python3
"""Weekly LambdaMART v6 retrain and prediction materialization.

This wrapper reuses the executable v6 Optuna walk-forward implementation in
run_p0b_lambdamart_v6.py, then retrains the best trial across each OOS window
and stores predictions in a dedicated v6 table for paper_sim.
"""
from __future__ import annotations

import argparse
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
    LAMBDAMART_V6_PREDICTIONS_TABLE,
    create_lambdamart_v6_predictions_ddl,
)
from services.schema_versions import ensure_schema_version_table, record_actual_version

from scripts.run_p0b_lambdamart_v6 import (
    RankPanel,
    WindowSpec,
    build_walk_forward_windows,
    load_rank_panel,
    run_optuna,
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
    # F1+F2 (Codex bocq8b60j 2026-05-20): Optuna SQLite persistent storage + checkpoint
    # 防 spot preempt 浪费 + interrupted 时 best params 可救回
    parser.add_argument("--study-storage", default=None,
                        help="Optuna storage URL e.g. sqlite:///data/reports/optuna/<model_id>.db (F1 resume on preempt)")
    parser.add_argument("--study-name", default=None,
                        help="Optuna study name, default = model_id (F1 resume)")
    parser.add_argument("--checkpoint-path", default=None,
                        help="JSON checkpoint path for best params (F2, atomic per-trial write)")
    parser.add_argument("--gcs-sync-uri", default=os.environ.get("RETRAIN_GCS_SYNC_URI"),
                        help="Optional gs:// directory for best-effort SIGTERM sync of best.json and Optuna SQLite")
    args = parser.parse_args()

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
    )
    _log_result_metrics(model_id, result)

    params = complete_lambdamart_params(result.best_params, seed=args.seed, n_estimators=n_estimators)
    predictions = materialize_best_predictions(
        panel=panel,
        windows=windows,
        params=params,
        label_col=args.label,
        model_id=model_id,
        feature_version=args.feature_version,
        label_version=args.label_version,
    )

    conn = duck_connect(db_path)
    try:
        n_rows = persist_predictions(conn, predictions, model_id=model_id)
        register_lambdamart_v6_asset(conn)
        conn.commit()
    finally:
        conn.close()

    log.info("wrote %s predictions to %s", f"{n_rows:,}", LAMBDAMART_V6_PREDICTIONS_TABLE)
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
