"""Champion detail read-model slices for the Workbench operations surface."""
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


def _json_count(value: Any) -> int:
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, list):
        return len(value)
    return 0


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


def build_champion_challengers(conn: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_model_lifecycle"):
        return []
    cols = _columns(conn, "mart_model_lifecycle")
    order_col = "updated_at" if "updated_at" in cols else "created_at" if "created_at" in cols else "model_id"
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "model_id")},
               {_select_expr(cols, "status")},
               {_select_expr(cols, "ic_holdout")},
               {_select_expr(cols, "ic_walkforward_avg")},
               {_select_expr(cols, "ic_walkforward_std")},
               {_select_expr(cols, "drift_score")},
               {_cast_select_expr(cols, "updated_at")}
          FROM mart_model_lifecycle
         WHERE status <> 'champion'
         ORDER BY {order_col} DESC
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [
        {
            "model_id": row["model_id"],
            "status": row["status"],
            "ic_holdout": row["ic_holdout"],
            "ic_walkforward_avg": row["ic_walkforward_avg"],
            "ic_walkforward_std": row["ic_walkforward_std"],
            "drift_score": row["drift_score"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def build_champion_candidate_evaluations(conn: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_champion_candidate_evaluation"):
        return []
    cols = _columns(conn, "mart_champion_candidate_evaluation")
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "evaluation_run_id")},
               {_select_expr(cols, "model_id")},
               {_select_expr(cols, "status")},
               {_select_expr(cols, "pit_status")},
               {_select_expr(cols, "pit_violation_rows")},
               {_select_expr(cols, "evidence_status")},
               {_select_expr(cols, "gate_status")},
               {_select_expr(cols, "failed_steps_json")},
               {_cast_select_expr(cols, "started_at")},
               {_cast_select_expr(cols, "ended_at")},
               {_select_expr(cols, "duration_s")}
          FROM mart_champion_candidate_evaluation
         ORDER BY {"started_at DESC" if "started_at" in cols else "evaluation_run_id DESC"}
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [
        {
            "evaluation_run_id": row["evaluation_run_id"],
            "model_id": row["model_id"],
            "status": row["status"],
            "pit_status": row["pit_status"],
            "pit_violation_rows": row["pit_violation_rows"],
            "evidence_status": row["evidence_status"],
            "gate_status": row["gate_status"],
            "failed_steps": _safe_json(row["failed_steps_json"]) or [],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "duration_s": row["duration_s"],
        }
        for row in rows
    ]


def build_champion_evidence_bundles(conn: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_challenger_evidence_bundle"):
        return []
    cols = _columns(conn, "mart_challenger_evidence_bundle")
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "evidence_run_id")},
               {_select_expr(cols, "model_id")},
               {_select_expr(cols, "status")},
               {_select_expr(cols, "steps_json")},
               {_select_expr(cols, "gate_run_id")},
               {_select_expr(cols, "gate_status")},
               {_select_expr(cols, "blockers_json")},
               {_cast_select_expr(cols, "started_at")},
               {_cast_select_expr(cols, "ended_at")},
               {_select_expr(cols, "duration_s")}
          FROM mart_challenger_evidence_bundle
         ORDER BY {"started_at DESC" if "started_at" in cols else "evidence_run_id DESC"}
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    evidence_bundles = []
    for row in rows:
        steps = _safe_json(row["steps_json"]) or []
        blockers = _safe_json(row["blockers_json"]) or []
        evidence_bundles.append(
            {
                "evidence_run_id": row["evidence_run_id"],
                "model_id": row["model_id"],
                "status": row["status"],
                "step_count": _json_count(steps),
                "steps": steps,
                "gate_run_id": row["gate_run_id"],
                "gate_status": row["gate_status"],
                "blockers": blockers,
                "blocker_count": _json_count(blockers),
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "duration_s": row["duration_s"],
            }
        )
    return evidence_bundles


def build_champion_promotion_gates(conn: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_tdx_keep_promotion_gate"):
        return []
    cols = _columns(conn, "mart_tdx_keep_promotion_gate")
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "gate_run_id")},
               {_select_expr(cols, "challenger_model_id")},
               {_select_expr(cols, "champion_model_id")},
               {_select_expr(cols, "promotion_status")},
               {_select_expr(cols, "decision")},
               {_select_expr(cols, "gate_results_json")},
               {_select_expr(cols, "blockers_json")},
               {_select_expr(cols, "rank_ic_challenger")},
               {_select_expr(cols, "rank_ic_champion")},
               {_select_expr(cols, "long_short_challenger")},
               {_select_expr(cols, "long_short_champion")},
               {_select_expr(cols, "max_drawdown_challenger")},
               {_select_expr(cols, "max_drawdown_champion")},
               {_cast_select_expr(cols, "evaluated_at")}
          FROM mart_tdx_keep_promotion_gate
         ORDER BY {"evaluated_at DESC" if "evaluated_at" in cols else "gate_run_id DESC"}
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [
        {
            "gate_run_id": row["gate_run_id"],
            "challenger_model_id": row["challenger_model_id"],
            "champion_model_id": row["champion_model_id"],
            "promotion_status": row["promotion_status"],
            "decision": row["decision"],
            "gate_results": _safe_json(row["gate_results_json"]) or {},
            "blockers": _safe_json(row["blockers_json"]) or [],
            "rank_ic_challenger": row["rank_ic_challenger"],
            "rank_ic_champion": row["rank_ic_champion"],
            "long_short_challenger": row["long_short_challenger"],
            "long_short_champion": row["long_short_champion"],
            "max_drawdown_challenger": row["max_drawdown_challenger"],
            "max_drawdown_champion": row["max_drawdown_champion"],
            "evaluated_at": row["evaluated_at"],
        }
        for row in rows
    ]
