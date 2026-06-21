"""technical_states.capital — 资金流向 + 换手率维度 (档案维度③, 用户点名接入)。

owner=backend/services/technical_states/ + config/technical_states.yaml 资金 段。
真相源: raw_tushare_moneyflow (主力净额 net_mf_amount, order-size sm/md/lg/elg) + raw_tushare_daily_basic (换手率)。
描述: 主力净流入/流出(net_mf_amount 20日累计趋势) + 换手活跃度(turnover_rate vs 自身分位)。
PIT: 资金/换手是盘后数据, 决策侧 JOIN 锚 t-1 (本档案描述用 bar 当日值, 消费侧选股须 t-1)。纯函数。
注: 真暗盘=Level-2 tick (项目无); tushare moneyflow 只给 order-size 桶, 非真 L2。
"""
from __future__ import annotations

import numpy as np

_DONGXIANG = {1: "看多", 2: "做T", 3: "低吸", 4: "看空", 5: "吸筹", 6: "出货", 0: "中性"}


def mainforce_net(f: dict) -> float:
    """**主力(大单+超大单)净额单一真相源** (reconcile wf_e6a0e9e8 裁决: =[b]口径=东财dc.net_amount 同构念)。
    = (买超大elg+买大lg) - (卖超大elg+卖大lg)。**禁用 tushare net_mf_amount 当主力净额** — 实测 net_mf=厂商
    净主动流(vol×VWAP)跟中小单/动量, 与大单主力档常反向(600519 5天4天符号相反), 是口径错配。
    """
    g = lambda k: (f.get(k) or 0.0)  # noqa: E731
    return g("buy_elg") + g("buy_lg") - g("sell_elg") - g("sell_lg")


def _path_weight(o, h, l, c, prev_c) -> float:
    """X_1..X_8: 日线 OHLC 路径权重 (逐字复刻 TDX 公式 X_8, capped 0.8)。"""
    if None in (o, h, l, c, prev_c) or not (prev_c and o and h and l):
        return 0.0
    x1 = (o - prev_c) / prev_c; x2 = (c - o) / o; x3 = (h - o) / o
    x4 = (c - h) / h; x5 = (l - o) / o; x6 = (c - l) / l
    x7 = x1 + x2 + x3 + x4 + x5 + x6
    return 0.8 if x7 >= 1 else x7


def _dongxiang(liu, ming, an) -> int:
    """6态动向 (逐字复刻 TDX: 看多1/做T2/低吸3/看空4/吸筹5/出货6/中性0), 按 流向/明盘/暗盘 符号。"""
    am, aa = abs(ming), abs(an)
    if liu > 0 and ming > 0 and an > 0: return 1
    if liu > 0 and ming > 0 and an < 0 and am > aa: return 2
    if liu > 0 and ming < 0 and an > 0 and aa > am: return 3
    if liu < 0 and ming < 0 and an < 0: return 4
    if liu < 0 and ming < 0 and an > 0 and am > aa: return 5
    if liu < 0 and ming > 0 and an < 0 and aa > am: return 6
    return 0


def mingan_flow(dates, o, h, l, c, flow_by_date: dict, unit_div: float = 1e4) -> dict:
    """明暗盘资金 + 今日/三日/五日 动向 (**日度近似 TDX 真L2公式; 非真L2 L2_AMO, 用 moneyflow order-size 桶替代**)。
    明盘(主力净额)=(elg+lg)买-卖; 暗盘=路径权重X_8 ×(md+sm)signed (近似 X_25); 流向=明+暗; 6态动向。
    flow_by_date={date:{buy_elg,buy_lg,buy_md,buy_sm,sell_elg,sell_lg,sell_md,sell_sm}} (万元)。unit_div=1e4→亿元。
    """
    ds = [str(x) for x in dates]
    ming, an, liu = [], [], []
    prev_c = None
    for i, d in enumerate(ds):
        f = flow_by_date.get(d, {}) or {}
        w = _path_weight(o[i], h[i], l[i], c[i], prev_c)
        prev_c = c[i]
        g = lambda k: (f.get(k) or 0.0)  # noqa: E731
        m = mainforce_net(f) / unit_div    # 明盘=主力净额(亿, elg+lg 净, 与 capital_signals 同源单一真相源)
        a = ((g("buy_md") + g("buy_sm")) * w if w > 0 else (g("sell_md") + g("sell_sm")) * w) / unit_div  # 暗盘(signed, 粗近似非真L2)
        ming.append(m); an.append(a); liu.append(m + a)
    out = {}
    for i, d in enumerate(ds):
        if flow_by_date.get(d) is None:
            continue
        row = {"明盘": round(ming[i], 4), "暗盘": round(an[i], 4), "今日流向": round(liu[i], 4),
               "今日动向": _DONGXIANG[_dongxiang(liu[i], ming[i], an[i])]}
        if i >= 2:
            l3, m3, a3 = sum(liu[i - 2:i + 1]), sum(ming[i - 2:i + 1]), sum(an[i - 2:i + 1])
            row["三日流向"] = round(l3, 4); row["三日动向"] = _DONGXIANG[_dongxiang(l3, m3, a3)]
        if i >= 4:
            l5, m5, a5 = sum(liu[i - 4:i + 1]), sum(ming[i - 4:i + 1]), sum(an[i - 4:i + 1])
            row["五日流向"] = round(l5, 4); row["五日动向"] = _DONGXIANG[_dongxiang(l5, m5, a5)]
        out[d] = row
    return out


def capital_signals(dates, money_by_date: dict, turnover_by_date: dict,
                    window: int = 20, cfg=None) -> dict:
    """资金流向 + 换手率 (PIT ≤t)。**主力净额走 mainforce_net(elg+lg) 单一真相源, 与明暗盘明盘同源**
    (reconcile 裁决: 禁用 net_mf_amount=厂商净主动流非主力净额)。turnover_by_date={date: turnover_rate}。
    返回 {date:{主力净额, 主力净额20日累计, 换手率, 换手分位, capital_state}}。
    """
    c = (cfg or {}).get("资金") or {}
    inflow_thr = c.get("净流入累计门", 0.0)
    hot_pct = c.get("换手活跃分位", 0.8)
    cold_pct = c.get("换手低迷分位", 0.2)
    nets = np.array([mainforce_net(money_by_date.get(str(d), {}) or {}) if money_by_date.get(str(d)) else np.nan
                     for d in dates], float)   # elg+lg 净 (= 明盘同源), 非 net_mf_amount
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
