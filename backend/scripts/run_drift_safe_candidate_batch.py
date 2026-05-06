#!/usr/bin/env python3
"""Batch-evaluate generated drift-safe candidates with model stability search."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402
from scripts.run_optuna_model_stability_search import (  # noqa: E402
    MODEL_FAMILIES,
    run_optuna_model_stability_search,
)


DDL = """
CREATE TABLE IF NOT EXISTS mart_drift_safe_candidate_batch_eval (
    batch_run_id TEXT NOT NULL,
    candidate_summary_run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    stability_run_id TEXT NOT NULL,
    model_family TEXT NOT NULL,
    status TEXT,
    objective_score DOUBLE,
    best_topk_size INTEGER,
    holdout_rank_ic DOUBLE,
    holdout_long_short_spread DOUBLE,
    walkforward_avg_rank_ic DOUBLE,
    walkforward_std_rank_ic DOUBLE,
    walkforward_worst_topk_drawdown DOUBLE,
    walkforward_worst_feature_drift_psi DOUBLE,
    rejection_reason TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (batch_run_id, candidate_id, model_family)
);
CREATE INDEX IF NOT EXISTS idx_drift_safe_batch_eval_status
    ON mart_drift_safe_candidate_batch_eval(batch_run_id, status);

CREATE TABLE IF NOT EXISTS mart_drift_safe_candidate_batch_summary (
    batch_run_id TEXT PRIMARY KEY,
    candidate_summary_run_id TEXT NOT NULL,
    evaluated_count INTEGER,
    pass_count INTEGER,
    best_candidate_id TEXT,
    best_stability_run_id TEXT,
    best_status TEXT,
    best_objective_score DOUBLE,
    candidate_ids_json TEXT,
    config_json TEXT,
    built_at TEXT NOT NULL
);
"""


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _safe_json(raw: Any, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _slug(value: str, *, max_len: int = 96) -> str:
    out = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return (out or "candidate")[-max_len:]


def latest_candidate_summary_run_id(conn: Any) -> str | None:
    if not _table_exists(conn, "mart_drift_safe_candidate_summary"):
        return None
    row = conn.execute(
        """
        SELECT run_id
          FROM mart_drift_safe_candidate_summary
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def load_candidate_ids(
    conn: Any,
    *,
    candidate_summary_run_id: str | None,
    explicit_candidate_ids: list[str] | None = None,
) -> tuple[str, list[str]]:
    summary_run_id = candidate_summary_run_id or latest_candidate_summary_run_id(conn)
    if not summary_run_id:
        raise RuntimeError("no drift-safe candidate summary found")
    if explicit_candidate_ids:
        return summary_run_id, list(dict.fromkeys(explicit_candidate_ids))
    row = conn.execute(
        """
        SELECT candidate_ids_json
          FROM mart_drift_safe_candidate_summary
         WHERE run_id = ?
        """,
        (summary_run_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"missing drift-safe candidate summary: {summary_run_id}")
    candidate_ids = _safe_json(row["candidate_ids_json"], [])
    if not isinstance(candidate_ids, list) or not candidate_ids:
        raise RuntimeError(f"candidate summary has no candidate ids: {summary_run_id}")
    return summary_run_id, [str(candidate_id) for candidate_id in candidate_ids]


def resolve_candidate_feature_set_id(
    conn: Any,
    *,
    candidate_id: str,
    explicit_feature_set_id: str | None,
) -> str | None:
    if explicit_feature_set_id:
        return explicit_feature_set_id
    row = conn.execute(
        """
        SELECT feature_set_id
          FROM mart_model_selection_run
         WHERE run_id = ?
        """,
        (candidate_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"missing model selection run for candidate: {candidate_id}")
    feature_set_id = row["feature_set_id"]
    if not feature_set_id or feature_set_id == "production_registry":
        return None
    return str(feature_set_id)


def _result_row(
    *,
    batch_run_id: str,
    candidate_summary_run_id: str,
    candidate_id: str,
    stability_run_id: str,
    model_family: str,
    result: dict[str, Any],
    built_at: str,
) -> tuple[Any, ...]:
    metrics = result.get("best_metrics") or {}
    return (
        batch_run_id,
        candidate_summary_run_id,
        candidate_id,
        stability_run_id,
        model_family,
        result.get("best_status"),
        result.get("best_score"),
        result.get("best_topk_size"),
        metrics.get("holdout_rank_ic"),
        metrics.get("holdout_long_short_spread"),
        metrics.get("walkforward_avg_rank_ic"),
        metrics.get("walkforward_std_rank_ic"),
        metrics.get("walkforward_worst_topk_drawdown"),
        metrics.get("walkforward_worst_feature_drift_psi"),
        result.get("best_rejection_reason"),
        built_at,
    )


def run_drift_safe_candidate_batch(
    conn: Any,
    *,
    candidate_summary_run_id: str | None = None,
    candidate_ids: list[str] | None = None,
    batch_run_id: str | None = None,
    model_families: list[str] | None = None,
    start: str = "2025-01-01",
    end: str = "2025-12-31",
    feature_table: str = "fact_feature_panel",
    feature_set_id: str | None = None,
    label_name: str = "forward_ret_20d",
    trials: int = 0,
    seed: int = 42,
    num_round: int = 80,
    num_threads: int = 4,
    train_days: int = 60,
    valid_days: int = 20,
    test_days: int = 20,
    step_days: int = 20,
    max_folds: int = 6,
    min_holdout_rank_ic: float = 0.0424,
    min_holdout_spread: float = 0.0092,
    min_walkforward_avg_rank_ic: float = 0.015,
    max_walkforward_std_rank_ic: float = 0.03,
    min_ok_folds: int = 4,
    topk_size: int = 50,
    topk_size_choices: list[int] | None = None,
    cost_bps: float = 10.0,
    max_topk_drawdown: float = 0.20,
    max_feature_drift_psi: float = 0.25,
    drift_bins: int = 10,
    limit: int = 0,
    evaluator: Callable[..., dict[str, Any]] = run_optuna_model_stability_search,
) -> dict[str, Any]:
    ensure_tables(conn)
    batch_run_id = batch_run_id or f"drift_safe_candidate_batch_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    candidate_summary_run_id, candidate_ids = load_candidate_ids(
        conn,
        candidate_summary_run_id=candidate_summary_run_id,
        explicit_candidate_ids=candidate_ids,
    )
    candidate_ids = list(candidate_ids)
    if limit > 0:
        candidate_ids = candidate_ids[:limit]
    candidate_feature_set_ids = {
        candidate_id: resolve_candidate_feature_set_id(
            conn,
            candidate_id=candidate_id,
            explicit_feature_set_id=feature_set_id,
        )
        for candidate_id in candidate_ids
    }
    model_families = model_families or ["lightgbm"]
    invalid_families = [family for family in model_families if family not in MODEL_FAMILIES]
    if invalid_families:
        raise ValueError(f"unsupported model family: {', '.join(invalid_families)}")
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    conn.execute("DELETE FROM mart_drift_safe_candidate_batch_eval WHERE batch_run_id = ?", (batch_run_id,))
    rows: list[tuple[Any, ...]] = []
    results: list[dict[str, Any]] = []
    for candidate_id in candidate_ids:
        for model_family in model_families:
            stability_run_id = f"{batch_run_id}_{_slug(candidate_id)}_{model_family}"
            result = evaluator(
                conn,
                model_selection_run_id=candidate_id,
                run_id=stability_run_id,
                start=start,
                end=end,
                feature_table=feature_table,
                feature_set_id=candidate_feature_set_ids.get(candidate_id),
                label_name=label_name,
                model_family=model_family,
                trials=trials,
                seed=seed,
                num_round=num_round,
                num_threads=num_threads,
                train_days=train_days,
                valid_days=valid_days,
                test_days=test_days,
                step_days=step_days,
                max_folds=max_folds,
                min_holdout_rank_ic=min_holdout_rank_ic,
                min_holdout_spread=min_holdout_spread,
                min_walkforward_avg_rank_ic=min_walkforward_avg_rank_ic,
                max_walkforward_std_rank_ic=max_walkforward_std_rank_ic,
                min_ok_folds=min_ok_folds,
                topk_size=topk_size,
                topk_size_choices=topk_size_choices,
                cost_bps=cost_bps,
                max_topk_drawdown=max_topk_drawdown,
                max_feature_drift_psi=max_feature_drift_psi,
                drift_bins=drift_bins,
                storage_url=None,
                study_name=stability_run_id,
                load_if_exists=False,
            )
            rows.append(
                _result_row(
                    batch_run_id=batch_run_id,
                    candidate_summary_run_id=candidate_summary_run_id,
                    candidate_id=candidate_id,
                    stability_run_id=stability_run_id,
                    model_family=model_family,
                    result=result,
                    built_at=built_at,
                )
            )
            results.append(
                {
                    "candidate_id": candidate_id,
                    "model_family": model_family,
                    "stability_run_id": stability_run_id,
                    **result,
                }
            )
    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_drift_safe_candidate_batch_eval
            (batch_run_id, candidate_summary_run_id, candidate_id,
             stability_run_id, model_family, status, objective_score,
             best_topk_size, holdout_rank_ic, holdout_long_short_spread,
             walkforward_avg_rank_ic, walkforward_std_rank_ic,
             walkforward_worst_topk_drawdown,
             walkforward_worst_feature_drift_psi,
             rejection_reason, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    best_row = max(rows, key=lambda row: float(row[6] or -1e9), default=None)
    pass_count = sum(1 for row in rows if row[5] == "pass")
    config = {
        "start": start,
        "end": end,
        "feature_table": feature_table,
        "feature_set_id": feature_set_id,
        "candidate_feature_set_ids": candidate_feature_set_ids,
        "label_name": label_name,
        "model_families": model_families,
        "trials": trials,
        "train_days": train_days,
        "valid_days": valid_days,
        "test_days": test_days,
        "step_days": step_days,
        "max_folds": max_folds,
        "topk_size": topk_size,
        "topk_size_choices": topk_size_choices or [],
        "cost_bps": cost_bps,
        "thresholds": {
            "min_holdout_rank_ic": min_holdout_rank_ic,
            "min_holdout_spread": min_holdout_spread,
            "min_walkforward_avg_rank_ic": min_walkforward_avg_rank_ic,
            "max_walkforward_std_rank_ic": max_walkforward_std_rank_ic,
            "min_ok_folds": min_ok_folds,
            "max_topk_drawdown": max_topk_drawdown,
            "max_feature_drift_psi": max_feature_drift_psi,
        },
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_drift_safe_candidate_batch_summary
        (batch_run_id, candidate_summary_run_id, evaluated_count, pass_count,
         best_candidate_id, best_stability_run_id, best_status,
         best_objective_score, candidate_ids_json, config_json, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_run_id,
            candidate_summary_run_id,
            len(rows),
            pass_count,
            best_row[2] if best_row else None,
            best_row[3] if best_row else None,
            best_row[5] if best_row else None,
            best_row[6] if best_row else None,
            json.dumps(candidate_ids, ensure_ascii=False),
            json.dumps(config, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    record_actual_version(conn, "mart_drift_safe_candidate_batch_eval")
    record_actual_version(conn, "mart_drift_safe_candidate_batch_summary")
    duration_s = time.perf_counter() - t0
    record_pipeline_run(
        conn,
        run_id=batch_run_id,
        pipeline_name="run_drift_safe_candidate_batch",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=["mart_drift_safe_candidate_summary", "mart_model_selection_run", feature_table],
        output_tables=[
            "mart_drift_safe_candidate_batch_eval",
            "mart_drift_safe_candidate_batch_summary",
            "mart_model_stability_search_trial",
            "mart_model_stability_search_summary",
        ],
        label_name=label_name,
        perf_summary={
            "candidate_summary_run_id": candidate_summary_run_id,
            "candidate_ids": candidate_ids,
            "candidate_feature_set_ids": candidate_feature_set_ids,
            "model_families": model_families,
            "evaluated_count": len(rows),
            "pass_count": pass_count,
            "best_candidate_id": best_row[2] if best_row else None,
            "best_stability_run_id": best_row[3] if best_row else None,
            "best_status": best_row[5] if best_row else None,
            "best_objective_score": best_row[6] if best_row else None,
            "duration_s": duration_s,
        },
    )
    conn.commit()
    return {
        "batch_run_id": batch_run_id,
        "candidate_summary_run_id": candidate_summary_run_id,
        "evaluated_count": len(rows),
        "pass_count": pass_count,
        "best_candidate_id": best_row[2] if best_row else None,
        "best_stability_run_id": best_row[3] if best_row else None,
        "best_status": best_row[5] if best_row else None,
        "best_objective_score": best_row[6] if best_row else None,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-summary-run-id", default=None)
    parser.add_argument("--candidate-id", action="append", default=[])
    parser.add_argument("--batch-run-id", default=None)
    parser.add_argument("--model-families", default="lightgbm")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--feature-table", default="fact_feature_panel")
    parser.add_argument("--feature-set-id", default=None)
    parser.add_argument("--label-name", default="forward_ret_20d")
    parser.add_argument("--trials", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-round", type=int, default=80)
    parser.add_argument("--num-threads", type=int, default=4)
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--valid-days", type=int, default=20)
    parser.add_argument("--test-days", type=int, default=20)
    parser.add_argument("--step-days", type=int, default=20)
    parser.add_argument("--max-folds", type=int, default=6)
    parser.add_argument("--min-holdout-rank-ic", type=float, default=0.0424)
    parser.add_argument("--min-holdout-spread", type=float, default=0.0092)
    parser.add_argument("--min-walkforward-avg-rank-ic", type=float, default=0.015)
    parser.add_argument("--max-walkforward-std-rank-ic", type=float, default=0.03)
    parser.add_argument("--min-ok-folds", type=int, default=4)
    parser.add_argument("--topk-size", type=int, default=50)
    parser.add_argument("--topk-size-choices", default="50,100,150")
    parser.add_argument("--cost-bps", type=float, default=10.0)
    parser.add_argument("--max-topk-drawdown", type=float, default=0.20)
    parser.add_argument("--max-feature-drift-psi", type=float, default=0.25)
    parser.add_argument("--drift-bins", type=int, default=10)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    with get_conn() as conn:
        result = run_drift_safe_candidate_batch(
            conn,
            candidate_summary_run_id=args.candidate_summary_run_id,
            candidate_ids=args.candidate_id,
            batch_run_id=args.batch_run_id,
            model_families=_parse_csv(args.model_families),
            start=args.start,
            end=args.end,
            feature_table=args.feature_table,
            feature_set_id=args.feature_set_id,
            label_name=args.label_name,
            trials=args.trials,
            seed=args.seed,
            num_round=args.num_round,
            num_threads=args.num_threads,
            train_days=args.train_days,
            valid_days=args.valid_days,
            test_days=args.test_days,
            step_days=args.step_days,
            max_folds=args.max_folds,
            min_holdout_rank_ic=args.min_holdout_rank_ic,
            min_holdout_spread=args.min_holdout_spread,
            min_walkforward_avg_rank_ic=args.min_walkforward_avg_rank_ic,
            max_walkforward_std_rank_ic=args.max_walkforward_std_rank_ic,
            min_ok_folds=args.min_ok_folds,
            topk_size=args.topk_size,
            topk_size_choices=[int(value) for value in _parse_csv(args.topk_size_choices)],
            cost_bps=args.cost_bps,
            max_topk_drawdown=args.max_topk_drawdown,
            max_feature_drift_psi=args.max_feature_drift_psi,
            drift_bins=args.drift_bins,
            limit=args.limit,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
