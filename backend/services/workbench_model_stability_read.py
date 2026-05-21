"""Model stability context read model for Workbench."""
from __future__ import annotations

from typing import Any
import json


def _relation_exists(conn: Any, relation: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {relation} LIMIT 0").fetchone()
        return True
    except Exception:
        return False


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
    return row is not None and _relation_exists(conn, table_name)


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


def _columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {
        str(_row_value(row, "column_name", 0))
        for row in conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    }


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0]


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _select_expr(cols: set[str], column: str, *, alias: str | None = None, default: str = "NULL") -> str:
    out = alias or column
    if column in cols:
        return f"{column} AS {out}"
    return f"{default} AS {out}"


def _cast_select_expr(cols: set[str], column: str, *, alias: str | None = None) -> str:
    out = alias or column
    if column in cols:
        return f"CAST({column} AS VARCHAR) AS {out}"
    return f"NULL AS {out}"


def _latest_run_id(conn: Any, table_name: str) -> str | None:
    if not _table_exists(conn, table_name):
        return None
    cols = _columns(conn, table_name)
    if "run_id" not in cols:
        return None
    order_col = "built_at" if "built_at" in cols else "created_at" if "created_at" in cols else None
    if order_col:
        return _scalar(conn, f"SELECT run_id FROM {table_name} ORDER BY {order_col} DESC LIMIT 1")
    return _scalar(conn, f"SELECT run_id FROM {table_name} LIMIT 1")


def build_model_stability_context(
    conn: Any,
    *,
    summary_limit: int = 8,
    diagnostic_limit: int = 12,
) -> dict[str, Any]:
    summary_table = "mart_model_stability_context_summary"
    detail_table = "mart_model_stability_context_diagnostic"
    if not _table_exists(conn, summary_table):
        return {"run_id": None, "summaries": [], "diagnostics": []}
    run_id = _latest_run_id(conn, summary_table)
    if not run_id:
        return {"run_id": None, "summaries": [], "diagnostics": []}

    summary_cols = _columns(conn, summary_table)
    summary_rows = conn.execute(
        f"""
        SELECT {_select_expr(summary_cols, "run_id")},
               {_select_expr(summary_cols, "source_run_id")},
               {_select_expr(summary_cols, "label_name")},
               {_select_expr(summary_cols, "model_family")},
               {_select_expr(summary_cols, "best_trial_number")},
               {_select_expr(summary_cols, "fold_count")},
               {_select_expr(summary_cols, "holdout_rank_ic")},
               {_select_expr(summary_cols, "walkforward_avg_rank_ic")},
               {_select_expr(summary_cols, "walkforward_std_rank_ic")},
               {_select_expr(summary_cols, "walkforward_worst_topk_drawdown")},
               {_select_expr(summary_cols, "walkforward_worst_feature_drift_psi")},
               {_select_expr(summary_cols, "negative_rank_ic_folds")},
               {_select_expr(summary_cols, "weak_rank_ic_periods")},
               {_select_expr(summary_cols, "low_holdout_rank_ic")},
               {_select_expr(summary_cols, "high_walkforward_std")},
               {_select_expr(summary_cols, "drift_gate_pass")},
               {_select_expr(summary_cols, "drawdown_gate_pass")},
               {_select_expr(summary_cols, "context_diagnosis_counts_json")},
               {_select_expr(summary_cols, "main_blockers_json")},
               {_select_expr(summary_cols, "recommendation")},
               {_cast_select_expr(summary_cols, "built_at")}
          FROM mart_model_stability_context_summary
         WHERE run_id = ?
         ORDER BY {"built_at DESC," if "built_at" in summary_cols else ""} source_run_id
         LIMIT ?
        """,
        (run_id, int(summary_limit)),
    ).fetchall()
    summaries = []
    for row in summary_rows:
        summaries.append(
            {
                "run_id": row["run_id"],
                "source_run_id": row["source_run_id"],
                "label_name": row["label_name"],
                "model_family": row["model_family"],
                "best_trial_number": row["best_trial_number"],
                "fold_count": row["fold_count"],
                "holdout_rank_ic": row["holdout_rank_ic"],
                "walkforward_avg_rank_ic": row["walkforward_avg_rank_ic"],
                "walkforward_std_rank_ic": row["walkforward_std_rank_ic"],
                "walkforward_worst_topk_drawdown": row["walkforward_worst_topk_drawdown"],
                "walkforward_worst_feature_drift_psi": row["walkforward_worst_feature_drift_psi"],
                "negative_rank_ic_folds": row["negative_rank_ic_folds"],
                "weak_rank_ic_periods": row["weak_rank_ic_periods"],
                "low_holdout_rank_ic": bool(row["low_holdout_rank_ic"]) if row["low_holdout_rank_ic"] is not None else None,
                "high_walkforward_std": bool(row["high_walkforward_std"]) if row["high_walkforward_std"] is not None else None,
                "drift_gate_pass": bool(row["drift_gate_pass"]) if row["drift_gate_pass"] is not None else None,
                "drawdown_gate_pass": bool(row["drawdown_gate_pass"]) if row["drawdown_gate_pass"] is not None else None,
                "diagnosis_counts": _safe_json(row["context_diagnosis_counts_json"]) or {},
                "main_blockers": _safe_json(row["main_blockers_json"]) or [],
                "recommendation": row["recommendation"],
                "built_at": row["built_at"],
            }
        )

    diagnostics = []
    if _table_exists(conn, detail_table):
        detail_cols = _columns(conn, detail_table)
        detail_rows = conn.execute(
            f"""
            SELECT {_select_expr(detail_cols, "source_run_id")},
                   {_select_expr(detail_cols, "scope")},
                   {_select_expr(detail_cols, "fold_id")},
                   {_select_expr(detail_cols, "period_start")},
                   {_select_expr(detail_cols, "period_end")},
                   {_select_expr(detail_cols, "rank_ic")},
                   {_select_expr(detail_cols, "spread")},
                   {_select_expr(detail_cols, "topk_net_return")},
                   {_select_expr(detail_cols, "topk_max_drawdown")},
                   {_select_expr(detail_cols, "feature_drift_psi_max")},
                   {_select_expr(detail_cols, "label_positive_rate")},
                   {_select_expr(detail_cols, "label_mean")},
                   {_select_expr(detail_cols, "market_ret_mean")},
                   {_select_expr(detail_cols, "dominant_regime")},
                   {_select_expr(detail_cols, "dominant_regime_share")},
                   {_select_expr(detail_cols, "diagnosis")}
              FROM mart_model_stability_context_diagnostic
             WHERE run_id = ?
             ORDER BY CASE WHEN diagnosis = 'ok' THEN 1 ELSE 0 END,
                      scope, COALESCE(fold_id, 999999)
             LIMIT ?
            """,
            (run_id, int(diagnostic_limit)),
        ).fetchall()
        diagnostics = [
            {
                "source_run_id": row["source_run_id"],
                "scope": row["scope"],
                "fold_id": row["fold_id"],
                "period_start": row["period_start"],
                "period_end": row["period_end"],
                "rank_ic": row["rank_ic"],
                "spread": row["spread"],
                "topk_net_return": row["topk_net_return"],
                "topk_max_drawdown": row["topk_max_drawdown"],
                "feature_drift_psi_max": row["feature_drift_psi_max"],
                "label_positive_rate": row["label_positive_rate"],
                "label_mean": row["label_mean"],
                "market_ret_mean": row["market_ret_mean"],
                "dominant_regime": row["dominant_regime"],
                "dominant_regime_share": row["dominant_regime_share"],
                "diagnosis": row["diagnosis"],
            }
            for row in detail_rows
        ]

    return {"run_id": run_id, "summaries": summaries, "diagnostics": diagnostics}
