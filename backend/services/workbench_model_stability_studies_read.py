"""Model stability study read model for the Workbench research surface."""
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


def build_model_stability_studies(conn: Any, *, study_limit: int = 12) -> list[dict[str, Any]]:
    studies = []
    if not _table_exists(conn, "mart_model_stability_search_summary"):
        return studies
    cols = _columns(conn, "mart_model_stability_search_summary")
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "run_id")},
               {_select_expr(cols, "model_selection_run_id")},
               {_select_expr(cols, "feature_table")},
               {_select_expr(cols, "label_name")},
               {_select_expr(cols, "best_trial_number")},
               {_select_expr(cols, "objective_score")},
               {_select_expr(cols, "trials")},
               {_select_expr(cols, "study_total_trials")},
               {_select_expr(cols, "config_json")},
               {_select_expr(cols, "built_at")}
          FROM mart_model_stability_search_summary
         ORDER BY {"built_at DESC" if "built_at" in cols else "run_id DESC"}
         LIMIT ?
        """,
        (int(study_limit),),
    ).fetchall()
    for row in rows:
        config = _safe_json(row["config_json"]) or {}
        best_metrics = config.get("best_metrics") if isinstance(config, dict) else {}
        studies.append(
            {
                "run_id": row["run_id"],
                "model_selection_run_id": row["model_selection_run_id"],
                "feature_table": row["feature_table"],
                "label_name": row["label_name"],
                "model_family": config.get("model_family") if isinstance(config, dict) else None,
                "best_status": config.get("best_status") if isinstance(config, dict) else None,
                "best_rejection_reason": config.get("best_rejection_reason") if isinstance(config, dict) else None,
                "best_trial_number": row["best_trial_number"],
                "objective_score": row["objective_score"],
                "trials": row["trials"],
                "study_total_trials": row["study_total_trials"],
                "built_at": row["built_at"],
                "walkforward_avg_rank_ic": (best_metrics or {}).get("walkforward_avg_rank_ic"),
                "walkforward_std_rank_ic": (best_metrics or {}).get("walkforward_std_rank_ic"),
                "walkforward_worst_topk_drawdown": (best_metrics or {}).get("walkforward_worst_topk_drawdown"),
                "walkforward_worst_feature_drift_psi": (best_metrics or {}).get("walkforward_worst_feature_drift_psi"),
            }
        )
    return studies
