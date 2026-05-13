"""Phase δ — 组合层指标 (纯函数)。

输入: positions (持仓字典) + cash + 当日价格 map
输出: NAV + 各项 KPI

不读 DB, 不写 DB。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def compute_nav(
    *,
    cash: float,
    positions: dict[str, dict],   # {stock_code: {shares, open_price, ...}}
    today_prices: dict[str, float],
) -> dict[str, Any]:
    """单日 NAV 计算。

    Returns:
        {
          "nav_value": float,         # 现金 + 持仓市值
          "position_value": float,
          "cash": float,
          "cash_pct": float,          # cash / nav_value
          "position_count": int,
          "mtm_by_stock": {code: market_value},  # for industry attribution
        }
    """
    pos_val = 0.0
    mtm = {}
    for code, p in positions.items():
        price = today_prices.get(code)
        if price is None or price <= 0:
            # 停牌: 用 open_price (持仓不变, 但参与估值)
            price = p.get("open_price") or 0.0
        mv = float(p["shares"]) * float(price)
        mtm[code] = mv
        pos_val += mv
    nav_value = cash + pos_val
    return {
        "nav_value": nav_value,
        "position_value": pos_val,
        "cash": cash,
        "cash_pct": (cash / nav_value) if nav_value > 0 else 1.0,
        "position_count": len(positions),
        "mtm_by_stock": mtm,
    }


def compute_top_industry(
    mtm_by_stock: dict[str, float],
    industry_by_stock: dict[str, str],
) -> tuple[str | None, float]:
    """返回 (top_industry, top_industry_pct)。

    pct = top industry 市值 / 总持仓市值。
    """
    if not mtm_by_stock:
        return (None, 0.0)
    total = sum(mtm_by_stock.values())
    if total <= 0:
        return (None, 0.0)
    by_ind: dict[str, float] = defaultdict(float)
    for code, mv in mtm_by_stock.items():
        ind = industry_by_stock.get(code) or "未分类"
        by_ind[ind] += mv
    top_ind = max(by_ind, key=by_ind.get)
    return (top_ind, by_ind[top_ind] / total)


def compute_drawdown(nav_value: float, peak_nav: float) -> tuple[float, float]:
    """返回 (drawdown_pct, new_peak)。"""
    new_peak = max(peak_nav, nav_value)
    dd = (nav_value - new_peak) / new_peak if new_peak > 0 else 0.0
    return (dd, new_peak)


def compute_kpis(nav_series: list[dict], starting_nav: float = 1_000_000) -> dict[str, Any]:
    """从 NAV 序列算 KPI (用于 mart_paper_nav 聚合 / API /kpis)。

    Args:
        nav_series: list of dict, 至少含 snapshot_date / nav_value / hs300_cum_ret / eqw_cum_ret

    Returns:
        {nav, nav_chg_pct, excess_pct, sharpe, max_dd_pct, monthly_win, turnover, ...}
    """
    import math
    if not nav_series:
        return {}
    n = len(nav_series)
    final_nav = nav_series[-1]["nav_value"]
    final_cum_ret = final_nav / starting_nav - 1
    final_hs = nav_series[-1].get("hs300_cum_ret")
    excess = (final_cum_ret - final_hs) if (final_hs is not None) else None

    # 日收益序列
    rets = []
    prev = starting_nav
    for e in nav_series:
        rets.append(e["nav_value"] / prev - 1)
        prev = e["nav_value"]

    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        sharpe = (mean * 252) / (sd * math.sqrt(252)) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    # 最大回撤
    peak = starting_nav
    max_dd = 0.0
    for e in nav_series:
        peak = max(peak, e["nav_value"])
        dd = (e["nav_value"] - peak) / peak if peak > 0 else 0.0
        max_dd = min(max_dd, dd)

    # 月度胜率: 简化, 用日胜率代替 (后续 Phase ε 改)
    win_days = sum(1 for r in rets if r > 0)
    monthly_win = win_days / max(1, len(rets))

    return {
        "nav": final_nav / starting_nav,
        "nav_value": final_nav,
        "nav_chg_pct": final_cum_ret,
        "excess_pct": excess,
        "sharpe": round(sharpe, 3),
        "max_dd_pct": round(max_dd, 4),
        "monthly_win": round(monthly_win, 3),
        "n_days": n,
    }
