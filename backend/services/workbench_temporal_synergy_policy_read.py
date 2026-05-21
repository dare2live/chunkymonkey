"""Temporal synergy policy, gate, and study read models for Workbench."""
from __future__ import annotations

from typing import Any
import json

from services.workbench_temporal_synergy_policy_mtm_read import (
    build_policy_mtm_gates,
    build_policy_mtm_strategy_sweeps,
)


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


def _build_optuna_studies(conn: Any, *, run_id: str) -> list[dict[str, Any]]:
    table = "mart_optuna_synergy_study_summary"
    if not _table_exists(conn, table):
        return []
    cols = _columns(conn, table)
    if not {"run_id", "source_run_id", "label_name"}.issubset(cols):
        return []
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "run_id")},
               {_select_expr(cols, "source_run_id")},
               {_select_expr(cols, "label_name")},
               {_select_expr(cols, "best_trial_number")},
               {_select_expr(cols, "objective_score")},
               {_select_expr(cols, "trials")},
               {_select_expr(cols, "study_total_trials")},
               {_select_expr(cols, "selected_features_json")},
               {_select_expr(cols, "selected_interactions_json")},
               {_select_expr(cols, "config_json")},
               {_cast_select_expr(cols, "built_at")}
          FROM mart_optuna_synergy_study_summary
         WHERE source_run_id = ?
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 8
        """,
        (run_id,),
    ).fetchall()
    studies: list[dict[str, Any]] = []
    for row in rows:
        config = _safe_json(row["config_json"]) or {}
        studies.append(
            {
                "run_id": row["run_id"],
                "source_run_id": row["source_run_id"],
                "label_name": row["label_name"],
                "best_trial_number": row["best_trial_number"],
                "objective_score": row["objective_score"],
                "trials": row["trials"],
                "study_total_trials": row["study_total_trials"],
                "selected_features": _safe_json(row["selected_features_json"]) or [],
                "selected_interactions": _safe_json(row["selected_interactions_json"]) or [],
                "best_metrics": config.get("best_metrics") or {},
                "built_at": row["built_at"],
            }
        )
    return studies


def _build_policy_candidates(conn: Any, *, run_id: str) -> list[dict[str, Any]]:
    table = "mart_synergy_policy_candidate"
    if not _table_exists(conn, table):
        return []
    cols = _columns(conn, table)
    if not {"run_id", "source_run_id", "label_name", "gate_status"}.issubset(cols):
        return []
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "run_id")},
               {_select_expr(cols, "source_run_id")},
               {_select_expr(cols, "label_name")},
               {_select_expr(cols, "objective_score")},
               {_select_expr(cols, "selected_features_json")},
               {_select_expr(cols, "selected_interactions_json")},
               {_select_expr(cols, "gate_status")},
               {_select_expr(cols, "notes_json")},
               {_cast_select_expr(cols, "built_at")}
          FROM mart_synergy_policy_candidate
         WHERE source_run_id = ?
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 8
        """,
        (run_id,),
    ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "source_run_id": row["source_run_id"],
            "label_name": row["label_name"],
            "objective_score": row["objective_score"],
            "selected_count": len(_safe_json(row["selected_features_json"]) or []),
            "selected_interaction_count": len(_safe_json(row["selected_interactions_json"]) or []),
            "gate_status": row["gate_status"],
            "notes": _safe_json(row["notes_json"]) or {},
            "built_at": row["built_at"],
        }
        for row in rows
    ]


def _build_policy_gates(conn: Any, *, run_id: str) -> list[dict[str, Any]]:
    table = "mart_synergy_policy_gate"
    if not _table_exists(conn, table):
        return []
    cols = _columns(conn, table)
    if not {"run_id", "source_run_id", "candidate_run_id", "validation_status"}.issubset(cols):
        return []
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "run_id")},
               {_select_expr(cols, "candidate_run_id")},
               {_select_expr(cols, "source_run_id")},
               {_select_expr(cols, "label_name")},
               {_select_expr(cols, "baseline_horizon_days")},
               {_select_expr(cols, "candidate_horizon_days")},
               {_select_expr(cols, "validation_status")},
               {_select_expr(cols, "promotion_status")},
               {_select_expr(cols, "production_eligible")},
               {_select_expr(cols, "fold_count")},
               {_select_expr(cols, "avg_rank_ic")},
               {_select_expr(cols, "std_rank_ic")},
               {_select_expr(cols, "avg_top_excess_return")},
               {_select_expr(cols, "worst_top_excess_return")},
               {_select_expr(cols, "avg_top_hit_rate")},
               {_select_expr(cols, "worst_max_drawdown")},
               {_select_expr(cols, "avg_turnover")},
               {_select_expr(cols, "avg_cost_adjusted_top_excess_return")},
               {_select_expr(cols, "worst_cost_adjusted_top_excess_return")},
               {_select_expr(cols, "transaction_cost_bps")},
               {_select_expr(cols, "blockers_json")},
               {_select_expr(cols, "thresholds_json")},
               {_cast_select_expr(cols, "built_at")}
          FROM mart_synergy_policy_gate
         WHERE source_run_id = ?
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 8
        """,
        (run_id,),
    ).fetchall()
    gates: list[dict[str, Any]] = []
    for row in rows:
        thresholds = _safe_json(row["thresholds_json"]) or {}
        gate_mode = (
            "strict_fold"
            if float(thresholds.get("min_cost_adjusted_positive_fold_ratio") or 0.0) > 0.0
            else "metric"
        )
        gates.append(
            {
                "run_id": row["run_id"],
                "candidate_run_id": row["candidate_run_id"],
                "source_run_id": row["source_run_id"],
                "label_name": row["label_name"],
                "baseline_horizon_days": row["baseline_horizon_days"],
                "candidate_horizon_days": row["candidate_horizon_days"],
                "validation_status": row["validation_status"],
                "promotion_status": row["promotion_status"],
                "production_eligible": bool(row["production_eligible"]),
                "fold_count": row["fold_count"],
                "avg_rank_ic": row["avg_rank_ic"],
                "std_rank_ic": row["std_rank_ic"],
                "avg_top_excess_return": row["avg_top_excess_return"],
                "worst_top_excess_return": row["worst_top_excess_return"],
                "avg_top_hit_rate": row["avg_top_hit_rate"],
                "worst_max_drawdown": row["worst_max_drawdown"],
                "avg_turnover": row["avg_turnover"],
                "avg_cost_adjusted_top_excess_return": row["avg_cost_adjusted_top_excess_return"],
                "worst_cost_adjusted_top_excess_return": row["worst_cost_adjusted_top_excess_return"],
                "transaction_cost_bps": row["transaction_cost_bps"],
                "blockers": _safe_json(row["blockers_json"]) or [],
                "thresholds": thresholds,
                "gate_mode": gate_mode,
                "built_at": row["built_at"],
            }
        )
    return gates


def build_temporal_synergy_policy_view(conn: Any, *, run_id: str) -> dict[str, Any]:
    return {
        "optuna_studies": _build_optuna_studies(conn, run_id=run_id),
        "policy_candidates": _build_policy_candidates(conn, run_id=run_id),
        "policy_gates": _build_policy_gates(conn, run_id=run_id),
        "policy_mtm_gates": build_policy_mtm_gates(conn, run_id=run_id),
        "policy_mtm_strategy_sweeps": build_policy_mtm_strategy_sweeps(conn, run_id=run_id),
    }
