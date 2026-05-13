"""Phase η — 3 视图 API (股票视图 / 公式视图 / 机构视图)。

3 端点对应 v3 新页面:
  GET /api/v3/view/stock/{stock_code}       — 单股全公式表现 + per-stock optuna + 今日推荐
  GET /api/v3/view/formula/{formula_id}     — 单公式全市场分析 + 5 维分桶 + 最适合的股票
  GET /api/v3/view/institution/{inst_id}    — 单机构持仓 + 跟随回测胜率
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services.db import get_conn

logger = logging.getLogger("cm-api.v3-views")
router = APIRouter()


def _table_exists(conn, table: str) -> bool:
    r = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(r and r[0])


# ============ 1. STOCK VIEW ============
@router.get("/stock/{stock_code}")
async def stock_view(stock_code: str):
    """单股完整视图: 画像 + 每公式表现 + 今日推荐 + 机构持仓。"""
    conn = get_conn()
    try:
        # 1. 主画像 (来自 Phase γ)
        picture = None
        if _table_exists(conn, "mart_stock_picture_daily"):
            row = conn.execute(
                """SELECT * FROM mart_stock_picture_daily
                   WHERE stock_code=? ORDER BY snapshot_date DESC LIMIT 1""",
                [stock_code],
            ).fetchone()
            if row:
                picture = dict(zip([d[0] for d in conn.execute(
                    "SELECT * FROM mart_stock_picture_daily LIMIT 0").description], row))

        # 2. per-stock 每公式表现 (来自 mart_stock_formula_optuna best 桶)
        formula_perf = []
        if _table_exists(conn, "mart_stock_formula_optuna"):
            rows = conn.execute(
                """SELECT formula_id, formula_variant, holding_days,
                          vol_bin, amt_bin, price_pos_bin, stage_bin,
                          n_signals, win_rate, avg_ret, avg_dd, sharpe, calmar,
                          is_best_hd, is_high_conviction
                     FROM mart_stock_formula_optuna
                    WHERE stock_code=? AND is_best_hd = TRUE
                    ORDER BY is_high_conviction DESC, calmar DESC LIMIT 20""",
                [stock_code],
            ).fetchall()
            formula_perf = [
                {
                    "formula_id": r[0], "formula_variant": r[1], "holding_days": r[2],
                    "context_bucket": f"{r[3]}·{r[4]}·{r[5]}·阶段{r[6]}",
                    "n_signals": r[7], "win_rate": r[8], "avg_ret": r[9],
                    "avg_dd": r[10], "sharpe": r[11], "calmar": r[12],
                    "is_high_conviction": bool(r[14]),
                } for r in rows
            ]

        # 3. 今日推荐 (来自 mart_daily_formula_buys)
        today_buys = []
        if _table_exists(conn, "mart_daily_formula_buys"):
            rows = conn.execute(
                """SELECT signal_date, buy_date, formula_id, formula_variant,
                          vol_bin, amt_bin, price_pos_bin, stage_bin,
                          historical_win_rate, historical_avg_ret, historical_avg_dd,
                          historical_n_signals, recommended_holding_days,
                          signal_close_price, buy_price_est, sell_target_price,
                          expected_max_dd_pct, confidence_score, rank_in_date
                     FROM mart_daily_formula_buys
                    WHERE stock_code=?
                    ORDER BY signal_date DESC, rank_in_date LIMIT 10""",
                [stock_code],
            ).fetchall()
            today_buys = [
                {
                    "signal_date": r[0], "buy_date": r[1],
                    "formula": r[3] or r[2],
                    "context": f"{r[4]}·{r[5]}·{r[6]}·阶段{r[7]}",
                    "win_rate": r[8], "avg_ret": r[9], "avg_dd": r[10],
                    "n_signals": r[11],
                    "holding_days": r[12],
                    "signal_close": r[13], "buy_price": r[14],
                    "sell_target": r[15], "expected_dd": r[16],
                    "confidence": r[17], "rank_in_date": r[18],
                } for r in rows
            ]

        # 4. 机构持仓 (来自 fact_top10_holder_period 该股最新一期)
        holders = []
        latest_rpt = conn.execute(
            """SELECT MAX(report_date) FROM fact_top10_holder_period WHERE stock_code=?""",
            [stock_code],
        ).fetchone()
        if latest_rpt and latest_rpt[0]:
            rows = conn.execute(
                """SELECT report_date, holder_name, holder_name_norm,
                          hold_ratio_total, hold_change_num, holder_rank
                     FROM fact_top10_holder_period
                    WHERE stock_code=? AND report_date=?
                    ORDER BY holder_rank""",
                [stock_code, latest_rpt[0]],
            ).fetchall()
            holders = [
                {
                    "report_date": r[0], "name": r[1], "name_norm": r[2],
                    "share_pct": r[3], "share_change": r[4], "rank": r[5],
                } for r in rows
            ]

        # 5. selection 历史 (来自 fact_stock_selection_log)
        selection_history_summary = None
        if _table_exists(conn, "mart_stock_selection_summary"):
            r = conn.execute(
                """SELECT n_total, n_30d, n_90d, win_rate, avg_ret,
                          last_select_date, last_formula, last_outcome
                     FROM mart_stock_selection_summary
                    WHERE stock_code=? ORDER BY snapshot_date DESC LIMIT 1""",
                [stock_code],
            ).fetchone()
            if r:
                selection_history_summary = {
                    "n_total": r[0], "n_30d": r[1], "n_90d": r[2],
                    "win_rate": r[3], "avg_ret": r[4],
                    "last_select_date": r[5], "last_formula": r[6], "last_outcome": r[7],
                }

        return {
            "ok": True,
            "stock_code": stock_code,
            "picture": picture,
            "formula_performance": formula_perf,
            "today_buys": today_buys,
            "holders": holders,
            "selection_summary": selection_history_summary,
        }
    finally:
        conn.close()


# ============ 2. FORMULA VIEW ============
@router.get("/formula/{formula_id}")
async def formula_view(
    formula_id: str,
    variant: str | None = Query(None),
    min_n: int = Query(5, ge=1, description="过滤桶最少信号数"),
):
    """单公式全市场视图: 全局胜率 + 5 维分桶 + 最适合的股票 + 今日触发。"""
    conn = get_conn()
    try:
        # 1. 全局 horizon_evidence (含 variant 分布)
        evidence = []
        if _table_exists(conn, "mart_formula_horizon_evidence"):
            sql = """SELECT formula_variant, holding_days, n_signals, n_matured,
                            win_rate, avg_ret, avg_dd, sharpe
                       FROM mart_formula_horizon_evidence
                      WHERE formula_id=?"""
            params = [formula_id]
            if variant:
                sql += " AND formula_variant=?"
                params.append(variant)
            sql += " ORDER BY formula_variant, holding_days"
            rows = conn.execute(sql, params).fetchall()
            evidence = [
                {"variant": r[0], "holding_days": r[1], "n_signals": r[2],
                 "n_matured": r[3], "win_rate": r[4], "avg_ret": r[5],
                 "avg_dd": r[6], "sharpe": r[7]} for r in rows
            ]

        # 2. 5 维 high-conviction 桶 (top 30 by calmar)
        buckets = []
        if _table_exists(conn, "mart_stock_formula_optuna"):
            sql = """SELECT formula_variant, holding_days,
                            vol_bin, amt_bin, price_pos_bin, stage_bin,
                            COUNT(*) AS n_stocks, AVG(win_rate) AS avg_win,
                            AVG(avg_ret) AS avg_ret, AVG(avg_dd) AS avg_dd,
                            AVG(calmar) AS avg_cal
                       FROM mart_stock_formula_optuna
                      WHERE formula_id=? AND is_best_hd=TRUE AND n_signals >= ?"""
            params = [formula_id, min_n]
            if variant:
                sql += " AND formula_variant=?"
                params.append(variant)
            sql += """ GROUP BY formula_variant, holding_days,
                                vol_bin, amt_bin, price_pos_bin, stage_bin
                       HAVING n_stocks >= 3
                       ORDER BY avg_cal DESC LIMIT 30"""
            rows = conn.execute(sql, params).fetchall()
            buckets = [
                {"variant": r[0], "holding_days": r[1],
                 "vol_bin": r[2], "amt_bin": r[3], "price_pos_bin": r[4], "stage_bin": r[5],
                 "n_stocks": r[6], "avg_win_rate": r[7], "avg_ret": r[8],
                 "avg_dd": r[9], "avg_calmar": r[10]} for r in rows
            ]

        # 3. 最适合该公式的股票 (top 10 stocks)
        top_stocks = []
        if _table_exists(conn, "mart_stock_formula_optuna"):
            sql = """SELECT stock_code, formula_variant, holding_days,
                            n_signals, win_rate, avg_ret, avg_dd, calmar
                       FROM mart_stock_formula_optuna
                      WHERE formula_id=? AND is_high_conviction=TRUE AND n_signals >= ?"""
            params = [formula_id, min_n]
            if variant:
                sql += " AND formula_variant=?"
                params.append(variant)
            sql += " ORDER BY calmar DESC LIMIT 10"
            rows = conn.execute(sql, params).fetchall()
            top_stocks = [
                {"stock_code": r[0], "variant": r[1], "holding_days": r[2],
                 "n_signals": r[3], "win_rate": r[4], "avg_ret": r[5],
                 "avg_dd": r[6], "calmar": r[7]} for r in rows
            ]

        # 4. 今日触发 + 推荐
        today_triggers = []
        if _table_exists(conn, "fact_technical_trigger"):
            today_row = conn.execute(
                "SELECT MAX(date) FROM fact_technical_trigger WHERE formula_id=?",
                [formula_id],
            ).fetchone()
            today = today_row[0] if today_row else None
            if today:
                sql = """SELECT stock_code, formula_variant, strength
                           FROM fact_technical_trigger
                          WHERE formula_id=? AND date=?"""
                params = [formula_id, today]
                if variant:
                    sql += " AND formula_variant=?"
                    params.append(variant)
                sql += " ORDER BY strength DESC LIMIT 50"
                rows = conn.execute(sql, params).fetchall()
                today_triggers = [
                    {"stock_code": r[0], "variant": r[1], "strength": r[2],
                     "signal_date": today} for r in rows
                ]

        return {
            "ok": True,
            "formula_id": formula_id,
            "variant_filter": variant,
            "global_evidence": evidence,
            "top_buckets": buckets,
            "top_stocks": top_stocks,
            "today_triggers": today_triggers,
        }
    finally:
        conn.close()


# ============ 3. INSTITUTION VIEW ============
@router.get("/institution/{institution_id}")
async def institution_view(institution_id: str):
    """单机构视图: profile + 当前持仓 + 跟随回测胜率。"""
    conn = get_conn()
    try:
        # 1. profile (来自 mart_institution_profile)
        profile = None
        if _table_exists(conn, "mart_institution_profile"):
            row = conn.execute(
                """SELECT institution_id, institution_name, inst_type,
                          win_rate_30d, win_rate_60d, win_rate_90d,
                          current_stock_count, total_events, total_periods,
                          avg_gain_30d, avg_gain_60d
                     FROM mart_institution_profile
                    WHERE institution_id=?""",
                [institution_id],
            ).fetchone()
            if row:
                profile = {
                    "institution_id": row[0], "name": row[1], "type": row[2],
                    "win_rate_30d": row[3], "win_rate_60d": row[4], "win_rate_90d": row[5],
                    "current_stock_count": row[6],
                    "total_events": row[7], "total_periods": row[8],
                    "avg_gain_30d": row[9], "avg_gain_60d": row[10],
                }

        if not profile:
            raise HTTPException(status_code=404, detail=f"institution {institution_id} 未找到")

        # 2. 当前持仓 (用 institution_name 在 fact_top10_holder_period 查最新一期)
        holdings = []
        latest_rpt = conn.execute(
            """SELECT MAX(report_date) FROM fact_top10_holder_period
                WHERE holder_name_norm=? OR holder_name=?""",
            [profile["name"], profile["name"]],
        ).fetchone()
        if latest_rpt and latest_rpt[0]:
            rows = conn.execute(
                """SELECT stock_code, stock_name, hold_ratio_total, hold_change_num
                     FROM fact_top10_holder_period
                    WHERE (holder_name_norm=? OR holder_name=?)
                      AND report_date=?
                    ORDER BY hold_ratio_total DESC LIMIT 50""",
                [profile["name"], profile["name"], latest_rpt[0]],
            ).fetchall()
            holdings = [
                {"stock_code": r[0], "stock_name": r[1],
                 "share_pct": r[2], "share_change": r[3]} for r in rows
            ]

        # 3. 跟随回测 (来自 fact_institution_follow_backtest, 若有)
        follow_backtest = None
        if _table_exists(conn, "fact_institution_follow_backtest"):
            try:
                row = conn.execute(
                    """SELECT n_filled, win_rate, avg_pnl, sharpe, avg_position_maxdd
                         FROM fact_institution_follow_backtest
                        WHERE cohort_id=? OR cohort_id LIKE ?
                        ORDER BY built_at DESC LIMIT 1""",
                    [institution_id, f"%{institution_id}%"],
                ).fetchone()
                if row:
                    follow_backtest = {
                        "n_trades": row[0], "win_rate": row[1],
                        "avg_pnl": row[2], "sharpe": row[3],
                        "avg_max_dd": row[4],
                    }
            except Exception as e:
                logger.debug(f"follow_backtest lookup failed: {e}")

        return {
            "ok": True,
            "institution_id": institution_id,
            "profile": profile,
            "holdings": holdings,
            "follow_backtest": follow_backtest,
        }
    finally:
        conn.close()


# ============ 4. STOCK BUY SIGNALS (Phase η+++++/++++++) ============
@router.get("/stock/{stock_code}/buy-signals")
async def stock_buy_signals(stock_code: str):
    """单股全公式买点判定 (Phase η+++++).

    返回每个公式当日的 tier/score/reasoning + 8 因子明细 + Optuna 寻优明细.
    """
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_stock_formula_buy_signal_daily"):
            return {"ok": True, "stock_code": stock_code, "signals": [], "message": "buy_signal 表未生成"}

        rows = conn.execute(
            """SELECT signal_date, formula_id, formula_variant,
                      score, tier, reasoning,
                      factor_trigger, factor_bucket_match, factor_historical_alpha,
                      factor_stage_fitness, factor_fundamental_stage, factor_sentiment,
                      factor_stock_archetype, factor_primary_type,
                      historical_sharpe, historical_win_rate, historical_n_traded,
                      optimal_hp, optimal_stop_pct, optimal_target_pct,
                      optimal_trailing_pct, optimal_buy_offset,
                      today_technical_stage, today_fundamental_stage,
                      today_stock_archetype, today_primary_type, today_survey_bin
                 FROM mart_stock_formula_buy_signal_daily
                WHERE stock_code = ?
                  AND signal_date = (SELECT MAX(signal_date) FROM mart_stock_formula_buy_signal_daily WHERE stock_code = ?)
                ORDER BY score DESC""",
            [stock_code, stock_code],
        ).fetchall()

        signals = [{
            "signal_date": r[0],
            "formula_id": r[1], "formula_variant": r[2],
            "score": r[3], "tier": r[4], "reasoning": r[5],
            "factors": {
                "trigger": r[6], "bucket_match": r[7], "historical_alpha": r[8],
                "stage_fitness": r[9], "fundamental_stage": r[10], "sentiment": r[11],
                "stock_archetype": r[12], "primary_type": r[13],
            },
            "historical": {"sharpe": r[14], "win_rate": r[15], "n_traded": r[16]},
            "optimal": {
                "hp": r[17], "stop_pct": r[18], "target_pct": r[19],
                "trailing_pct": r[20], "buy_offset": r[21],
            },
            "today": {
                "technical_stage": r[22], "fundamental_stage": r[23],
                "stock_archetype": r[24], "primary_type": r[25], "survey_bin": r[26],
            },
        } for r in rows]

        # KPI summary (best signal)
        kpi = None
        if signals:
            best = signals[0]
            kpi = {
                "best_formula": best["formula_variant"],
                "best_score": best["score"],
                "best_tier": best["tier"],
                "best_sharpe": best["historical"]["sharpe"],
                "best_win_rate": best["historical"]["win_rate"],
                "n_signals_today": len(signals),
            }
        return {"ok": True, "stock_code": stock_code, "kpi": kpi, "signals": signals}
    finally:
        conn.close()


# ============ 5. FORMULA STAGE FITNESS MATRIX (Phase η++++++) ============
@router.get("/formula/{formula_variant}/stage-fitness")
async def formula_stage_fitness(formula_variant: str):
    """单公式 在 (fund_stage × tech_stage) 矩阵下的适配度 (Phase η++++++).

    数据源: mart_stage_formula_fitness (6 fund × 6 tech × 7 hp 矩阵)
    """
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_stage_formula_fitness"):
            return {"ok": True, "formula_variant": formula_variant, "matrix": [],
                    "message": "mart_stage_formula_fitness 未生成"}

        rows = conn.execute(
            """SELECT fundamental_stage, technical_stage, holding_days,
                      n_signals, n_stocks, win_rate, avg_ret, avg_dd,
                      calmar, sharpe, rank_in_stage, is_recommended
                 FROM mart_stage_formula_fitness
                WHERE formula_variant = ?
                ORDER BY fundamental_stage, technical_stage, holding_days""",
            [formula_variant],
        ).fetchall()

        matrix = [{
            "fundamental_stage": r[0], "technical_stage": r[1],
            "holding_days": r[2],
            "n_signals": r[3], "n_stocks": r[4],
            "win_rate": r[5], "avg_ret": r[6], "avg_dd": r[7],
            "calmar": r[8], "sharpe": r[9],
            "rank_in_stage": r[10], "is_recommended": bool(r[11]),
        } for r in rows]

        # 提取 best hp per stage 组合 (供 UI 矩阵展示)
        best_per_stage: dict = {}
        for r in matrix:
            k = (r["fundamental_stage"], r["technical_stage"])
            if k not in best_per_stage or r["sharpe"] > best_per_stage[k]["sharpe"]:
                best_per_stage[k] = r

        return {
            "ok": True,
            "formula_variant": formula_variant,
            "matrix": matrix,
            "best_per_stage_combo": [v for v in best_per_stage.values()],
            "total_combos": len(matrix),
            "recommended_combos": sum(1 for r in matrix if r["is_recommended"]),
        }
    finally:
        conn.close()
