"""Workbench read models for the frontend operations surface."""
from __future__ import annotations

from typing import Any
import json

from services.feature_registry import load_feature_registry
from services.schema_versions import detect_drift
from services.storage_retention import load_storage_retention_policy, plan_storage_cleanup


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


def _stock_horizon_profile(conn: Any, *, stock_limit: int = 12, effect_limit: int = 12) -> dict[str, Any]:
    profile_table = "mart_stock_horizon_profile"
    effect_table = "mart_stock_horizon_feature_effect"
    empty = {
        "run_id": None,
        "baseline_label": "forward_ret_60d",
        "horizon_distribution": [],
        "horizon_comparison": [],
        "best_stocks": [],
        "top_effects": [],
        "feature_effects_by_horizon": [],
        "profile_count": 0,
        "best_count": 0,
        "effect_count": 0,
    }
    if not _table_exists(conn, profile_table):
        return empty
    run_id = _latest_run_id(conn, profile_table)
    if not run_id:
        return empty
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
    if _table_exists(conn, effect_table):
        effect_cols = _columns(conn, effect_table)
        effect_required = {"run_id", "stock_code", "label_name", "feature_name", "corr", "abs_corr_rank", "effect_direction"}
        if effect_required.issubset(effect_cols):
            effect_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mart_stock_horizon_feature_effect WHERE run_id = ?",
                    (run_id,),
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
                (run_id, int(effect_limit)),
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
                (run_id, 240),
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

    return {
        "run_id": run_id,
        "baseline_label": "forward_ret_60d",
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
                "is_baseline": row["label_name"] == "forward_ret_60d",
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
                "is_baseline": row["label_name"] == "forward_ret_60d",
            }
            for row in distribution_rows
        ],
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
                "is_baseline": row["label_name"] == "forward_ret_60d",
            }
            for row in stock_rows
        ],
        "top_effects": top_effects,
        "feature_effects_by_horizon": feature_effects_by_horizon,
        "profile_count": int((counts or {})["profile_count"] or 0),
        "best_count": int((counts or {})["best_count"] or 0),
        "effect_count": effect_count,
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
    tier_dist_expr = "m.source_tier_dist" if "source_tier_dist" in health_cols else "NULL AS source_tier_dist"
    rows = conn.execute(
        f"""
        SELECT d.table_name, d.layer, d.purpose, d.writer_module,
               d.upstream_source, d.source_tier, d.expected_freshness, d.sla_hours,
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
                "requires_backup_before_delete": report.get("requires_backup_before_delete"),
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
        "top_features": top_features,
        "top_feature_values": stock_feature_values,
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
        "stability_context": _model_stability_context(conn),
        "stock_horizon_profile": _stock_horizon_profile(conn),
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
