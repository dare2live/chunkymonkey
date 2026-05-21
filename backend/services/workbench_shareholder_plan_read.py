"""Shareholder-plan research read models for Workbench."""
from __future__ import annotations

from typing import Any
import json

from services.workbench_shareholder_plan_family_eval_read import build_shareholder_plan_family_eval_view
from services.workbench_shareholder_plan_initial_read import build_shareholder_plan_initial_feature_panel_view


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


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


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


def build_shareholder_plan_family_walkforward_view(conn: Any, *, limit: int = 12) -> dict[str, Any]:
    table = "mart_shareholder_plan_family_walkforward_summary"
    empty = {
        "run_id": None,
        "summary": {},
        "gate_summary": [],
        "top_rows": [],
        "paired_rows": [],
    }
    if not _table_exists(conn, table):
        return empty
    run_id = _latest_run_id(conn, table)
    if not run_id:
        return empty
    row = conn.execute(
        """
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT source_family) AS source_family_count,
               COUNT(DISTINCT feature_name) AS feature_count,
               COUNT(DISTINCT label_name) AS label_count,
               MAX(fold_count) AS fold_count,
               MAX(valid_fold_count) AS max_valid_fold_count,
               MAX(built_at) AS built_at
          FROM mart_shareholder_plan_family_walkforward_summary
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    status_rows = conn.execute(
        """
        SELECT gate_status, COUNT(*) AS n
          FROM mart_shareholder_plan_family_walkforward_summary
         WHERE run_id = ?
         GROUP BY gate_status
         ORDER BY gate_status
        """,
        (run_id,),
    ).fetchall()
    summary = {
        "row_count": int(row["row_count"] or 0) if row else 0,
        "source_family_count": int(row["source_family_count"] or 0) if row else 0,
        "feature_count": int(row["feature_count"] or 0) if row else 0,
        "label_count": int(row["label_count"] or 0) if row else 0,
        "fold_count": int(row["fold_count"] or 0) if row else 0,
        "max_valid_fold_count": int(row["max_valid_fold_count"] or 0) if row else 0,
        "built_at": row["built_at"] if row else None,
        "gate_status_counts": {str(item["gate_status"]): int(item["n"]) for item in status_rows},
    }
    gate_rows = conn.execute(
        """
        SELECT source_family,
               label_name,
               gate_status,
               COUNT(*) AS feature_count,
               MAX(valid_fold_count) AS max_valid_fold_count,
               MAX(avg_signal_adjusted_holdout_rank_ic) AS max_signal_rank_ic,
               MAX(avg_holdout_long_short_spread) AS max_long_short_spread,
               MIN(worst_holdout_long_short_max_drawdown) AS worst_drawdown,
               AVG(avg_holdout_active_pct) AS avg_active_pct
          FROM mart_shareholder_plan_family_walkforward_summary
         WHERE run_id = ?
         GROUP BY source_family, label_name, gate_status
         ORDER BY label_name, source_family, gate_status
        """,
        (run_id,),
    ).fetchall()
    top_rows = conn.execute(
        """
        SELECT source_family, source_table, feature_name, feature_purpose, label_name,
               window_days, fold_count, valid_fold_count, gate_status,
               avg_signal_adjusted_holdout_rank_ic, avg_holdout_long_short_spread,
               positive_long_short_fold_share, worst_holdout_long_short_max_drawdown,
               avg_holdout_active_pct, min_holdout_active_rows,
               blockers_json, cautions_json, built_at
          FROM mart_shareholder_plan_family_walkforward_summary
         WHERE run_id = ?
         ORDER BY CASE gate_status
                    WHEN 'candidate_for_multivariate_validation' THEN 0
                    WHEN 'research_only' THEN 1
                    ELSE 2
                  END,
                  avg_holdout_long_short_spread DESC NULLS LAST,
                  avg_signal_adjusted_holdout_rank_ic DESC NULLS LAST
         LIMIT ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    paired_rows = conn.execute(
        """
        WITH paired AS (
            SELECT feature_name,
                   label_name,
                   MAX(CASE WHEN source_family = 'latest_state' THEN gate_status END) AS latest_gate_status,
                   MAX(CASE WHEN source_family = 'initial_event' THEN gate_status END) AS initial_gate_status,
                   MAX(CASE WHEN source_family = 'latest_state' THEN avg_signal_adjusted_holdout_rank_ic END) AS latest_signal_rank_ic,
                   MAX(CASE WHEN source_family = 'initial_event' THEN avg_signal_adjusted_holdout_rank_ic END) AS initial_signal_rank_ic,
                   MAX(CASE WHEN source_family = 'latest_state' THEN avg_holdout_long_short_spread END) AS latest_long_short_spread,
                   MAX(CASE WHEN source_family = 'initial_event' THEN avg_holdout_long_short_spread END) AS initial_long_short_spread,
                   MAX(CASE WHEN source_family = 'latest_state' THEN valid_fold_count END) AS latest_valid_fold_count,
                   MAX(CASE WHEN source_family = 'initial_event' THEN valid_fold_count END) AS initial_valid_fold_count,
                   MAX(CASE WHEN source_family = 'latest_state' THEN avg_holdout_active_pct END) AS latest_active_pct,
                   MAX(CASE WHEN source_family = 'initial_event' THEN avg_holdout_active_pct END) AS initial_active_pct
              FROM mart_shareholder_plan_family_walkforward_summary
             WHERE run_id = ?
               AND feature_name != 'shareholder_plan_completed_count_180d'
             GROUP BY feature_name, label_name
        )
        SELECT feature_name,
               label_name,
               latest_gate_status,
               initial_gate_status,
               latest_signal_rank_ic,
               initial_signal_rank_ic,
               latest_long_short_spread,
               initial_long_short_spread,
               COALESCE(initial_long_short_spread, 0) - COALESCE(latest_long_short_spread, 0) AS long_short_advantage,
               latest_valid_fold_count,
               initial_valid_fold_count,
               latest_active_pct,
               initial_active_pct
          FROM paired
         WHERE latest_long_short_spread IS NOT NULL
            OR initial_long_short_spread IS NOT NULL
         ORDER BY long_short_advantage DESC, feature_name, label_name
         LIMIT ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    return {
        "run_id": run_id,
        "summary": summary,
        "gate_summary": [
            {
                "source_family": item["source_family"],
                "label_name": item["label_name"],
                "gate_status": item["gate_status"],
                "feature_count": int(item["feature_count"] or 0),
                "max_valid_fold_count": int(item["max_valid_fold_count"] or 0),
                "max_signal_rank_ic": item["max_signal_rank_ic"],
                "max_long_short_spread": item["max_long_short_spread"],
                "worst_drawdown": item["worst_drawdown"],
                "avg_active_pct": item["avg_active_pct"],
            }
            for item in gate_rows
        ],
        "top_rows": [
            {
                "source_family": item["source_family"],
                "source_table": item["source_table"],
                "feature_name": item["feature_name"],
                "feature_purpose": item["feature_purpose"],
                "label_name": item["label_name"],
                "window_days": item["window_days"],
                "fold_count": int(item["fold_count"] or 0),
                "valid_fold_count": int(item["valid_fold_count"] or 0),
                "gate_status": item["gate_status"],
                "avg_signal_adjusted_holdout_rank_ic": item["avg_signal_adjusted_holdout_rank_ic"],
                "avg_holdout_long_short_spread": item["avg_holdout_long_short_spread"],
                "positive_long_short_fold_share": item["positive_long_short_fold_share"],
                "worst_holdout_long_short_max_drawdown": item["worst_holdout_long_short_max_drawdown"],
                "avg_holdout_active_pct": item["avg_holdout_active_pct"],
                "min_holdout_active_rows": int(item["min_holdout_active_rows"] or 0),
                "blockers": _safe_json(item["blockers_json"]) or [],
                "cautions": _safe_json(item["cautions_json"]) or [],
                "built_at": item["built_at"],
            }
            for item in top_rows
        ],
        "paired_rows": [
            {
                "feature_name": item["feature_name"],
                "label_name": item["label_name"],
                "latest_gate_status": item["latest_gate_status"],
                "initial_gate_status": item["initial_gate_status"],
                "latest_signal_rank_ic": item["latest_signal_rank_ic"],
                "initial_signal_rank_ic": item["initial_signal_rank_ic"],
                "latest_long_short_spread": item["latest_long_short_spread"],
                "initial_long_short_spread": item["initial_long_short_spread"],
                "long_short_advantage": item["long_short_advantage"],
                "latest_valid_fold_count": int(item["latest_valid_fold_count"] or 0),
                "initial_valid_fold_count": int(item["initial_valid_fold_count"] or 0),
                "latest_active_pct": item["latest_active_pct"],
                "initial_active_pct": item["initial_active_pct"],
            }
            for item in paired_rows
        ],
    }
