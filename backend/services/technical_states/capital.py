"""technical_states.capital — 资金流向 + 换手率维度 (档案维度③, 用户点名接入)。

owner=backend/services/technical_states/ + config/technical_states.yaml 资金 段。
真相源: raw_tushare_moneyflow (主力净额 net_mf_amount, order-size sm/md/lg/elg) + raw_tushare_daily_basic (换手率)。
描述: 主力净流入/流出(net_mf_amount 20日累计趋势) + 换手活跃度(turnover_rate vs 自身分位)。
PIT: 资金/换手是盘后数据, 决策侧 JOIN 锚 t-1 (本档案描述用 bar 当日值, 消费侧选股须 t-1)。纯函数。
注: 真暗盘=Level-2 tick (项目无); tushare moneyflow 只给 order-size 桶, 非真 L2。
"""
from __future__ import annotations

import numpy as np


def capital_signals(dates, money_by_date: dict, turnover_by_date: dict,
                    window: int = 20, cfg=None) -> dict:
    """资金流向 + 换手率 (PIT ≤t)。
    money_by_date={date:{net_mf_amount, buy_lg, buy_elg, sell_lg, sell_elg}}; turnover_by_date={date: turnover_rate}。
    返回 {date:{主力净额, 主力净额20日累计, 换手率, 换手分位, capital_state}}。
    """
    c = (cfg or {}).get("资金") or {}
    inflow_thr = c.get("净流入累计门", 0.0)
    hot_pct = c.get("换手活跃分位", 0.8)
    cold_pct = c.get("换手低迷分位", 0.2)
    nets = np.array([(money_by_date.get(str(d), {}) or {}).get("net_mf_amount", np.nan) for d in dates], float)
    turns = np.array([turnover_by_date.get(str(d), np.nan) for d in dates], float)
    out = {}
    for i in range(window, len(dates)):
        net = nets[i]
        cum = float(np.nansum(nets[i - window + 1:i + 1]))                # 20日主力净额累计 (PIT)
        tr = turns[i]
        seg = turns[max(0, i - 120):i + 1]                               # 换手率自身分位 (近120日)
        tpct = float(np.nanmean(seg <= tr)) if not np.isnan(tr) and np.isfinite(seg).any() else None
        if np.isnan(net) and np.isnan(tr):
            continue
        cap = ("主力净流入" if cum > inflow_thr else "主力净流出" if cum < -inflow_thr else "资金中性")
        hot = ("换手活跃" if (tpct is not None and tpct > hot_pct)
               else "换手低迷" if (tpct is not None and tpct < cold_pct) else "换手正常")
        out[str(dates[i])] = {
            "主力净额": None if np.isnan(net) else float(net),
            "主力净额20日累计": cum, "换手率": None if np.isnan(tr) else float(tr),
            "换手分位": tpct, "capital_state": cap, "turnover_state": hot,
        }
    return out
