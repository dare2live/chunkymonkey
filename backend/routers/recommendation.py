"""每日 topK 推荐 + 模型性能监测 API (Phase 4-5)"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Query
from services.db import get_conn
from services.feature_labels import (
    FEATURE_LABELS, MODEL_NAME_LABELS,
    format_model_id, composite_grade, grade_metric,
)
from services.ml_lifecycle.registry import (
    get_model_status,
    select_default_model_id,
)
from services.stock_horizon_read import load_stock_horizon_evidence

logger = logging.getLogger("cm-api")
router = APIRouter()


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _has_column(conn, table: str, column: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
          FROM information_schema.columns
         WHERE table_name = ? AND column_name = ?
        """,
        (table, column),
    ).fetchone()
    return bool(row and row[0])


def _safe_json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except Exception:
        return default


def _resolve_model_id(conn, requested_model_id: str | None) -> tuple[str | None, bool, str | None]:
    """Resolve default model safely through lifecycle champion."""
    if requested_model_id:
        return requested_model_id, False, get_model_status(conn, requested_model_id)
    model_id, fallback = select_default_model_id(conn)
    return model_id, fallback, get_model_status(conn, model_id)


@router.get("/labels")
async def get_labels():
    """全局英文→中文字段映射 (前端一次加载后缓存)"""
    return {
        "ok": True,
        "features": FEATURE_LABELS,
        "models": MODEL_NAME_LABELS,
    }


@router.get("/daily-topk")
async def get_daily_topk(
    date: str = Query(None, description="YYYY-MM-DD, 默认最新"),
    limit: int = Query(50, ge=1, le=500),
    regime: str = Query(None, description="up/flat/down 过滤"),
    model_id: str = Query(None, description="显式 model_id; 默认 lifecycle champion"),
    run_mode: str = Query(None, description="champion/shadow 过滤; 默认正式推荐"),
):
    """返回最新一天的 topK 推荐 + model_id + key features"""
    conn = get_conn()
    try:
        requested_model_id = model_id
        model_id, selection_fallback, model_role = _resolve_model_id(conn, requested_model_id)
        if not model_id:
            return {"ok": False, "message": "尚无训练好的模型"}

        has_run_mode = _has_column(conn, "mart_daily_recommendation", "run_mode")
        if not date:
            date_where = ["model_id = ?"]
            date_params = [model_id]
            if has_run_mode:
                if run_mode:
                    date_where.append("run_mode = ?")
                    date_params.append(run_mode)
                elif not requested_model_id:
                    date_where.append("COALESCE(run_mode, 'champion') != 'shadow'")
            row = conn.execute(
                f"SELECT MAX(snapshot_date) FROM mart_daily_recommendation WHERE {' AND '.join(date_where)}",
                date_params,
            ).fetchone()
            date = row[0] if row and row[0] else None
        if not date:
            return {
                "ok": False,
                "message": "尚未生成该模型每日推荐, 请先运行 run_daily_topk",
                "model_id": model_id,
                "model_role": model_role,
                "selection_fallback": selection_fallback,
            }

        where = ["r.snapshot_date = ?"]
        params = [date]
        where.append("r.model_id = ?")
        params.append(model_id)
        if has_run_mode:
            if run_mode:
                where.append("r.run_mode = ?")
                params.append(run_mode)
            elif not requested_model_id:
                where.append("COALESCE(r.run_mode, 'champion') != 'shadow'")
        if regime:
            where.append("r.regime_flag = ?")
            params.append(regime)

        cache_available = _table_exists(conn, "mart_daily_topk_view_cache")
        cache_rows = []
        if cache_available:
            cache_has_horizon = _has_column(conn, "mart_daily_topk_view_cache", "selected_horizon_days")
            cache_horizon_select = (
                "baseline_horizon_days, selected_horizon_days, "
                "selected_horizon_confidence, horizon_selection_run_id,"
                if cache_has_horizon
                else "60 AS baseline_horizon_days, 60 AS selected_horizon_days, "
                     "NULL AS selected_horizon_confidence, NULL AS horizon_selection_run_id,"
            )
            cache_where = ["snapshot_date = ?", "model_id = ?"]
            cache_params = [date, model_id]
            if run_mode:
                cache_where.append("run_mode = ?")
                cache_params.append(run_mode)
            elif not requested_model_id:
                cache_where.append("COALESCE(run_mode, 'champion') != 'shadow'")
            if regime:
                cache_where.append("regime_flag = ?")
                cache_params.append(regime)
            cache_rows = conn.execute(
                f"""
                SELECT snapshot_date, stock_code, model_id, rank_in_date,
                       pred_score, percentile, regime_flag, run_mode,
                       key_features_json, track_id, is_primary,
                       {cache_horizon_select}
                       stock_name, tdx_l1_name AS l1, tdx_l2_name AS l2
                  FROM mart_daily_topk_view_cache
                 WHERE {' AND '.join(cache_where)}
                 ORDER BY rank_in_date
                 LIMIT ?
                """,
                cache_params + [limit],
            ).fetchall()

        run_mode_select = "r.run_mode," if has_run_mode else "NULL AS run_mode,"
        has_horizon = _has_column(conn, "mart_daily_recommendation", "selected_horizon_days")
        horizon_select = (
            "r.baseline_horizon_days, r.selected_horizon_days, "
            "r.selected_horizon_confidence, r.horizon_selection_run_id,"
            if has_horizon
            else "60 AS baseline_horizon_days, 60 AS selected_horizon_days, "
                 "NULL AS selected_horizon_confidence, NULL AS horizon_selection_run_id,"
        )
        sql = f"""
            WITH name_ref AS (
                SELECT stock_code, stock_name, 1 AS source_priority
                  FROM dim_active_a_stock
                 WHERE stock_name IS NOT NULL AND stock_name <> ''
                UNION ALL
                SELECT stock_code, stock_name, 2 AS source_priority
                  FROM mart_stock_trend
                 WHERE stock_name IS NOT NULL AND stock_name <> ''
                UNION ALL
                SELECT stock_code, stock_name, 3 AS source_priority
                  FROM fact_institution_event
                 WHERE stock_name IS NOT NULL AND stock_name <> ''
            ),
            stock_names AS (
                SELECT stock_code, stock_name
                  FROM (
                    SELECT stock_code, stock_name,
                           ROW_NUMBER() OVER (
                               PARTITION BY stock_code
                               ORDER BY source_priority
                           ) AS rn
                      FROM name_ref
                  )
                 WHERE rn = 1
            )
            SELECT r.snapshot_date, r.stock_code, r.model_id, r.rank_in_date,
                   r.pred_score, r.percentile, r.regime_flag, {run_mode_select}
                   r.key_features_json, r.track_id, r.is_primary,
                   {horizon_select}
                   sn.stock_name,
                   ind.tdx_l1_name l1, ind.tdx_l2_name l2
            FROM mart_daily_recommendation r
            LEFT JOIN stock_names sn ON r.stock_code = sn.stock_code
            LEFT JOIN dim_stock_tdx_industry ind ON r.stock_code = ind.stock_code
            WHERE {' AND '.join(where)}
            ORDER BY r.rank_in_date
            LIMIT ?
        """
        rows = cache_rows or conn.execute(sql, params + [limit]).fetchall()
        horizon_evidence_by_stock = load_stock_horizon_evidence(
            conn,
            [r["stock_code"] for r in rows],
        )
        items = []
        key_features_cache = None
        for r in rows:
            horizon_evidence = horizon_evidence_by_stock.get(str(r["stock_code"]))
            baseline_horizon_days = (
                horizon_evidence.get("baseline_horizon_days")
                if horizon_evidence
                else r["baseline_horizon_days"]
            )
            selected_horizon_days = (
                horizon_evidence.get("selected_horizon_days")
                if horizon_evidence
                else r["selected_horizon_days"]
            )
            selected_horizon_confidence = (
                horizon_evidence.get("selected_horizon_confidence")
                if horizon_evidence
                else r["selected_horizon_confidence"]
            )
            horizon_selection_run_id = (
                horizon_evidence.get("run_id")
                if horizon_evidence
                else r["horizon_selection_run_id"]
            )
            stock_feature_values = []
            stock_feature_contributions = []
            explanation_status = None
            base_value = None
            additivity_error = None
            if key_features_cache is None:
                try:
                    kf = json.loads(r["key_features_json"]) if r["key_features_json"] else {}
                    key_features_cache = kf.get("model_top_features", [])
                except Exception:
                    key_features_cache = []
            try:
                kf = json.loads(r["key_features_json"]) if r["key_features_json"] else {}
                stock_feature_values = kf.get("stock_feature_values", []) if isinstance(kf, dict) else []
                stock_feature_contributions = (
                    kf.get("stock_feature_contributions", [])
                    if isinstance(kf, dict)
                    else []
                )
                explanation_status = kf.get("explanation_status") if isinstance(kf, dict) else None
                base_value = kf.get("base_value") if isinstance(kf, dict) else None
                additivity_error = kf.get("additivity_error") if isinstance(kf, dict) else None
            except Exception:
                stock_feature_values = []
                stock_feature_contributions = []
            items.append({
                "rank": r["rank_in_date"],
                "stock_code": r["stock_code"],
                "snapshot_date": r["snapshot_date"],
                "model_id": r["model_id"],
                "stock_name": r["stock_name"],
                "pred_score": round(float(r["pred_score"]), 4),
                "percentile": round(float(r["percentile"]), 3),
                "regime_flag": r["regime_flag"],
                "run_mode": r["run_mode"],
                "track_id": r["track_id"],
                "is_primary": bool(r["is_primary"]),
                "l1": r["l1"],
                "l2": r["l2"],
                "baseline_horizon_days": baseline_horizon_days,
                "selected_horizon_days": selected_horizon_days,
                "selected_horizon_confidence": selected_horizon_confidence,
                "horizon_selection_run_id": horizon_selection_run_id,
                "horizon_evidence": horizon_evidence,
                "top_feature_values": stock_feature_values[:8] if isinstance(stock_feature_values, list) else [],
                "top_feature_contributions": (
                    stock_feature_contributions[:8]
                    if isinstance(stock_feature_contributions, list)
                    else []
                ),
                "explanation_status": explanation_status,
                "base_value": base_value,
                "additivity_error": additivity_error,
            })

        # 模型元数据
        model_id = rows[0]["model_id"] if rows else None
        model_meta = {}
        if model_id:
            mrow = conn.execute("""
                SELECT holdout_ic, holdout_rank_ic,
                       holdout_top_decile_avg, holdout_long_short_spread,
                       holdout_winrate_top, n_features, created_at
                FROM mart_multidim_model WHERE model_id = ?
            """, (model_id,)).fetchone()
            if mrow:
                model_meta = dict(mrow)

        return {
            "ok": True,
            "snapshot_date": date,
            "model_id": model_id,
            "requested_model_id": requested_model_id,
            "model_role": model_role,
            "selection_fallback": selection_fallback,
            "run_mode": run_mode or (rows[0]["run_mode"] if rows else None),
            "is_default_champion": (not requested_model_id and model_role == "champion"),
            "model_meta": model_meta,
            "top_features": key_features_cache or [],
            "regime_filter": regime,
            "count": len(items),
            "items": items,
        }
    finally:
        conn.close()


@router.get("/stock-prediction")
async def get_stock_prediction(
    code: str = Query(..., description="股票代码"),
    model_id: str = Query(None, description="指定 model_id, 默认 lifecycle champion"),
):
    """单只股票在最新模型下的最近预测 (按日期 DESC, 取最新一天)"""
    conn = get_conn()
    try:
        requested_model_id = model_id
        model_id, selection_fallback, model_role = _resolve_model_id(conn, requested_model_id)
        if not model_id:
            return {"ok": False, "message": "尚无训练好的模型"}

        row = conn.execute("""
            SELECT stock_code, date, pred_score, rank_in_date, percentile
            FROM mart_multidim_prediction
            WHERE stock_code = ? AND model_id = ?
            ORDER BY date DESC LIMIT 1
        """, (code, model_id)).fetchone()
        if not row:
            return {
                "ok": True,
                "model_id": model_id,
                "model_role": model_role,
                "selection_fallback": selection_fallback,
                "has_prediction": False,
            }

        meta = conn.execute(
            "SELECT holdout_ic, holdout_rank_ic FROM mart_multidim_model WHERE model_id = ?",
            (model_id,)
        ).fetchone()
        return {
            "ok": True,
            "model_id": model_id,
            "requested_model_id": requested_model_id,
            "model_role": model_role,
            "selection_fallback": selection_fallback,
            "has_prediction": True,
            "stock_code": row["stock_code"],
            "date": row["date"],
            "pred_score": float(row["pred_score"]),
            "rank_in_date": int(row["rank_in_date"]),
            "percentile": float(row["percentile"]),
            "model_ic": float(meta["holdout_ic"]) if meta and meta["holdout_ic"] else None,
            "model_rank_ic": float(meta["holdout_rank_ic"]) if meta and meta["holdout_rank_ic"] else None,
        }
    finally:
        conn.close()


@router.get("/model-performance")
async def get_model_performance(
    model_id: str = Query(None, description="指定 model_id, 默认 lifecycle champion"),
):
    """模型性能监测: 历史 holdout 指标 + 每日 top-decile 实际表现 (若已过 20 交易日)"""
    conn = get_conn()
    try:
        # 1. 模型元数据
        requested_model_id = model_id
        model_id, selection_fallback, model_role = _resolve_model_id(conn, requested_model_id)
        if not model_id:
            return {"ok": False, "message": "尚无训练好的模型"}

        mrow = conn.execute("""
            SELECT * FROM mart_multidim_model WHERE model_id = ?
        """, (model_id,)).fetchone()
        if not mrow:
            return {"ok": False, "message": f"model_id={model_id} 不存在"}
        meta = dict(mrow)
        # feature importance json 解析
        try:
            fi = json.loads(meta.pop("feature_importance_json", "{}"))
            meta["feature_importance"] = sorted(
                [{"name": k, "importance": v} for k, v in fi.items()],
                key=lambda x: x["importance"], reverse=True,
            )[:30]
        except Exception:
            meta["feature_importance"] = []

        best_params = meta.pop("best_params_json", None)
        meta["best_params"] = json.loads(best_params) if best_params else {}
        meta["feature_cols"] = _safe_json(meta.get("feature_cols_json"), [])

        # 附加中文名 + 综合评级 + 单指标评级
        meta["model_name_cn"] = format_model_id(model_id)
        meta["composite_grade"] = composite_grade(meta)
        meta["metric_grades"] = {
            k: grade_metric(k, meta.get(k)) for k in [
                "holdout_ic", "holdout_rank_ic", "holdout_top_decile_avg",
                "holdout_long_short_spread", "holdout_winrate_top",
            ]
        }
        # feature importance 附加中文
        for fi in meta.get("feature_importance", []):
            fi["label_cn"] = FEATURE_LABELS.get(fi["name"], "")

        # 2. 每日实际表现 (topK 事后追踪)
        # 对 mart_multidim_prediction 里 holdout 期的 top-decile, 查 fact_feature_panel
        # 看 forward_ret_20d 实测 vs 预测
        daily_real = conn.execute("""
            SELECT p.date,
                   AVG(CASE WHEN p.percentile >= 0.9 THEN fp.forward_ret_20d END) top_actual,
                   AVG(CASE WHEN p.percentile <= 0.1 THEN fp.forward_ret_20d END) bot_actual,
                   COUNT(CASE WHEN p.percentile >= 0.9 AND fp.forward_ret_20d > 0 THEN 1 END) * 1.0
                     / NULLIF(COUNT(CASE WHEN p.percentile >= 0.9 THEN 1 END), 0) top_wr
            FROM mart_multidim_prediction p
            JOIN fact_feature_panel fp ON fp.stock_code = p.stock_code AND fp.date = p.date
            WHERE p.model_id = ? AND fp.forward_ret_20d IS NOT NULL
            GROUP BY p.date ORDER BY p.date
        """, (model_id,)).fetchall()
        daily_series = [
            {
                "date": r["date"],
                "top_actual": r["top_actual"],
                "bot_actual": r["bot_actual"],
                "top_wr": r["top_wr"],
                "spread": (r["top_actual"] or 0) - (r["bot_actual"] or 0),
            }
            for r in daily_real
        ]

        # 3. regime breakdown
        regime_stats = conn.execute("""
            SELECT fp.regime_flag,
                   COUNT(*) n,
                   AVG(CASE WHEN p.percentile >= 0.9 THEN fp.forward_ret_20d END) top_avg,
                   AVG(CASE WHEN p.percentile <= 0.1 THEN fp.forward_ret_20d END) bot_avg
            FROM mart_multidim_prediction p
            JOIN fact_feature_panel fp ON fp.stock_code = p.stock_code AND fp.date = p.date
            WHERE p.model_id = ? AND fp.forward_ret_20d IS NOT NULL
            GROUP BY fp.regime_flag
        """, (model_id,)).fetchall()
        regime = [dict(r) for r in regime_stats]

        portfolio = {"latest_run_id": None, "items": [], "random_l1": []}
        if _table_exists(conn, "mart_model_portfolio_summary"):
            prow = conn.execute(
                """
                SELECT run_id FROM mart_model_portfolio_summary
                WHERE model_id = ?
                ORDER BY built_at DESC LIMIT 1
                """,
                (model_id,),
            ).fetchone()
            if prow:
                portfolio["latest_run_id"] = prow["run_id"]
                rows = conn.execute(
                    """
                    SELECT curve_id, curve_type, model_id, benchmark_id, cost_bps,
                           final_nav, total_return, annualized_return, max_drawdown,
                           sharpe, avg_turnover, rebalance_count, start_date, end_date
                    FROM mart_model_portfolio_summary
                    WHERE run_id = ? AND curve_type != 'random'
                    ORDER BY cost_bps, curve_type, curve_id
                    """,
                    (prow["run_id"],),
                ).fetchall()
                portfolio["items"] = [dict(r) for r in rows]
                random_rows = conn.execute(
                    """
                    SELECT cost_bps,
                           COUNT(*) n,
                           AVG(total_return) avg_total_return,
                           QUANTILE_CONT(total_return, 0.1) p10_total_return,
                           QUANTILE_CONT(total_return, 0.5) median_total_return,
                           QUANTILE_CONT(total_return, 0.9) p90_total_return,
                           AVG(sharpe) avg_sharpe
                    FROM mart_model_portfolio_summary
                    WHERE run_id = ? AND curve_type = 'random'
                    GROUP BY cost_bps ORDER BY cost_bps
                    """,
                    (prow["run_id"],),
                ).fetchall()
                portfolio["random_l1"] = [dict(r) for r in random_rows]

        walkforward = {"latest_run_id": None, "summary": None, "folds": []}
        if _table_exists(conn, "mart_model_walkforward_fold"):
            wfrow = conn.execute(
                """
                SELECT run_id FROM mart_model_walkforward_fold
                WHERE model_id = ? OR model_id IS NULL
                ORDER BY built_at DESC LIMIT 1
                """,
                (model_id,),
            ).fetchone()
            if wfrow:
                walkforward["latest_run_id"] = wfrow["run_id"]
                folds = conn.execute(
                    """
                    SELECT fold_id, train_start, train_end, valid_start, valid_end,
                           test_start, test_end, n_features, test_ic, test_rank_ic,
                           test_long_short_spread, test_winrate_top
                    FROM mart_model_walkforward_fold
                    WHERE run_id = ? ORDER BY fold_id
                    """,
                    (wfrow["run_id"],),
                ).fetchall()
                fold_items = [dict(r) for r in folds]
                walkforward["folds"] = fold_items
                if fold_items:
                    rank_vals = [r["test_rank_ic"] for r in fold_items if r["test_rank_ic"] is not None]
                    spread_vals = [r["test_long_short_spread"] for r in fold_items if r["test_long_short_spread"] is not None]
                    walkforward["summary"] = {
                        "fold_count": len(fold_items),
                        "rank_ic_mean": sum(rank_vals) / len(rank_vals) if rank_vals else None,
                        "rank_ic_positive_ratio": sum(1 for v in rank_vals if v > 0) / len(rank_vals) if rank_vals else None,
                        "spread_mean": sum(spread_vals) / len(spread_vals) if spread_vals else None,
                    }

        data_quality = {}
        if _table_exists(conn, "fact_feature_panel"):
            dq = conn.execute(
                """
                SELECT MAX(date) latest_panel_date,
                       COUNT(*) AS row_count,
                       COUNT(DISTINCT stock_code) AS codes,
                       COUNT(DISTINCT date) AS dates,
                       SUM(CASE WHEN forward_ret_20d IS NOT NULL THEN 1 ELSE 0 END) label_rows
                FROM fact_feature_panel
                """
            ).fetchone()
            if dq:
                data_quality = dict(dq)
                data_quality["rows"] = data_quality.pop("row_count", None)

        return {
            "ok": True,
            "model_id": model_id,
            "requested_model_id": requested_model_id,
            "model_role": model_role,
            "selection_fallback": selection_fallback,
            "meta": meta,
            "daily_series": daily_series,
            "regime_breakdown": regime,
            "portfolio": portfolio,
            "walkforward": walkforward,
            "data_quality": data_quality,
        }
    finally:
        conn.close()


@router.get("/tdx-feature-validation")
async def get_tdx_feature_validation():
    """TDX keep/watch/drop validation summary for frontend display."""
    conn = get_conn()
    try:
        def latest_decision_run(feature_set_id: str, fallback: str) -> str:
            row = conn.execute("""
                SELECT decision_run_id
                  FROM mart_feature_retention_decision
                 WHERE feature_set_id = ?
                 GROUP BY decision_run_id
                 ORDER BY MAX(built_at) DESC
                 LIMIT 1
            """, (feature_set_id,)).fetchone()
            return row["decision_run_id"] if row else fallback

        manual_feature_set_id = "tdx_f10_gpcw_v1"
        auto_feature_set_id = "tdx_gpcw_auto_v1_pit"
        manual_run = latest_decision_run(manual_feature_set_id, "retention_tdx_f10_gpcw_v1")
        auto_run = latest_decision_run(auto_feature_set_id, "retention_tdx_gpcw_auto_v1")
        manual = conn.execute("""
            SELECT feature_name, decision, primary_reason, coverage_pct,
                   pit_violation_rows, mean_rank_ic, fold_same_sign_rate,
                   group_ablation_delta
              FROM mart_feature_retention_decision
             WHERE feature_set_id=?
               AND decision_run_id=?
             ORDER BY CASE decision WHEN 'keep' THEN 0 WHEN 'watch' THEN 1 ELSE 2 END,
                      ABS(COALESCE(mean_rank_ic, 0)) DESC
        """, (manual_feature_set_id, manual_run)).fetchall()
        auto = conn.execute("""
            SELECT feature_name, decision, primary_reason, coverage_pct,
                   pit_violation_rows, mean_rank_ic, fold_same_sign_rate
              FROM mart_feature_retention_decision
             WHERE feature_set_id=?
               AND decision_run_id=?
             ORDER BY CASE decision WHEN 'keep' THEN 0 WHEN 'watch' THEN 1 ELSE 2 END,
                      ABS(COALESCE(mean_rank_ic, 0)) DESC
             LIMIT 40
        """, (auto_feature_set_id, auto_run)).fetchall()
        source_rows = conn.execute("""
            SELECT data_domain, preferred_source, fallback_1, fallback_2, reason
              FROM dim_data_source_priority
             ORDER BY data_domain
        """).fetchall()
        pit_manual = conn.execute("""
            SELECT COALESCE(SUM(violation_rows), 0)
              FROM mart_feature_pit_audit
             WHERE audit_run_id='pit_tdx_f10_gpcw_v1'
        """).fetchone()[0]
        pit_auto = conn.execute("""
            SELECT COALESCE(SUM(violation_rows), 0)
              FROM mart_tdx_gpcw_auto_pit_audit
             WHERE audit_run_id='pit_tdx_gpcw_auto_v1'
        """).fetchone()[0]
        return {
            "ok": True,
            "manual_feature_set_id": manual_feature_set_id,
            "manual_decision_run_id": manual_run,
            "manual": [dict(r) for r in manual],
            "auto_feature_set_id": auto_feature_set_id,
            "auto_decision_run_id": auto_run,
            "auto_optional_watch_pool": [dict(r) for r in auto],
            "pit": {
                "tdx_f10_gpcw_v1": {"violation_rows": pit_manual},
                "tdx_gpcw_auto_v1": {"violation_rows": pit_auto},
            },
            "sources": [dict(r) for r in source_rows],
        }
    finally:
        conn.close()


@router.get("/model-comparison")
async def get_model_comparison(
    challenger_model_id: str = Query(None, description="指定 challenger; 默认最新 TDX keep challenger"),
):
    """Champion vs challenger comparison with gate and shadow evidence."""
    conn = get_conn()
    try:
        champion_id, selection_fallback = select_default_model_id(conn)
        if not challenger_model_id:
            row = None
            if _table_exists(conn, "mart_tdx_keep_promotion_gate"):
                row = conn.execute("""
                    SELECT g.challenger_model_id AS model_id
                      FROM mart_tdx_keep_promotion_gate g
                      JOIN mart_model_lifecycle l
                        ON l.model_id = g.challenger_model_id
                     WHERE l.status = 'challenger'
                     ORDER BY g.evaluated_at DESC
                     LIMIT 1
                """).fetchone()
            if not row:
                row = conn.execute("""
                    SELECT model_id
                      FROM mart_model_lifecycle
                     WHERE status='challenger'
                     ORDER BY updated_at DESC
                     LIMIT 1
                """).fetchone()
            challenger_model_id = row["model_id"] if row else None

        def model_meta(mid):
            if not mid:
                return None
            row = conn.execute("""
                SELECT model_id, feature_schema_version, n_features,
                       holdout_ic, holdout_rank_ic,
                       holdout_top_decile_avg, holdout_long_short_spread,
                       holdout_winrate_top, created_at, feature_cols_json
                  FROM mart_multidim_model
                 WHERE model_id = ?
            """, (mid,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["feature_cols"] = _safe_json(d.pop("feature_cols_json", None), [])
            d["status"] = get_model_status(conn, mid)
            return d

        def latest_portfolio(mid):
            if not mid or not _table_exists(conn, "mart_model_portfolio_summary"):
                return None
            row = conn.execute("""
                SELECT run_id, curve_id, curve_type, total_return, annualized_return,
                       max_drawdown, sharpe, avg_turnover, cost_bps, rebalance_days
                  FROM mart_model_portfolio_summary
                 WHERE model_id = ? AND curve_type = 'model_top20'
                 ORDER BY built_at DESC, cost_bps
                 LIMIT 1
            """, (mid,)).fetchone()
            return dict(row) if row else None

        def latest_walkforward(mid):
            if not mid or not _table_exists(conn, "mart_model_walkforward_fold"):
                return None
            row = conn.execute("""
                SELECT run_id, COUNT(*) fold_count,
                       AVG(test_rank_ic) rank_ic_mean,
                       AVG(test_long_short_spread) long_short_mean,
                       SUM(CASE WHEN quality_flag='ok' THEN 1 ELSE 0 END) ok_folds
                  FROM mart_model_walkforward_fold
                 WHERE model_id = ?
                 GROUP BY run_id
                 ORDER BY MAX(built_at) DESC
                 LIMIT 1
            """, (mid,)).fetchone()
            return dict(row) if row else None

        gate = None
        if _table_exists(conn, "mart_tdx_keep_promotion_gate"):
            row = conn.execute("""
                SELECT *
                  FROM mart_tdx_keep_promotion_gate
                 WHERE challenger_model_id = ?
                 ORDER BY evaluated_at DESC LIMIT 1
            """, (challenger_model_id,)).fetchone()
            gate = dict(row) if row else None
            if gate:
                gate["gate_results"] = _safe_json(gate.get("gate_results_json"), [])
                gate["blockers"] = _safe_json(gate.get("blockers_json"), [])

        shadow = None
        if challenger_model_id:
            run_mode_filter = "AND COALESCE(run_mode, '') = 'shadow'" if _has_column(conn, "mart_daily_recommendation", "run_mode") else ""
            row = conn.execute(f"""
                SELECT MAX(snapshot_date) snapshot_date, COUNT(*) row_count
                  FROM mart_daily_recommendation
                 WHERE model_id = ? {run_mode_filter}
            """, (challenger_model_id,)).fetchone()
            shadow = dict(row) if row else None
            if shadow is not None:
                shadow["rows"] = shadow.get("row_count")

        evidence = None
        if challenger_model_id and _table_exists(conn, "mart_challenger_evidence_bundle"):
            row = conn.execute("""
                SELECT evidence_run_id, status, gate_run_id, gate_status,
                       blockers_json, started_at, ended_at, duration_s
                  FROM mart_challenger_evidence_bundle
                 WHERE model_id = ?
                 ORDER BY started_at DESC
                 LIMIT 1
            """, (challenger_model_id,)).fetchone()
            evidence = dict(row) if row else None
            if evidence:
                evidence["blockers"] = _safe_json(evidence.get("blockers_json"), [])

        return {
            "ok": True,
            "selection_fallback": selection_fallback,
            "champion": model_meta(champion_id),
            "challenger": model_meta(challenger_model_id),
            "walkforward": {
                "champion": latest_walkforward(champion_id),
                "challenger": latest_walkforward(challenger_model_id),
            },
            "portfolio": {
                "champion": latest_portfolio(champion_id),
                "challenger": latest_portfolio(challenger_model_id),
            },
            "shadow_topk": shadow,
            "promotion_gate": gate,
            "evidence_bundle": evidence,
        }
    finally:
        conn.close()


@router.get("/model-history")
async def list_model_history(limit: int = Query(20, ge=1, le=100)):
    """所有已训练模型的简表"""
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT model_id, created_at, n_features,
                   holdout_ic, holdout_rank_ic,
                   holdout_top_decile_avg, holdout_long_short_spread,
                   holdout_winrate_top, notes
            FROM mart_multidim_model
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["model_name_cn"] = format_model_id(d["model_id"])
            d["composite_grade"] = composite_grade(d)
            items.append(d)
        return {"ok": True, "count": len(items), "items": items}
    finally:
        conn.close()
