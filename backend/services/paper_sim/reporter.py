"""Paper Sim v2 — KPI metrics + 决策树阻断式验证.

输出 6 类 20 KPI:
  A. 用户 3 标准 + 月胜率
  B. Anti-churn (用户最关心: 调仓频率高 = 无效)
  C. Robustness (跨周期稳定)
  D/E/F. ablation/sensitivity/reality-check 由 run_paper_sim_v2.py 在多 variant
         调用本模块 + 自己计算交叉比较

复用 services/portfolio_walk_forward/metrics 已有的 compute_metrics +
compute_excess_alpha. Paper sim 特化指标 (anti-churn, swap uplift) 在这里实现.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Optional

import numpy as np

from services.paper_sim.config import PaperSimConfig
from services.portfolio_walk_forward.metrics import compute_metrics, compute_excess_alpha
from services.portfolio_walk_forward.regime import classify_regime


def _load_nav_series(conn, sim_run_id: str) -> tuple[list[str], list[float], list[Optional[float]]]:
    """加载 mart_paper_sim_nav. Returns (dates, total_values, hs300_navs)."""
    rows = conn.execute(
        """SELECT date, total_value, hs300_nav FROM mart_paper_sim_nav
            WHERE sim_run_id = ?
            ORDER BY date""",
        [sim_run_id],
    ).fetchall()
    dates = [r[0] for r in rows]
    navs = [float(r[1]) for r in rows]
    hs300 = [float(r[2]) if r[2] is not None else None for r in rows]
    return dates, navs, hs300


def _load_trade_log(conn, sim_run_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT trade_id, position_id, date, type, reason, price, shares,
                  gross_amount, tx_cost, net_amount, swap_uplift_estimate
             FROM fact_paper_sim_trade
            WHERE sim_run_id = ?
            ORDER BY date""",
        [sim_run_id],
    ).fetchall()
    return [
        {"trade_id": r[0], "position_id": r[1], "date": r[2], "type": r[3],
         "reason": r[4], "price": float(r[5]), "shares": int(r[6]),
         "gross": float(r[7]), "tx_cost": float(r[8]), "net": float(r[9]),
         "swap_uplift": float(r[10]) if r[10] is not None else None}
        for r in rows
    ]


def _load_closed_positions(conn, sim_run_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT stock_code, open_date, close_date, days_held, pnl, pnl_pct, close_reason,
                  COALESCE(exit_source, 'pit') AS exit_source, buy_cost
             FROM fact_paper_sim_position
            WHERE sim_run_id = ? AND is_open = FALSE""",
        [sim_run_id],
    ).fetchall()
    return [
        {"stock_code": r[0], "open_date": r[1], "close_date": r[2],
         "days_held": int(r[3] or 0), "pnl": float(r[4] or 0),
         "pnl_pct": float(r[5] or 0), "reason": r[6],
         "exit_source": str(r[7]) if r[7] else "pit",
         "buy_cost": float(r[8] or 0)}
        for r in rows
    ]


# Phase 1a Option C (Codex round 4 MAJOR + round 6 MAJOR fix): exit_source 分层 attribution
def compute_partition_kpi_by_exit_source(
    conn, sim_run_id: str,
    initial_cash: float = 1_000_000,
    period_days: int | None = None,
) -> dict:
    """按 exit_source ('pit' / 'fallback' / 'all') 算 closed position attribution.

    Codex round 6 MAJOR #2 fix: ann_ret_approx 按**测试窗口长度** annualize (period_days),
    不按平均持仓期 (那样不可加总, 容易夸大). 若 period_days 缺, 从 navs date range 推.

    返回 dict 含每个 partition 的 (count / sum_pnl / sum_buy_cost / pnl_pct_aggregate / ann_ret_approx).

    用于 Phase 1a F vs C 对照: 跑 C.yaml 后看 pit_only 跟 fallback_only 各自贡献.
    """
    closed = _load_closed_positions(conn, sim_run_id)
    # period_days: 优先 arg, 否则从 mart_paper_sim_nav date 推. 至少 1 防 0 除.
    if period_days is None:
        try:
            r = conn.execute(
                "SELECT COUNT(DISTINCT date) FROM mart_paper_sim_nav WHERE sim_run_id = ?",
                [sim_run_id],
            ).fetchone()
            period_days = int(r[0] or 1)
        except Exception:
            period_days = 1
    period_days = max(int(period_days), 1)

    if not closed:
        empty = {"count": 0, "sum_pnl": 0.0, "sum_buy_cost": 0.0,
                 "pnl_pct_aggregate": 0.0, "ann_ret_approx": 0.0,
                 "win_rate": 0.0, "avg_days_held": 0.0}
        return {"all": empty, "pit_only": empty, "fallback_only": empty,
                "period_days": period_days}

    def _aggregate(positions: list[dict]) -> dict:
        if not positions:
            return {"count": 0, "sum_pnl": 0.0, "sum_buy_cost": 0.0,
                    "pnl_pct_aggregate": 0.0, "ann_ret_approx": 0.0,
                    "win_rate": 0.0, "avg_days_held": 0.0}
        sum_pnl = sum(p["pnl"] for p in positions)
        sum_cost = sum(p["buy_cost"] for p in positions)
        wins = sum(1 for p in positions if p["pnl"] > 0)
        avg_days = sum(p["days_held"] for p in positions) / len(positions)
        return {
            "count": len(positions),
            "sum_pnl": sum_pnl,
            "sum_buy_cost": sum_cost,
            "pnl_pct_aggregate": sum_pnl / sum_cost if sum_cost > 0 else 0.0,
            # Codex round 6 MAJOR #2: 按测试窗口 annualize, 不按平均持仓期
            "ann_ret_approx": (sum_pnl / initial_cash) * (252.0 / period_days),
            "win_rate": wins / len(positions),
            "avg_days_held": avg_days,
        }

    pit_only = [p for p in closed if p["exit_source"] == "pit"]
    fallback_only = [p for p in closed if p["exit_source"] == "fallback"]
    return {
        "all": _aggregate(closed),
        "pit_only": _aggregate(pit_only),
        "fallback_only": _aggregate(fallback_only),
        "period_days": period_days,
    }


# ===================== A. 用户终极标准 =====================

def compute_user_criteria(navs: list[float], hs300: list[Optional[float]]) -> dict:
    """年化 / max_dd / sharpe / calmar / 月胜率 / 超额 / IR."""
    m = compute_metrics(navs)
    out = {
        "annual_return": m.annual_return,
        "max_dd": m.max_drawdown,
        "sharpe": m.sharpe,
        "calmar": m.calmar,
        "monthly_win_rate": m.monthly_win_rate,
        "total_return": m.total_return,
    }
    # HS300 对比
    valid_hs300 = [h for h in hs300 if h is not None]
    if len(valid_hs300) == len(navs) and len(navs) >= 2:
        hs300_initial = valid_hs300[0]
        hs300_navs_norm = [h / hs300_initial for h in valid_hs300]   # 归一化到 1
        # 策略 navs 也归一化
        strategy_navs_norm = [n / navs[0] for n in navs]
        excess = compute_excess_alpha(strategy_navs_norm, hs300_navs_norm)
        out.update({
            "excess_total_return": excess.get("excess_total_return", 0),
            "information_ratio": excess.get("information_ratio", 0),
        })
    else:
        out["excess_total_return"] = 0
        out["information_ratio"] = 0
    return out


# ===================== B. Anti-churn =====================

def compute_anti_churn(
    closed_positions: list[dict],
    trades: list[dict],
    initial_capital: float,
    period_days: int,
) -> dict:
    """关键 KPI — 用户最在意调仓频率."""
    n_closed = len(closed_positions)
    if n_closed == 0:
        return {
            "avg_holding_days": 0, "annual_turnover": 0,
            "tx_cost_total": 0, "tx_cost_pct_of_gross_pnl": 0,
            "swap_count": 0, "swap_uplift_total": 0,
            "n_closed_positions": 0,
        }
    avg_days = sum(p["days_held"] for p in closed_positions) / n_closed

    # 总交易额 / 初始资金 → 年化换手
    gross_total = sum(t["gross"] for t in trades)
    if period_days > 0:
        annual_turnover = (gross_total / initial_capital) * (252 / period_days)
    else:
        annual_turnover = 0

    tx_cost_total = sum(t["tx_cost"] for t in trades)
    gross_pnl = sum(p["pnl"] for p in closed_positions if p["pnl"] > 0)
    tx_pct = tx_cost_total / gross_pnl if gross_pnl > 0 else 1.0

    swap_trades = [t for t in trades if t["type"] in {"SWAP_OUT", "SWAP_IN"}]
    swap_count = len(swap_trades) // 2  # 每次 swap 一 in 一 out
    swap_uplift_total = sum(t["swap_uplift"] or 0 for t in swap_trades if t["type"] == "SWAP_OUT")

    return {
        "avg_holding_days": avg_days,
        "annual_turnover": annual_turnover,
        "tx_cost_total": tx_cost_total,
        "tx_cost_pct_of_gross_pnl": tx_pct,
        "swap_count": swap_count,
        "swap_uplift_total": swap_uplift_total,
        "n_closed_positions": n_closed,
    }


# ===================== C. Robustness =====================

def compute_robustness(navs: list[float], hs300: list[Optional[float]]) -> dict:
    """60d 滚动 IR 中位数 + p25; 90d 滚动年化 中位数; regime 分层."""
    n = len(navs)
    if n < 90:
        return {
            "rolling_ir_60d_median": None, "rolling_ir_60d_p25": None,
            "rolling_annual_90d_median": None,
            "regime_bull_return": None, "regime_bear_return": None,
            "regime_sideways_return": None,
        }

    nav_arr = np.array(navs)
    daily_ret = np.diff(nav_arr) / nav_arr[:-1]

    # HS300 daily ret
    valid_hs300 = [h for h in hs300 if h is not None]
    has_hs300 = len(valid_hs300) == n
    if has_hs300:
        hs300_arr = np.array(valid_hs300)
        hs300_daily = np.diff(hs300_arr) / hs300_arr[:-1]
        excess_daily = daily_ret - hs300_daily
    else:
        excess_daily = None

    # 60d 滚动 IR
    rolling_ir = []
    if excess_daily is not None:
        for i in range(60, len(excess_daily) + 1):
            window = excess_daily[i - 60:i]
            if window.std() > 0:
                ir = window.mean() / window.std() * np.sqrt(252)
                rolling_ir.append(float(ir))

    rolling_ir_arr = np.array(rolling_ir) if rolling_ir else np.array([])

    # 90d 滚动年化
    rolling_annual = []
    for i in range(90, n + 1):
        window_nav = nav_arr[i - 90:i]
        total = window_nav[-1] / window_nav[0] - 1
        ann = (1 + total) ** (252 / 90) - 1
        rolling_annual.append(float(ann))
    rolling_annual_arr = np.array(rolling_annual) if rolling_annual else np.array([])

    # Regime 分层 (HS300 60d 回报)
    regime_returns = {"bull": [], "bear": [], "sideways": []}
    if has_hs300 and n >= 60:
        for i in range(60, n):
            r60 = valid_hs300[i] / valid_hs300[i - 60] - 1
            label = classify_regime(r60)
            regime_returns[label].append(float(daily_ret[i - 1]) if i - 1 < len(daily_ret) else 0)

    def _cum(rets: list) -> float:
        cum = 1.0
        for r in rets:
            cum *= (1 + r)
        return cum - 1

    return {
        "rolling_ir_60d_median": float(np.median(rolling_ir_arr)) if rolling_ir_arr.size > 0 else None,
        "rolling_ir_60d_p25": float(np.percentile(rolling_ir_arr, 25)) if rolling_ir_arr.size > 0 else None,
        "rolling_annual_90d_median": float(np.median(rolling_annual_arr)) if rolling_annual_arr.size > 0 else None,
        "regime_bull_return": _cum(regime_returns["bull"]) if regime_returns["bull"] else None,
        "regime_bear_return": _cum(regime_returns["bear"]) if regime_returns["bear"] else None,
        "regime_sideways_return": _cum(regime_returns["sideways"]) if regime_returns["sideways"] else None,
    }


# ===================== 决策树阻断 =====================

def check_user_criteria_pass(uc: dict, thresh: dict) -> bool:
    return (
        uc.get("annual_return", 0) >= thresh["annual_return_min"]
        and uc.get("max_dd", 0) >= thresh["max_dd_min"]
        and uc.get("excess_total_return", 0) >= thresh["excess_vs_hs300_min"]
        and uc.get("monthly_win_rate", 0) >= thresh["monthly_win_rate_min"]
    )


def check_anti_churn_pass(ac: dict, thresh: dict) -> bool:
    return (
        ac["avg_holding_days"] >= thresh["avg_holding_days_min"]
        and ac["annual_turnover"] <= thresh["annual_turnover_max"]
        and ac["tx_cost_pct_of_gross_pnl"] <= thresh["tx_cost_pct_of_gross_pnl_max"]
        and ac["swap_uplift_total"] >= thresh["swap_uplift_total_min"]
    )


def check_robustness_pass(rb: dict, thresh: dict) -> bool:
    # 任意 metric None 都视为 fail (数据不够)
    needed = ["rolling_ir_60d_median", "rolling_ir_60d_p25",
              "rolling_annual_90d_median"]
    for k in needed:
        if rb.get(k) is None:
            return False
    if (rb["rolling_ir_60d_median"] < thresh["rolling_ir_60d_median_min"]
        or rb["rolling_ir_60d_p25"] < thresh["rolling_ir_60d_p25_min"]
        or rb["rolling_annual_90d_median"] < thresh["rolling_annual_90d_median_min"]):
        return False
    # regime 各段都不亏 (≥ -2%)
    floor = thresh["regime_min_return_per_segment"]
    for k in ["regime_bull_return", "regime_bear_return", "regime_sideways_return"]:
        if rb.get(k) is not None and rb[k] < floor:
            return False
    return True


# ===================== 总入口 =====================

def write_kpi_summary(
    conn,
    sim_run_id: str,
    variant: str,
    cfg: PaperSimConfig,
) -> dict:
    """跑完一次 walk-forward 调本函数. 算所有 KPI, 写 mart_paper_sim_kpi, 返回 dict.

    决策: 同一 sim_run_id 同一 variant 写 1 行 (INSERT OR REPLACE).
    """
    dates, navs, hs300 = _load_nav_series(conn, sim_run_id)
    if len(navs) < 2:
        raise RuntimeError(f"sim {sim_run_id} 没数据 (nav rows={len(navs)})")

    closed = _load_closed_positions(conn, sim_run_id)
    trades = _load_trade_log(conn, sim_run_id)

    uc = compute_user_criteria(navs, hs300)
    ac = compute_anti_churn(closed, trades, cfg.portfolio.initial_cash, len(navs))
    rb = compute_robustness(navs, hs300)

    pass_uc = check_user_criteria_pass(uc, cfg.validation.user_criteria)
    pass_ac = check_anti_churn_pass(ac, cfg.validation.anti_churn)
    pass_rb = check_robustness_pass(rb, cfg.validation.robustness)
    all_pass = pass_uc and pass_ac and pass_rb

    # Phase 1a Option C (Codex round 6 MAJOR #1 fix): exit_source 分层 attribution 落库
    partition = compute_partition_kpi_by_exit_source(
        conn, sim_run_id,
        initial_cash=float(cfg.portfolio.initial_cash),
        period_days=len(navs),
    )
    _pit = partition.get("pit_only") or {}
    _fb = partition.get("fallback_only") or {}

    conn.execute(
        """INSERT OR REPLACE INTO mart_paper_sim_kpi
        (sim_run_id, variant, period_start, period_end, n_days,
         annual_return, max_dd, sharpe, calmar, monthly_win_rate,
         total_return, excess_vs_hs300, information_ratio, user_criteria_pass,
         avg_holding_days, annual_turnover, tx_cost_total, tx_cost_pct_of_gross_pnl,
         swap_count, swap_uplift_total, anti_churn_pass,
         rolling_ir_60d_median, rolling_ir_60d_p25, rolling_annual_90d_median,
         regime_bull_return, regime_bear_return, regime_sideways_return,
         robustness_pass, all_kpi_pass, config_snapshot,
         pit_count, pit_pnl, pit_pnl_pct,
         fallback_count, fallback_pnl, fallback_pnl_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?)""",
        [sim_run_id, variant, dates[0], dates[-1], len(navs),
         uc["annual_return"], uc["max_dd"], uc["sharpe"], uc["calmar"],
         uc["monthly_win_rate"], uc["total_return"], uc.get("excess_total_return"),
         uc.get("information_ratio"), pass_uc,
         ac["avg_holding_days"], ac["annual_turnover"], ac["tx_cost_total"],
         ac["tx_cost_pct_of_gross_pnl"], ac["swap_count"], ac["swap_uplift_total"], pass_ac,
         rb.get("rolling_ir_60d_median"), rb.get("rolling_ir_60d_p25"),
         rb.get("rolling_annual_90d_median"),
         rb.get("regime_bull_return"), rb.get("regime_bear_return"),
         rb.get("regime_sideways_return"), pass_rb, all_pass,
         json.dumps({"portfolio": asdict(cfg.portfolio), "swap": asdict(cfg.swap)},
                    ensure_ascii=False),
         _pit.get("count"), _pit.get("sum_pnl"), _pit.get("pnl_pct_aggregate"),
         _fb.get("count"), _fb.get("sum_pnl"), _fb.get("pnl_pct_aggregate")],
    )
    conn.commit()

    return {
        "variant": variant, "n_days": len(navs),
        "user_criteria": {**uc, "pass": pass_uc},
        "anti_churn": {**ac, "pass": pass_ac},
        "robustness": {**rb, "pass": pass_rb},
        "all_pass": all_pass,
        # Phase 1a Option C: exit_source 分层 attribution (Codex round 6 MAJOR #1)
        "partition_by_exit_source": partition,
    }
