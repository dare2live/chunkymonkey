#!/usr/bin/env python3
"""Build context diagnostics for model stability failures."""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent

DDL = """
CREATE TABLE IF NOT EXISTS mart_model_stability_context_diagnostic (
    run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    model_selection_run_id TEXT,
    label_name TEXT,
    model_family TEXT,
    scope TEXT NOT NULL,
    fold_id INTEGER,
    period_start TEXT,
    period_end TEXT,
    rank_ic DOUBLE,
    spread DOUBLE,
    topk_net_return DOUBLE,
    topk_turnover DOUBLE,
    topk_max_drawdown DOUBLE,
    feature_drift_psi_max DOUBLE,
    row_count BIGINT,
    date_count BIGINT,
    label_mean DOUBLE,
    label_median DOUBLE,
    label_std DOUBLE,
    label_positive_rate DOUBLE,
    market_ret_mean DOUBLE,
    regime_counts_json TEXT,
    dominant_regime TEXT,
    dominant_regime_share DOUBLE,
    diagnosis TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, source_run_id, scope, fold_id)
);
CREATE INDEX IF NOT EXISTS idx_model_stability_context_diag_source
    ON mart_model_stability_context_diagnostic(run_id, source_run_id, scope);

CREATE TABLE IF NOT EXISTS mart_model_stability_context_summary (
    run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    label_name TEXT,
    model_family TEXT,
    best_trial_number INTEGER,
    fold_count INTEGER,
    holdout_rank_ic DOUBLE,
    walkforward_avg_rank_ic DOUBLE,
    walkforward_std_rank_ic DOUBLE,
    walkforward_worst_topk_drawdown DOUBLE,
    walkforward_worst_feature_drift_psi DOUBLE,
    negative_rank_ic_folds INTEGER,
    weak_rank_ic_periods INTEGER,
    low_holdout_rank_ic BOOLEAN,
    high_walkforward_std BOOLEAN,
    drift_gate_pass BOOLEAN,
    drawdown_gate_pass BOOLEAN,
    context_diagnosis_counts_json TEXT,
    main_blockers_json TEXT,
    recommendation TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, source_run_id)
);
ALTER TABLE mart_model_stability_context_summary ADD COLUMN IF NOT EXISTS fold_count INTEGER;
ALTER TABLE mart_model_stability_context_summary ADD COLUMN IF NOT EXISTS low_holdout_rank_ic BOOLEAN;
ALTER TABLE mart_model_stability_context_summary ADD COLUMN IF NOT EXISTS high_walkforward_std BOOLEAN;
ALTER TABLE mart_model_stability_context_summary ADD COLUMN IF NOT EXISTS drift_gate_pass BOOLEAN;
ALTER TABLE mart_model_stability_context_summary ADD COLUMN IF NOT EXISTS drawdown_gate_pass BOOLEAN;
ALTER TABLE mart_model_stability_context_summary ADD COLUMN IF NOT EXISTS main_blockers_json TEXT;
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
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        (table_name,),
    ).fetchone() is not None


def _columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {
        str(row["column_name"])
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table_name,),
        ).fetchall()
    }


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _json_loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def _source_summary(conn: Any, source_run_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT run_id, model_selection_run_id, feature_table, feature_set_id,
               label_name, best_trial_number, config_json
          FROM mart_model_stability_search_summary
         WHERE run_id = ?
        """,
        (source_run_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"missing model stability summary: {source_run_id}")
    config = _json_loads(row["config_json"], {})
    return {**dict(row), "config": config if isinstance(config, dict) else {}}


def _best_trial(conn: Any, source_run_id: str, best_trial_number: int | None) -> dict[str, Any]:
    if best_trial_number is not None:
        row = conn.execute(
            """
            SELECT *
              FROM mart_model_stability_search_trial
             WHERE run_id = ?
               AND trial_number = ?
             LIMIT 1
            """,
            (source_run_id, int(best_trial_number)),
        ).fetchone()
        if row:
            return dict(row)
    row = conn.execute(
        """
        SELECT *
          FROM mart_model_stability_search_trial
         WHERE run_id = ?
         ORDER BY objective_value DESC NULLS LAST, trial_number
         LIMIT 1
        """,
        (source_run_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"missing stability trial rows: {source_run_id}")
    return dict(row)


def _period_context(
    conn: Any,
    *,
    feature_table: str,
    label_name: str,
    feature_set_id: str | None,
    period_start: str,
    period_end: str,
) -> dict[str, Any]:
    cols = _columns(conn, feature_table)
    if label_name not in cols:
        raise RuntimeError(f"{feature_table} missing label column: {label_name}")
    label_expr = _quote_ident(label_name)
    market_col = "hs300_ret_60d" if label_name.endswith("60d") and "hs300_ret_60d" in cols else None
    if market_col is None and "hs300_ret_20d" in cols:
        market_col = "hs300_ret_20d"
    market_expr = f"AVG(CAST({_quote_ident(market_col)} AS DOUBLE))" if market_col else "NULL"
    filters = [
        "CAST(date AS DATE) >= CAST(? AS DATE)",
        "CAST(date AS DATE) <= CAST(? AS DATE)",
        f"{label_expr} IS NOT NULL",
    ]
    params: list[Any] = [period_start, period_end]
    if feature_set_id and "feature_set_id" in cols:
        filters.append("feature_set_id = ?")
        params.append(feature_set_id)
    where = " AND ".join(filters)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT date) AS date_count,
               AVG(CAST({label_expr} AS DOUBLE)) AS label_mean,
               MEDIAN(CAST({label_expr} AS DOUBLE)) AS label_median,
               STDDEV_SAMP(CAST({label_expr} AS DOUBLE)) AS label_std,
               AVG(CASE WHEN CAST({label_expr} AS DOUBLE) > 0 THEN 1.0 ELSE 0.0 END) AS label_positive_rate,
               {market_expr} AS market_ret_mean
          FROM {_quote_ident(feature_table)}
         WHERE {where}
        """,
        params,
    ).fetchone()
    regime_counts: dict[str, int] = {}
    if "regime_flag" in cols:
        regime_rows = conn.execute(
            f"""
            SELECT COALESCE(CAST(regime_flag AS TEXT), 'unknown') AS regime,
                   COUNT(*) AS n
              FROM {_quote_ident(feature_table)}
             WHERE {where}
             GROUP BY COALESCE(CAST(regime_flag AS TEXT), 'unknown')
            """,
            params,
        ).fetchall()
        regime_counts = {str(item["regime"]): int(item["n"] or 0) for item in regime_rows}
    dominant_regime = None
    dominant_share = None
    total = sum(regime_counts.values())
    if total > 0:
        dominant_regime, dominant_count = max(regime_counts.items(), key=lambda item: item[1])
        dominant_share = dominant_count / total
    return {
        "row_count": int(row["row_count"] or 0),
        "date_count": int(row["date_count"] or 0),
        "label_mean": _finite(row["label_mean"]),
        "label_median": _finite(row["label_median"]),
        "label_std": _finite(row["label_std"]),
        "label_positive_rate": _finite(row["label_positive_rate"]),
        "market_ret_mean": _finite(row["market_ret_mean"]),
        "regime_counts": regime_counts,
        "dominant_regime": dominant_regime,
        "dominant_regime_share": dominant_share,
    }


def _holdout_bounds(conn: Any, *, feature_table: str, label_name: str, start: str, end: str, feature_set_id: str | None) -> tuple[str | None, str | None]:
    cols = _columns(conn, feature_table)
    filters = [
        "CAST(date AS DATE) >= CAST(? AS DATE)",
        "CAST(date AS DATE) <= CAST(? AS DATE)",
        f"{_quote_ident(label_name)} IS NOT NULL",
    ]
    params: list[Any] = [start, end]
    if feature_set_id and "feature_set_id" in cols:
        filters.append("feature_set_id = ?")
        params.append(feature_set_id)
    rows = conn.execute(
        f"""
        SELECT DISTINCT CAST(date AS VARCHAR) AS date_value
          FROM {_quote_ident(feature_table)}
         WHERE {" AND ".join(filters)}
         ORDER BY CAST(date AS DATE)
        """,
        params,
    ).fetchall()
    dates = [str(row["date_value"]) for row in rows]
    if not dates:
        return None, None
    holdout_start_idx = int(len(dates) * 0.85)
    holdout_start_idx = min(max(holdout_start_idx, 0), len(dates) - 1)
    return dates[holdout_start_idx], dates[-1]


def _diagnosis(metrics: dict[str, Any], context: dict[str, Any], *, min_rank_ic: float, max_drift_psi: float, max_drawdown: float) -> str:
    rank_ic = _finite(metrics.get("rank_ic"))
    spread = _finite(metrics.get("spread"))
    topk = _finite(metrics.get("topk_net_return"))
    drift = _finite(metrics.get("feature_drift_psi_max"))
    drawdown = _finite(metrics.get("topk_max_drawdown"))
    positive_rate = _finite(context.get("label_positive_rate"))
    market_ret = _finite(context.get("market_ret_mean"))
    if drift is not None and drift > max_drift_psi:
        return "feature_drift_gate"
    if drawdown is not None and drawdown < -abs(max_drawdown):
        return "drawdown_gate"
    if rank_ic is not None and rank_ic < 0 and topk is not None and topk > 0 and positive_rate is not None and positive_rate >= 0.60:
        return "broad_rally_rank_inversion"
    if rank_ic is not None and rank_ic < 0:
        return "rank_inversion"
    if rank_ic is not None and rank_ic < min_rank_ic and spread is not None and spread > 0:
        return "spread_ok_rank_weak"
    if market_ret is not None and abs(market_ret) > 0.10 and rank_ic is not None and rank_ic < min_rank_ic:
        return "market_regime_sensitive_rank"
    if rank_ic is not None and rank_ic < min_rank_ic:
        return "rank_weak"
    return "ok"


def _insert_detail(conn: Any, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_model_stability_context_diagnostic
        (run_id, source_run_id, model_selection_run_id, label_name, model_family,
         scope, fold_id, period_start, period_end, rank_ic, spread,
         topk_net_return, topk_turnover, topk_max_drawdown, feature_drift_psi_max,
         row_count, date_count, label_mean, label_median, label_std,
         label_positive_rate, market_ret_mean, regime_counts_json,
         dominant_regime, dominant_regime_share, diagnosis, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["run_id"],
            row["source_run_id"],
            row["model_selection_run_id"],
            row["label_name"],
            row["model_family"],
            row["scope"],
            row["fold_id"],
            row["period_start"],
            row["period_end"],
            row["rank_ic"],
            row["spread"],
            row["topk_net_return"],
            row["topk_turnover"],
            row["topk_max_drawdown"],
            row["feature_drift_psi_max"],
            row["row_count"],
            row["date_count"],
            row["label_mean"],
            row["label_median"],
            row["label_std"],
            row["label_positive_rate"],
            row["market_ret_mean"],
            json.dumps(row["regime_counts"], ensure_ascii=False, sort_keys=True),
            row["dominant_regime"],
            row["dominant_regime_share"],
            row["diagnosis"],
            row["built_at"],
        ),
    )


def _recommendation(detail_rows: list[dict[str, Any]], trial: dict[str, Any], *, min_holdout_rank_ic: float, max_walkforward_std_rank_ic: float) -> str:
    diagnoses = {row["diagnosis"] for row in detail_rows}
    holdout_rank_ic = _finite(trial.get("holdout_rank_ic")) or 0.0
    wf_std = _finite(trial.get("walkforward_std_rank_ic")) or 0.0
    drift = _finite(trial.get("walkforward_worst_feature_drift_psi")) or 0.0
    drawdown = _finite(trial.get("walkforward_worst_topk_drawdown")) or 0.0
    if drift <= 0.25 and drawdown >= -0.20 and holdout_rank_ic < min_holdout_rank_ic and wf_std > max_walkforward_std_rank_ic:
        return "regime_split_or_holdout_rank_calibration_before_larger_study"
    if "broad_rally_rank_inversion" in diagnoses:
        return "test_market_phase_split_and_portfolio_beta_controls"
    if "rank_inversion" in diagnoses:
        return "inspect_feature_direction_by_fold_before_expansion"
    return "continue_current_branch_with_caution"


def _main_blockers(
    *,
    negative_rank_ic: int,
    low_holdout_rank_ic: bool,
    high_walkforward_std: bool,
    drift_gate_pass: bool,
    drawdown_gate_pass: bool,
) -> list[str]:
    blockers: list[str] = []
    if negative_rank_ic > 0:
        blockers.append("market_phase_rank_inversion")
    if low_holdout_rank_ic:
        blockers.append("low_holdout_rank_ic")
    if high_walkforward_std:
        blockers.append("high_walkforward_std_rank_ic")
    if not drift_gate_pass:
        blockers.append("feature_drift_psi")
    if not drawdown_gate_pass:
        blockers.append("topk_drawdown")
    return blockers


def build_model_stability_context_diagnostics(
    conn: Any,
    *,
    run_id: str | None = None,
    source_run_ids: list[str],
    min_rank_ic: float = 0.015,
    min_holdout_rank_ic: float = 0.0424,
    max_walkforward_std_rank_ic: float = 0.03,
    max_topk_drawdown: float = 0.20,
    max_feature_drift_psi: float = 0.25,
) -> dict[str, Any]:
    ensure_tables(conn)
    started_at = utc_now_iso()
    t0 = time.perf_counter()
    run_id = run_id or f"model_stability_context_diag_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute("DELETE FROM mart_model_stability_context_diagnostic WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_model_stability_context_summary WHERE run_id = ?", (run_id,))

    all_detail_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    input_feature_tables: set[str] = set()
    for source_run_id in source_run_ids:
        summary = _source_summary(conn, source_run_id)
        trial = _best_trial(conn, source_run_id, summary.get("best_trial_number"))
        label_name = summary["label_name"]
        feature_table = summary["feature_table"]
        input_feature_tables.add(str(feature_table))
        feature_set_id = summary["feature_set_id"]
        model_family = trial.get("model_family") or summary["config"].get("model_family")
        detail_rows: list[dict[str, Any]] = []

        folds = _json_loads(trial.get("fold_metrics_json"), [])
        if not isinstance(folds, list):
            folds = []
        for fold in folds:
            if not isinstance(fold, dict) or not fold.get("test_start") or not fold.get("test_end"):
                continue
            context = _period_context(
                conn,
                feature_table=feature_table,
                label_name=label_name,
                feature_set_id=feature_set_id,
                period_start=str(fold["test_start"]),
                period_end=str(fold["test_end"]),
            )
            metrics = {
                "rank_ic": _finite(fold.get("rank_ic")),
                "spread": _finite(fold.get("spread")),
                "topk_net_return": _finite(fold.get("topk_net_return")),
                "topk_turnover": _finite(fold.get("topk_turnover")),
                "topk_max_drawdown": _finite(fold.get("topk_max_drawdown")),
                "feature_drift_psi_max": _finite(fold.get("feature_drift_psi_max")),
            }
            row = {
                "run_id": run_id,
                "source_run_id": source_run_id,
                "model_selection_run_id": summary["model_selection_run_id"],
                "label_name": label_name,
                "model_family": model_family,
                "scope": "walkforward_fold",
                "fold_id": int(fold.get("fold_id") or 0),
                "period_start": str(fold["test_start"]),
                "period_end": str(fold["test_end"]),
                **metrics,
                **context,
                "diagnosis": _diagnosis(metrics, context, min_rank_ic=min_rank_ic, max_drift_psi=max_feature_drift_psi, max_drawdown=max_topk_drawdown),
                "built_at": built_at,
            }
            _insert_detail(conn, row)
            detail_rows.append(row)
            all_detail_rows.append(row)

        holdout_start, holdout_end = _holdout_bounds(
            conn,
            feature_table=feature_table,
            label_name=label_name,
            start=str(summary["config"].get("start") or "2025-01-01"),
            end=str(summary["config"].get("end") or "2025-12-31"),
            feature_set_id=feature_set_id,
        )
        if holdout_start and holdout_end:
            context = _period_context(
                conn,
                feature_table=feature_table,
                label_name=label_name,
                feature_set_id=feature_set_id,
                period_start=holdout_start,
                period_end=holdout_end,
            )
            metrics = {
                "rank_ic": _finite(trial.get("holdout_rank_ic")),
                "spread": _finite(trial.get("holdout_long_short_spread")),
                "topk_net_return": _finite(trial.get("holdout_topk_net_return")),
                "topk_turnover": _finite(trial.get("holdout_topk_turnover")),
                "topk_max_drawdown": _finite(trial.get("holdout_topk_max_drawdown")),
                "feature_drift_psi_max": _finite(trial.get("holdout_feature_drift_psi_max")),
            }
            row = {
                "run_id": run_id,
                "source_run_id": source_run_id,
                "model_selection_run_id": summary["model_selection_run_id"],
                "label_name": label_name,
                "model_family": model_family,
                "scope": "holdout",
                "fold_id": 0,
                "period_start": holdout_start,
                "period_end": holdout_end,
                **metrics,
                **context,
                "diagnosis": _diagnosis(metrics, context, min_rank_ic=min_holdout_rank_ic, max_drift_psi=max_feature_drift_psi, max_drawdown=max_topk_drawdown),
                "built_at": built_at,
            }
            _insert_detail(conn, row)
            detail_rows.append(row)
            all_detail_rows.append(row)

        diagnosis_counts: dict[str, int] = {}
        for row in detail_rows:
            diagnosis_counts[row["diagnosis"]] = diagnosis_counts.get(row["diagnosis"], 0) + 1
        negative_rank_ic = sum(1 for row in detail_rows if row["scope"] == "walkforward_fold" and (row["rank_ic"] or 0.0) < 0)
        weak_rank_ic = sum(1 for row in detail_rows if (row["rank_ic"] or 0.0) < min_rank_ic)
        fold_count = sum(1 for row in detail_rows if row["scope"] == "walkforward_fold")
        holdout_rank_ic = _finite(trial.get("holdout_rank_ic"))
        walkforward_std_rank_ic = _finite(trial.get("walkforward_std_rank_ic"))
        worst_topk_drawdown = _finite(trial.get("walkforward_worst_topk_drawdown"))
        worst_feature_drift_psi = _finite(trial.get("walkforward_worst_feature_drift_psi"))
        low_holdout_rank_ic = (holdout_rank_ic or 0.0) < min_holdout_rank_ic
        high_walkforward_std = (walkforward_std_rank_ic or 0.0) > max_walkforward_std_rank_ic
        drift_gate_pass = worst_feature_drift_psi is not None and worst_feature_drift_psi <= max_feature_drift_psi
        drawdown_gate_pass = worst_topk_drawdown is not None and worst_topk_drawdown >= -abs(max_topk_drawdown)
        main_blockers = _main_blockers(
            negative_rank_ic=negative_rank_ic,
            low_holdout_rank_ic=low_holdout_rank_ic,
            high_walkforward_std=high_walkforward_std,
            drift_gate_pass=drift_gate_pass,
            drawdown_gate_pass=drawdown_gate_pass,
        )
        recommendation = _recommendation(
            detail_rows,
            trial,
            min_holdout_rank_ic=min_holdout_rank_ic,
            max_walkforward_std_rank_ic=max_walkforward_std_rank_ic,
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO mart_model_stability_context_summary
            (run_id, source_run_id, label_name, model_family, best_trial_number,
             fold_count, holdout_rank_ic, walkforward_avg_rank_ic, walkforward_std_rank_ic,
             walkforward_worst_topk_drawdown, walkforward_worst_feature_drift_psi,
             negative_rank_ic_folds, weak_rank_ic_periods,
             low_holdout_rank_ic, high_walkforward_std, drift_gate_pass,
             drawdown_gate_pass, context_diagnosis_counts_json,
             main_blockers_json, recommendation, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_run_id,
                label_name,
                model_family,
                int(summary["best_trial_number"]) if summary["best_trial_number"] is not None else None,
                fold_count,
                holdout_rank_ic,
                _finite(trial.get("walkforward_avg_rank_ic")),
                walkforward_std_rank_ic,
                worst_topk_drawdown,
                worst_feature_drift_psi,
                negative_rank_ic,
                weak_rank_ic,
                low_holdout_rank_ic,
                high_walkforward_std,
                drift_gate_pass,
                drawdown_gate_pass,
                json.dumps(diagnosis_counts, ensure_ascii=False, sort_keys=True),
                json.dumps(main_blockers, ensure_ascii=False),
                recommendation,
                built_at,
            ),
        )
        summaries.append(
            {
                "source_run_id": source_run_id,
                "label_name": label_name,
                "model_family": model_family,
                "diagnosis_counts": diagnosis_counts,
                "fold_count": fold_count,
                "negative_rank_ic_folds": negative_rank_ic,
                "weak_rank_ic_periods": weak_rank_ic,
                "main_blockers": main_blockers,
                "recommendation": recommendation,
            }
        )

    record_actual_version(conn, "mart_model_stability_context_diagnostic")
    record_actual_version(conn, "mart_model_stability_context_summary")
    ended_at = utc_now_iso()
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_model_stability_context_diagnostics",
        status="success",
        started_at=started_at,
        ended_at=ended_at,
        duration_s=time.perf_counter() - t0,
        commit_sha=git_commit_sha(REPO),
        input_tables=[
            "mart_model_stability_search_summary",
            "mart_model_stability_search_trial",
            *sorted(input_feature_tables),
        ],
        output_tables=["mart_model_stability_context_diagnostic", "mart_model_stability_context_summary"],
        perf_summary={
            "source_run_ids": source_run_ids,
            "detail_rows": len(all_detail_rows),
            "summary_rows": len(summaries),
            "recommendations": {row["source_run_id"]: row["recommendation"] for row in summaries},
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "status": "success",
        "detail_rows": len(all_detail_rows),
        "summary_rows": len(summaries),
        "summaries": summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--source-run-id", action="append", default=[])
    parser.add_argument("--min-rank-ic", type=float, default=0.015)
    parser.add_argument("--min-holdout-rank-ic", type=float, default=0.0424)
    parser.add_argument("--max-walkforward-std-rank-ic", type=float, default=0.03)
    parser.add_argument("--max-topk-drawdown", type=float, default=0.20)
    parser.add_argument("--max-feature-drift-psi", type=float, default=0.25)
    args = parser.parse_args()
    if not args.source_run_id:
        raise SystemExit("--source-run-id is required")
    conn = get_conn()
    try:
        result = build_model_stability_context_diagnostics(
            conn,
            run_id=args.run_id,
            source_run_ids=args.source_run_id,
            min_rank_ic=args.min_rank_ic,
            min_holdout_rank_ic=args.min_holdout_rank_ic,
            max_walkforward_std_rank_ic=args.max_walkforward_std_rank_ic,
            max_topk_drawdown=args.max_topk_drawdown,
            max_feature_drift_psi=args.max_feature_drift_psi,
        )
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
