"""每日 topK 推荐 + 模型性能监测 API (Phase 4-5)"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Query
from services.db import get_conn

logger = logging.getLogger("cm-api")
router = APIRouter()


@router.get("/daily-topk")
async def get_daily_topk(
    date: str = Query(None, description="YYYY-MM-DD, 默认最新"),
    limit: int = Query(50, ge=1, le=500),
    regime: str = Query(None, description="up/flat/down 过滤"),
):
    """返回最新一天的 topK 推荐 + model_id + key features"""
    conn = get_conn()
    try:
        if not date:
            row = conn.execute("SELECT MAX(snapshot_date) FROM mart_daily_recommendation").fetchone()
            date = row[0] if row and row[0] else None
        if not date:
            return {"ok": False, "message": "尚未生成每日推荐, 请先运行 run_daily_topk"}

        where = ["r.snapshot_date = ?"]
        params = [date]
        if regime:
            where.append("r.regime_flag = ?")
            params.append(regime)

        sql = f"""
            SELECT r.snapshot_date, r.stock_code, r.model_id, r.rank_in_date,
                   r.pred_score, r.percentile, r.regime_flag, r.key_features_json,
                   ii.name stock_name_via_event,
                   ind.tdx_l1_name l1, ind.tdx_l2_name l2
            FROM mart_daily_recommendation r
            LEFT JOIN (
                SELECT DISTINCT stock_code, stock_name as name FROM fact_institution_event
            ) ii ON r.stock_code = ii.stock_code
            LEFT JOIN dim_stock_tdx_industry ind ON r.stock_code = ind.stock_code
            WHERE {' AND '.join(where)}
            ORDER BY r.rank_in_date
            LIMIT ?
        """
        rows = conn.execute(sql, params + [limit]).fetchall()
        items = []
        key_features_cache = None
        for r in rows:
            if key_features_cache is None:
                try:
                    kf = json.loads(r["key_features_json"]) if r["key_features_json"] else {}
                    key_features_cache = kf.get("model_top_features", [])
                except Exception:
                    key_features_cache = []
            items.append({
                "rank": r["rank_in_date"],
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name_via_event"],
                "pred_score": round(float(r["pred_score"]), 4),
                "percentile": round(float(r["percentile"]), 3),
                "regime_flag": r["regime_flag"],
                "l1": r["l1"],
                "l2": r["l2"],
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
            "model_meta": model_meta,
            "top_features": key_features_cache or [],
            "regime_filter": regime,
            "count": len(items),
            "items": items,
        }
    finally:
        conn.close()


@router.get("/model-performance")
async def get_model_performance(
    model_id: str = Query(None, description="指定 model_id, 默认最新"),
):
    """模型性能监测: 历史 holdout 指标 + 每日 top-decile 实际表现 (若已过 20 交易日)"""
    conn = get_conn()
    try:
        # 1. 模型元数据
        if not model_id:
            row = conn.execute("""
                SELECT model_id FROM mart_multidim_model
                ORDER BY created_at DESC LIMIT 1
            """).fetchone()
            if not row:
                return {"ok": False, "message": "尚无训练好的模型"}
            model_id = row[0]

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

        return {
            "ok": True,
            "model_id": model_id,
            "meta": meta,
            "daily_series": daily_series,
            "regime_breakdown": regime,
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
        return {
            "ok": True,
            "count": len(rows),
            "items": [dict(r) for r in rows],
        }
    finally:
        conn.close()
