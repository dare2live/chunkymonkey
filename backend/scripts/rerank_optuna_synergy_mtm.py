#!/usr/bin/env python3
"""Post-Optuna MTM reranker for synergy policy trials."""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.validate_synergy_policy_mark_to_market import (  # noqa: E402
    validate_synergy_policy_mark_to_market,
)
from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent

DDL = """
CREATE TABLE IF NOT EXISTS mart_synergy_policy_candidate (
    run_id TEXT PRIMARY KEY,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    objective_score DOUBLE,
    selected_features_json TEXT,
    selected_interactions_json TEXT,
    gate_status TEXT NOT NULL,
    notes_json TEXT,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_synergy_policy_mtm_rerank (
    run_id TEXT NOT NULL,
    optuna_run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    trial_number INTEGER NOT NULL,
    candidate_run_id TEXT NOT NULL,
    mtm_run_id TEXT NOT NULL,
    proxy_objective DOUBLE,
    mtm_objective DOUBLE,
    validation_status TEXT,
    promotion_status TEXT,
    production_eligible BOOLEAN,
    position_count BIGINT,
    signal_count BIGINT,
    repeated_signal_suppressed_count BIGINT,
    repeated_signal_suppression_ratio DOUBLE,
    total_return DOUBLE,
    annualized_return DOUBLE,
    max_drawdown DOUBLE,
    sharpe DOUBLE,
    avg_active_positions DOUBLE,
    position_hit_rate DOUBLE,
    missing_entry_price_count BIGINT,
    missing_exit_price_count BIGINT,
    missing_path_price_count BIGINT,
    non_tdxhub_kline_count BIGINT,
    blockers_json TEXT,
    selected_features_json TEXT,
    selected_interactions_json TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, trial_number)
);
CREATE INDEX IF NOT EXISTS idx_synergy_mtm_rerank_run
    ON mart_synergy_policy_mtm_rerank(run_id);

CREATE TABLE IF NOT EXISTS mart_synergy_policy_mtm_rerank_summary (
    run_id TEXT PRIMARY KEY,
    optuna_run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    label_name TEXT NOT NULL,
    evaluated_trials INTEGER,
    best_trial_number INTEGER,
    best_candidate_run_id TEXT,
    best_mtm_run_id TEXT,
    best_mtm_objective DOUBLE,
    best_validation_status TEXT,
    best_blockers_json TEXT,
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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _finite(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def _candidate_fingerprint(selected_features_json: str, selected_interactions_json: str) -> str:
    features = sorted(str(item) for item in (_safe_json(selected_features_json, []) or []))
    interactions = _safe_json(selected_interactions_json, []) or []
    normalized_interactions = [
        {
            "interaction_type": str(item.get("interaction_type") or "pair"),
            "feature_a": str(item.get("feature_a") or ""),
            "feature_b": str(item.get("feature_b") or ""),
        }
        for item in interactions
        if isinstance(item, dict)
    ]
    normalized_interactions.sort(
        key=lambda item: (
            item["interaction_type"],
            item["feature_a"],
            item["feature_b"],
        )
    )
    return _json({"features": features, "interactions": normalized_interactions})


def _progress(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[mtm-rerank] {utc_now_iso()} {message}", flush=True)


def _load_optuna_summary(conn: Any, optuna_run_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT run_id, source_run_id, label_name
          FROM mart_optuna_synergy_study_summary
         WHERE run_id = ?
         LIMIT 1
        """,
        (optuna_run_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"optuna synergy study summary not found: {optuna_run_id}")
    return {
        "run_id": row["run_id"],
        "source_run_id": row["source_run_id"],
        "label_name": row["label_name"],
    }


def _load_trials(conn: Any, optuna_run_id: str, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT trial_number, objective_value,
               selected_count, selected_interaction_count,
               selected_features_json, selected_interactions_json
          FROM mart_optuna_synergy_trial
         WHERE run_id = ?
         ORDER BY objective_value DESC NULLS LAST, trial_number
         LIMIT ?
        """,
        (optuna_run_id, int(limit)),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"no optuna synergy trials found: {optuna_run_id}")
    return [dict(row) for row in rows]


def _upsert_trial_candidate(
    conn: Any,
    *,
    candidate_run_id: str,
    source_run_id: str,
    label_name: str,
    proxy_objective: float,
    selected_features_json: str,
    selected_interactions_json: str,
    built_at: str,
    optuna_run_id: str,
    trial_number: int,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_synergy_policy_candidate (
            run_id, source_run_id, label_name, objective_score,
            selected_features_json, selected_interactions_json,
            gate_status, notes_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_run_id,
            source_run_id,
            label_name,
            proxy_objective,
            selected_features_json,
            selected_interactions_json,
            "research_only",
            _json(
                {
                    "research_only": True,
                    "promotion_gate_required": True,
                    "origin": "post_optuna_mtm_rerank",
                    "optuna_run_id": optuna_run_id,
                    "trial_number": trial_number,
                }
            ),
            built_at,
        ),
    )


def _mtm_objective(
    mtm: dict[str, Any],
    *,
    max_drawdown: float,
    drawdown_penalty_weight: float,
    exposure_penalty_weight: float,
    exposure_scale: float,
    repeated_signal_penalty_weight: float,
    sharpe_weight: float,
    total_return_weight: float,
) -> float:
    total_return = _finite(mtm.get("total_return"))
    annualized_return = _finite(mtm.get("annualized_return"))
    max_dd_abs = abs(min(_finite(mtm.get("max_drawdown")), 0.0))
    drawdown_excess = max(0.0, max_dd_abs - abs(float(max_drawdown)))
    sharpe = _finite(mtm.get("sharpe"))
    active_exposure = _finite(mtm.get("avg_active_positions")) / max(float(exposure_scale), 1.0)
    signal_count = max(int(mtm.get("signal_count") or 0), 1)
    repeated_ratio = _finite(mtm.get("repeated_signal_suppressed_count")) / signal_count
    quality_penalty = 0.0
    for key in ("missing_entry_price_count", "missing_exit_price_count", "missing_path_price_count"):
        quality_penalty += min(float(mtm.get(key) or 0), 1000.0) / 1000.0
    quality_penalty += min(float(mtm.get("non_tdxhub_kline_count") or 0), 1000.0) / 1000.0
    return (
        annualized_return
        + float(total_return_weight) * total_return
        + float(sharpe_weight) * sharpe
        - float(drawdown_penalty_weight) * (max_dd_abs + drawdown_excess)
        - float(exposure_penalty_weight) * active_exposure
        - float(repeated_signal_penalty_weight) * repeated_ratio
        - quality_penalty
    )


def rerank_optuna_synergy_mtm(
    conn: Any,
    *,
    optuna_run_id: str,
    run_id: str,
    max_trials: int = 8,
    top_quantile: float = 0.10,
    daily_top_k: int | None = None,
    min_market_hs300_ret_20d: float | None = None,
    min_market_hs300_ret_60d: float | None = None,
    baseline_horizon_days: int = 60,
    min_positions: int = 500,
    min_active_days: int = 200,
    min_total_return: float = 0.0,
    max_drawdown: float = 0.25,
    transaction_cost_bps: float | None = None,
    conditional_threshold: float = 0.80,
    drawdown_penalty_weight: float = 2.0,
    exposure_penalty_weight: float = 0.05,
    exposure_scale: float = 1000.0,
    repeated_signal_penalty_weight: float = 0.10,
    sharpe_weight: float = 0.05,
    total_return_weight: float = 0.10,
    force_research_only: bool = True,
    dedupe_candidates: bool = True,
    progress: bool = True,
) -> dict[str, Any]:
    ensure_tables(conn)
    started_at = utc_now_iso()
    started = time.perf_counter()
    stage_timings: dict[str, float] = {}
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    stage_started = time.perf_counter()
    summary = _load_optuna_summary(conn, optuna_run_id)
    source_run_id = str(summary["source_run_id"])
    label_name = str(summary["label_name"])
    trials = _load_trials(conn, optuna_run_id, limit=max_trials)
    stage_timings["load_trials_s"] = round(time.perf_counter() - stage_started, 3)
    _progress(
        progress,
        f"start run_id={run_id} optuna_run_id={optuna_run_id} trials={len(trials)}",
    )
    conn.execute("DELETE FROM mart_synergy_policy_mtm_rerank WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_synergy_policy_mtm_rerank_summary WHERE run_id = ?", (run_id,))

    rows: list[tuple[Any, ...]] = []
    best: dict[str, Any] | None = None
    seen_fingerprints: set[str] = set()
    for idx, trial in enumerate(trials, start=1):
        trial_number = int(trial["trial_number"])
        candidate_run_id = f"{run_id}_trial_{trial_number}"
        mtm_run_id = f"{run_id}_mtm_trial_{trial_number}"
        proxy_objective = _finite(trial["objective_value"])
        selected_features_json = str(trial["selected_features_json"] or "[]")
        selected_interactions_json = str(trial["selected_interactions_json"] or "[]")
        fingerprint = _candidate_fingerprint(selected_features_json, selected_interactions_json)
        if dedupe_candidates and fingerprint in seen_fingerprints:
            _progress(progress, f"trial_skip_duplicate {idx}/{len(trials)} trial={trial_number}")
            continue
        seen_fingerprints.add(fingerprint)
        _progress(progress, f"trial_start {idx}/{len(trials)} trial={trial_number}")
        stage_started = time.perf_counter()
        _upsert_trial_candidate(
            conn,
            candidate_run_id=candidate_run_id,
            source_run_id=source_run_id,
            label_name=label_name,
            proxy_objective=proxy_objective,
            selected_features_json=selected_features_json,
            selected_interactions_json=selected_interactions_json,
            built_at=built_at,
            optuna_run_id=optuna_run_id,
            trial_number=trial_number,
        )
        mtm = validate_synergy_policy_mark_to_market(
            conn,
            candidate_run_id=candidate_run_id,
            run_id=mtm_run_id,
            top_quantile=top_quantile,
            daily_top_k=daily_top_k,
            min_market_hs300_ret_20d=min_market_hs300_ret_20d,
            min_market_hs300_ret_60d=min_market_hs300_ret_60d,
            baseline_horizon_days=baseline_horizon_days,
            min_positions=min_positions,
            min_active_days=min_active_days,
            min_total_return=min_total_return,
            max_drawdown=max_drawdown,
            transaction_cost_bps=transaction_cost_bps,
            conditional_threshold=conditional_threshold,
            force_research_only=force_research_only,
            progress=False,
        )
        mtm_objective = _mtm_objective(
            mtm,
            max_drawdown=max_drawdown,
            drawdown_penalty_weight=drawdown_penalty_weight,
            exposure_penalty_weight=exposure_penalty_weight,
            exposure_scale=exposure_scale,
            repeated_signal_penalty_weight=repeated_signal_penalty_weight,
            sharpe_weight=sharpe_weight,
            total_return_weight=total_return_weight,
        )
        signal_count = int(mtm.get("signal_count") or 0)
        repeated_count = int(mtm.get("repeated_signal_suppressed_count") or 0)
        repeated_ratio = repeated_count / max(signal_count, 1)
        blockers = mtm.get("blockers") or []
        item = {
            "trial_number": trial_number,
            "candidate_run_id": candidate_run_id,
            "mtm_run_id": mtm_run_id,
            "mtm_objective": mtm_objective,
            "validation_status": mtm.get("validation_status"),
            "blockers": blockers,
            "total_return": mtm.get("total_return"),
            "max_drawdown": mtm.get("max_drawdown"),
        }
        if best is None or mtm_objective > float(best["mtm_objective"]):
            best = item
        rows.append(
            (
                run_id,
                optuna_run_id,
                source_run_id,
                label_name,
                trial_number,
                candidate_run_id,
                mtm_run_id,
                proxy_objective,
                mtm_objective,
                mtm.get("validation_status"),
                mtm.get("promotion_status"),
                bool(mtm.get("production_eligible")),
                int(mtm.get("position_count") or 0),
                signal_count,
                repeated_count,
                repeated_ratio,
                mtm.get("total_return"),
                mtm.get("annualized_return"),
                mtm.get("max_drawdown"),
                mtm.get("sharpe"),
                mtm.get("avg_active_positions"),
                mtm.get("position_hit_rate"),
                int(mtm.get("missing_entry_price_count") or 0),
                int(mtm.get("missing_exit_price_count") or 0),
                int(mtm.get("missing_path_price_count") or 0),
                int(mtm.get("non_tdxhub_kline_count") or 0),
                _json(blockers),
                selected_features_json,
                selected_interactions_json,
                built_at,
            )
        )
        _progress(
            progress,
            "trial_done "
            f"trial={trial_number} mtm_objective={mtm_objective:.6f} "
            f"status={mtm.get('validation_status')} dd={_finite(mtm.get('max_drawdown')):.4f}",
        )
        stage_timings[f"trial_{trial_number}_mtm_s"] = round(
            time.perf_counter() - stage_started,
            3,
        )

    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_synergy_policy_mtm_rerank (
            run_id, optuna_run_id, source_run_id, label_name, trial_number,
            candidate_run_id, mtm_run_id, proxy_objective, mtm_objective,
            validation_status, promotion_status, production_eligible,
            position_count, signal_count, repeated_signal_suppressed_count,
            repeated_signal_suppression_ratio, total_return, annualized_return,
            max_drawdown, sharpe, avg_active_positions, position_hit_rate,
            missing_entry_price_count, missing_exit_price_count,
            missing_path_price_count, non_tdxhub_kline_count, blockers_json,
            selected_features_json, selected_interactions_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    config = {
        "max_trials": max_trials,
        "top_quantile": top_quantile,
        "daily_top_k": daily_top_k,
        "min_market_hs300_ret_20d": min_market_hs300_ret_20d,
        "min_market_hs300_ret_60d": min_market_hs300_ret_60d,
        "baseline_horizon_days": baseline_horizon_days,
        "min_positions": min_positions,
        "min_active_days": min_active_days,
        "min_total_return": min_total_return,
        "max_drawdown": max_drawdown,
        "transaction_cost_bps": transaction_cost_bps,
        "conditional_threshold": conditional_threshold,
        "drawdown_penalty_weight": drawdown_penalty_weight,
        "exposure_penalty_weight": exposure_penalty_weight,
        "exposure_scale": exposure_scale,
        "repeated_signal_penalty_weight": repeated_signal_penalty_weight,
        "sharpe_weight": sharpe_weight,
        "total_return_weight": total_return_weight,
        "force_research_only": force_research_only,
        "dedupe_candidates": dedupe_candidates,
        "input_trial_count": len(trials),
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_synergy_policy_mtm_rerank_summary (
            run_id, optuna_run_id, source_run_id, label_name,
            evaluated_trials, best_trial_number, best_candidate_run_id,
            best_mtm_run_id, best_mtm_objective, best_validation_status,
            best_blockers_json, config_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            optuna_run_id,
            source_run_id,
            label_name,
            len(rows),
            int(best["trial_number"]) if best else None,
            best["candidate_run_id"] if best else None,
            best["mtm_run_id"] if best else None,
            best["mtm_objective"] if best else None,
            best["validation_status"] if best else None,
            _json(best["blockers"] if best else []),
            _json(config),
            built_at,
        ),
    )
    for table in (
        "mart_synergy_policy_candidate",
        "mart_synergy_policy_mtm_rerank",
        "mart_synergy_policy_mtm_rerank_summary",
    ):
        record_actual_version(conn, table)
    duration_s = time.perf_counter() - started
    stage_timings["total_s"] = round(duration_s, 3)
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="rerank_optuna_synergy_mtm",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO),
        input_tables=[
            "mart_optuna_synergy_trial",
            "mart_optuna_synergy_study_summary",
            "mart_temporal_research_panel",
            "mart_feature_temporal_relevance",
        ],
        output_tables=[
            "mart_synergy_policy_candidate",
            "mart_synergy_policy_mtm_rerank",
            "mart_synergy_policy_mtm_rerank_summary",
            "mart_synergy_policy_mtm_gate",
        ],
        label_name=label_name,
        gate_result=best["validation_status"] if best else "no_trials",
        blockers=best["blockers"] if best else ["no_trials"],
        perf_summary={
            "optuna_run_id": optuna_run_id,
            "source_run_id": source_run_id,
            "evaluated_trials": len(rows),
            "best": best or {},
            "config": config,
            "stage_timings": stage_timings,
        },
    )
    conn.commit()
    _progress(progress, f"done run_id={run_id} evaluated={len(rows)}")
    return {
        "run_id": run_id,
        "optuna_run_id": optuna_run_id,
        "source_run_id": source_run_id,
        "label_name": label_name,
        "evaluated_trials": len(rows),
        "best": best or {},
        "duration_s": duration_s,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--optuna-run-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-trials", type=int, default=8)
    parser.add_argument("--top-quantile", type=float, default=0.10)
    parser.add_argument("--daily-top-k", type=int, default=None)
    parser.add_argument("--min-market-hs300-ret-20d", type=float, default=None)
    parser.add_argument("--min-market-hs300-ret-60d", type=float, default=None)
    parser.add_argument("--baseline-horizon-days", type=int, default=60)
    parser.add_argument("--min-positions", type=int, default=500)
    parser.add_argument("--min-active-days", type=int, default=200)
    parser.add_argument("--min-total-return", type=float, default=0.0)
    parser.add_argument("--max-drawdown", type=float, default=0.25)
    parser.add_argument("--transaction-cost-bps", type=float, default=None)
    parser.add_argument("--conditional-threshold", type=float, default=0.80)
    parser.add_argument("--drawdown-penalty-weight", type=float, default=2.0)
    parser.add_argument("--exposure-penalty-weight", type=float, default=0.05)
    parser.add_argument("--exposure-scale", type=float, default=1000.0)
    parser.add_argument("--repeated-signal-penalty-weight", type=float, default=0.10)
    parser.add_argument("--sharpe-weight", type=float, default=0.05)
    parser.add_argument("--total-return-weight", type=float, default=0.10)
    parser.add_argument("--allow-production-candidate", action="store_true")
    parser.add_argument("--no-dedupe-candidates", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    with get_conn() as conn:
        result = rerank_optuna_synergy_mtm(
            conn,
            optuna_run_id=args.optuna_run_id,
            run_id=args.run_id,
            max_trials=args.max_trials,
            top_quantile=args.top_quantile,
            daily_top_k=args.daily_top_k,
            min_market_hs300_ret_20d=args.min_market_hs300_ret_20d,
            min_market_hs300_ret_60d=args.min_market_hs300_ret_60d,
            baseline_horizon_days=args.baseline_horizon_days,
            min_positions=args.min_positions,
            min_active_days=args.min_active_days,
            min_total_return=args.min_total_return,
            max_drawdown=args.max_drawdown,
            transaction_cost_bps=args.transaction_cost_bps,
            conditional_threshold=args.conditional_threshold,
            drawdown_penalty_weight=args.drawdown_penalty_weight,
            exposure_penalty_weight=args.exposure_penalty_weight,
            exposure_scale=args.exposure_scale,
            repeated_signal_penalty_weight=args.repeated_signal_penalty_weight,
            sharpe_weight=args.sharpe_weight,
            total_return_weight=args.total_return_weight,
            force_research_only=not args.allow_production_candidate,
            dedupe_candidates=not args.no_dedupe_candidates,
            progress=not args.quiet,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
