"""Phase η++ — 组合构建器 API (3 risk profile 推荐).

端点:
  GET /api/v3/portfolio/profiles            — 3 个 profile 参数表
  GET /api/v3/portfolio/recommendations     — 每日推荐 (按 profile 过滤)
  GET /api/v3/portfolio/backtest            — Phase η+++++++ walk-forward NAV + KPI + regime
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, HTTPException

from services.db import get_conn


# 与 scripts/portfolio_backtest.py 同一路径 (output of walk-forward backtest)
NAV_CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "portfolio_backtest_nav.csv"

logger = logging.getLogger("cm-api.v3-portfolio-builder")
router = APIRouter()


def _table_exists(conn, table: str) -> bool:
    r = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(r and r[0])


@router.get("/profiles")
async def get_profiles():
    """3 个 risk profile 参数表."""
    from services.portfolio_sizer.profiles import list_profiles
    return {"ok": True, "data": list_profiles()}


@router.get("/factors")
async def get_factors():
    """sentiment / context 因子注册表摘要 (Phase η++++)."""
    from services.sentiment.factor_registry import factor_summary
    return {"ok": True, "data": factor_summary()}


@router.get("/buy-signals")
async def get_buy_signals(
    tier: str | None = Query(None, description="STRONG_BUY / BUY / WATCH / NO_SIGNAL"),
    signal_date: str | None = Query(None, description="default=最新"),
    limit: int = Query(50, ge=1, le=500),
):
    """Phase η+++++ 形态识别 + 每股每公式买点判定 (mart_stock_formula_buy_signal_daily).

    返回每股每公式的综合 score + tier + reasoning + 6 因子明细.
    """
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_stock_formula_buy_signal_daily"):
            return {"ok": True, "data": [], "total": 0,
                    "message": "未生成, 先跑 scripts/build_stock_formula_buy_signal_daily.py"}

        if signal_date is None:
            r = conn.execute("SELECT MAX(signal_date) FROM mart_stock_formula_buy_signal_daily").fetchone()
            signal_date = r[0] if r else None
        if not signal_date:
            return {"ok": True, "data": [], "total": 0}

        # 构造 where
        params = [signal_date]
        tier_where = ""
        if tier:
            tier_where = " AND tier = ?"
            params.append(tier)
        params.append(limit)

        rows = conn.execute(
            f"""SELECT signal_date, stock_code, formula_id, formula_variant,
                       score, tier, reasoning,
                       factor_trigger, factor_bucket_match, factor_historical_alpha,
                       factor_technical_stage, factor_fundamental_stage, factor_sentiment,
                       contrib_trigger, contrib_bucket_match, contrib_historical_alpha,
                       contrib_technical_stage, contrib_fundamental_stage, contrib_sentiment,
                       today_technical_stage, today_fundamental_stage, today_survey_bin,
                       today_vol_bin, today_amt_bin, today_p60_bin,
                       historical_sharpe, historical_win_rate, historical_n_traded,
                       optimal_hp, optimal_stop_pct, optimal_target_pct,
                       optimal_trailing_pct, optimal_buy_offset
                  FROM mart_stock_formula_buy_signal_daily
                 WHERE signal_date = ? {tier_where}
                 ORDER BY score DESC
                 LIMIT ?""",
            params,
        ).fetchall()

        data = [{
            "signal_date": r[0], "stock_code": r[1],
            "formula_id": r[2], "formula_variant": r[3],
            "score": r[4], "tier": r[5], "reasoning": r[6],
            "factors": {
                "trigger": r[7], "bucket_match": r[8], "historical_alpha": r[9],
                "technical_stage": r[10], "fundamental_stage": r[11], "sentiment": r[12],
            },
            "contributions": {
                "trigger": r[13], "bucket_match": r[14], "historical_alpha": r[15],
                "technical_stage": r[16], "fundamental_stage": r[17], "sentiment": r[18],
            },
            "today": {
                "technical_stage": r[19], "fundamental_stage": r[20], "survey_bin": r[21],
                "vol_bin": r[22], "amt_bin": r[23], "p60_bin": r[24],
            },
            "historical": {
                "sharpe": r[25], "win_rate": r[26], "n_traded": r[27],
            },
            "optimal": {
                "hp": r[28], "stop_pct": r[29], "target_pct": r[30],
                "trailing_pct": r[31], "buy_offset": r[32],
            },
        } for r in rows]

        # 整体 tier 分布
        tier_dist = dict(conn.execute(
            "SELECT tier, COUNT(*) FROM mart_stock_formula_buy_signal_daily WHERE signal_date=? GROUP BY 1",
            [signal_date],
        ).fetchall())

        return {
            "ok": True,
            "data": data,
            "signal_date": signal_date,
            "total": len(data),
            "tier_distribution": tier_dist,
        }
    finally:
        conn.close()


@router.get("/recommendations")
async def get_recommendations(
    profile: str = Query(..., description="short / mid / long"),
    signal_date: str | None = Query(None, description="default=最新"),
    limit: int = Query(20, ge=1, le=100),
):
    """获取指定 profile 的当日推荐 (按 rank_in_profile 升序)."""
    if profile not in ("short", "mid", "long"):
        raise HTTPException(status_code=400, detail=f"invalid profile {profile}")
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_daily_position_recommendation"):
            return {"ok": True, "data": [], "profile": profile, "total": 0,
                    "message": "未生成推荐, 请先跑 scripts/build_daily_position_recommendations.py"}

        if signal_date is None:
            r = conn.execute(
                "SELECT MAX(signal_date) FROM mart_daily_position_recommendation WHERE profile_id=?",
                [profile],
            ).fetchone()
            signal_date = r[0] if r else None
        if not signal_date:
            return {"ok": True, "data": [], "profile": profile, "total": 0}

        # match_tier 列在 Phase η+++ 添加, 老库可能没有 — 用 try/except 兼容
        # survey_bin / sentiment_mult 是 Phase η++++ 新增
        cols_select = """rank_in_profile, stock_code,
                      formula_id, formula_variant,
                      vol_bin, amt_bin, price_pos_bin, stage_bin,
                      fundamental_stage, match_tier,
                      survey_bin, survey_count_60d, sentiment_mult, sentiment_trace,
                      n_signals, raw_win_rate, wilson_win_rate,
                      avg_ret, avg_dd, sharpe, calmar,
                      kelly_f, position_pct, confidence_tier, score,
                      holding_days,
                      optimal_stop_pct, optimal_target_pct, optimal_trailing_pct,
                      signal_close_price, buy_price,
                      sell_target_price, stop_price, trailing_pct,
                      signal_date, buy_date"""
        try:
            rows = conn.execute(
                f"""SELECT {cols_select}
                     FROM mart_daily_position_recommendation
                    WHERE profile_id=? AND signal_date=?
                    ORDER BY rank_in_profile
                    LIMIT ?""",
                [profile, signal_date, limit],
            ).fetchall()
        except Exception:
            # 兜底: 老 schema 没 match_tier / sentiment / optimal_* 列
            rows = conn.execute(
                """SELECT rank_in_profile, stock_code,
                          formula_id, formula_variant,
                          vol_bin, amt_bin, price_pos_bin, stage_bin,
                          fundamental_stage, NULL AS match_tier,
                          NULL AS survey_bin, NULL AS survey_count_60d,
                          NULL AS sentiment_mult, NULL AS sentiment_trace,
                          n_signals, raw_win_rate, wilson_win_rate,
                          avg_ret, avg_dd, sharpe, calmar,
                          kelly_f, position_pct, confidence_tier, score,
                          holding_days,
                          NULL AS optimal_stop_pct, NULL AS optimal_target_pct, NULL AS optimal_trailing_pct,
                          signal_close_price, buy_price,
                          sell_target_price, stop_price, trailing_pct,
                          signal_date, buy_date
                     FROM mart_daily_position_recommendation
                    WHERE profile_id=? AND signal_date=?
                    ORDER BY rank_in_profile
                    LIMIT ?""",
                [profile, signal_date, limit],
            ).fetchall()

        data = [
            {
                "rank": r[0], "stock_code": r[1],
                "formula_id": r[2], "formula_variant": r[3],
                "vol_bin": r[4], "amt_bin": r[5], "price_pos_bin": r[6], "stage_bin": r[7],
                "fundamental_stage": r[8], "match_tier": r[9],
                "survey_bin": r[10], "survey_count_60d": r[11],
                "sentiment_mult": r[12], "sentiment_trace": r[13],
                "n_signals": r[14], "raw_win_rate": r[15], "wilson_win_rate": r[16],
                "avg_ret": r[17], "avg_dd": r[18], "sharpe": r[19], "calmar": r[20],
                "kelly_f": r[21], "position_pct": r[22],
                "confidence_tier": r[23], "score": r[24],
                "holding_days": r[25],
                # Phase ζ: 寻优明细 (用户能看到这是寻优 vs 默认)
                "optimal_stop_pct": r[26], "optimal_target_pct": r[27], "optimal_trailing_pct": r[28],
                "signal_close_price": r[29], "buy_price": r[30],
                "sell_target_price": r[31], "stop_price": r[32], "trailing_pct": r[33],
                "signal_date": r[34], "buy_date": r[35],
            } for r in rows
        ]

        # 加总: 总仓位 + 现金占比
        total_pos = sum(d["position_pct"] or 0 for d in data)
        return {
            "ok": True,
            "data": data,
            "profile": profile,
            "signal_date": signal_date,
            "total_positions": len(data),
            "total_position_pct": round(total_pos, 4),
            "cash_pct": round(1.0 - total_pos, 4),
        }
    finally:
        conn.close()


# ============ Phase η+++++++ ψ.3 — walk-forward backtest endpoint ============
@router.get("/backtest")
async def get_portfolio_backtest(
    start: str | None = Query(None, description="过滤起始日 (YYYY-MM-DD)"),
    end:   str | None = Query(None, description="过滤结束日 (YYYY-MM-DD)"),
    sample: int = Query(0, ge=0, le=10000, description=">0 时下采样到该点数 (用于 UI 性能)"),
):
    """Phase η+++++++ walk-forward portfolio backtest 完整结果.

    数据源: scripts/portfolio_backtest.py 输出的 NAV CSV (策略 vs HS300).
    实时算 KPI + regime + excess alpha — 不存中间表, 每次请求重算 (~10ms).

    返回:
      - nav_curve: 日级 (date, strategy_nav, benchmark_nav, excess_pct)
      - metrics: 年化 / max_dd / Sharpe / Calmar / IR / monthly_win / total_return
      - regime_segments: 牛/熊/震荡 各段平均收益 (HS300 60d 阈值)
      - user_criteria: 用户 3 个 PASS 标准的 ✅/❌
    """
    if not NAV_CSV_PATH.exists():
        return {
            "ok": False,
            "message": f"NAV CSV 未生成 (期望路径: {NAV_CSV_PATH}). 先跑 scripts/portfolio_backtest.py.",
            "nav_curve": [], "metrics": {}, "regime_segments": [],
        }

    # 1. 读 CSV
    dates: list[str] = []
    s_navs: list[float] = []
    b_navs: list[float] = []
    with open(NAV_CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            d = row["date"]
            if start and d < start:
                continue
            if end and d > end:
                continue
            dates.append(d)
            s_navs.append(float(row["strategy_nav"]))
            b_navs.append(float(row["benchmark_nav"]))

    if len(s_navs) < 2:
        return {"ok": False, "message": "NAV 数据太少 (<2 行)", "nav_curve": [], "metrics": {}}

    # 2. 实时算 metrics + excess alpha
    from services.portfolio_walk_forward.metrics import compute_metrics, compute_excess_alpha
    m_strategy = compute_metrics(s_navs)
    m_benchmark = compute_metrics(b_navs)
    excess = compute_excess_alpha(s_navs, b_navs)

    # 3. regime 分段统计 (HS300 60d 回报)
    try:
        from services.portfolio_walk_forward.regime import classify_regime
        regime_buckets: dict[str, list[float]] = {"bull": [], "bear": [], "sideways": []}
        for i in range(60, len(b_navs)):
            r60 = b_navs[i] / b_navs[i - 60] - 1
            label = classify_regime(r60)
            # 该日策略 vs 前 1 日的日收益
            s_d = s_navs[i] / s_navs[i - 1] - 1
            regime_buckets[label].append(s_d)
        regime_segments = []
        for label, rets in regime_buckets.items():
            if rets:
                # cumulative return for that regime
                cum = 1.0
                for r in rets:
                    cum *= (1 + r)
                regime_segments.append({
                    "regime": label,
                    "n_days": len(rets),
                    "avg_daily_ret": sum(rets) / len(rets),
                    "cumulative_return": cum - 1,
                })
            else:
                regime_segments.append({"regime": label, "n_days": 0,
                                         "avg_daily_ret": 0, "cumulative_return": 0})
    except Exception as exc:
        logger.warning("regime split failed: %s", exc)
        regime_segments = []

    # 4. 用户 3 标准 PASS 检查
    user_criteria = {
        "annual_return_ge_30pct":  {"target": 0.30,  "actual": m_strategy.annual_return,
                                     "pass": m_strategy.annual_return >= 0.30},
        "max_dd_ge_neg_20pct":     {"target": -0.20, "actual": m_strategy.max_drawdown,
                                     "pass": m_strategy.max_drawdown >= -0.20},
        "excess_vs_hs300_positive": {"target": 0.0,
                                      "actual": excess.get("excess_total_return", 0),
                                      "pass": excess.get("excess_total_return", 0) > 0},
    }
    all_pass = all(c["pass"] for c in user_criteria.values())

    # 5. NAV 曲线 (可选下采样)
    nav_curve = []
    n_pts = len(dates)
    step = max(1, n_pts // sample) if sample > 0 else 1
    for i in range(0, n_pts, step):
        nav_curve.append({
            "date": dates[i],
            "strategy_nav": s_navs[i],
            "benchmark_nav": b_navs[i],
            "excess_pct": (s_navs[i] / s_navs[0]) - (b_navs[i] / b_navs[0]),
        })
    # 确保最后一个点一定包含
    if nav_curve and nav_curve[-1]["date"] != dates[-1]:
        nav_curve.append({
            "date": dates[-1],
            "strategy_nav": s_navs[-1],
            "benchmark_nav": b_navs[-1],
            "excess_pct": (s_navs[-1] / s_navs[0]) - (b_navs[-1] / b_navs[0]),
        })

    return {
        "ok": True,
        "csv_path": str(NAV_CSV_PATH),
        "period": {"start": dates[0], "end": dates[-1], "n_days": n_pts},
        "metrics": {
            "strategy": {
                "total_return":   m_strategy.total_return,
                "annual_return":  m_strategy.annual_return,
                "max_drawdown":   m_strategy.max_drawdown,
                "calmar":         m_strategy.calmar,
                "sharpe":         m_strategy.sharpe,
                "monthly_win_rate": m_strategy.monthly_win_rate,
            },
            "benchmark": {
                "total_return":   m_benchmark.total_return,
                "annual_return":  m_benchmark.annual_return,
                "max_drawdown":   m_benchmark.max_drawdown,
                "sharpe":         m_benchmark.sharpe,
            },
            "excess": excess,
        },
        "regime_segments": regime_segments,
        "user_criteria": user_criteria,
        "all_user_criteria_pass": all_pass,
        "nav_curve": nav_curve,
    }
