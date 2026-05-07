"""Read helpers for per-stock holding horizon evidence."""
from __future__ import annotations

from typing import Any


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_exists(conn: Any, table: str) -> bool:
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
              FROM information_schema.tables
             WHERE table_name = ?
            """,
            (table.split(".")[-1],),
        ).fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def _columns(conn: Any, table: str) -> set[str]:
    try:
        return {str(row[0]) for row in conn.execute(f"DESCRIBE {table}").fetchall()}
    except Exception:
        return set()


def latest_stock_horizon_run_id(conn: Any) -> str | None:
    if not _table_exists(conn, "mart_stock_horizon_selection"):
        return None
    cols = _columns(conn, "mart_stock_horizon_selection")
    if "run_id" not in cols:
        return None
    order_expr = "MAX(built_at) DESC NULLS LAST, run_id DESC" if "built_at" in cols else "run_id DESC"
    row = conn.execute(
        f"""
        SELECT run_id
          FROM mart_stock_horizon_selection
         GROUP BY run_id
         ORDER BY {order_expr}
         LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row and row["run_id"] else None


def _select_expr(cols: set[str], column: str, default_sql: str = "NULL") -> str:
    if column in cols:
        return _quote_ident(column)
    return f"{default_sql} AS {_quote_ident(column)}"


def load_stock_horizon_evidence(
    conn: Any,
    stock_codes: list[str] | tuple[str, ...] | set[str],
    *,
    run_id: str | None = None,
    effect_limit: int = 3,
) -> dict[str, dict[str, Any]]:
    """Return latest per-stock holding-period selection and top feature effects."""

    codes = sorted({str(code) for code in stock_codes if code})
    if not codes or not _table_exists(conn, "mart_stock_horizon_selection"):
        return {}
    run_id = run_id or latest_stock_horizon_run_id(conn)
    if not run_id:
        return {}
    selection_cols = _columns(conn, "mart_stock_horizon_selection")
    required = {"run_id", "stock_code", "selected_horizon_days"}
    if not required.issubset(selection_cols):
        return {}
    placeholders = ", ".join(["?"] * len(codes))
    rows = conn.execute(
        f"""
        SELECT stock_code,
               {_select_expr(selection_cols, "baseline_label", "'follow_net_return_60d'")},
               {_select_expr(selection_cols, "baseline_horizon_days", "60")},
               {_select_expr(selection_cols, "selected_label", "'follow_net_return_60d'")},
               selected_horizon_days,
               {_select_expr(selection_cols, "selected_horizon_confidence", "NULL")},
               {_select_expr(selection_cols, "selected_horizon_score", "NULL")},
               {_select_expr(selection_cols, "baseline_horizon_score", "NULL")},
               {_select_expr(selection_cols, "score_advantage", "NULL")},
               {_select_expr(selection_cols, "avg_return_advantage", "NULL")},
               {_select_expr(selection_cols, "selected_max_drawdown", "NULL")},
               {_select_expr(selection_cols, "baseline_max_drawdown", "NULL")},
               {_select_expr(selection_cols, "selected_obs_count", "NULL")},
               {_select_expr(selection_cols, "baseline_obs_count", "NULL")},
               {_select_expr(selection_cols, "gate_status", "'baseline'")},
               {_select_expr(selection_cols, "fallback_reason", "NULL")}
          FROM mart_stock_horizon_selection
         WHERE run_id = ?
           AND stock_code IN ({placeholders})
        """,
        [run_id, *codes],
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        selected_horizon = int(row["selected_horizon_days"] or 60)
        baseline_horizon = int(row["baseline_horizon_days"] or 60)
        out[str(row["stock_code"])] = {
            "run_id": run_id,
            "baseline_label": row["baseline_label"],
            "baseline_horizon_days": baseline_horizon,
            "selected_label": row["selected_label"],
            "selected_horizon_days": selected_horizon,
            "selected_horizon_confidence": row["selected_horizon_confidence"],
            "selected_horizon_score": row["selected_horizon_score"],
            "baseline_horizon_score": row["baseline_horizon_score"],
            "score_advantage": row["score_advantage"],
            "avg_return_advantage": row["avg_return_advantage"],
            "selected_max_drawdown": row["selected_max_drawdown"],
            "baseline_max_drawdown": row["baseline_max_drawdown"],
            "selected_obs_count": row["selected_obs_count"],
            "baseline_obs_count": row["baseline_obs_count"],
            "gate_status": row["gate_status"],
            "fallback_reason": row["fallback_reason"],
            "is_baseline": selected_horizon == baseline_horizon,
            "top_feature_effects": [],
            "horizon_comparison": [],
        }
    _load_horizon_comparison(conn, out, run_id)
    if not out or effect_limit <= 0 or not _table_exists(conn, "mart_stock_horizon_feature_effect"):
        return out
    effect_cols = _columns(conn, "mart_stock_horizon_feature_effect")
    effect_required = {
        "run_id",
        "stock_code",
        "label_name",
        "horizon_days",
        "feature_name",
        "obs_count",
        "corr",
        "abs_corr_rank",
        "effect_direction",
    }
    if not effect_required.issubset(effect_cols):
        return out
    effect_codes = list(out.keys())
    effect_placeholders = ", ".join(["?"] * len(effect_codes))
    effect_rows = conn.execute(
        f"""
        SELECT e.stock_code,
               e.label_name,
               e.horizon_days,
               e.feature_name,
               e.obs_count,
               e.corr,
               e.abs_corr_rank,
               e.effect_direction
          FROM mart_stock_horizon_feature_effect e
          JOIN mart_stock_horizon_selection s
            ON s.run_id = e.run_id
           AND s.stock_code = e.stock_code
           AND s.selected_label = e.label_name
         WHERE e.run_id = ?
           AND e.stock_code IN ({effect_placeholders})
           AND e.abs_corr_rank <= ?
         ORDER BY e.stock_code, e.abs_corr_rank, e.feature_name
        """,
        [run_id, *effect_codes, int(effect_limit)],
    ).fetchall()
    for row in effect_rows:
        target = out.get(str(row["stock_code"]))
        if target is None:
            continue
        target["top_feature_effects"].append(
            {
                "label_name": row["label_name"],
                "horizon_days": row["horizon_days"],
                "feature_name": row["feature_name"],
                "obs_count": row["obs_count"],
                "corr": row["corr"],
                "abs_corr_rank": row["abs_corr_rank"],
                "effect_direction": row["effect_direction"],
            }
        )
    return out


def _load_horizon_comparison(conn: Any, out: dict[str, dict[str, Any]], run_id: str) -> None:
    if not out:
        return
    stock_codes = list(out.keys())
    placeholders = ", ".join(["?"] * len(stock_codes))
    if _table_exists(conn, "mart_stock_horizon_candidate_gate"):
        cols = _columns(conn, "mart_stock_horizon_candidate_gate")
        required = {
            "run_id",
            "stock_code",
            "label_name",
            "horizon_days",
            "candidate_status",
            "reason_code",
        }
        if required.issubset(cols):
            rows = conn.execute(
                f"""
                SELECT stock_code,
                       label_name,
                       horizon_days,
                       {_select_expr(cols, "obs_count", "NULL")},
                       {_select_expr(cols, "avg_return", "NULL")},
                       {_select_expr(cols, "median_return", "NULL")},
                       {_select_expr(cols, "max_return", "NULL")},
                       {_select_expr(cols, "min_return", "NULL")},
                       {_select_expr(cols, "win_rate", "NULL")},
                       {_select_expr(cols, "volatility", "NULL")},
                       {_select_expr(cols, "downside_avg", "NULL")},
                       {_select_expr(cols, "compounded_return", "NULL")},
                       {_select_expr(cols, "max_drawdown", "NULL")},
                       {_select_expr(cols, "path_obs_count", "NULL")},
                       {_select_expr(cols, "horizon_score", "NULL")},
                       {_select_expr(cols, "baseline_horizon_score", "NULL")},
                       {_select_expr(cols, "score_advantage", "NULL")},
                       {_select_expr(cols, "avg_return_advantage", "NULL")},
                       {_select_expr(cols, "selection_confidence", "NULL")},
                       candidate_status,
                       reason_code
                  FROM mart_stock_horizon_candidate_gate
                 WHERE run_id = ?
                   AND stock_code IN ({placeholders})
                 ORDER BY stock_code, horizon_days
                """,
                [run_id, *stock_codes],
            ).fetchall()
            for row in rows:
                target = out.get(str(row["stock_code"]))
                if target is None:
                    continue
                selected_label = target.get("selected_label")
                baseline_label = target.get("baseline_label")
                target["horizon_comparison"].append(
                    {
                        "label_name": row["label_name"],
                        "horizon_days": row["horizon_days"],
                        "obs_count": row["obs_count"],
                        "avg_return": row["avg_return"],
                        "median_return": row["median_return"],
                        "max_return": row["max_return"],
                        "min_return": row["min_return"],
                        "win_rate": row["win_rate"],
                        "volatility": row["volatility"],
                        "downside_avg": row["downside_avg"],
                        "compounded_return": row["compounded_return"],
                        "max_drawdown": row["max_drawdown"],
                        "path_obs_count": row["path_obs_count"],
                        "horizon_score": row["horizon_score"],
                        "baseline_horizon_score": row["baseline_horizon_score"],
                        "score_advantage": row["score_advantage"],
                        "avg_return_advantage": row["avg_return_advantage"],
                        "selection_confidence": row["selection_confidence"],
                        "candidate_status": row["candidate_status"],
                        "reason_code": row["reason_code"],
                        "is_selected": row["label_name"] == selected_label,
                        "is_baseline": row["label_name"] == baseline_label,
                    }
                )
            return

    if not _table_exists(conn, "mart_stock_horizon_profile"):
        return
    cols = _columns(conn, "mart_stock_horizon_profile")
    required = {"run_id", "stock_code", "label_name", "horizon_days"}
    if not required.issubset(cols):
        return
    rows = conn.execute(
        f"""
        SELECT stock_code,
               label_name,
               horizon_days,
               {_select_expr(cols, "obs_count", "NULL")},
               {_select_expr(cols, "avg_return", "NULL")},
               {_select_expr(cols, "median_return", "NULL")},
               {_select_expr(cols, "max_return", "NULL")},
               {_select_expr(cols, "min_return", "NULL")},
               {_select_expr(cols, "win_rate", "NULL")},
               {_select_expr(cols, "volatility", "NULL")},
               {_select_expr(cols, "downside_avg", "NULL")},
               {_select_expr(cols, "compounded_return", "NULL")},
               {_select_expr(cols, "max_drawdown", "NULL")},
               {_select_expr(cols, "path_obs_count", "NULL")},
               {_select_expr(cols, "horizon_score", "NULL")}
          FROM mart_stock_horizon_profile
         WHERE run_id = ?
           AND stock_code IN ({placeholders})
         ORDER BY stock_code, horizon_days
        """,
        [run_id, *stock_codes],
    ).fetchall()
    for row in rows:
        target = out.get(str(row["stock_code"]))
        if target is None:
            continue
        selected_label = target.get("selected_label")
        baseline_label = target.get("baseline_label")
        is_selected = row["label_name"] == selected_label
        is_baseline = row["label_name"] == baseline_label
        target["horizon_comparison"].append(
            {
                "label_name": row["label_name"],
                "horizon_days": row["horizon_days"],
                "obs_count": row["obs_count"],
                "avg_return": row["avg_return"],
                "median_return": row["median_return"],
                "max_return": row["max_return"],
                "min_return": row["min_return"],
                "win_rate": row["win_rate"],
                "volatility": row["volatility"],
                "downside_avg": row["downside_avg"],
                "compounded_return": row["compounded_return"],
                "max_drawdown": row["max_drawdown"],
                "path_obs_count": row["path_obs_count"],
                "horizon_score": row["horizon_score"],
                "baseline_horizon_score": target.get("baseline_horizon_score"),
                "score_advantage": None,
                "avg_return_advantage": None,
                "selection_confidence": target.get("selected_horizon_confidence") if is_selected else None,
                "candidate_status": "selected" if is_selected else ("baseline" if is_baseline else "profile_only"),
                "reason_code": "selected_horizon" if is_selected else ("baseline_60d" if is_baseline else "gate_detail_unavailable"),
                "is_selected": is_selected,
                "is_baseline": is_baseline,
            }
        )


__all__ = ["latest_stock_horizon_run_id", "load_stock_horizon_evidence"]
