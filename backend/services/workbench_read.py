"""Workbench read models for the frontend operations surface."""
from __future__ import annotations

from typing import Any
import json
from pathlib import Path

from services.feature_registry import load_feature_registry
from services.schema_versions import detect_drift
from services.storage_retention import load_storage_retention_policy, plan_storage_cleanup


REPO = Path(__file__).resolve().parent.parent.parent
MARKET_DB = REPO / "data" / "market.duckdb"


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
    return row is not None


def _relation_exists(conn: Any, relation: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {relation} LIMIT 0").fetchone()
        return True
    except Exception:
        return False


def _attach_market_readonly(conn: Any) -> bool:
    if _relation_exists(conn, "market.sqlite_master"):
        return True
    if not MARKET_DB.exists():
        return False
    try:
        conn.execute(f"ATTACH IF NOT EXISTS '{MARKET_DB}' AS market (READ_ONLY)")
        return True
    except Exception:
        return False


def _columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {
        str(row["column_name"])
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


def _latest_trading_day(conn: Any, *, as_of_date: str | None = None) -> str | None:
    if not _table_exists(conn, "dim_trading_calendar"):
        return None
    cols = _columns(conn, "dim_trading_calendar")
    date_col = "trade_date" if "trade_date" in cols else "date" if "date" in cols else None
    if not date_col:
        return None
    filters = []
    if "is_open" in cols:
        filters.append("is_open = TRUE")
    elif "is_trading_day" in cols:
        filters.append("is_trading_day = TRUE")
    params: tuple[Any, ...] = ()
    if as_of_date:
        filters.append(f"CAST({date_col} AS DATE) <= CAST(? AS DATE)")
        params = (as_of_date,)
    else:
        filters.append(f"CAST({date_col} AS DATE) <= CURRENT_DATE")
    where = "WHERE " + " AND ".join(filters) if filters else ""
    return _scalar(conn, f"SELECT CAST(MAX({date_col}) AS VARCHAR) FROM dim_trading_calendar {where}", params)


def _latest_manifest(conn: Any) -> dict[str, Any] | None:
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        return None
    row = conn.execute(
        """
        SELECT run_id, pipeline_name, status,
               CAST(started_at AS VARCHAR) AS started_at,
               CAST(ended_at AS VARCHAR) AS ended_at,
               duration_s, gate_result
          FROM mart_pipeline_run_manifest
         ORDER BY COALESCE(started_at, created_at) DESC
         LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return {
        "run_id": row["run_id"],
        "pipeline_name": row["pipeline_name"],
        "status": row["status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "duration_s": row["duration_s"],
        "gate_result": row["gate_result"],
    }


def _latest_run_id(conn: Any, table_name: str) -> str | None:
    if not _table_exists(conn, table_name):
        return None
    cols = _columns(conn, table_name)
    order_col = "built_at" if "built_at" in cols else "created_at" if "created_at" in cols else None
    if order_col:
        return _scalar(conn, f"SELECT run_id FROM {table_name} ORDER BY {order_col} DESC LIMIT 1")
    return _scalar(conn, f"SELECT run_id FROM {table_name} LIMIT 1")


def _status_counts(conn: Any, table_name: str, *, run_id: str | None = None) -> dict[str, int]:
    if not _table_exists(conn, table_name) or "status" not in _columns(conn, table_name):
        return {}
    params: tuple[Any, ...] = ()
    where = ""
    if run_id and "run_id" in _columns(conn, table_name):
        where = "WHERE run_id = ?"
        params = (run_id,)
    rows = conn.execute(
        f"""
        SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS n
          FROM {table_name}
          {where}
         GROUP BY COALESCE(status, 'unknown')
        """,
        params,
    ).fetchall()
    return {str(row["status"]): int(row["n"]) for row in rows}


def _champion_summary(conn: Any) -> dict[str, Any]:
    if not _table_exists(conn, "mart_model_lifecycle"):
        return {"counts": {}, "champions": []}
    cols = _columns(conn, "mart_model_lifecycle")
    if "status" not in cols or "model_id" not in cols:
        return {"counts": {}, "champions": []}
    counts = _status_counts(conn, "mart_model_lifecycle")
    rows = conn.execute(
        """
        SELECT model_id, status
          FROM mart_model_lifecycle
         WHERE status = 'champion'
         ORDER BY model_id
         LIMIT 10
        """
    ).fetchall()
    return {
        "counts": counts,
        "champions": [{"model_id": row["model_id"], "status": row["status"]} for row in rows],
    }


def _current_champion_model_id(conn: Any) -> str | None:
    champions = _champion_summary(conn).get("champions") or []
    if not champions:
        return None
    return champions[0].get("model_id")


def _champion_deployment_summary(
    *,
    lifecycle: dict[str, Any],
    gates: list[dict[str, Any]],
    topk: dict[str, Any],
) -> dict[str, Any]:
    champions = lifecycle.get("champions") or []
    champion_id = champions[0].get("model_id") if champions else None
    latest_promotion_gate = None
    latest_self_check = None
    for gate in gates:
        challenger = gate.get("challenger_model_id")
        previous_champion = gate.get("champion_model_id")
        if champion_id and challenger == champion_id and previous_champion != champion_id and not latest_promotion_gate:
            latest_promotion_gate = gate
        if champion_id and challenger == champion_id and previous_champion == champion_id and not latest_self_check:
            latest_self_check = gate

    blockers: list[str] = []
    if not champion_id:
        blockers.append("missing_lifecycle_champion")
    if champion_id and not latest_promotion_gate:
        blockers.append("missing_passing_promotion_gate")
    if latest_promotion_gate and str(latest_promotion_gate.get("promotion_status") or "").upper() != "PASS":
        blockers.append("latest_promotion_gate_not_pass")
    if champion_id and topk.get("model_id") != champion_id:
        blockers.append("primary_topk_model_mismatch")
    if champion_id and not topk.get("count"):
        blockers.append("missing_primary_topk")

    status = "deployed" if not blockers else "needs_attention"
    return {
        "status": status,
        "champion_model_id": champion_id,
        "primary_topk_model_id": topk.get("model_id"),
        "primary_topk_count": topk.get("count"),
        "latest_promotion_gate": latest_promotion_gate,
        "latest_self_check": latest_self_check,
        "blockers": blockers,
    }


def _drift_offenders(conn: Any, limit: int) -> dict[str, Any]:
    table = "mart_feature_drift_root_cause_summary"
    if not _table_exists(conn, table):
        return {"run_id": None, "top": []}
    run_id = _latest_run_id(conn, table)
    if not run_id:
        return {"run_id": None, "top": []}
    rows = conn.execute(
        """
        SELECT source_run_id, feature_name, offender_count, severe_count,
               max_psi, recommendation
          FROM mart_feature_drift_root_cause_summary
         WHERE run_id = ?
         ORDER BY max_psi DESC, offender_count DESC
         LIMIT ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    return {
        "run_id": run_id,
        "top": [
            {
                "source_run_id": row["source_run_id"],
                "feature_name": row["feature_name"],
                "offender_count": int(row["offender_count"]),
                "severe_count": int(row["severe_count"]),
                "max_psi": row["max_psi"],
                "recommendation": row["recommendation"],
            }
            for row in rows
        ],
    }


def _model_stability_context(conn: Any, *, summary_limit: int = 8, diagnostic_limit: int = 12) -> dict[str, Any]:
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


def _is_baseline_horizon(label_name: Any, horizon_days: Any) -> bool:
    try:
        if int(horizon_days or 0) == 60:
            return True
    except Exception:
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


def _stock_horizon_profile(conn: Any, *, stock_limit: int = 12, effect_limit: int = 12) -> dict[str, Any]:
    profile_table = "mart_stock_horizon_profile"
    effect_table = "mart_stock_horizon_feature_effect"
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

    effect_count = 0
    top_effects: list[dict[str, Any]] = []
    feature_effects_by_horizon: list[dict[str, Any]] = []
    effect_run_id = run_id
    selection_count = 0
    horizon_selection: list[dict[str, Any]] = []
    selected_horizon_distribution: list[dict[str, Any]] = []
    selected_stocks: list[dict[str, Any]] = []
    if _table_exists(conn, "mart_stock_horizon_selection"):
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
        if selection_required.issubset(selection_cols):
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
    if _table_exists(conn, effect_table):
        effect_cols = _columns(conn, effect_table)
        effect_required = {"run_id", "stock_code", "label_name", "feature_name", "corr", "abs_corr_rank", "effect_direction"}
        if effect_required.issubset(effect_cols):
            local_effect_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mart_stock_horizon_feature_effect WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                or 0
            )
            if local_effect_count == 0:
                fallback_run = _scalar(
                    conn,
                    """
                    SELECT run_id
                      FROM mart_stock_horizon_feature_effect
                     GROUP BY run_id
                    HAVING COUNT(*) > 0
                     ORDER BY MAX(built_at) DESC NULLS LAST, run_id DESC
                     LIMIT 1
                    """,
                )
                if fallback_run:
                    effect_run_id = str(fallback_run)
            effect_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mart_stock_horizon_feature_effect WHERE run_id = ?",
                    (effect_run_id,),
                ).fetchone()[0]
                or 0
            )
            effect_rows = conn.execute(
                """
                SELECT e.feature_name,
                       e.effect_direction,
                       COUNT(*) AS stock_count,
                       AVG(ABS(e.corr)) AS avg_abs_corr,
                       AVG(e.corr) AS avg_corr,
                       MIN(e.horizon_days) AS min_horizon_days,
                       MAX(e.horizon_days) AS max_horizon_days
                  FROM mart_stock_horizon_feature_effect e
                  JOIN mart_stock_horizon_profile p
                    ON p.run_id = e.run_id
                   AND p.stock_code = e.stock_code
                   AND p.label_name = e.label_name
                 WHERE e.run_id = ?
                   AND p.is_best
                   AND e.abs_corr_rank = 1
                 GROUP BY e.feature_name, e.effect_direction
                 ORDER BY stock_count DESC, avg_abs_corr DESC NULLS LAST, e.feature_name
                 LIMIT ?
                """,
                (effect_run_id, int(effect_limit)),
            ).fetchall()
            top_effects = [
                {
                    "feature_name": row["feature_name"],
                    "effect_direction": row["effect_direction"],
                    "stock_count": int(row["stock_count"] or 0),
                    "avg_abs_corr": row["avg_abs_corr"],
                    "avg_corr": row["avg_corr"],
                    "min_horizon_days": row["min_horizon_days"],
                    "max_horizon_days": row["max_horizon_days"],
                }
                for row in effect_rows
            ]
            detail_rows = conn.execute(
                """
                SELECT label_name,
                       horizon_days,
                       feature_name,
                       COUNT(*) AS stock_count,
                       AVG(ABS(corr)) AS avg_abs_corr,
                       AVG(corr) AS avg_corr,
                       AVG(CASE WHEN corr > 0 THEN 1.0 WHEN corr < 0 THEN 0.0 ELSE NULL END) AS positive_share,
                       AVG(obs_count) AS avg_obs_count
                 FROM mart_stock_horizon_feature_effect
                 WHERE run_id = ?
                 GROUP BY label_name, horizon_days, feature_name
                 ORDER BY horizon_days, avg_abs_corr DESC NULLS LAST, feature_name
                 LIMIT ?
                """,
                (effect_run_id, 240),
            ).fetchall()
            for row in detail_rows:
                positive_share = row["positive_share"]
                if positive_share is None:
                    direction = "flat"
                elif positive_share >= 0.55:
                    direction = "positive"
                elif positive_share <= 0.45:
                    direction = "negative"
                else:
                    direction = "mixed"
                feature_effects_by_horizon.append(
                    {
                        "label_name": row["label_name"],
                        "horizon_days": row["horizon_days"],
                        "feature_name": row["feature_name"],
                        "stock_count": int(row["stock_count"] or 0),
                        "avg_abs_corr": row["avg_abs_corr"],
                        "avg_corr": row["avg_corr"],
                        "positive_share": positive_share,
                        "dominant_direction": direction,
                        "avg_obs_count": row["avg_obs_count"],
                    }
                )
            if horizon_selection:
                selected_by_stock = {
                    str(row.get("stock_code")): row
                    for row in horizon_selection
                    if row.get("stock_code")
                }
                effect_stock_rows = conn.execute(
                    """
                    SELECT stock_code,
                           label_name,
                           horizon_days,
                           feature_name,
                           obs_count,
                           corr,
                           abs_corr_rank,
                           effect_direction
                      FROM mart_stock_horizon_feature_effect
                     WHERE run_id = ?
                       AND stock_code IN ({})
                       AND abs_corr_rank <= 3
                     ORDER BY stock_code, abs_corr_rank, feature_name
                    """.format(", ".join(["?"] * len(selected_by_stock))),
                    (effect_run_id, *selected_by_stock.keys()),
                ).fetchall() if selected_by_stock else []
                effects_by_stock: dict[str, list[dict[str, Any]]] = {}
                for row in effect_stock_rows:
                    selection = selected_by_stock.get(str(row["stock_code"]))
                    if not selection:
                        continue
                    if str(row["label_name"]) != str(selection.get("selected_label")):
                        continue
                    effects_by_stock.setdefault(str(row["stock_code"]), []).append(
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
                for row in horizon_selection:
                    row["top_feature_effects"] = effects_by_stock.get(str(row.get("stock_code")), [])

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


def _storage_cleanup_summary(conn: Any) -> dict[str, Any]:
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        return {"latest_run_id": None, "latest_status": None}
    row = conn.execute(
        """
        SELECT run_id, status, CAST(started_at AS VARCHAR) AS started_at
          FROM mart_pipeline_run_manifest
         WHERE pipeline_name IN ('plan_storage_retention', 'execute_storage_cleanup')
         ORDER BY started_at DESC
         LIMIT 1
        """
    ).fetchone()
    if not row:
        return {"latest_run_id": None, "latest_status": None}
    return {
        "latest_run_id": row["run_id"],
        "latest_status": row["status"],
        "started_at": row["started_at"],
    }


def _pipeline_manifest_rows(conn: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        return []
    rows = conn.execute(
        """
        SELECT run_id, pipeline_name, status,
               CAST(started_at AS VARCHAR) AS started_at,
               CAST(ended_at AS VARCHAR) AS ended_at,
               duration_s, gate_result, blockers_json, perf_summary_json,
               model_id, feature_group, label_name, holding_period
          FROM mart_pipeline_run_manifest
         ORDER BY COALESCE(started_at, created_at) DESC
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    out = []
    for row in rows:
        blockers = _safe_json(row["blockers_json"]) or []
        perf = _safe_json(row["perf_summary_json"]) or {}
        out.append(
            {
                "run_id": row["run_id"],
                "pipeline_name": row["pipeline_name"],
                "status": row["status"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "duration_s": row["duration_s"],
                "gate_result": row["gate_result"],
                "blockers": blockers,
                "blocker_count": _json_count(blockers),
                "perf_summary": perf,
                "model_id": row["model_id"],
                "feature_group": row["feature_group"],
                "label_name": row["label_name"],
                "holding_period": row["holding_period"],
            }
        )
    return out


def _manifest_status_counts(conn: Any, *, limit: int = 50) -> dict[str, int]:
    if not _table_exists(conn, "mart_pipeline_run_manifest"):
        return {}
    rows = conn.execute(
        """
        SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS n
          FROM (
            SELECT status
              FROM mart_pipeline_run_manifest
             ORDER BY COALESCE(started_at, created_at) DESC
             LIMIT ?
          )
         GROUP BY COALESCE(status, 'unknown')
        """,
        (int(limit),),
    ).fetchall()
    return {str(row["status"]): int(row["n"]) for row in rows}


def _latest_feature_panel_validation(conn: Any) -> dict[str, Any] | None:
    if not _table_exists(conn, "mart_feature_panel_validation"):
        return None
    cols = _columns(conn, "mart_feature_panel_validation")
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "validation_id")},
               {_select_expr(cols, "run_mode")},
               {_select_expr(cols, "status")},
               {_select_expr(cols, "validated_at")},
               {_select_expr(cols, "rows")},
               {_select_expr(cols, "duplicate_keys")},
               {_select_expr(cols, "close_coverage")},
               {_select_expr(cols, "source_lineage_coverage")},
               {_select_expr(cols, "source_fallback_ratio")},
               {_select_expr(cols, "source_distribution_json")},
               {_select_expr(cols, "blockers_json")}
          FROM mart_feature_panel_validation
         ORDER BY {"validated_at DESC" if "validated_at" in cols else "validation_id DESC"}
         LIMIT 1
        """
    ).fetchone()
    if not rows:
        return None
    blockers = _safe_json(rows["blockers_json"]) or []
    return {
        "validation_id": rows["validation_id"],
        "run_mode": rows["run_mode"],
        "status": rows["status"],
        "validated_at": rows["validated_at"],
        "rows": rows["rows"],
        "duplicate_keys": rows["duplicate_keys"],
        "close_coverage": rows["close_coverage"],
        "source_lineage_coverage": rows["source_lineage_coverage"],
        "source_fallback_ratio": rows["source_fallback_ratio"],
        "source_distribution": _safe_json(rows["source_distribution_json"]) or [],
        "blockers": blockers,
        "blocker_count": _json_count(blockers),
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


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timing_seconds(timing: Any, key: str) -> float | None:
    if not isinstance(timing, dict):
        return None
    seconds = timing.get("seconds") if isinstance(timing.get("seconds"), dict) else {}
    for probe in (f"{key}_s", key):
        value = _float_or_none(seconds.get(probe))
        if value is not None:
            return value
    for probe in (f"{key}_s", key):
        value = _float_or_none(timing.get(probe))
        if value is not None:
            return value
    return None


def _runtime_profile_summary(
    perf: dict[str, Any],
    *,
    duration_s: Any,
    regression_per_trial_s: float | None = None,
) -> dict[str, Any]:
    trials = int(perf.get("trials") or perf.get("study_total_trials") or 0)
    duration = _float_or_none(duration_s)
    if duration is None:
        duration = _float_or_none(perf.get("duration_s"))
    duration_per_trial = duration / trials if duration is not None and trials > 0 else None
    timing = perf.get("timing") if isinstance(perf.get("timing"), dict) else {}
    train_s = _timing_seconds(timing, "train")
    cache = perf.get("ranker_cache") if isinstance(perf.get("ranker_cache"), dict) else {}
    cache_hits = int(cache.get("hits") or 0)
    cache_misses = int(cache.get("misses") or 0)
    cache_total = cache_hits + cache_misses
    eval_cache = perf.get("evaluation_cache") if isinstance(perf.get("evaluation_cache"), dict) else {}
    eval_hits = eval_cache.get("hits") if isinstance(eval_cache.get("hits"), dict) else {}
    eval_misses = eval_cache.get("misses") if isinstance(eval_cache.get("misses"), dict) else {}

    def eval_cache_rate(key: str | None = None) -> float | None:
        if key is None:
            hits = sum(int(value or 0) for value in eval_hits.values())
            misses = sum(int(value or 0) for value in eval_misses.values())
        else:
            hits = int(eval_hits.get(key) or 0)
            misses = int(eval_misses.get(key) or 0)
        total = hits + misses
        return hits / total if total > 0 else None

    return {
        "trials": trials,
        "duration_per_trial_s": duration_per_trial,
        "train_time_pct": train_s / duration if train_s is not None and duration and duration > 0 else None,
        "cache_hit_rate": cache_hits / cache_total if cache_total > 0 else None,
        "eval_cache_hit_rate": eval_cache_rate(),
        "matrix_cache_hit_rate": eval_cache_rate("matrix"),
        "feature_drift_cache_hit_rate": eval_cache_rate("feature_drift"),
        "runtime_ratio_vs_regression": (
            duration_per_trial / regression_per_trial_s
            if duration_per_trial is not None and regression_per_trial_s and regression_per_trial_s > 0
            else None
        ),
    }


def _rank_matrix_cache_view(conn: Any, *, limit: int = 8) -> dict[str, Any]:
    summary = {
        "entry_count": 0,
        "total_rows": 0,
        "total_hits": 0,
        "latest_used_at": None,
    }
    cache_entries: list[dict[str, Any]] = []
    latest_benchmarks: list[dict[str, Any]] = []
    if _table_exists(conn, "mart_feature_rank_matrix_cache_manifest"):
        rows = conn.execute(
            """
            SELECT cache_key, table_name, panel_table, feature_set_id,
                   row_count, rank_column_count, build_duration_s,
                   created_at, last_used_at, hit_count
              FROM mart_feature_rank_matrix_cache_manifest
             ORDER BY last_used_at DESC NULLS LAST, created_at DESC NULLS LAST
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        cache_entries = [
            {
                "cache_key": row["cache_key"],
                "table_name": row["table_name"],
                "panel_table": row["panel_table"],
                "feature_set_id": row["feature_set_id"],
                "row_count": int(row["row_count"] or 0),
                "rank_column_count": int(row["rank_column_count"] or 0),
                "build_duration_s": row["build_duration_s"],
                "created_at": row["created_at"],
                "last_used_at": row["last_used_at"],
                "hit_count": int(row["hit_count"] or 0),
            }
            for row in rows
        ]
        agg = conn.execute(
            """
            SELECT COUNT(*) AS entry_count,
                   SUM(COALESCE(row_count, 0)) AS total_rows,
                   SUM(COALESCE(hit_count, 0)) AS total_hits,
                   MAX(last_used_at) AS latest_used_at
              FROM mart_feature_rank_matrix_cache_manifest
            """
        ).fetchone()
        if agg:
            summary = {
                "entry_count": int(agg["entry_count"] or 0),
                "total_rows": int(agg["total_rows"] or 0),
                "total_hits": int(agg["total_hits"] or 0),
                "latest_used_at": agg["latest_used_at"],
            }
    if _table_exists(conn, "mart_feature_rank_matrix_benchmark"):
        cols = _columns(conn, "mart_feature_rank_matrix_benchmark")
        rows = conn.execute(
            f"""
            SELECT run_id, panel_table, label_name, feature_count, label_count,
                   total_rows, rank_matrix_rows, proxy_rows,
                   matrix_duration_s, rank_matrix_build_s, proxy_association_s,
                   compared_pairs, max_abs_rank_ic_delta, avg_abs_rank_ic_delta,
                   {_select_expr(cols, "gate_status")},
                   {_select_expr(cols, "config_json")},
                   {_select_expr(cols, "stage_timings_json")},
                   {_cast_select_expr(cols, "built_at")}
              FROM mart_feature_rank_matrix_benchmark
             ORDER BY built_at DESC NULLS LAST, run_id DESC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        for row in rows:
            config = _safe_json(row["config_json"]) or {}
            timings = _safe_json(row["stage_timings_json"]) or {}
            latest_benchmarks.append(
                {
                    "run_id": row["run_id"],
                    "panel_table": row["panel_table"],
                    "label_name": row["label_name"],
                    "feature_count": int(row["feature_count"] or 0),
                    "label_count": int(row["label_count"] or 0),
                    "total_rows": int(row["total_rows"] or 0),
                    "rank_matrix_rows": int(row["rank_matrix_rows"] or 0),
                    "proxy_rows": int(row["proxy_rows"] or 0),
                    "matrix_duration_s": row["matrix_duration_s"],
                    "rank_matrix_build_s": row["rank_matrix_build_s"],
                    "proxy_association_s": row["proxy_association_s"],
                    "compared_pairs": int(row["compared_pairs"] or 0),
                    "max_abs_rank_ic_delta": row["max_abs_rank_ic_delta"],
                    "avg_abs_rank_ic_delta": row["avg_abs_rank_ic_delta"],
                    "gate_status": row["gate_status"],
                    "rank_matrix_cache": config.get("rank_matrix_cache") if isinstance(config, dict) else None,
                    "stage_timings": timings if isinstance(timings, dict) else {},
                    "built_at": row["built_at"],
                }
            )
    return {
        "summary": summary,
        "cache_entries": cache_entries,
        "latest_benchmarks": latest_benchmarks,
    }


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


def build_workbench_overview(conn: Any, *, drift_limit: int = 8, as_of_date: str | None = None) -> dict[str, Any]:
    research_run_id = _latest_run_id(conn, "mart_research_schedule_plan")
    drift = detect_drift(conn)
    blockers = []
    if drift:
        blockers.append({"kind": "schema_drift", "count": len(drift)})

    overview = {
        "latest_trading_day": _latest_trading_day(conn, as_of_date=as_of_date),
        "latest_manifest": _latest_manifest(conn),
        "schema_drift_count": len(drift),
        "research_schedule": {
            "run_id": research_run_id,
            "status_counts": _status_counts(conn, "mart_research_schedule_plan", run_id=research_run_id),
        },
        "champion": _champion_summary(conn),
        "feature_drift": _drift_offenders(conn, drift_limit),
        "storage": _storage_cleanup_summary(conn),
        "blockers": blockers,
    }
    return overview


def _latest_data_health_snapshot_at(conn: Any) -> str | None:
    if not _table_exists(conn, "mart_data_health"):
        return None
    return _scalar(conn, "SELECT CAST(MAX(snapshot_at) AS VARCHAR) FROM mart_data_health")


def _asset_health_snapshot(conn: Any) -> dict[str, Any]:
    empty = {
        "snapshot_at": None,
        "summary": {"total": 0, "green": 0, "yellow": 0, "red": 0, "unknown": 0},
        "by_layer": {},
        "governance_counts": {
            "coverage_policy": {},
            "null_policy": {},
            "model_eligibility": {},
            "quality_gate_level": {},
        },
        "items": [],
        "red_list": [],
        "fallback_active": [],
    }
    if not _table_exists(conn, "dim_data_asset") or not _table_exists(conn, "mart_data_health"):
        return empty
    snap_at = _latest_data_health_snapshot_at(conn)
    if not snap_at:
        return empty
    health_cols = _columns(conn, "mart_data_health")
    dim_cols = _columns(conn, "dim_data_asset")
    tier_dist_expr = "m.source_tier_dist" if "source_tier_dist" in health_cols else "NULL AS source_tier_dist"
    governance_exprs = []
    for column in (
        "asset_grain",
        "asset_cadence",
        "coverage_policy",
        "null_policy",
        "pit_policy",
        "intended_use",
        "model_eligibility",
        "strategy_eligibility",
        "frontend_visibility",
        "quality_gate_level",
    ):
        governance_exprs.append(f"d.{column}" if column in dim_cols else f"NULL AS {column}")
    rows = conn.execute(
        f"""
        SELECT d.table_name, d.layer, d.purpose, d.writer_module,
               d.upstream_source, d.source_tier, d.expected_freshness, d.sla_hours,
               {", ".join(governance_exprs)},
               m.row_count, CAST(m.last_data_date AS VARCHAR) AS last_data_date,
               m.freshness_hours, m.freshness_ok, m.severity, m.issue_summary,
               {tier_dist_expr}
          FROM dim_data_asset d
          LEFT JOIN mart_data_health m
            ON m.table_name = d.table_name
           AND CAST(m.snapshot_at AS VARCHAR) = ?
         ORDER BY d.layer, d.table_name
        """,
        (snap_at,),
    ).fetchall()
    by_layer: dict[str, dict[str, int]] = {}
    severity_total = {"green": 0, "yellow": 0, "red": 0, "unknown": 0}
    governance_counts: dict[str, dict[str, int]] = {
        "coverage_policy": {},
        "null_policy": {},
        "model_eligibility": {},
        "quality_gate_level": {},
    }
    items = []
    red_list = []
    fallback_active = []
    for row in rows:
        sev = str(row["severity"] or "unknown")
        layer = str(row["layer"] or "unknown")
        severity_total[sev] = severity_total.get(sev, 0) + 1
        layer_counts = by_layer.setdefault(layer, {"green": 0, "yellow": 0, "red": 0, "unknown": 0, "total": 0})
        layer_counts[sev] = layer_counts.get(sev, 0) + 1
        layer_counts["total"] += 1
        for field, counts in governance_counts.items():
            value = str(row[field] or "unknown")
            counts[value] = counts.get(value, 0) + 1
        tier_dist = _safe_json(row["source_tier_dist"]) or None
        if isinstance(tier_dist, dict):
            fallback_rows = sum(int(value) for key, value in tier_dist.items() if str(key).isdigit() and int(key) > 1)
            if fallback_rows > 0:
                fallback_active.append(
                    {
                        "table": row["table_name"],
                        "tier_distribution": tier_dist,
                        "fallback_rows": fallback_rows,
                    }
                )
        item = {
            "table_name": row["table_name"],
            "layer": layer,
            "severity": sev,
            "row_count": row["row_count"],
            "last_data_date": row["last_data_date"],
            "freshness_hours": row["freshness_hours"],
            "sla_hours": row["sla_hours"],
            "expected_freshness": row["expected_freshness"],
            "writer_module": row["writer_module"],
            "upstream_source": row["upstream_source"],
            "source_tier": row["source_tier"],
            "asset_grain": row["asset_grain"],
            "asset_cadence": row["asset_cadence"],
            "coverage_policy": row["coverage_policy"],
            "null_policy": row["null_policy"],
            "pit_policy": row["pit_policy"],
            "intended_use": row["intended_use"],
            "model_eligibility": row["model_eligibility"],
            "strategy_eligibility": row["strategy_eligibility"],
            "frontend_visibility": row["frontend_visibility"],
            "quality_gate_level": row["quality_gate_level"],
            "issue_summary": row["issue_summary"],
            "source_tier_distribution": tier_dist,
        }
        items.append(item)
        if sev == "red":
            red_list.append(item)
    return {
        "snapshot_at": snap_at,
        "summary": {"total": sum(severity_total.values()), **severity_total},
        "by_layer": by_layer,
        "governance_counts": governance_counts,
        "items": items,
        "red_list": red_list,
        "fallback_active": fallback_active,
    }


def _source_health_overview(conn: Any) -> dict[str, Any]:
    if not _table_exists(conn, "dim_data_asset") or not _table_exists(conn, "mart_data_health"):
        return {"snapshot_at": None, "sources": [], "source_priorities": [], "watermarks": [], "failure_queue": []}
    snap_at = _latest_data_health_snapshot_at(conn)
    dim_cols = _columns(conn, "dim_data_asset")
    deprecation_filter = "AND COALESCE(d.deprecation_status, 'active') = 'active'" if "deprecation_status" in dim_cols else ""
    rows = conn.execute(
        f"""
        SELECT d.upstream_source, d.source_tier,
               COUNT(*) AS asset_count,
               SUM(COALESCE(m.row_count, 0)) AS total_rows,
               SUM(CASE WHEN m.severity = 'red' THEN 1 ELSE 0 END) AS red_count,
               SUM(CASE WHEN m.severity = 'yellow' THEN 1 ELSE 0 END) AS yellow_count,
               SUM(CASE WHEN m.severity = 'green' THEN 1 ELSE 0 END) AS green_count,
               MAX(m.freshness_hours) AS max_freshness_h
          FROM dim_data_asset d
          LEFT JOIN mart_data_health m
            ON m.table_name = d.table_name
           AND CAST(m.snapshot_at AS VARCHAR) = COALESCE(?, CAST(m.snapshot_at AS VARCHAR))
         WHERE d.upstream_source IS NOT NULL
           AND d.source_tier IN (1, 2, 3)
           {deprecation_filter}
         GROUP BY d.upstream_source, d.source_tier
         ORDER BY d.source_tier, d.upstream_source
        """,
        (snap_at,),
    ).fetchall()
    priorities = []
    if _table_exists(conn, "dim_data_source_priority"):
        priorities = [
            dict(row)
            for row in conn.execute(
                """
                SELECT data_domain, preferred_source, fallback_1, fallback_2, reason
                  FROM dim_data_source_priority
                 ORDER BY data_domain
                """
            ).fetchall()
        ]
    return {
        "snapshot_at": snap_at,
        "sources": [dict(row) for row in rows],
        "source_priorities": priorities,
        "watermarks": [],
        "failure_queue": [],
    }


def _data_processing_monitor_view(conn: Any, *, limit: int = 30) -> dict[str, Any]:
    sources: list[tuple[str, str]] = []
    if _table_exists(conn, "mart_data_processing_tool_run"):
        sources.append(("main", "mart_data_processing_tool_run"))
    if _attach_market_readonly(conn) and _relation_exists(conn, "market.mart_data_processing_tool_run"):
        sources.append(("market", "market.mart_data_processing_tool_run"))

    runs: list[dict[str, Any]] = []
    for scope, table in sources:
        try:
            query_rows = conn.execute(
                f"""
                SELECT run_id, tool_name, policy_id, source_name, status,
                       input_rows, accepted_rows, rejected_rows,
                       reason_counts_json, output_table, batch_id,
                       CAST(ended_at AS VARCHAR) AS ended_at, duration_s
                  FROM {table}
                 ORDER BY ended_at DESC
                 LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        except Exception:
            continue
        for row in query_rows:
            runs.append({
                "scope": scope,
                "run_id": row["run_id"],
                "tool_name": row["tool_name"],
                "policy_id": row["policy_id"],
                "source_name": row["source_name"],
                "status": row["status"],
                "input_rows": int(row["input_rows"] or 0),
                "accepted_rows": int(row["accepted_rows"] or 0),
                "rejected_rows": int(row["rejected_rows"] or 0),
                "reason_counts": _safe_json(row["reason_counts_json"]) or {},
                "output_table": row["output_table"],
                "batch_id": row["batch_id"],
                "ended_at": row["ended_at"],
                "duration_s": row["duration_s"],
            })

    runs.sort(key=lambda item: str(item.get("ended_at") or ""), reverse=True)
    runs = runs[: int(limit)]
    reason_counts: dict[str, int] = {}
    for row in runs:
        for reason, count in (row.get("reason_counts") or {}).items():
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + int(count or 0)
    return {
        "sources": [scope for scope, _ in sources],
        "run_count": len(runs),
        "total_input_rows": sum(row["input_rows"] for row in runs),
        "total_accepted_rows": sum(row["accepted_rows"] for row in runs),
        "total_rejected_rows": sum(row["rejected_rows"] for row in runs),
        "reason_counts": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "recent_runs": runs,
    }


def _today_signal_cache_view(conn: Any) -> dict[str, Any]:
    try:
        from services.signals_v2 import describe_today_signal_cache  # noqa: WPS433

        status = describe_today_signal_cache(conn)
    except Exception as exc:
        status = {
            "status": "unavailable",
            "signal_count": 0,
            "freshness_days": None,
            "source_max_notice_date": None,
            "current_source_max_notice_date": None,
            "built_at": None,
            "stale": True,
            "requires_refresh": True,
            "error": str(exc)[:160],
        }
    step = None
    if _table_exists(conn, "step_status"):
        cols = _columns(conn, "step_status")
        if {"step_id", "status"}.issubset(cols):
            records_expr = "records" if "records" in cols else "NULL AS records"
            started_expr = "CAST(started_at AS VARCHAR) AS started_at" if "started_at" in cols else "NULL AS started_at"
            finished_expr = "CAST(finished_at AS VARCHAR) AS finished_at" if "finished_at" in cols else "NULL AS finished_at"
            error_expr = "error" if "error" in cols else "NULL AS error"
            row = conn.execute(
                f"""
                SELECT status, {records_expr}, {started_expr},
                       {finished_expr}, {error_expr}
                  FROM step_status
                 WHERE step_id = 'refresh_today_signals'
                 LIMIT 1
                """
            ).fetchone()
            if row:
                step = {
                    "status": row["status"],
                    "records": row["records"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "error": row["error"],
                }
    status["step"] = step
    return status


def _empty_tdx_server_health_view() -> dict[str, Any]:
    return {
        "summary": {
            "server_count": 0,
            "healthy_count": 0,
            "failure_server_count": 0,
            "timeout_server_count": 0,
            "total_successes": 0,
            "total_failures": 0,
            "total_timeouts": 0,
            "capabilities": [],
            "latest_updated_at": None,
        },
        "servers": [],
        "top_servers": [],
        "failing_servers": [],
        "updated_at": None,
    }


def _tdx_server_health_relation(conn: Any) -> str | None:
    if _relation_exists(conn, "mart_tdx_server_health"):
        return "mart_tdx_server_health"
    if _relation_exists(conn, "market.mart_tdx_server_health"):
        return "market.mart_tdx_server_health"
    if _attach_market_readonly(conn) and _relation_exists(conn, "market.mart_tdx_server_health"):
        return "market.mart_tdx_server_health"
    return None


def _relation_columns(conn: Any, relation: str) -> set[str]:
    try:
        rows = conn.execute(f"DESCRIBE {relation}").fetchall()
    except Exception:
        return set()
    return {str(row["column_name"]) for row in rows}


def _tdx_server_health_view(conn: Any, *, limit: int = 30) -> dict[str, Any]:
    relation = _tdx_server_health_relation(conn)
    if not relation:
        return _empty_tdx_server_health_view()
    cols = _relation_columns(conn, relation)
    required = {
        "server_host",
        "server_port",
        "capability",
        "success_count",
        "failure_count",
        "timeout_count",
        "health_score",
        "updated_at",
    }
    missing = sorted(required - cols)
    if missing:
        view = _empty_tdx_server_health_view()
        view["schema_issues"] = [{"kind": "missing_columns", "columns": missing}]
        return view

    query_rows = conn.execute(
        f"""
        SELECT server_host, server_port, capability,
               success_count, failure_count, timeout_count,
               last_success_at, last_failure_at, last_error_type,
               avg_success_elapsed_s, last_attempt_elapsed_s,
               health_score, source_run_id, updated_at
          FROM {relation}
         ORDER BY capability, health_score DESC, server_host, server_port
        """,
    ).fetchall()
    rows: list[dict[str, Any]] = []
    for row in query_rows:
        rows.append(
            {
                "server_host": row["server_host"],
                "server_port": int(row["server_port"] or 0),
                "capability": row["capability"],
                "success_count": int(row["success_count"] or 0),
                "failure_count": int(row["failure_count"] or 0),
                "timeout_count": int(row["timeout_count"] or 0),
                "last_success_at": row["last_success_at"],
                "last_failure_at": row["last_failure_at"],
                "last_error_type": row["last_error_type"],
                "avg_success_elapsed_s": row["avg_success_elapsed_s"],
                "last_attempt_elapsed_s": row["last_attempt_elapsed_s"],
                "health_score": row["health_score"],
                "source_run_id": row["source_run_id"],
                "updated_at": row["updated_at"],
            }
        )
    if not rows:
        return _empty_tdx_server_health_view()

    latest_updated_at = max(str(row.get("updated_at") or "") for row in rows) or None
    summary = {
        "server_count": len(rows),
        "healthy_count": sum(1 for row in rows if row["success_count"] > 0),
        "failure_server_count": sum(1 for row in rows if row["failure_count"] > 0),
        "timeout_server_count": sum(1 for row in rows if row["timeout_count"] > 0),
        "total_successes": sum(row["success_count"] for row in rows),
        "total_failures": sum(row["failure_count"] for row in rows),
        "total_timeouts": sum(row["timeout_count"] for row in rows),
        "capabilities": sorted({str(row["capability"]) for row in rows if row.get("capability")}),
        "latest_updated_at": latest_updated_at,
    }
    top_servers = [row for row in rows if row["success_count"] > 0]
    top_servers.sort(key=lambda row: (float(row["health_score"] or 0), row["success_count"]), reverse=True)
    failing_servers = [row for row in rows if row["failure_count"] > 0 or row["timeout_count"] > 0]
    failing_servers.sort(
        key=lambda row: (
            row["timeout_count"],
            row["failure_count"],
            str(row.get("last_failure_at") or row.get("updated_at") or ""),
        ),
        reverse=True,
    )
    return {
        "summary": summary,
        "servers": rows[: int(limit)],
        "top_servers": top_servers[: int(limit)],
        "failing_servers": failing_servers[: min(int(limit), 12)],
        "updated_at": latest_updated_at,
    }


def _f10_source_date_audit_view(conn: Any, *, limit: int = 30) -> dict[str, Any]:
    table = "mart_tdx_f10_source_date_section_audit"
    empty = {
        "run_id": None,
        "built_at": None,
        "summary": {
            "audit_rows": 0,
            "occurrence_count": 0,
            "future_occurrence_count": 0,
            "source_notice_candidate_occurrences": 0,
            "source_notice_candidate_future_occurrences": 0,
            "raw_row_count": 0,
        },
        "rows": [],
    }
    if not _table_exists(conn, table):
        return empty
    row = conn.execute(
        """
        SELECT run_id, MAX(CAST(built_at AS VARCHAR)) AS built_at
          FROM mart_tdx_f10_source_date_section_audit
         GROUP BY run_id
         ORDER BY built_at DESC, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    if not row:
        return empty
    run_id = row["run_id"]
    summary = conn.execute(
        """
        SELECT COUNT(*) AS audit_rows,
               SUM(COALESCE(occurrence_count, 0)) AS occurrence_count,
               SUM(COALESCE(future_occurrence_count, 0)) AS future_occurrence_count,
               SUM(CASE WHEN source_notice_candidate
                        THEN COALESCE(occurrence_count, 0) ELSE 0 END) AS source_notice_candidate_occurrences,
               SUM(CASE WHEN source_notice_candidate
                        THEN COALESCE(future_occurrence_count, 0) ELSE 0 END) AS source_notice_candidate_future_occurrences,
               MAX(COALESCE(raw_row_count, 0)) AS raw_row_count
          FROM mart_tdx_f10_source_date_section_audit
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    rows = conn.execute(
        """
        SELECT section_id, section_name, pattern_name, date_role,
               source_notice_candidate, raw_row_count, stock_count,
               occurrence_count, future_occurrence_count, min_date, max_date,
               sample_json
          FROM mart_tdx_f10_source_date_section_audit
         WHERE run_id = ?
         ORDER BY source_notice_candidate DESC,
                  future_occurrence_count DESC,
                  section_id, pattern_name, date_role
         LIMIT ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    return {
        "run_id": run_id,
        "built_at": row["built_at"],
        "summary": {
            "audit_rows": int((summary or {})["audit_rows"] or 0),
            "occurrence_count": int((summary or {})["occurrence_count"] or 0),
            "future_occurrence_count": int((summary or {})["future_occurrence_count"] or 0),
            "source_notice_candidate_occurrences": int(
                (summary or {})["source_notice_candidate_occurrences"] or 0
            ),
            "source_notice_candidate_future_occurrences": int(
                (summary or {})["source_notice_candidate_future_occurrences"] or 0
            ),
            "raw_row_count": int((summary or {})["raw_row_count"] or 0),
        },
        "rows": [
            {
                "section_id": row["section_id"],
                "section_name": row["section_name"],
                "pattern_name": row["pattern_name"],
                "date_role": row["date_role"],
                "source_notice_candidate": bool(row["source_notice_candidate"]),
                "raw_row_count": int(row["raw_row_count"] or 0),
                "stock_count": int(row["stock_count"] or 0),
                "occurrence_count": int(row["occurrence_count"] or 0),
                "future_occurrence_count": int(row["future_occurrence_count"] or 0),
                "min_date": row["min_date"],
                "max_date": row["max_date"],
                "samples": _safe_json(row["sample_json"]) or [],
            }
            for row in rows
        ],
    }


def _tdx_f10_source_dq_view(conn: Any, *, limit: int = 30) -> dict[str, Any]:
    empty = {
        "gate_run_id": None,
        "gate_status": None,
        "ended_at": None,
        "duration_s": None,
        "blocker_count": 0,
        "warning_count": 0,
        "summary": {},
        "details": [],
    }
    if not _table_exists(conn, "mart_global_data_quality_gate") or not _table_exists(
        conn,
        "mart_global_data_quality_detail",
    ):
        return empty
    gate = conn.execute(
        """
        SELECT gate_run_id, gate_status, CAST(ended_at AS VARCHAR) AS ended_at,
               duration_s, blockers_json, warnings_json
          FROM mart_global_data_quality_gate
         WHERE gate_scope = 'production'
         ORDER BY ended_at DESC, gate_run_id DESC
         LIMIT 1
        """
    ).fetchone()
    if not gate:
        return empty
    gate_run_id = gate["gate_run_id"]
    rows = conn.execute(
        """
        SELECT table_name, column_name, check_name, status, severity,
               row_count, violation_count, reason, examples_json,
               CAST(built_at AS VARCHAR) AS built_at
          FROM mart_global_data_quality_detail
         WHERE gate_run_id = ?
           AND domain = 'tdx_f10_source_availability'
         ORDER BY CASE status WHEN 'fail' THEN 0 ELSE 1 END,
                  violation_count DESC NULLS LAST,
                  table_name, check_name
         LIMIT ?
        """,
        (gate_run_id, int(limit)),
    ).fetchall()
    status_counts: dict[str, int] = {}
    table_counts: dict[str, int] = {}
    details = []
    for row in rows:
        status = str(row["status"] or "unknown")
        table_name = str(row["table_name"] or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        table_counts[table_name] = table_counts.get(table_name, 0) + 1
        details.append(
            {
                "table_name": row["table_name"],
                "column_name": row["column_name"],
                "check_name": row["check_name"],
                "status": row["status"],
                "severity": row["severity"],
                "row_count": int(row["row_count"] or 0),
                "violation_count": int(row["violation_count"] or 0),
                "reason": row["reason"],
                "examples": _safe_json(row["examples_json"]) or [],
                "built_at": row["built_at"],
            }
        )
    blockers = _safe_json(gate["blockers_json"]) or []
    warnings = _safe_json(gate["warnings_json"]) or []
    return {
        "gate_run_id": gate_run_id,
        "gate_status": gate["gate_status"],
        "ended_at": gate["ended_at"],
        "duration_s": gate["duration_s"],
        "blocker_count": _json_count(blockers),
        "warning_count": _json_count(warnings),
        "summary": {
            "status_counts": status_counts,
            "table_counts": table_counts,
            "detail_count": len(details),
        },
        "details": details,
    }


def build_workbench_data_sources(conn: Any, *, limit: int = 30, as_of_date: str | None = None) -> dict[str, Any]:
    rows = []
    if _table_exists(conn, "mart_data_source_watermark"):
        wm_cols = _columns(conn, "mart_data_source_watermark")
        query_rows = conn.execute(
            f"""
            SELECT {_select_expr(wm_cols, "data_domain")},
                   {_select_expr(wm_cols, "source_name")},
                   {_select_expr(wm_cols, "source_tier")},
                   {_cast_select_expr(wm_cols, "last_success_at")},
                   {_select_expr(wm_cols, "last_data_date")},
                   {_select_expr(wm_cols, "row_count")},
                   {_select_expr(wm_cols, "consecutive_failures", default="0")},
                   {_select_expr(wm_cols, "fallback_active", default="FALSE")},
                   {_select_expr(wm_cols, "fallback_reason")},
                   {_select_expr(wm_cols, "parser_version")},
                   {_cast_select_expr(wm_cols, "updated_at")}
              FROM mart_data_source_watermark
             ORDER BY data_domain, source_tier, source_name
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        rows = [
            {
                "data_domain": row["data_domain"],
                "source_name": row["source_name"],
                "source_tier": row["source_tier"],
                "last_success_at": row["last_success_at"],
                "last_data_date": row["last_data_date"],
                "row_count": row["row_count"],
                "consecutive_failures": int(row["consecutive_failures"] or 0),
                "fallback_active": bool(row["fallback_active"]),
                "fallback_reason": row["fallback_reason"],
                "parser_version": row["parser_version"],
                "updated_at": row["updated_at"],
            }
            for row in query_rows
        ]

    kline_rows = [row for row in rows if row["data_domain"] == "kline_daily"]
    primary = next((row for row in kline_rows if int(row["source_tier"] or 0) == 1), None)
    fallbacks = [row for row in kline_rows if int(row["source_tier"] or 0) > 1]
    latest_validation = _latest_feature_panel_validation(conn)
    blockers = []
    if not primary:
        blockers.append({"kind": "missing_tdxhub_primary", "count": 1})
    elif "tdxhub" not in str(primary.get("source_name") or "").lower():
        blockers.append({"kind": "kline_primary_not_tdxhub", "count": 1})
    for row in rows:
        if row["consecutive_failures"] > 0:
            blockers.append(
                {
                    "kind": "source_failures",
                    "data_domain": row["data_domain"],
                    "source_name": row["source_name"],
                    "count": row["consecutive_failures"],
                }
            )
    tdx_f10_capabilities = []
    if _table_exists(conn, "mart_tdx_f10_capability_matrix"):
        tdx_f10_capabilities = [
            dict(row)
            for row in conn.execute(
                """
                SELECT module_id, module_name, endpoint, parser, raw_table,
                       fact_table, raw_text_available, parsed_table_available,
                       coverage_stock_count, row_count, latest_page_update_date,
                       latest_fetched_at, parser_version, pit_risk,
                       source_date_field, availability_date_field, status, notes,
                       built_at
                  FROM mart_tdx_f10_capability_matrix
                 ORDER BY module_id
                """
            ).fetchall()
        ]
    return {
        "calendar_target": _latest_trading_day(conn, as_of_date=as_of_date),
        "watermark_count": len(rows),
        "watermarks": rows,
        "kline": {
            "primary": primary,
            "fallbacks": fallbacks,
            "fallback_active_count": sum(1 for row in fallbacks if row["fallback_active"]),
            "primary_is_tdxhub": bool(primary and "tdxhub" in str(primary.get("source_name") or "").lower()),
        },
        "latest_feature_validation": latest_validation,
        "asset_health": _asset_health_snapshot(conn),
        "source_health": _source_health_overview(conn),
        "processing_monitor": _data_processing_monitor_view(conn, limit=limit),
        "today_signal_cache": _today_signal_cache_view(conn),
        "tdx_server_health": _tdx_server_health_view(conn, limit=limit),
        "tdx_f10_capabilities": tdx_f10_capabilities,
        "f10_source_date_audit": _f10_source_date_audit_view(conn, limit=limit),
        "tdx_f10_source_dq": _tdx_f10_source_dq_view(conn, limit=limit),
        "blockers": blockers,
    }


def build_workbench_pipelines(conn: Any, *, limit: int = 30) -> dict[str, Any]:
    rows = _pipeline_manifest_rows(conn, limit=limit)
    latest_by_pipeline: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest_by_pipeline.setdefault(str(row["pipeline_name"]), row)
    slowest = sorted(
        [row for row in rows if row["duration_s"] is not None],
        key=lambda row: float(row["duration_s"] or 0),
        reverse=True,
    )[:8]
    blocker_rows = [row for row in rows if row["blocker_count"] or str(row["status"]).lower() not in {"success", "completed"}]
    return {
        "status_counts": _manifest_status_counts(conn, limit=limit),
        "recent": rows,
        "latest_by_pipeline": list(latest_by_pipeline.values()),
        "slowest": slowest,
        "blockers": blocker_rows,
    }


def _feature_availability_contract_view(conn: Any, *, limit: int = 160) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    source = "registry"
    if _table_exists(conn, "mart_feature_availability_contract"):
        source = "mart_feature_availability_contract"
        cols = _columns(conn, "mart_feature_availability_contract")
        selected = [
            "feature_name",
            "feature_group",
            "feature_role",
            "availability_cadence",
            "panel_density",
            "expected_update_frequency",
            "null_policy",
            "coverage_universe",
            "model_input",
            "production_ready",
            "enabled",
            "frontend_visible",
            "pit_release_lag_days",
            "notes",
            "built_at",
        ]
        if set(selected).issubset(cols):
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT feature_name, feature_group, feature_role,
                           availability_cadence, panel_density,
                           expected_update_frequency, null_policy,
                           coverage_universe, model_input, production_ready,
                           enabled, frontend_visible, pit_release_lag_days,
                           notes, built_at
                      FROM mart_feature_availability_contract
                     WHERE frontend_visible = TRUE
                     ORDER BY
                           CASE WHEN model_input THEN 0 ELSE 1 END,
                           CASE WHEN production_ready THEN 0 ELSE 1 END,
                           feature_role, feature_group, feature_name
                     LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
            ]
    if not rows:
        registry = load_feature_registry()
        rows = [
            {
                "feature_name": spec.name,
                "feature_group": spec.group,
                "feature_role": spec.feature_role,
                "availability_cadence": spec.availability_cadence,
                "panel_density": spec.panel_density,
                "expected_update_frequency": spec.expected_update_frequency,
                "null_policy": spec.null_policy,
                "coverage_universe": spec.coverage_universe,
                "model_input": spec.model_input,
                "production_ready": spec.production_ready,
                "enabled": spec.enabled,
                "frontend_visible": spec.frontend_visible,
                "pit_release_lag_days": spec.pit_release_lag_days,
                "notes": spec.notes,
                "built_at": None,
            }
            for spec in registry.features.values()
            if spec.frontend_visible
        ][:limit]

    role_counts: dict[str, int] = {}
    cadence_counts: dict[str, int] = {}
    density_counts: dict[str, int] = {}
    null_policy_counts: dict[str, int] = {}
    for row in rows:
        role = str(row.get("feature_role") or "unknown")
        cadence = str(row.get("availability_cadence") or "unknown")
        density = str(row.get("panel_density") or "unknown")
        null_policy = str(row.get("null_policy") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        cadence_counts[cadence] = cadence_counts.get(cadence, 0) + 1
        density_counts[density] = density_counts.get(density, 0) + 1
        null_policy_counts[null_policy] = null_policy_counts.get(null_policy, 0) + 1
    return {
        "source": source,
        "rows": rows,
        "role_counts": role_counts,
        "cadence_counts": cadence_counts,
        "density_counts": density_counts,
        "null_policy_counts": null_policy_counts,
    }


def _feature_catalog_current_view(conn: Any, *, limit: int = 240) -> dict[str, Any]:
    table = "mart_feature_catalog_current"
    if not _table_exists(conn, table):
        return {"run_id": None, "summary": {}, "risk_counts": {}, "table_counts": {}, "rows": []}
    run_id = _latest_run_id(conn, table)
    if not run_id:
        return {"run_id": None, "summary": {}, "risk_counts": {}, "table_counts": {}, "rows": []}
    cols = _columns(conn, table)
    required = {
        "run_id",
        "feature_table",
        "feature_name",
        "feature_family",
        "registry_status",
        "model_input",
        "production_ready",
        "pit_risk_level",
        "total_rows",
        "non_null_rows",
        "coverage_pct",
        "allowed_in_production_research",
    }
    if not required.issubset(cols):
        return {"run_id": run_id, "summary": {}, "risk_counts": {}, "table_counts": {}, "rows": []}

    risk_rows = conn.execute(
        """
        SELECT pit_risk_level, COUNT(*) AS n
          FROM mart_feature_catalog_current
         WHERE run_id = ?
         GROUP BY pit_risk_level
         ORDER BY pit_risk_level
        """,
        (run_id,),
    ).fetchall()
    table_rows = conn.execute(
        """
        SELECT feature_table, COUNT(*) AS n
          FROM mart_feature_catalog_current
         WHERE run_id = ?
         GROUP BY feature_table
         ORDER BY feature_table
        """,
        (run_id,),
    ).fetchall()
    summary_row = conn.execute(
        """
        SELECT COUNT(*) AS total_features,
               SUM(CASE WHEN allowed_in_production_research THEN 1 ELSE 0 END) AS allowed_features,
               SUM(CASE WHEN model_input THEN 1 ELSE 0 END) AS model_input_features,
               SUM(CASE WHEN registry_status = 'unknown' THEN 1 ELSE 0 END) AS unknown_features,
               SUM(CASE WHEN non_null_rows = 0 THEN 1 ELSE 0 END) AS zero_coverage_features,
               SUM(CASE WHEN pit_risk_level = 'critical' THEN 1 ELSE 0 END) AS critical_features,
               SUM(CASE WHEN pit_risk_level = 'high' THEN 1 ELSE 0 END) AS high_features
          FROM mart_feature_catalog_current
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()

    join_exists = _table_exists(conn, "mart_feature_pit_join_plan")
    exclusion_exists = _table_exists(conn, "mart_feature_exclusion_reason")
    join_sql = ""
    if join_exists:
        join_sql = """
        LEFT JOIN mart_feature_pit_join_plan j
          ON j.run_id = c.run_id
         AND j.feature_table = c.feature_table
         AND j.feature_name = c.feature_name
        """
    reason_sql = ""
    if exclusion_exists:
        reason_sql = """
        LEFT JOIN (
            SELECT run_id, feature_table, feature_name,
                   STRING_AGG(reason_code, ', ' ORDER BY reason_code) AS reason_codes,
                   MAX(CASE WHEN production_blocking THEN 1 ELSE 0 END) AS production_blocking
              FROM mart_feature_exclusion_reason
             WHERE run_id = ?
             GROUP BY run_id, feature_table, feature_name
        ) r
          ON r.run_id = c.run_id
         AND r.feature_table = c.feature_table
         AND r.feature_name = c.feature_name
        """
    params: list[Any] = [run_id]
    if exclusion_exists:
        params.append(run_id)
    params.append(int(limit))
    query_rows = conn.execute(
        f"""
        SELECT c.feature_table,
               c.feature_name,
               c.feature_family,
               c.registry_status,
               c.model_input,
               c.production_ready,
               c.candidate_only,
               c.label,
               c.pit_risk_level,
               c.total_rows,
               c.non_null_rows,
               c.coverage_pct,
               c.source_event_date_column,
               c.source_available_date_column,
               c.allowed_in_production_research,
               {"j.join_policy" if join_exists else "NULL"} AS join_policy,
               {"j.production_blocking" if join_exists else "FALSE"} AS join_blocking,
               {"r.production_blocking" if exclusion_exists else "FALSE"} AS exclusion_blocking,
               {"r.reason_codes" if exclusion_exists else "NULL"} AS reason_codes
          FROM mart_feature_catalog_current c
          {join_sql}
          {reason_sql}
         WHERE c.run_id = ?
         ORDER BY
               CASE WHEN COALESCE({"j.production_blocking" if join_exists else "FALSE"}, FALSE)
                      OR COALESCE({"r.production_blocking" if exclusion_exists else "FALSE"}, FALSE)
                    THEN 0 ELSE 1 END,
               CASE c.pit_risk_level
                    WHEN 'critical' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                    ELSE 4
               END,
               c.coverage_pct ASC NULLS FIRST,
               c.feature_table,
               c.feature_name
         LIMIT ?
        """,
        tuple(params),
    ).fetchall()

    return {
        "run_id": run_id,
        "summary": dict(summary_row) if summary_row else {},
        "risk_counts": {str(row["pit_risk_level"]): int(row["n"] or 0) for row in risk_rows},
        "table_counts": {str(row["feature_table"]): int(row["n"] or 0) for row in table_rows},
        "rows": [
            {
                "feature_table": row["feature_table"],
                "feature_name": row["feature_name"],
                "feature_family": row["feature_family"],
                "registry_status": row["registry_status"],
                "model_input": bool(row["model_input"]) if row["model_input"] is not None else None,
                "production_ready": (
                    bool(row["production_ready"]) if row["production_ready"] is not None else None
                ),
                "candidate_only": bool(row["candidate_only"]) if row["candidate_only"] is not None else None,
                "label": bool(row["label"]) if row["label"] is not None else None,
                "pit_risk_level": row["pit_risk_level"],
                "total_rows": row["total_rows"],
                "non_null_rows": row["non_null_rows"],
                "coverage_pct": row["coverage_pct"],
                "source_event_date_column": row["source_event_date_column"],
                "source_available_date_column": row["source_available_date_column"],
                "allowed_in_production_research": bool(row["allowed_in_production_research"]),
                "join_policy": row["join_policy"],
                "production_blocking": bool(row["join_blocking"]) or bool(row["exclusion_blocking"]),
                "reason_codes": row["reason_codes"],
            }
            for row in query_rows
        ],
    }


def build_workbench_features(conn: Any, *, association_limit: int = 12) -> dict[str, Any]:
    registry = load_feature_registry()
    group_counts: dict[str, int] = {}
    production_ready = 0
    candidate_only = 0
    for spec in registry.features.values():
        group_counts[spec.group] = group_counts.get(spec.group, 0) + 1
        if spec.production_ready:
            production_ready += 1
        if spec.candidate_only:
            candidate_only += 1

    latest_validation = _latest_feature_panel_validation(conn)
    search_spaces = []
    if _table_exists(conn, "mart_feature_search_space_summary"):
        rows = conn.execute(
            """
            SELECT run_id, source_association_run_id, panel_table, label_name,
                   selected_count, excluded_count, selected_features_json,
                   group_counts_json, built_at
              FROM mart_feature_search_space_summary
             ORDER BY built_at DESC
             LIMIT 5
            """
        ).fetchall()
        search_spaces = [
            {
                "run_id": row["run_id"],
                "source_association_run_id": row["source_association_run_id"],
                "panel_table": row["panel_table"],
                "label_name": row["label_name"],
                "selected_count": row["selected_count"],
                "excluded_count": row["excluded_count"],
                "selected_features": _safe_json(row["selected_features_json"]) or [],
                "group_counts": _safe_json(row["group_counts_json"]) or {},
                "built_at": row["built_at"],
            }
            for row in rows
        ]

    top_associations = []
    if _table_exists(conn, "mart_feature_association_stat"):
        latest_run = _scalar(
            conn,
            """
            SELECT run_id
              FROM mart_feature_association_stat
             ORDER BY built_at DESC
             LIMIT 1
            """,
        )
        if latest_run:
            rows = conn.execute(
                """
                SELECT run_id, panel_table, label_name, feature_name,
                       feature_group, coverage_pct, rank_ic,
                       long_short_spread, source_fallback_pct, built_at
                  FROM mart_feature_association_stat
                 WHERE run_id = ?
                 ORDER BY ABS(COALESCE(rank_ic, 0)) DESC, coverage_pct DESC
                 LIMIT ?
                """,
                (latest_run, int(association_limit)),
            ).fetchall()
            top_associations = [
                {
                    "run_id": row["run_id"],
                    "panel_table": row["panel_table"],
                    "label_name": row["label_name"],
                    "feature_name": row["feature_name"],
                    "feature_group": row["feature_group"],
                    "coverage_pct": row["coverage_pct"],
                    "rank_ic": row["rank_ic"],
                    "long_short_spread": row["long_short_spread"],
                    "source_fallback_pct": row["source_fallback_pct"],
                    "built_at": row["built_at"],
                }
                for row in rows
            ]

    drift_mitigation_builds = []
    if _table_exists(conn, "mart_feature_drift_mitigation_panel_build"):
        rows = conn.execute(
            """
            SELECT run_id, output_feature_set_id, model_selection_run_id,
                   base_model_selection_run_id, base_table, root_cause_run_id,
                   transformed_features_json, copied_features_json,
                   selected_features_json, row_count, stock_count, date_count,
                   min_date, max_date, built_at
              FROM mart_feature_drift_mitigation_panel_build
             ORDER BY built_at DESC NULLS LAST, run_id DESC
             LIMIT 5
            """
        ).fetchall()
        drift_mitigation_builds = [
            {
                "run_id": row["run_id"],
                "output_feature_set_id": row["output_feature_set_id"],
                "model_selection_run_id": row["model_selection_run_id"],
                "base_model_selection_run_id": row["base_model_selection_run_id"],
                "base_table": row["base_table"],
                "root_cause_run_id": row["root_cause_run_id"],
                "transformed_features": _safe_json(row["transformed_features_json"]) or {},
                "copied_features": _safe_json(row["copied_features_json"]) or [],
                "selected_features": _safe_json(row["selected_features_json"]) or [],
                "row_count": row["row_count"],
                "stock_count": row["stock_count"],
                "date_count": row["date_count"],
                "min_date": row["min_date"],
                "max_date": row["max_date"],
                "built_at": row["built_at"],
            }
            for row in rows
        ]

    pit_coverage = []
    if _table_exists(conn, "mart_feature_pit_coverage_summary"):
        pit_coverage = [
            dict(row)
            for row in conn.execute(
                """
                SELECT audit_run_id, feature_set_id, feature_table, audit_scope,
                       total_columns, audited_columns, passed_columns,
                       failed_columns, unknown_blocking_columns,
                       missing_source_columns, not_applicable_columns,
                       high_risk_columns, critical_risk_columns, audited_at
                  FROM mart_feature_pit_coverage_summary
                 ORDER BY audited_at DESC NULLS LAST, audit_run_id DESC
                 LIMIT 5
                """
            ).fetchall()
        ]

    return {
        "registry": {
            "feature_count": len(registry.features),
            "model_input_count": len(registry.model_input_columns()),
            "label_count": len(registry.label_columns()),
            "production_ready_count": production_ready,
            "candidate_only_count": candidate_only,
            "group_counts": group_counts,
        },
        "latest_validation": latest_validation,
        "search_spaces": search_spaces,
        "availability_contract": _feature_availability_contract_view(conn),
        "feature_catalog": _feature_catalog_current_view(conn),
        "pit_coverage": pit_coverage,
        "drift_mitigation_builds": drift_mitigation_builds,
        "top_associations": top_associations,
        "feature_drift": _drift_offenders(conn, 12),
    }


def _architecture_cleanup_summary(conn: Any) -> dict[str, Any]:
    if not _table_exists(conn, "mart_architecture_inventory_asset"):
        return {"run_id": None, "classification_counts": {}, "cleanup_candidates": []}
    run_id = _latest_run_id(conn, "mart_architecture_inventory_asset")
    if not run_id:
        return {"run_id": None, "classification_counts": {}, "cleanup_candidates": []}
    counts = conn.execute(
        """
        SELECT classification, COUNT(*) AS n
          FROM mart_architecture_inventory_asset
         WHERE run_id = ?
         GROUP BY classification
        """,
        (run_id,),
    ).fetchall()
    candidates = conn.execute(
        """
        SELECT path, asset_type, module_area, classification, notes
          FROM mart_architecture_inventory_asset
         WHERE run_id = ?
           AND classification IN ('deprecated_pending_cleanup', 'delete_after_tests', 'compatibility_shim')
         ORDER BY classification, path
         LIMIT 20
        """,
        (run_id,),
    ).fetchall()
    return {
        "run_id": run_id,
        "classification_counts": {str(row["classification"]): int(row["n"]) for row in counts},
        "cleanup_candidates": [
            {
                "path": row["path"],
                "asset_type": row["asset_type"],
                "module_area": row["module_area"],
                "classification": row["classification"],
                "notes": row["notes"],
            }
            for row in candidates
        ],
    }


def _architecture_cleanup_plan_summary(conn: Any) -> dict[str, Any]:
    table = "mart_architecture_cleanup_plan"
    manifest_run_id = None
    manifest_perf: dict[str, Any] = {}
    if _table_exists(conn, "mart_pipeline_run_manifest"):
        manifest_cols = _columns(conn, "mart_pipeline_run_manifest")
        perf_expr = "perf_summary_json" if "perf_summary_json" in manifest_cols else "NULL AS perf_summary_json"
        started_expr = "CAST(started_at AS VARCHAR) AS started_at" if "started_at" in manifest_cols else "NULL AS started_at"
        manifest_row = conn.execute(
            f"""
            SELECT run_id, {perf_expr},
                   {started_expr}
              FROM mart_pipeline_run_manifest
             WHERE pipeline_name IN (
                   'plan_architecture_cleanup',
                   'import_architecture_cleanup_smoke',
                   'execute_architecture_cleanup'
             )
             ORDER BY started_at DESC
             LIMIT 1
            """
        ).fetchone()
        if manifest_row:
            manifest_run_id = manifest_row["run_id"]
            parsed = _safe_json(manifest_row["perf_summary_json"]) or {}
            manifest_perf = parsed if isinstance(parsed, dict) else {}
    if not _table_exists(conn, table) and not manifest_run_id:
        return {
            "run_id": None,
            "inventory_run_id": None,
            "candidate_count": 0,
            "status_counts": {},
            "action_counts": {},
            "smoke_counts": {},
            "candidates": [],
        }
    run_id = manifest_run_id or _latest_run_id(conn, table)
    if not run_id:
        return {
            "run_id": None,
            "inventory_run_id": None,
            "candidate_count": 0,
            "status_counts": {},
            "action_counts": {},
            "smoke_counts": {},
            "candidates": [],
        }
    if not _table_exists(conn, table):
        return {
            "run_id": run_id,
            "inventory_run_id": manifest_perf.get("inventory_run_id"),
            "candidate_count": int(manifest_perf.get("candidate_count") or 0),
            "status_counts": manifest_perf.get("status_counts") if isinstance(manifest_perf.get("status_counts"), dict) else {},
            "action_counts": manifest_perf.get("action_counts") if isinstance(manifest_perf.get("action_counts"), dict) else {},
            "smoke_counts": manifest_perf.get("smoke_counts") if isinstance(manifest_perf.get("smoke_counts"), dict) else {},
            "candidates": [],
        }
    status_counts = conn.execute(
        """
        SELECT status, COUNT(*) AS n
          FROM mart_architecture_cleanup_plan
         WHERE run_id = ?
         GROUP BY status
        """,
        (run_id,),
    ).fetchall()
    action_counts = conn.execute(
        """
        SELECT action, COUNT(*) AS n
          FROM mart_architecture_cleanup_plan
         WHERE run_id = ?
         GROUP BY action
        """,
        (run_id,),
    ).fetchall()
    smoke_counts = conn.execute(
        """
        SELECT COALESCE(smoke_status, 'none') AS smoke_status, COUNT(*) AS n
          FROM mart_architecture_cleanup_plan
         WHERE run_id = ?
         GROUP BY COALESCE(smoke_status, 'none')
        """,
        (run_id,),
    ).fetchall()
    rows = conn.execute(
        """
        SELECT inventory_run_id, asset_type, path, classification, action,
               status, reason, blockers_json, smoke_status, smoke_error
          FROM mart_architecture_cleanup_plan
         WHERE run_id = ?
         ORDER BY status, action, path
         LIMIT 30
        """,
        (run_id,),
    ).fetchall()
    inventory_run_id = rows[0]["inventory_run_id"] if rows else None
    if not rows and manifest_perf:
        return {
            "run_id": run_id,
            "inventory_run_id": manifest_perf.get("inventory_run_id"),
            "candidate_count": int(manifest_perf.get("candidate_count") or 0),
            "status_counts": manifest_perf.get("status_counts") if isinstance(manifest_perf.get("status_counts"), dict) else {},
            "action_counts": manifest_perf.get("action_counts") if isinstance(manifest_perf.get("action_counts"), dict) else {},
            "smoke_counts": manifest_perf.get("smoke_counts") if isinstance(manifest_perf.get("smoke_counts"), dict) else {},
            "candidates": [],
        }
    return {
        "run_id": run_id,
        "inventory_run_id": inventory_run_id,
        "candidate_count": len(rows),
        "status_counts": {str(row["status"]): int(row["n"]) for row in status_counts},
        "action_counts": {str(row["action"]): int(row["n"]) for row in action_counts},
        "smoke_counts": {str(row["smoke_status"]): int(row["n"]) for row in smoke_counts},
        "candidates": [
            {
                "asset_type": row["asset_type"],
                "path": row["path"],
                "classification": row["classification"],
                "action": row["action"],
                "status": row["status"],
                "reason": row["reason"],
                "blockers": _safe_json(row["blockers_json"]) or [],
                "smoke_status": row["smoke_status"],
                "smoke_error": row["smoke_error"],
            }
            for row in rows
        ],
    }


def build_workbench_storage(conn: Any, *, include_live_plan: bool = True) -> dict[str, Any]:
    latest_manifest = _storage_cleanup_summary(conn)
    retention = {
        "mode": "unavailable",
        "candidate_count": 0,
        "protected_model_count": 0,
        "active_optuna_study_count": 0,
        "compaction": {"recommended": False},
        "candidates": [],
        "error": None,
    }
    if include_live_plan:
        try:
            report = plan_storage_cleanup(conn, load_storage_retention_policy())
            retention = {
                "mode": report.get("mode"),
                "candidate_count": report.get("candidate_count", 0),
                "protected_model_count": len(report.get("protected_model_ids") or []),
                "protected_model_ids": report.get("protected_model_ids") or [],
                "active_optuna_study_count": report.get("active_optuna_study_count", 0),
                "compaction": report.get("compaction") or {"recommended": False},
                "candidates": (report.get("candidates") or [])[:20],
                "delete_policy": report.get("delete_policy"),
                "error": None,
            }
        except Exception as exc:  # pragma: no cover - defensive for partially migrated local DBs.
            retention["error"] = str(exc)
    return {
        "latest_manifest": latest_manifest,
        "retention": retention,
        "architecture": _architecture_cleanup_summary(conn),
        "architecture_cleanup": _architecture_cleanup_plan_summary(conn),
    }


def _temporal_synergy_research(
    conn: Any,
    *,
    relevance_limit: int = 15,
    synergy_limit: int = 15,
) -> dict[str, Any]:
    empty = {
        "run_id": None,
        "quality": None,
        "label_summary": [],
        "top_relevance": [],
        "top_synergies": [],
        "selected_interactions": [],
        "optuna_studies": [],
        "policy_candidates": [],
        "policy_gates": [],
        "redundancy_clusters": [],
        "conditional_synergies": [],
    }
    quality_table = "mart_temporal_research_panel_quality"
    relevance_table = "mart_feature_temporal_relevance"
    pair_table = "mart_feature_pair_synergy"
    candidate_table = "mart_feature_interaction_candidate"
    optuna_table = "mart_optuna_synergy_study_summary"
    policy_table = "mart_synergy_policy_candidate"
    gate_table = "mart_synergy_policy_gate"
    redundancy_table = "mart_feature_cluster_redundancy"
    conditional_table = "mart_feature_conditional_synergy"
    if not _table_exists(conn, quality_table):
        return empty
    run_id = _latest_run_id(conn, quality_table)
    if not run_id:
        return empty
    quality_cols = _columns(conn, quality_table)
    quality_row = conn.execute(
        f"""
        SELECT {_select_expr(quality_cols, "run_id")},
               {_select_expr(quality_cols, "source_panel_table")},
               {_select_expr(quality_cols, "feature_set_id")},
               {_select_expr(quality_cols, "source_available_date_column")},
               {_select_expr(quality_cols, "source_date_filter_applied", default="FALSE")},
               {_select_expr(quality_cols, "input_rows")},
               {_select_expr(quality_cols, "panel_rows")},
               {_select_expr(quality_cols, "dropped_future_source_rows")},
               {_select_expr(quality_cols, "stock_count")},
               {_select_expr(quality_cols, "min_signal_date")},
               {_select_expr(quality_cols, "max_signal_date")},
               {_select_expr(quality_cols, "feature_count")},
               {_select_expr(quality_cols, "label_count")},
               {_select_expr(quality_cols, "labels_json")},
               {_select_expr(quality_cols, "features_json")},
               {_cast_select_expr(quality_cols, "built_at")}
          FROM mart_temporal_research_panel_quality
         WHERE run_id = ?
         LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    quality = None
    if quality_row:
        quality = {
            "run_id": quality_row["run_id"],
            "source_panel_table": quality_row["source_panel_table"],
            "feature_set_id": quality_row["feature_set_id"],
            "source_available_date_column": quality_row["source_available_date_column"],
            "source_date_filter_applied": bool(quality_row["source_date_filter_applied"]),
            "input_rows": quality_row["input_rows"],
            "panel_rows": quality_row["panel_rows"],
            "dropped_future_source_rows": quality_row["dropped_future_source_rows"],
            "stock_count": quality_row["stock_count"],
            "min_signal_date": quality_row["min_signal_date"],
            "max_signal_date": quality_row["max_signal_date"],
            "feature_count": quality_row["feature_count"],
            "label_count": quality_row["label_count"],
            "labels": _safe_json(quality_row["labels_json"]) or [],
            "features": _safe_json(quality_row["features_json"]) or [],
            "built_at": quality_row["built_at"],
        }

    label_summary: list[dict[str, Any]] = []
    top_relevance: list[dict[str, Any]] = []
    if _table_exists(conn, relevance_table):
        rel_cols = _columns(conn, relevance_table)
        if {"run_id", "label_name", "feature_name"}.issubset(rel_cols):
            coverage_expr = "coverage_pct" if "coverage_pct" in rel_cols else "NULL"
            rank_ic_expr = "rank_ic" if "rank_ic" in rel_cols else "NULL"
            directional_spread_expr = "directional_spread" if "directional_spread" in rel_cols else "NULL"
            summary_rows = conn.execute(
                f"""
                SELECT label_name,
                       COUNT(*) AS feature_count,
                       AVG({coverage_expr}) AS avg_coverage_pct,
                       MAX(ABS(COALESCE({rank_ic_expr}, 0))) AS max_abs_rank_ic,
                       MAX(COALESCE({directional_spread_expr}, 0)) AS max_directional_spread
                  FROM mart_feature_temporal_relevance
                 WHERE run_id = ?
                 GROUP BY label_name
                 ORDER BY label_name
                """,
                (run_id,),
            ).fetchall()
            label_summary = [
                {
                    "label_name": row["label_name"],
                    "feature_count": int(row["feature_count"] or 0),
                    "avg_coverage_pct": row["avg_coverage_pct"],
                    "max_abs_rank_ic": row["max_abs_rank_ic"],
                    "max_directional_spread": row["max_directional_spread"],
                }
                for row in summary_rows
            ]
            rel_rows = conn.execute(
                f"""
                SELECT {_select_expr(rel_cols, "label_name")},
                       {_select_expr(rel_cols, "horizon_days")},
                       {_select_expr(rel_cols, "feature_name")},
                       {_select_expr(rel_cols, "coverage_pct")},
                       {_select_expr(rel_cols, "rank_ic")},
                       {_select_expr(rel_cols, "directional_spread")},
                       {_select_expr(rel_cols, "stability_score")},
                       {_select_expr(rel_cols, "long_short_spread")},
                       {_select_expr(rel_cols, "daily_count")}
                  FROM mart_feature_temporal_relevance
                 WHERE run_id = ?
                 ORDER BY ABS(COALESCE(rank_ic, 0)) DESC,
                          ABS(COALESCE(directional_spread, 0)) DESC,
                          feature_name
                 LIMIT ?
                """,
                (run_id, int(relevance_limit)),
            ).fetchall()
            top_relevance = [
                {
                    "label_name": row["label_name"],
                    "horizon_days": row["horizon_days"],
                    "feature_name": row["feature_name"],
                    "coverage_pct": row["coverage_pct"],
                    "rank_ic": row["rank_ic"],
                    "directional_spread": row["directional_spread"],
                    "stability_score": row["stability_score"],
                    "long_short_spread": row["long_short_spread"],
                    "daily_count": row["daily_count"],
                }
                for row in rel_rows
            ]

    top_synergies: list[dict[str, Any]] = []
    if _table_exists(conn, pair_table):
        pair_cols = _columns(conn, pair_table)
        if {"run_id", "label_name", "feature_a", "feature_b"}.issubset(pair_cols):
            pair_rows = conn.execute(
                f"""
                SELECT {_select_expr(pair_cols, "label_name")},
                       {_select_expr(pair_cols, "horizon_days")},
                       {_select_expr(pair_cols, "feature_a")},
                       {_select_expr(pair_cols, "feature_b")},
                       {_select_expr(pair_cols, "joint_uplift")},
                       {_select_expr(pair_cols, "interaction_score")},
                       {_select_expr(pair_cols, "joint_obs_count")},
                       {_select_expr(pair_cols, "feature_corr")},
                       {_select_expr(pair_cols, "joint_active_label_mean")},
                       {_select_expr(pair_cols, "best_standalone_label_mean")}
                  FROM mart_feature_pair_synergy
                 WHERE run_id = ?
                 ORDER BY interaction_score DESC NULLS LAST,
                          joint_uplift DESC NULLS LAST,
                          feature_a,
                          feature_b
                 LIMIT ?
                """,
                (run_id, int(synergy_limit)),
            ).fetchall()
            top_synergies = [
                {
                    "label_name": row["label_name"],
                    "horizon_days": row["horizon_days"],
                    "feature_a": row["feature_a"],
                    "feature_b": row["feature_b"],
                    "joint_uplift": row["joint_uplift"],
                    "interaction_score": row["interaction_score"],
                    "joint_obs_count": row["joint_obs_count"],
                    "feature_corr": row["feature_corr"],
                    "joint_active_label_mean": row["joint_active_label_mean"],
                    "best_standalone_label_mean": row["best_standalone_label_mean"],
                }
                for row in pair_rows
            ]

    selected_interactions: list[dict[str, Any]] = []
    if _table_exists(conn, candidate_table):
        candidate_cols = _columns(conn, candidate_table)
        if {"run_id", "label_name", "feature_a", "feature_b", "selected"}.issubset(candidate_cols):
            candidate_rows = conn.execute(
                f"""
                SELECT {_select_expr(candidate_cols, "label_name")},
                       {_select_expr(candidate_cols, "horizon_days")},
                       {_select_expr(candidate_cols, "feature_a")},
                       {_select_expr(candidate_cols, "feature_b")},
                       {_select_expr(candidate_cols, "selected")},
                       {_select_expr(candidate_cols, "selection_reason")},
                       {_select_expr(candidate_cols, "joint_uplift")},
                       {_select_expr(candidate_cols, "interaction_score")},
                       {_select_expr(candidate_cols, "joint_obs_count")}
                  FROM mart_feature_interaction_candidate
                 WHERE run_id = ?
                   AND selected = TRUE
                 ORDER BY interaction_score DESC NULLS LAST,
                          joint_uplift DESC NULLS LAST,
                          feature_a,
                          feature_b
                 LIMIT ?
                """,
                (run_id, int(synergy_limit)),
            ).fetchall()
            selected_interactions = [
                {
                    "label_name": row["label_name"],
                    "horizon_days": row["horizon_days"],
                    "feature_a": row["feature_a"],
                    "feature_b": row["feature_b"],
                    "selected": bool(row["selected"]),
                    "selection_reason": row["selection_reason"],
                    "joint_uplift": row["joint_uplift"],
                    "interaction_score": row["interaction_score"],
                    "joint_obs_count": row["joint_obs_count"],
                }
                for row in candidate_rows
            ]

    optuna_studies: list[dict[str, Any]] = []
    if _table_exists(conn, optuna_table):
        optuna_cols = _columns(conn, optuna_table)
        if {"run_id", "source_run_id", "label_name"}.issubset(optuna_cols):
            optuna_rows = conn.execute(
                f"""
                SELECT {_select_expr(optuna_cols, "run_id")},
                       {_select_expr(optuna_cols, "source_run_id")},
                       {_select_expr(optuna_cols, "label_name")},
                       {_select_expr(optuna_cols, "best_trial_number")},
                       {_select_expr(optuna_cols, "objective_score")},
                       {_select_expr(optuna_cols, "trials")},
                       {_select_expr(optuna_cols, "study_total_trials")},
                       {_select_expr(optuna_cols, "selected_features_json")},
                       {_select_expr(optuna_cols, "selected_interactions_json")},
                       {_select_expr(optuna_cols, "config_json")},
                       {_cast_select_expr(optuna_cols, "built_at")}
                  FROM mart_optuna_synergy_study_summary
                 WHERE source_run_id = ?
                 ORDER BY built_at DESC NULLS LAST, run_id DESC
                 LIMIT 8
                """,
                (run_id,),
            ).fetchall()
            for row in optuna_rows:
                config = _safe_json(row["config_json"]) or {}
                optuna_studies.append(
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

    policy_candidates: list[dict[str, Any]] = []
    if _table_exists(conn, policy_table):
        policy_cols = _columns(conn, policy_table)
        if {"run_id", "source_run_id", "label_name", "gate_status"}.issubset(policy_cols):
            policy_rows = conn.execute(
                f"""
                SELECT {_select_expr(policy_cols, "run_id")},
                       {_select_expr(policy_cols, "source_run_id")},
                       {_select_expr(policy_cols, "label_name")},
                       {_select_expr(policy_cols, "objective_score")},
                       {_select_expr(policy_cols, "selected_features_json")},
                       {_select_expr(policy_cols, "selected_interactions_json")},
                       {_select_expr(policy_cols, "gate_status")},
                       {_select_expr(policy_cols, "notes_json")},
                       {_cast_select_expr(policy_cols, "built_at")}
                  FROM mart_synergy_policy_candidate
                 WHERE source_run_id = ?
                 ORDER BY built_at DESC NULLS LAST, run_id DESC
                 LIMIT 8
                """,
                (run_id,),
            ).fetchall()
            policy_candidates = [
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
                for row in policy_rows
            ]

    policy_gates: list[dict[str, Any]] = []
    if _table_exists(conn, gate_table):
        gate_cols = _columns(conn, gate_table)
        if {"run_id", "source_run_id", "candidate_run_id", "validation_status"}.issubset(gate_cols):
            gate_rows = conn.execute(
                f"""
                SELECT {_select_expr(gate_cols, "run_id")},
                       {_select_expr(gate_cols, "candidate_run_id")},
                       {_select_expr(gate_cols, "source_run_id")},
                       {_select_expr(gate_cols, "label_name")},
                       {_select_expr(gate_cols, "baseline_horizon_days")},
                       {_select_expr(gate_cols, "candidate_horizon_days")},
                       {_select_expr(gate_cols, "validation_status")},
                       {_select_expr(gate_cols, "promotion_status")},
                       {_select_expr(gate_cols, "production_eligible")},
                       {_select_expr(gate_cols, "fold_count")},
                       {_select_expr(gate_cols, "avg_rank_ic")},
                       {_select_expr(gate_cols, "std_rank_ic")},
                       {_select_expr(gate_cols, "avg_top_excess_return")},
                       {_select_expr(gate_cols, "worst_top_excess_return")},
                       {_select_expr(gate_cols, "avg_top_hit_rate")},
                       {_select_expr(gate_cols, "worst_max_drawdown")},
                       {_select_expr(gate_cols, "avg_turnover")},
                       {_select_expr(gate_cols, "avg_cost_adjusted_top_excess_return")},
                       {_select_expr(gate_cols, "worst_cost_adjusted_top_excess_return")},
                       {_select_expr(gate_cols, "transaction_cost_bps")},
                       {_select_expr(gate_cols, "blockers_json")},
                       {_cast_select_expr(gate_cols, "built_at")}
                  FROM mart_synergy_policy_gate
                 WHERE source_run_id = ?
                 ORDER BY built_at DESC NULLS LAST, run_id DESC
                 LIMIT 8
                """,
                (run_id,),
            ).fetchall()
            policy_gates = [
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
                    "built_at": row["built_at"],
                }
                for row in gate_rows
            ]

    redundancy_clusters: list[dict[str, Any]] = []
    if _table_exists(conn, redundancy_table):
        redundancy_cols = _columns(conn, redundancy_table)
        if {"run_id", "source_run_id", "cluster_id", "feature_name"}.issubset(redundancy_cols):
            redundancy_run = conn.execute(
                """
                SELECT run_id
                  FROM mart_feature_cluster_redundancy
                 WHERE source_run_id = ?
                 ORDER BY built_at DESC NULLS LAST, run_id DESC
                 LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if redundancy_run:
                redundancy_rows = conn.execute(
                    """
                    SELECT run_id,
                           cluster_id,
                           representative_feature,
                           cluster_size,
                           MAX(max_abs_corr_in_cluster) AS max_abs_corr_in_cluster,
                           STRING_AGG(feature_name, ', ' ORDER BY redundancy_status, feature_name) AS members,
                           CAST(MAX(built_at) AS VARCHAR) AS built_at
                      FROM mart_feature_cluster_redundancy
                     WHERE run_id = ?
                     GROUP BY run_id, cluster_id, representative_feature, cluster_size
                     ORDER BY cluster_size DESC, max_abs_corr_in_cluster DESC, cluster_id
                     LIMIT 12
                    """,
                    (redundancy_run["run_id"],),
                ).fetchall()
                redundancy_clusters = [
                    {
                        "run_id": row["run_id"],
                        "cluster_id": row["cluster_id"],
                        "representative_feature": row["representative_feature"],
                        "cluster_size": row["cluster_size"],
                        "max_abs_corr_in_cluster": row["max_abs_corr_in_cluster"],
                        "members": row["members"],
                        "built_at": row["built_at"],
                    }
                    for row in redundancy_rows
                ]

    conditional_synergies: list[dict[str, Any]] = []
    if _table_exists(conn, conditional_table):
        conditional_cols = _columns(conn, conditional_table)
        if {"run_id", "label_name", "condition_feature", "response_feature"}.issubset(conditional_cols):
            conditional_rows = conn.execute(
                f"""
                SELECT {_select_expr(conditional_cols, "label_name")},
                       {_select_expr(conditional_cols, "horizon_days")},
                       {_select_expr(conditional_cols, "condition_feature")},
                       {_select_expr(conditional_cols, "response_feature")},
                       {_select_expr(conditional_cols, "incremental_uplift")},
                       {_select_expr(conditional_cols, "conditional_response_uplift")},
                       {_select_expr(conditional_cols, "response_uplift")},
                       {_select_expr(conditional_cols, "interaction_score")},
                       {_select_expr(conditional_cols, "conditional_response_obs_count")},
                       {_select_expr(conditional_cols, "feature_corr")},
                       {_select_expr(conditional_cols, "selected")},
                       {_select_expr(conditional_cols, "selection_reason")}
                  FROM mart_feature_conditional_synergy
                 WHERE run_id = ?
                 ORDER BY interaction_score DESC NULLS LAST,
                          incremental_uplift DESC NULLS LAST,
                          condition_feature,
                          response_feature
                 LIMIT ?
                """,
                (run_id, int(synergy_limit)),
            ).fetchall()
            conditional_synergies = [
                {
                    "label_name": row["label_name"],
                    "horizon_days": row["horizon_days"],
                    "condition_feature": row["condition_feature"],
                    "response_feature": row["response_feature"],
                    "incremental_uplift": row["incremental_uplift"],
                    "conditional_response_uplift": row["conditional_response_uplift"],
                    "response_uplift": row["response_uplift"],
                    "interaction_score": row["interaction_score"],
                    "conditional_response_obs_count": row["conditional_response_obs_count"],
                    "feature_corr": row["feature_corr"],
                    "selected": bool(row["selected"]),
                    "selection_reason": row["selection_reason"],
                }
                for row in conditional_rows
            ]

    return {
        "run_id": run_id,
        "quality": quality,
        "label_summary": label_summary,
        "top_relevance": top_relevance,
        "top_synergies": top_synergies,
        "selected_interactions": selected_interactions,
        "optuna_studies": optuna_studies,
        "policy_candidates": policy_candidates,
        "policy_gates": policy_gates,
        "redundancy_clusters": redundancy_clusters,
        "conditional_synergies": conditional_synergies,
    }


def _latest_recommendation_key(conn: Any, *, primary_only: bool = True) -> dict[str, Any] | None:
    if not _table_exists(conn, "mart_daily_recommendation"):
        return None
    cols = _columns(conn, "mart_daily_recommendation")
    primary_filter = "WHERE is_primary = TRUE" if primary_only and "is_primary" in cols else ""
    champion_model_id = _current_champion_model_id(conn)
    champion_order = "CASE WHEN model_id = ? THEN 0 ELSE 1 END," if champion_model_id else ""
    params: tuple[Any, ...] = (champion_model_id,) if champion_model_id else ()
    latest_built_expr = "MAX(CAST(built_at AS VARCHAR)) AS latest_built_at" if "built_at" in cols else "NULL AS latest_built_at"
    row = conn.execute(
        f"""
        SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date, model_id,
               COUNT(*) AS n, {latest_built_expr}
          FROM mart_daily_recommendation
         {primary_filter}
         GROUP BY CAST(snapshot_date AS VARCHAR), model_id
         ORDER BY snapshot_date DESC, {champion_order} latest_built_at DESC, model_id
         LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        return None
    return {"snapshot_date": row["snapshot_date"], "model_id": row["model_id"], "count": int(row["n"])}


def _recommendation_rows(conn: Any, key: dict[str, Any], *, limit: int = 50) -> list[dict[str, Any]]:
    if not key:
        return []
    table = "mart_daily_topk_view_cache" if _table_exists(conn, "mart_daily_topk_view_cache") else "mart_daily_recommendation"
    if not _table_exists(conn, table):
        return []
    cols = _columns(conn, table)
    primary_filter = "AND is_primary = TRUE" if "is_primary" in cols else ""
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "stock_code")},
               {_select_expr(cols, "stock_name")},
               {_select_expr(cols, "xueqiu_symbol")},
               {_select_expr(cols, "tdx_l1_name")},
               {_select_expr(cols, "tdx_l2_name")},
               {_select_expr(cols, "rank_in_date")},
               {_select_expr(cols, "pred_score")},
               {_select_expr(cols, "percentile")},
               {_select_expr(cols, "regime_flag")},
               {_select_expr(cols, "track_id")},
               {_select_expr(cols, "run_mode")},
               {_select_expr(cols, "baseline_horizon_days", default="60")},
               {_select_expr(cols, "selected_horizon_days", default="60")},
               {_select_expr(cols, "selected_horizon_confidence")},
               {_select_expr(cols, "horizon_selection_run_id")},
               {_select_expr(cols, "key_features_json")},
               {_select_expr(cols, "built_at")}
          FROM {table}
         WHERE CAST(snapshot_date AS VARCHAR) = ?
           AND model_id = ?
           {primary_filter}
         ORDER BY {"rank_in_date" if "rank_in_date" in cols else "stock_code"}
         LIMIT ?
        """,
        (key["snapshot_date"], key["model_id"], int(limit)),
    ).fetchall()
    if not rows and table != "mart_daily_recommendation" and _table_exists(conn, "mart_daily_recommendation"):
        return _recommendation_rows_from_table(conn, key, "mart_daily_recommendation", limit=limit)
    return [_recommendation_row_dict(row) for row in rows]


def _recommendation_rows_from_table(
    conn: Any,
    key: dict[str, Any],
    table: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    cols = _columns(conn, table)
    primary_filter = "AND is_primary = TRUE" if "is_primary" in cols else ""
    rows = conn.execute(
        f"""
        SELECT {_select_expr(cols, "stock_code")},
               {_select_expr(cols, "stock_name")},
               {_select_expr(cols, "xueqiu_symbol")},
               {_select_expr(cols, "tdx_l1_name")},
               {_select_expr(cols, "tdx_l2_name")},
               {_select_expr(cols, "rank_in_date")},
               {_select_expr(cols, "pred_score")},
               {_select_expr(cols, "percentile")},
               {_select_expr(cols, "regime_flag")},
               {_select_expr(cols, "track_id")},
               {_select_expr(cols, "run_mode")},
               {_select_expr(cols, "baseline_horizon_days", default="60")},
               {_select_expr(cols, "selected_horizon_days", default="60")},
               {_select_expr(cols, "selected_horizon_confidence")},
               {_select_expr(cols, "horizon_selection_run_id")},
               {_select_expr(cols, "key_features_json")},
               {_select_expr(cols, "built_at")}
          FROM {table}
         WHERE CAST(snapshot_date AS VARCHAR) = ?
           AND model_id = ?
           {primary_filter}
         ORDER BY {"rank_in_date" if "rank_in_date" in cols else "stock_code"}
         LIMIT ?
        """,
        (key["snapshot_date"], key["model_id"], int(limit)),
    ).fetchall()
    return [_recommendation_row_dict(row) for row in rows]


def _recommendation_row_dict(row: Any) -> dict[str, Any]:
    key_features = _safe_json(row["key_features_json"]) or {}
    top_features = key_features.get("model_top_features") if isinstance(key_features, dict) else []
    stock_feature_values = key_features.get("stock_feature_values") if isinstance(key_features, dict) else []
    stock_feature_contributions = (
        key_features.get("stock_feature_contributions") if isinstance(key_features, dict) else []
    )
    if isinstance(top_features, list):
        top_features = [
            item.get("name") if isinstance(item, dict) else str(item)
            for item in top_features[:5]
        ]
    else:
        top_features = []
    if isinstance(stock_feature_values, list):
        stock_feature_values = [
            item
            for item in stock_feature_values[:5]
            if isinstance(item, dict)
        ]
    else:
        stock_feature_values = []
    if isinstance(stock_feature_contributions, list):
        stock_feature_contributions = [
            item
            for item in stock_feature_contributions[:5]
            if isinstance(item, dict)
        ]
    else:
        stock_feature_contributions = []
    return {
        "stock_code": row["stock_code"],
        "stock_name": row["stock_name"],
        "xueqiu_symbol": row["xueqiu_symbol"],
        "tdx_l1_name": row["tdx_l1_name"],
        "tdx_l2_name": row["tdx_l2_name"],
        "rank_in_date": row["rank_in_date"],
        "pred_score": row["pred_score"],
        "percentile": row["percentile"],
        "regime_flag": row["regime_flag"],
        "track_id": row["track_id"],
        "run_mode": row["run_mode"],
        "baseline_horizon_days": row["baseline_horizon_days"],
        "selected_horizon_days": row["selected_horizon_days"],
        "selected_horizon_confidence": row["selected_horizon_confidence"],
        "horizon_selection_run_id": row["horizon_selection_run_id"],
        "top_features": top_features,
        "top_feature_values": stock_feature_values,
        "top_feature_contributions": stock_feature_contributions,
        "explanation_status": (
            key_features.get("explanation_status") if isinstance(key_features, dict) else None
        ),
        "base_value": key_features.get("base_value") if isinstance(key_features, dict) else None,
        "additivity_error": key_features.get("additivity_error") if isinstance(key_features, dict) else None,
        "built_at": row["built_at"],
    }


def _recommendation_risk(conn: Any, key: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not key or not _table_exists(conn, "mart_daily_recommendation_risk"):
        return []
    rows = conn.execute(
        """
        SELECT CAST(snapshot_date AS VARCHAR) AS snapshot_date,
               model_id, track_id, is_primary, top_size, top1_industry,
               top1_industry_share, top3_industry_share,
               top20_amount_ma20_p25, top20_amount_ma20_median,
               overlap_with_primary, built_at
          FROM mart_daily_recommendation_risk
         WHERE CAST(snapshot_date AS VARCHAR) = ?
         ORDER BY is_primary DESC, top_size, track_id
         LIMIT 20
        """,
        (key["snapshot_date"],),
    ).fetchall()
    return [
        {
            "snapshot_date": row["snapshot_date"],
            "model_id": row["model_id"],
            "track_id": row["track_id"],
            "is_primary": bool(row["is_primary"]),
            "top_size": row["top_size"],
            "top1_industry": row["top1_industry"],
            "top1_industry_share": row["top1_industry_share"],
            "top3_industry_share": row["top3_industry_share"],
            "top20_amount_ma20_p25": row["top20_amount_ma20_p25"],
            "top20_amount_ma20_median": row["top20_amount_ma20_median"],
            "overlap_with_primary": row["overlap_with_primary"],
            "built_at": row["built_at"],
        }
        for row in rows
    ]


def _recommendation_outcomes(conn: Any, key: dict[str, Any] | None) -> dict[str, Any]:
    if not key or not _table_exists(conn, "mart_prediction_outcome"):
        return {"count": 0}
    row = conn.execute(
        """
        SELECT COUNT(*) AS n,
               AVG(ret_5d) AS avg_ret_5d,
               AVG(ret_10d) AS avg_ret_10d,
               AVG(ret_30d) AS avg_ret_30d,
               AVG(CASE WHEN hit_5d THEN 1.0 WHEN hit_5d IS NULL THEN NULL ELSE 0.0 END) AS hit_rate_5d,
               AVG(CASE WHEN hit_30d THEN 1.0 WHEN hit_30d IS NULL THEN NULL ELSE 0.0 END) AS hit_rate_30d,
               MAX(CAST(outcome_known_at AS VARCHAR)) AS latest_outcome_known_at
          FROM mart_prediction_outcome
         WHERE CAST(snapshot_date AS VARCHAR) = ?
           AND model_id = ?
        """,
        (key["snapshot_date"], key["model_id"]),
    ).fetchone()
    if not row:
        return {"count": 0}
    return {
        "count": int(row["n"] or 0),
        "avg_ret_5d": row["avg_ret_5d"],
        "avg_ret_10d": row["avg_ret_10d"],
        "avg_ret_30d": row["avg_ret_30d"],
        "hit_rate_5d": row["hit_rate_5d"],
        "hit_rate_30d": row["hit_rate_30d"],
        "latest_outcome_known_at": row["latest_outcome_known_at"],
    }


def build_workbench_recommendations(conn: Any, *, limit: int = 50) -> dict[str, Any]:
    key = _latest_recommendation_key(conn, primary_only=True)
    rows = _recommendation_rows(conn, key, limit=limit) if key else []
    validation = _latest_feature_panel_validation(conn)
    data_sources = build_workbench_data_sources(conn, limit=20)
    kline = data_sources.get("kline") or {}
    return {
        "latest_primary": key or {"snapshot_date": None, "model_id": None, "count": 0},
        "rows": rows,
        "risk": _recommendation_risk(conn, key),
        "outcomes": _recommendation_outcomes(conn, key),
        "source_quality": {
            "kline_primary": (kline.get("primary") or {}).get("source_name"),
            "kline_primary_is_tdxhub": kline.get("primary_is_tdxhub"),
            "source_fallback_ratio": (validation or {}).get("source_fallback_ratio"),
            "source_lineage_coverage": (validation or {}).get("source_lineage_coverage"),
            "feature_validation_id": (validation or {}).get("validation_id"),
            "feature_validation_status": (validation or {}).get("status"),
        },
    }


def _shareholder_plan_family_eval(conn: Any, *, limit: int = 12) -> dict[str, Any]:
    table = "mart_shareholder_plan_feature_family_eval"
    empty = {
        "run_id": None,
        "summary": {},
        "family_summary": [],
        "top_effects": [],
        "paired_advantages": [],
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
               MAX(total_rows) AS panel_rows,
               MAX(built_at) AS built_at
          FROM mart_shareholder_plan_feature_family_eval
         WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    summary = {
        "row_count": int(row["row_count"] or 0) if row else 0,
        "source_family_count": int(row["source_family_count"] or 0) if row else 0,
        "feature_count": int(row["feature_count"] or 0) if row else 0,
        "label_count": int(row["label_count"] or 0) if row else 0,
        "panel_rows": int(row["panel_rows"] or 0) if row else 0,
        "built_at": row["built_at"] if row else None,
    }
    family_rows = conn.execute(
        """
        SELECT source_family,
               label_name,
               COUNT(*) AS feature_count,
               MAX(total_rows) AS panel_rows,
               AVG(nondefault_pct) AS avg_nondefault_pct,
               MAX(ABS(COALESCE(rank_ic, 0))) AS max_abs_rank_ic,
               MAX(ABS(COALESCE(active_inactive_label_spread, 0))) AS max_abs_spread,
               AVG(CASE WHEN active_inactive_label_spread > 0 THEN 1.0 ELSE 0.0 END) AS positive_spread_share
          FROM mart_shareholder_plan_feature_family_eval
         WHERE run_id = ?
         GROUP BY source_family, label_name
         ORDER BY label_name, source_family
        """,
        (run_id,),
    ).fetchall()
    family_summary = [
        {
            "source_family": item["source_family"],
            "label_name": item["label_name"],
            "feature_count": int(item["feature_count"] or 0),
            "panel_rows": int(item["panel_rows"] or 0),
            "avg_nondefault_pct": item["avg_nondefault_pct"],
            "max_abs_rank_ic": item["max_abs_rank_ic"],
            "max_abs_spread": item["max_abs_spread"],
            "positive_spread_share": item["positive_spread_share"],
        }
        for item in family_rows
    ]
    top_rows = conn.execute(
        """
        SELECT source_family, source_table, feature_name, feature_purpose, label_name,
               window_days, valid_rows, nondefault_pct, event_rows,
               distinct_event_stocks, ic, rank_ic, daily_rank_ic_count,
               positive_rank_ic_share, label_mean_when_active,
               label_mean_when_inactive, active_inactive_label_spread, built_at
          FROM mart_shareholder_plan_feature_family_eval
         WHERE run_id = ?
         ORDER BY ABS(COALESCE(active_inactive_label_spread, 0)) DESC,
                  ABS(COALESCE(rank_ic, 0)) DESC,
                  source_family,
                  feature_name
         LIMIT ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    top_effects = [
        {
            "source_family": item["source_family"],
            "source_table": item["source_table"],
            "feature_name": item["feature_name"],
            "feature_purpose": item["feature_purpose"],
            "label_name": item["label_name"],
            "window_days": item["window_days"],
            "valid_rows": int(item["valid_rows"] or 0),
            "nondefault_pct": item["nondefault_pct"],
            "event_rows": int(item["event_rows"] or 0),
            "distinct_event_stocks": int(item["distinct_event_stocks"] or 0),
            "ic": item["ic"],
            "rank_ic": item["rank_ic"],
            "daily_rank_ic_count": int(item["daily_rank_ic_count"] or 0),
            "positive_rank_ic_share": item["positive_rank_ic_share"],
            "label_mean_when_active": item["label_mean_when_active"],
            "label_mean_when_inactive": item["label_mean_when_inactive"],
            "active_inactive_label_spread": item["active_inactive_label_spread"],
            "built_at": item["built_at"],
        }
        for item in top_rows
    ]
    paired_rows = conn.execute(
        """
        WITH paired AS (
            SELECT feature_name,
                   label_name,
                   MAX(CASE WHEN source_family = 'latest_state' THEN rank_ic END) AS latest_rank_ic,
                   MAX(CASE WHEN source_family = 'initial_event' THEN rank_ic END) AS initial_rank_ic,
                   MAX(CASE WHEN source_family = 'latest_state' THEN active_inactive_label_spread END) AS latest_spread,
                   MAX(CASE WHEN source_family = 'initial_event' THEN active_inactive_label_spread END) AS initial_spread,
                   MAX(CASE WHEN source_family = 'latest_state' THEN nondefault_pct END) AS latest_nondefault_pct,
                   MAX(CASE WHEN source_family = 'initial_event' THEN nondefault_pct END) AS initial_nondefault_pct
              FROM mart_shareholder_plan_feature_family_eval
             WHERE run_id = ?
               AND feature_name != 'shareholder_plan_completed_count_180d'
             GROUP BY feature_name, label_name
        )
        SELECT feature_name,
               label_name,
               latest_rank_ic,
               initial_rank_ic,
               latest_spread,
               initial_spread,
               ABS(COALESCE(initial_spread, 0)) - ABS(COALESCE(latest_spread, 0)) AS abs_spread_advantage,
               latest_nondefault_pct,
               initial_nondefault_pct
          FROM paired
         WHERE latest_spread IS NOT NULL
           AND initial_spread IS NOT NULL
         ORDER BY abs_spread_advantage DESC, feature_name, label_name
         LIMIT ?
        """,
        (run_id, int(limit)),
    ).fetchall()
    paired_advantages = [
        {
            "feature_name": item["feature_name"],
            "label_name": item["label_name"],
            "latest_rank_ic": item["latest_rank_ic"],
            "initial_rank_ic": item["initial_rank_ic"],
            "latest_spread": item["latest_spread"],
            "initial_spread": item["initial_spread"],
            "abs_spread_advantage": item["abs_spread_advantage"],
            "latest_nondefault_pct": item["latest_nondefault_pct"],
            "initial_nondefault_pct": item["initial_nondefault_pct"],
        }
        for item in paired_rows
    ]
    return {
        "run_id": run_id,
        "summary": summary,
        "family_summary": family_summary,
        "top_effects": top_effects,
        "paired_advantages": paired_advantages,
    }


def build_workbench_research(conn: Any, *, task_limit: int = 20, study_limit: int = 12) -> dict[str, Any]:
    schedule_run_id = _latest_run_id(conn, "mart_research_schedule_plan")
    tasks = []
    if schedule_run_id and _table_exists(conn, "mart_research_schedule_plan"):
        cols = _columns(conn, "mart_research_schedule_plan")
        rows = conn.execute(
            f"""
            SELECT {_select_expr(cols, "task_id")},
                   {_select_expr(cols, "task_type")},
                   {_select_expr(cols, "priority", default="999999")},
                   {_select_expr(cols, "status")},
                   {_select_expr(cols, "enabled", default="TRUE")},
                   {_select_expr(cols, "evidence_table")},
                   {_select_expr(cols, "evidence_run_id")},
                   {_select_expr(cols, "evidence_found", default="FALSE")},
                   {_select_expr(cols, "evidence_status")},
                   {_select_expr(cols, "reason")},
                   {_select_expr(cols, "command_text")}
              FROM mart_research_schedule_plan
             WHERE run_id = ?
             ORDER BY priority, task_id
             LIMIT ?
            """,
            (schedule_run_id, int(task_limit)),
        ).fetchall()
        tasks = [
            {
                "task_id": row["task_id"],
                "task_type": row["task_type"],
                "priority": row["priority"],
                "status": row["status"],
                "enabled": bool(row["enabled"]),
                "evidence_table": row["evidence_table"],
                "evidence_run_id": row["evidence_run_id"],
                "evidence_found": bool(row["evidence_found"]),
                "evidence_status": row["evidence_status"],
                "reason": row["reason"],
                "command_text": row["command_text"],
            }
            for row in rows
        ]

    studies = []
    if _table_exists(conn, "mart_model_stability_search_summary"):
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

    ranker_profiles = []
    ranker_policy = {"run_id": None, "ranker_policy_deferred": 0, "policy": {}}
    if _table_exists(conn, "mart_pipeline_run_manifest"):
        policy_row = None
        if schedule_run_id:
            policy_row = conn.execute(
                """
                SELECT run_id, perf_summary_json,
                       CAST(started_at AS VARCHAR) AS started_at
                  FROM mart_pipeline_run_manifest
                 WHERE pipeline_name = 'plan_research_schedule'
                   AND run_id = ?
                 LIMIT 1
                """,
                (schedule_run_id,),
            ).fetchone()
        if not policy_row:
            policy_row = conn.execute(
                """
                SELECT run_id, perf_summary_json,
                       CAST(started_at AS VARCHAR) AS started_at
                  FROM mart_pipeline_run_manifest
                 WHERE pipeline_name = 'plan_research_schedule'
                 ORDER BY started_at DESC
                 LIMIT 1
                """
            ).fetchone()
        if policy_row:
            policy_perf = _safe_json(policy_row["perf_summary_json"]) or {}
            if isinstance(policy_perf, dict):
                ranker_policy = {
                    "run_id": policy_row["run_id"],
                    "started_at": policy_row["started_at"],
                    "ranker_policy_deferred": int(policy_perf.get("ranker_policy_deferred") or 0),
                    "policy": policy_perf.get("ranker_policy") if isinstance(policy_perf.get("ranker_policy"), dict) else {},
                }
        rows = conn.execute(
            """
            SELECT run_id, duration_s, perf_summary_json,
                   CAST(started_at AS VARCHAR) AS started_at
              FROM mart_pipeline_run_manifest
             WHERE pipeline_name = 'run_optuna_model_stability_search'
             ORDER BY started_at DESC
            LIMIT 12
            """
        ).fetchall()
        parsed_rows = []
        regression_per_trial_s = None
        for row in rows:
            perf = _safe_json(row["perf_summary_json"]) or {}
            if not isinstance(perf, dict):
                perf = {}
            summary = _runtime_profile_summary(perf, duration_s=row["duration_s"])
            parsed_rows.append((row, perf, summary))
            if perf.get("model_family") == "lightgbm" and regression_per_trial_s is None:
                regression_per_trial_s = summary.get("duration_per_trial_s")

        for row, perf, _summary in parsed_rows:
            cache = perf.get("ranker_cache") if isinstance(perf, dict) else None
            is_ranker_profile = (
                isinstance(perf, dict)
                and (
                    perf.get("model_family") == "lightgbm_ranker"
                    or (isinstance(cache, dict) and bool(cache.get("enabled")))
                    or "ranker" in str(row["run_id"]).lower()
                )
            )
            if not is_ranker_profile:
                continue
            runtime_summary = _runtime_profile_summary(
                perf,
                duration_s=row["duration_s"],
                regression_per_trial_s=regression_per_trial_s,
            )
            ranker_profiles.append(
                {
                    "run_id": row["run_id"],
                    "duration_s": row["duration_s"],
                    "started_at": row["started_at"],
                    "model_family": perf.get("model_family") if isinstance(perf, dict) else None,
                    "trials": runtime_summary["trials"],
                    "duration_per_trial_s": runtime_summary["duration_per_trial_s"],
                    "train_time_pct": runtime_summary["train_time_pct"],
                    "cache_hit_rate": runtime_summary["cache_hit_rate"],
                    "eval_cache_hit_rate": runtime_summary["eval_cache_hit_rate"],
                    "matrix_cache_hit_rate": runtime_summary["matrix_cache_hit_rate"],
                    "feature_drift_cache_hit_rate": runtime_summary["feature_drift_cache_hit_rate"],
                    "runtime_ratio_vs_regression": runtime_summary["runtime_ratio_vs_regression"],
                    "ranker_cache": cache,
                    "evaluation_cache": perf.get("evaluation_cache") if isinstance(perf, dict) else None,
                    "timing": perf.get("timing") if isinstance(perf, dict) else None,
                }
            )

    return {
        "research_schedule": {
            "run_id": schedule_run_id,
            "status_counts": _status_counts(conn, "mart_research_schedule_plan", run_id=schedule_run_id),
            "tasks": tasks,
        },
        "ranker_policy": ranker_policy,
        "model_stability": studies,
        "ranker_profiles": ranker_profiles,
        "rank_matrix_cache": _rank_matrix_cache_view(conn),
        "stability_context": _model_stability_context(conn),
        "stock_horizon_profile": _stock_horizon_profile(conn),
        "shareholder_plan_family_eval": _shareholder_plan_family_eval(conn),
        "temporal_synergy": _temporal_synergy_research(conn),
        "feature_drift": _drift_offenders(conn, 12),
    }


def build_workbench_champion(conn: Any, *, limit: int = 12) -> dict[str, Any]:
    lifecycle = _champion_summary(conn)
    challengers = []
    if _table_exists(conn, "mart_model_lifecycle"):
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
        challengers = [
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

    evaluations = []
    if _table_exists(conn, "mart_champion_candidate_evaluation"):
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
        evaluations = [
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

    evidence_bundles = []
    if _table_exists(conn, "mart_challenger_evidence_bundle"):
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

    gates = []
    if _table_exists(conn, "mart_tdx_keep_promotion_gate"):
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
        gates = [
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

    topk = {"snapshot_date": None, "model_id": None, "count": 0, "rows": []}
    key = _latest_recommendation_key(conn, primary_only=True)
    if key and _table_exists(conn, "mart_daily_recommendation"):
        cols = _columns(conn, "mart_daily_recommendation")
        top_rows = conn.execute(
            f"""
            SELECT {_select_expr(cols, "stock_code")},
                   {_select_expr(cols, "rank_in_date")},
                   {_select_expr(cols, "pred_score")},
                   {_select_expr(cols, "percentile")},
                   {_select_expr(cols, "regime_flag")},
                   {_select_expr(cols, "track_id")},
                   {_select_expr(cols, "run_mode")}
              FROM mart_daily_recommendation
             WHERE CAST(snapshot_date AS VARCHAR) = ?
               AND model_id = ?
               {"AND is_primary = TRUE" if "is_primary" in cols else ""}
             ORDER BY {"rank_in_date" if "rank_in_date" in cols else "stock_code"}
             LIMIT 10
            """,
            (key["snapshot_date"], key["model_id"]),
        ).fetchall()
        topk = {
            "snapshot_date": key["snapshot_date"],
            "model_id": key["model_id"],
            "count": int(key["count"]),
            "rows": [
                {
                    "stock_code": item["stock_code"],
                    "rank_in_date": item["rank_in_date"],
                    "pred_score": item["pred_score"],
                    "percentile": item["percentile"],
                    "regime_flag": item["regime_flag"],
                    "track_id": item["track_id"],
                    "run_mode": item["run_mode"],
                }
                for item in top_rows
            ],
        }

    deployment = _champion_deployment_summary(lifecycle=lifecycle, gates=gates, topk=topk)
    return {
        "lifecycle": lifecycle,
        "deployment": deployment,
        "challengers": challengers,
        "candidate_evaluations": evaluations,
        "evidence_bundles": evidence_bundles,
        "promotion_gates": gates,
        "stability_context": _model_stability_context(conn, summary_limit=4, diagnostic_limit=8),
        "latest_primary_topk": topk,
    }
