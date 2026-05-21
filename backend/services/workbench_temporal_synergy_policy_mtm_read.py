"""Temporal synergy MTM policy read models for Workbench."""
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


def _cast_select_expr(cols: set[str], column: str, *, alias: str | None = None) -> str:
    out = alias or column
    if column in cols:
        return f"CAST({column} AS VARCHAR) AS {out}"
    return f"NULL AS {out}"


def build_policy_mtm_gates(conn: Any, *, run_id: str) -> list[dict[str, Any]]:
    table = "mart_synergy_policy_mtm_gate"
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
               {_select_expr(cols, "position_count")},
               {_select_expr(cols, "date_count")},
               {_select_expr(cols, "total_return")},
               {_select_expr(cols, "annualized_return")},
               {_select_expr(cols, "max_drawdown")},
               {_select_expr(cols, "sharpe")},
               {_select_expr(cols, "avg_active_positions")},
               {_select_expr(cols, "avg_position_net_return")},
               {_select_expr(cols, "position_hit_rate")},
               {_select_expr(cols, "transaction_cost_bps")},
               {_select_expr(cols, "non_tdxhub_kline_count")},
               {_select_expr(cols, "missing_path_price_count")},
               {_select_expr(cols, "blockers_json")},
               {_select_expr(cols, "thresholds_json")},
               {_select_expr(cols, "evidence_json")},
               {_cast_select_expr(cols, "built_at")}
          FROM mart_synergy_policy_mtm_gate
         WHERE source_run_id = ?
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 8
        """,
        (run_id,),
    ).fetchall()
    gates: list[dict[str, Any]] = []
    for row in rows:
        evidence = _safe_json(row["evidence_json"]) or {}
        thresholds = _safe_json(row["thresholds_json"]) or {}
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
                "position_count": row["position_count"],
                "date_count": row["date_count"],
                "total_return": row["total_return"],
                "annualized_return": row["annualized_return"],
                "max_drawdown": row["max_drawdown"],
                "sharpe": row["sharpe"],
                "avg_active_positions": row["avg_active_positions"],
                "avg_position_net_return": row["avg_position_net_return"],
                "position_hit_rate": row["position_hit_rate"],
                "transaction_cost_bps": row["transaction_cost_bps"],
                "non_tdxhub_kline_count": row["non_tdxhub_kline_count"],
                "missing_path_price_count": row["missing_path_price_count"],
                "forward_filled_path_price_count": evidence.get("forward_filled_path_price_count"),
                "rank_threshold_signal_count": evidence.get("rank_threshold_signal_count"),
                "market_eligible_signal_count": evidence.get("market_eligible_signal_count"),
                "market_filter_removed_signal_count": evidence.get("market_filter_removed_signal_count"),
                "daily_top_k_filtered_count": evidence.get("daily_top_k_filtered_count"),
                "market_allowed_date_count": evidence.get("market_allowed_date_count"),
                "market_blocked_date_count": evidence.get("market_blocked_date_count"),
                "daily_top_k": thresholds.get("daily_top_k"),
                "top_quantile": thresholds.get("top_quantile"),
                "signal_selection_mode": thresholds.get("signal_selection_mode"),
                "market_filter_enabled": bool(thresholds.get("market_filter_enabled")),
                "min_market_hs300_ret_20d": thresholds.get("min_market_hs300_ret_20d"),
                "min_market_hs300_ret_60d": thresholds.get("min_market_hs300_ret_60d"),
                "max_industry_l1_active_positions": thresholds.get("max_industry_l1_active_positions"),
                "industry_constraints_requested": bool(thresholds.get("industry_constraints_requested")),
                "industry_constraints_applied": bool(thresholds.get("industry_constraints_applied")),
                "industry_pit_eligible": evidence.get("industry_pit_eligible"),
                "industry_pit_fallback_ratio": evidence.get("industry_pit_fallback_ratio"),
                "industry_constraint_blockers": evidence.get("industry_constraint_blockers") or [],
                "blockers": _safe_json(row["blockers_json"]) or [],
                "thresholds": thresholds,
                "built_at": row["built_at"],
            }
        )
    return gates


def build_policy_mtm_strategy_sweeps(conn: Any, *, run_id: str) -> list[dict[str, Any]]:
    table = "mart_synergy_policy_mtm_strategy_sweep"
    if not _table_exists(conn, table):
        return []
    cols = _columns(conn, table)
    if not {"run_id", "variant_id", "source_run_id", "validation_status"}.issubset(cols):
        return []
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "run_id")},
               {_select_expr(cols, "variant_id")},
               {_select_expr(cols, "mtm_run_id")},
               {_select_expr(cols, "candidate_run_id")},
               {_select_expr(cols, "source_run_id")},
               {_select_expr(cols, "label_name")},
               {_select_expr(cols, "top_quantile")},
               {_select_expr(cols, "daily_top_k")},
               {_select_expr(cols, "min_market_hs300_ret_20d")},
               {_select_expr(cols, "min_market_hs300_ret_60d")},
               {_select_expr(cols, "objective_score")},
               {_select_expr(cols, "validation_status")},
               {_select_expr(cols, "blockers_json")},
               {_select_expr(cols, "signal_count")},
               {_select_expr(cols, "market_filter_removed_signal_count")},
               {_select_expr(cols, "daily_top_k_filtered_count")},
               {_select_expr(cols, "position_count")},
               {_select_expr(cols, "total_return")},
               {_select_expr(cols, "annualized_return")},
               {_select_expr(cols, "max_drawdown")},
               {_select_expr(cols, "sharpe")},
               {_select_expr(cols, "avg_active_positions")},
               {_cast_select_expr(cols, "built_at")}
          FROM mart_synergy_policy_mtm_strategy_sweep
         WHERE source_run_id = ?
         ORDER BY built_at DESC NULLS LAST,
                  run_id DESC,
                  objective_score DESC NULLS LAST
         LIMIT 16
        """,
        (run_id,),
    ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "variant_id": row["variant_id"],
            "mtm_run_id": row["mtm_run_id"],
            "candidate_run_id": row["candidate_run_id"],
            "source_run_id": row["source_run_id"],
            "label_name": row["label_name"],
            "top_quantile": row["top_quantile"],
            "daily_top_k": row["daily_top_k"],
            "min_market_hs300_ret_20d": row["min_market_hs300_ret_20d"],
            "min_market_hs300_ret_60d": row["min_market_hs300_ret_60d"],
            "objective_score": row["objective_score"],
            "validation_status": row["validation_status"],
            "blockers": _safe_json(row["blockers_json"]) or [],
            "signal_count": row["signal_count"],
            "market_filter_removed_signal_count": row["market_filter_removed_signal_count"],
            "daily_top_k_filtered_count": row["daily_top_k_filtered_count"],
            "position_count": row["position_count"],
            "total_return": row["total_return"],
            "annualized_return": row["annualized_return"],
            "max_drawdown": row["max_drawdown"],
            "sharpe": row["sharpe"],
            "avg_active_positions": row["avg_active_positions"],
            "built_at": row["built_at"],
        }
        for row in rows
    ]
