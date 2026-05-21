"""Stock horizon selection read model for Workbench."""
from __future__ import annotations

from typing import Any


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
    if row is None:
        return False
    try:
        conn.execute(f"SELECT 1 FROM {table_name} LIMIT 0").fetchone()
        return True
    except Exception:
        return False


def _columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {
        str(row["column_name"] if hasattr(row, "keys") else row[0])
        for row in conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    }


def _is_baseline_horizon(label_name: Any, horizon_days: Any) -> bool:
    try:
        if int(horizon_days or 0) == 60:
            return True
    except Exception:  # rule-compliance: ok evidence=type-coercion-fallback
        pass
    return str(label_name or "") in {"forward_ret_60d", "follow_net_return_60d"}


def build_stock_horizon_selection_view(
    conn: Any,
    *,
    run_id: str,
    stock_limit: int = 12,
) -> dict[str, Any]:
    selection_count = 0
    horizon_selection: list[dict[str, Any]] = []
    selected_horizon_distribution: list[dict[str, Any]] = []
    selected_stocks: list[dict[str, Any]] = []
    if not _table_exists(conn, "mart_stock_horizon_selection"):
        return {
            "selection_count": selection_count,
            "horizon_selection": horizon_selection,
            "selected_horizon_distribution": selected_horizon_distribution,
            "selected_stocks": selected_stocks,
        }

    selection_cols = _columns(conn, "mart_stock_horizon_selection")
    selection_required = {
        "run_id",
        "stock_code",
        "baseline_label",
        "selected_label",
        "selected_horizon_days",
        "selected_horizon_confidence",
        "score_advantage",
        "avg_return_advantage",
        "gate_status",
    }
    if not selection_required.issubset(selection_cols):
        return {
            "selection_count": selection_count,
            "horizon_selection": horizon_selection,
            "selected_horizon_distribution": selected_horizon_distribution,
            "selected_stocks": selected_stocks,
        }

    selection_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM mart_stock_horizon_selection WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
        or 0
    )
    dist_rows = conn.execute(
        """
        SELECT selected_label,
               selected_horizon_days,
               gate_status,
               COUNT(*) AS stock_count,
               AVG(selected_horizon_confidence) AS avg_confidence,
               AVG(score_advantage) AS avg_score_advantage,
               AVG(avg_return_advantage) AS avg_return_advantage
          FROM mart_stock_horizon_selection
         WHERE run_id = ?
         GROUP BY selected_label, selected_horizon_days, gate_status
         ORDER BY selected_horizon_days, gate_status
        """,
        (run_id,),
    ).fetchall()
    selected_horizon_distribution = [
        {
            "selected_label": row["selected_label"],
            "selected_horizon_days": row["selected_horizon_days"],
            "gate_status": row["gate_status"],
            "stock_count": int(row["stock_count"] or 0),
            "avg_confidence": row["avg_confidence"],
            "avg_score_advantage": row["avg_score_advantage"],
            "avg_return_advantage": row["avg_return_advantage"],
            "is_baseline": _is_baseline_horizon(row["selected_label"], row["selected_horizon_days"]),
        }
        for row in dist_rows
    ]
    sel_rows = conn.execute(
        """
        SELECT stock_code,
               baseline_label,
               selected_label,
               selected_horizon_days,
               selected_horizon_confidence,
               score_advantage,
               avg_return_advantage,
               selected_max_drawdown,
               baseline_max_drawdown,
               selected_obs_count,
               gate_status,
               fallback_reason
          FROM mart_stock_horizon_selection
         WHERE run_id = ?
         ORDER BY selected_horizon_confidence DESC NULLS LAST,
                  score_advantage DESC NULLS LAST,
                  stock_code
         LIMIT ?
        """,
        (run_id, int(stock_limit)),
    ).fetchall()
    horizon_selection = [dict(row) for row in sel_rows]
    selected_stocks = horizon_selection
    return {
        "selection_count": selection_count,
        "horizon_selection": horizon_selection,
        "selected_horizon_distribution": selected_horizon_distribution,
        "selected_stocks": selected_stocks,
    }
