"""Workbench read model for stock horizon research evidence."""
from __future__ import annotations

from typing import Any

from services.workbench_stock_horizon_effect_read import build_stock_horizon_effect_view
from services.workbench_stock_horizon_selection_read import build_stock_horizon_selection_view


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


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0]


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


def _is_baseline_horizon(label_name: Any, horizon_days: Any) -> bool:
    try:
        if int(horizon_days or 0) == 60:
            return True
    except Exception:  # rule-compliance: ok evidence=type-coercion-fallback
        pass
    return str(label_name or "") in {"forward_ret_60d", "follow_net_return_60d"}


def _stock_horizon_baseline_label(conn: Any, run_id: str) -> str:
    if _table_exists(conn, "mart_stock_horizon_selection"):
        cols = _columns(conn, "mart_stock_horizon_selection")
        if {"run_id", "baseline_label"}.issubset(cols):
            row = conn.execute(
                """
                SELECT baseline_label
                  FROM mart_stock_horizon_selection
                 WHERE run_id = ?
                   AND baseline_label IS NOT NULL
                   AND baseline_label <> ''
                 LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if row and row["baseline_label"]:
                return str(row["baseline_label"])
    row = conn.execute(
        """
        SELECT label_name
          FROM mart_stock_horizon_profile
         WHERE run_id = ?
           AND horizon_days = 60
         LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    return str(row["label_name"]) if row and row["label_name"] else "follow_net_return_60d"


def build_workbench_stock_horizon_profile(
    conn: Any,
    *,
    stock_limit: int = 12,
    effect_limit: int = 12,
) -> dict[str, Any]:
    profile_table = "mart_stock_horizon_profile"
    empty = {
        "run_id": None,
        "baseline_label": "follow_net_return_60d",
        "horizon_distribution": [],
        "horizon_comparison": [],
        "horizon_selection": [],
        "selected_horizon_distribution": [],
        "best_stocks": [],
        "selected_stocks": [],
        "top_effects": [],
        "feature_effects_by_horizon": [],
        "profile_count": 0,
        "best_count": 0,
        "effect_count": 0,
        "selection_count": 0,
    }
    if not _table_exists(conn, profile_table):
        return empty
    run_id = _latest_run_id(conn, profile_table)
    if not run_id:
        return empty
    baseline_label = _stock_horizon_baseline_label(conn, run_id)
    profile_cols = _columns(conn, profile_table)
    required = {
        "run_id",
        "stock_code",
        "label_name",
        "horizon_days",
        "obs_count",
        "avg_return",
        "win_rate",
        "volatility",
        "horizon_score",
        "is_best",
    }
    if not required.issubset(profile_cols):
        return {**empty, "run_id": run_id}
    compounded_expr = "compounded_return" if "compounded_return" in profile_cols else "NULL"
    max_drawdown_expr = "max_drawdown" if "max_drawdown" in profile_cols else "NULL"
    path_obs_expr = "path_obs_count" if "path_obs_count" in profile_cols else "NULL"

    counts = conn.execute(
        """
        SELECT COUNT(*) AS profile_count,
               SUM(CASE WHEN is_best THEN 1 ELSE 0 END) AS best_count
          FROM mart_stock_horizon_profile
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    comparison_rows = conn.execute(
        f"""
        SELECT label_name,
               horizon_days,
               COUNT(*) AS stock_count,
               AVG(horizon_score) AS avg_horizon_score,
               AVG(avg_return) AS avg_return,
               AVG({compounded_expr}) AS avg_compounded_return,
               MEDIAN({compounded_expr}) AS median_compounded_return,
               AVG({max_drawdown_expr}) AS avg_max_drawdown,
               MEDIAN({max_drawdown_expr}) AS median_max_drawdown,
               AVG({path_obs_expr}) AS avg_path_obs_count,
               AVG(win_rate) AS avg_win_rate,
               AVG(volatility) AS avg_volatility,
               AVG(obs_count) AS avg_obs_count
          FROM mart_stock_horizon_profile
         WHERE run_id = ?
         GROUP BY label_name, horizon_days
         ORDER BY horizon_days
        """,
        (run_id,),
    ).fetchall()
    distribution_rows = conn.execute(
        f"""
        SELECT label_name,
               horizon_days,
               COUNT(*) AS stock_count,
               AVG(horizon_score) AS avg_horizon_score,
               AVG(avg_return) AS avg_return,
               AVG({compounded_expr}) AS avg_compounded_return,
               MEDIAN({compounded_expr}) AS median_compounded_return,
               AVG({max_drawdown_expr}) AS avg_max_drawdown,
               MEDIAN({max_drawdown_expr}) AS median_max_drawdown,
               AVG({path_obs_expr}) AS avg_path_obs_count,
               AVG(win_rate) AS avg_win_rate,
               AVG(volatility) AS avg_volatility,
               AVG(obs_count) AS avg_obs_count
          FROM mart_stock_horizon_profile
         WHERE run_id = ?
           AND is_best
         GROUP BY label_name, horizon_days
         ORDER BY horizon_days
        """,
        (run_id,),
    ).fetchall()
    stock_rows = conn.execute(
        f"""
        SELECT stock_code,
               label_name,
               horizon_days,
               obs_count,
               avg_return,
               {compounded_expr} AS compounded_return,
               {max_drawdown_expr} AS max_drawdown,
               {path_obs_expr} AS path_obs_count,
               win_rate,
               volatility,
               horizon_score
          FROM mart_stock_horizon_profile
         WHERE run_id = ?
           AND is_best
         ORDER BY horizon_score DESC NULLS LAST, avg_return DESC NULLS LAST, stock_code
         LIMIT ?
        """,
        (run_id, int(stock_limit)),
    ).fetchall()

    selection_view = build_stock_horizon_selection_view(conn, run_id=run_id, stock_limit=stock_limit)
    selection_count = selection_view["selection_count"]
    horizon_selection = selection_view["horizon_selection"]
    selected_horizon_distribution = selection_view["selected_horizon_distribution"]
    selected_stocks = selection_view["selected_stocks"]
    effect_view = build_stock_horizon_effect_view(
        conn,
        run_id=run_id,
        horizon_selection=horizon_selection,
        effect_limit=effect_limit,
    )
    effect_run_id = effect_view["effect_run_id"]
    effect_count = effect_view["effect_count"]
    top_effects = effect_view["top_effects"]
    feature_effects_by_horizon = effect_view["feature_effects_by_horizon"]
    horizon_selection = effect_view["horizon_selection"]

    return {
        "run_id": run_id,
        "effect_run_id": effect_run_id,
        "baseline_label": baseline_label,
        "horizon_comparison": [
            {
                "label_name": row["label_name"],
                "horizon_days": row["horizon_days"],
                "stock_count": int(row["stock_count"] or 0),
                "avg_horizon_score": row["avg_horizon_score"],
                "avg_return": row["avg_return"],
                "avg_compounded_return": row["avg_compounded_return"],
                "median_compounded_return": row["median_compounded_return"],
                "avg_max_drawdown": row["avg_max_drawdown"],
                "median_max_drawdown": row["median_max_drawdown"],
                "avg_path_obs_count": row["avg_path_obs_count"],
                "avg_win_rate": row["avg_win_rate"],
                "avg_volatility": row["avg_volatility"],
                "avg_obs_count": row["avg_obs_count"],
                "is_baseline": _is_baseline_horizon(row["label_name"], row["horizon_days"]),
            }
            for row in comparison_rows
        ],
        "horizon_distribution": [
            {
                "label_name": row["label_name"],
                "horizon_days": row["horizon_days"],
                "stock_count": int(row["stock_count"] or 0),
                "avg_horizon_score": row["avg_horizon_score"],
                "avg_return": row["avg_return"],
                "avg_compounded_return": row["avg_compounded_return"],
                "median_compounded_return": row["median_compounded_return"],
                "avg_max_drawdown": row["avg_max_drawdown"],
                "median_max_drawdown": row["median_max_drawdown"],
                "avg_path_obs_count": row["avg_path_obs_count"],
                "avg_win_rate": row["avg_win_rate"],
                "avg_volatility": row["avg_volatility"],
                "avg_obs_count": row["avg_obs_count"],
                "is_baseline": _is_baseline_horizon(row["label_name"], row["horizon_days"]),
            }
            for row in distribution_rows
        ],
        "horizon_selection": horizon_selection,
        "selected_horizon_distribution": selected_horizon_distribution,
        "best_stocks": [
            {
                "stock_code": row["stock_code"],
                "label_name": row["label_name"],
                "horizon_days": row["horizon_days"],
                "obs_count": row["obs_count"],
                "avg_return": row["avg_return"],
                "compounded_return": row["compounded_return"],
                "max_drawdown": row["max_drawdown"],
                "path_obs_count": row["path_obs_count"],
                "win_rate": row["win_rate"],
                "volatility": row["volatility"],
                "horizon_score": row["horizon_score"],
                "is_baseline": _is_baseline_horizon(row["label_name"], row["horizon_days"]),
            }
            for row in stock_rows
        ],
        "selected_stocks": selected_stocks,
        "top_effects": top_effects,
        "feature_effects_by_horizon": feature_effects_by_horizon,
        "profile_count": int((counts or {})["profile_count"] or 0),
        "best_count": int((counts or {})["best_count"] or 0),
        "effect_count": effect_count,
        "selection_count": selection_count,
    }
