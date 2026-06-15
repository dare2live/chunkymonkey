"""Return-based 组合回测引擎 — 干净重建 (2026-06-15 用户: 旧 portfolio_backtest.py [5-07] 退役重建)。

旧引擎问题 (审计): cash*0.95 部分买入欠仓 + 5% 容差不完全调仓 + 不透明状态机 = -42%裁决被污染。
本引擎透明可逐行核验: 等权 long-only, 日度 mark-to-market, 显式 T+1 + 显式换手成本。零旧依赖 (含 metrics 内联)。

架构师合约 (architect-controller):
  输入: rebalances=[(decision_date, [codes])] 按时间序; price_by_code={code:{date:close}} (qfq);
        calendar=全交易日升序; cost_bps (单边 bps)。
  输出: {nav:[(date,nav)], metrics:{annual_return,max_drawdown,sharpe,calmar,monthly_win_rate},
         cost_drag, avg_turnover, n_rebalances}。
  不变量 (PIT 死亡条款): NAV(d) 只用 price[<=d]; 决策日 dd 选股 (调用方保证只用<=dd) -> 执行于 dd 的
        下一交易日 (T+1, 防当日成交未来函数); 持有到下一调仓的 T+1。成本在每次调仓按换手扣。
  失败模式: 缺价 -> 该股当日剔出等权篮 (不假装成交); 空篮 -> 持平 (nav 不变)。
  证伪门: test_portfolio_returnbacktest.py 手算 2股2期已知场景逐字核对。
"""
from __future__ import annotations

import numpy as np


def _metrics(nav_dates: list[str], nav: list[float], tdays: int = 252) -> dict:
    """内联 metrics (零旧依赖): 年化/最大回撤/sharpe/calmar/月胜率, 从日度 NAV。"""
    if len(nav) < 2:
        return {"annual_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "calmar": 0.0, "monthly_win_rate": None}
    arr = np.asarray(nav, float)
    total_ret = arr[-1] / arr[0] - 1.0
    years = max(len(arr) / tdays, 1e-9)
    annual = (1 + total_ret) ** (1 / years) - 1 if total_ret > -1 else -1.0
    run_max = np.maximum.accumulate(arr)
    max_dd = float(((arr - run_max) / run_max).min())
    daily_ret = np.diff(arr) / arr[:-1]
    sd = float(daily_ret.std(ddof=1)) if daily_ret.size > 1 else 0.0
    sharpe = float(daily_ret.mean() * tdays / (sd * np.sqrt(tdays))) if sd > 0 else 0.0
    calmar = float(annual / abs(max_dd)) if abs(max_dd) > 1e-3 else 0.0
    # 月胜率: 月末 NAV 环比 > 0 的月比例
    by_month: dict[str, float] = {}
    for d, v in zip(nav_dates, nav):
        by_month[d[:7]] = v  # 覆盖 -> 月末值
    months = sorted(by_month)
    mwr = None
    if len(months) >= 2:
        wins = sum(1 for i in range(1, len(months)) if by_month[months[i]] > by_month[months[i - 1]])
        mwr = wins / (len(months) - 1)
    return {"annual_return": float(annual), "max_drawdown": max_dd, "sharpe": sharpe,
            "calmar": calmar, "monthly_win_rate": mwr}


def run_return_backtest(rebalances, price_by_code, calendar, *, cost_bps: float = 10.0) -> dict:
    cal_idx = {d: i for i, d in enumerate(calendar)}
    n = len(calendar)

    # 持有段: (entry_idx, exit_idx, basket) — entry=决策日 T+1, exit=下一调仓 T+1 (或日历末)
    segs: list[tuple[int, int, list[str]]] = []
    rebs = [(dd, codes) for dd, codes in rebalances if cal_idx.get(dd) is not None]
    for k, (dd, codes) in enumerate(rebs):
        di = cal_idx[dd]
        if di + 1 >= n:
            continue
        entry_i = di + 1
        if k + 1 < len(rebs):
            nxt = cal_idx[rebs[k + 1][0]]
            exit_i = min(nxt + 1, n)   # 下一调仓 T+1 (该日由下段标记, range 排他防双计)
        else:
            exit_i = n                  # 末段含日历最后一天 (range(entry,n) -> n-1)
        if exit_i > entry_i:
            segs.append((entry_i, exit_i, codes))

    nav = 1.0
    nav_dates: list[str] = []
    nav_vals: list[float] = []
    prev_basket: set[str] = set()
    turnovers: list[float] = []
    total_cost = 0.0

    for (entry_i, exit_i, codes) in segs:
        entry_date = calendar[entry_i]
        new_basket = [c for c in codes if price_by_code.get(c, {}).get(entry_date) not in (None, 0)]
        new_set = set(new_basket)
        # 换手成本 (等权): 卖出权重 = |prev-new|/|prev|, 买入权重 = |new-prev|/|new|; 各单边 cost_bps
        w_sell = (len(prev_basket - new_set) / len(prev_basket)) if prev_basket else 0.0
        w_buy = (len(new_set - prev_basket) / len(new_set)) if new_set else 0.0
        cost = (w_sell + w_buy) * cost_bps / 10000.0
        turnovers.append(w_sell + w_buy)
        nav *= (1 - cost)
        total_cost += cost
        if not new_basket:  # 空篮持平
            prev_basket = set()
            continue
        p_entry = {c: price_by_code[c][entry_date] for c in new_basket}
        nav_at_entry = nav
        for di in range(entry_i, exit_i):
            d = calendar[di]
            rels = [price_by_code[c][d] / p_entry[c]
                    for c in new_basket if price_by_code.get(c, {}).get(d) not in (None, 0)]
            if rels:
                nav = nav_at_entry * float(np.mean(rels))
            nav_dates.append(d)
            nav_vals.append(nav)
        prev_basket = new_set

    if not nav_vals:
        return {"nav": [], "metrics": _metrics([], []), "cost_drag": 0.0, "avg_turnover": 0.0, "n_rebalances": len(segs)}
    return {"nav": list(zip(nav_dates, nav_vals)), "metrics": _metrics(nav_dates, nav_vals),
            "cost_drag": total_cost, "avg_turnover": float(np.mean(turnovers)) if turnovers else 0.0,
            "n_rebalances": len(segs), "final_nav": nav_vals[-1]}
