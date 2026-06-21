"""technical_states.capital — 资金流向 + 换手率维度 (档案维度③, 用户点名接入)。

owner=backend/services/technical_states/ + config/technical_states.yaml 资金 段。
真相源: raw_tushare_moneyflow_dc (东财 net_amount 主力大单净 + net_amount_rate + pct_change) + raw_tushare_daily_basic (换手率)。
描述: 主力净流入/流出(net_amount 20日累计) + 换手活跃度 + 主力意图(明盘×量价背离)。
PIT: 资金/换手盘后数据, 决策侧 JOIN 锚 t-1 (档案描述用 bar 当日值, 消费侧选股须 t-1)。纯函数。
**暗盘伪维度裁决 (2026-06-21 measured, sandbox/mingan_redesign)**: 东财日度桶零和 (elg+lg+md+sm=0) →
  中小单净 ≡ −大单净, 无独立暗盘维度; 同花顺L2暗盘量级差20x 物理复现不了 (need_027 order-flow BLOCKED 无源)。
  → 砍伪暗盘 (X_8×中小单 = 明盘的价格加权镜像)。"隐蔽资金"语义改用 **明盘(主力大单净, 89%对齐同花顺明盘) ×
  价格 量价背离代理** (主力流出但价涨=隐性承接; 流入但价跌=隐性派发), 诚实命名非伪造暗盘金额。
"""
from __future__ import annotations

import numpy as np


def mainforce_net(f: dict) -> float:
    """**主力(大单+超大单)净额单一真相源 = 东财 moneyflow_dc.net_amount** (万元)。
    **单一供应商=东财**(与项目 概念=东财 同源, flow-vendor=membership-vendor 红线, 口径自洽; 禁同花顺第三套/tushare net_mf)。
    实测 东财 net_amount ≡ buy_elg+buy_lg (大单净), 各档 buy_* 已是净额。东财数据 2023-09 起。
    """
    if f.get("net_amount") is not None:
        return f["net_amount"]
    return (f.get("buy_elg") or 0.0) + (f.get("buy_lg") or 0.0)   # 东财净桶 fallback


def _intent_atom(key: str, val, net: float, pct: float, rate: float, c: dict) -> bool:
    """主力意图单条件原子 (明盘方向/价格方向/主力强弱)。net=主力大单净额(亿), pct=涨跌%, rate=净额占成交额%。"""
    weak = c.get("主力清淡门", 1.5)   # from yaml: |net_amount_rate| < 此 = 主力参与清淡
    up = c.get("价格涨门", 0.5)       # from yaml: pct > 此 = 价涨
    dn = c.get("价格跌门", 0.5)       # from yaml: pct < -此 = 价跌
    if key == "明盘":
        return (net > 0) if val == "入" else (net < 0)
    if key == "价格":
        return (pct > up) if val == "涨" else (pct < -dn)
    if key == "明盘弱":               # 主力参与清淡 (净额占成交额比例小)
        return abs(rate) < weak
    return False


def zhuli_intent(net: float, pct: float, rate: float = 0.0, cfg=None) -> dict:
    """主力意图解读 (同花顺"暗盘追踪"解读语义, 用**可靠维度重锚**)。
    **暗盘伪维度裁决 (2026-06-21 measured)**: 东财桶零和 → 中小单净≡−大单净, 无独立暗盘; 同花顺L2暗盘复现不了。
    → 改用 **明盘(主力大单净 net_amount, 89%对齐同花顺明盘) × 价格** 两个真独立维度 + 量价背离 (隐蔽资金代理)。
    net=主力大单净额(亿), pct=涨跌%, rate=净额占成交额%(主力强弱)。按 config 主力意图 段有序匹配命中即停。
    口径: 档案描述维度 (无 forward claim; 选股需含成本OOS另证)。三因子分离: 明盘/量价背离/意图 各独立。
    返回 {主力意图, 解读, 量价背离}。
    """
    c = (cfg or {}).get("主力意图") or {}
    for rule in c.get("规则", []):
        cond = rule.get("条件", {})
        if cond and all(_intent_atom(k, v, net, pct, rate, c) for k, v in cond.items()):
            return {"主力意图": rule.get("意图"), "解读": rule.get("解读"), "量价背离": rule.get("背离", "")}
    return {"主力意图": c.get("默认意图", "资金分歧"), "解读": c.get("默认解读", ""), "量价背离": "中性"}


def capital_intent(dates, money_by_date: dict, unit_div: float = 1e4, cfg=None) -> dict:
    """主力意图 + 量价背离 (替代旧 mingan_flow 伪暗盘动向)。
    明盘 = 主力大单净额 (东财 net_amount, 万元→亿); 量价背离 = 主力净额方向 vs 价格(pct_change) 背离
    (隐性承接/隐性派发/量价一致); 主力意图 = 明盘×价格 6象限 (config 主力意图 段)。
    money_by_date={date:{net_amount(万元), net_amount_rate(%), pct_change(%)}}。**三因子分离**: 明盘数值独立, 意图只描述。
    """
    ds = [str(x) for x in dates]
    nets = [(money_by_date.get(d) or {}).get("net_amount") for d in ds]   # 主力大单净 (万元), PIT 顺序
    out = {}
    for i, d in enumerate(ds):
        f = money_by_date.get(d)
        if f is None or f.get("net_amount") is None:
            continue
        net_yi = (f.get("net_amount") or 0.0) / unit_div                  # 明盘 (亿, 东财大单净=可靠真信息)
        rate = f.get("net_amount_rate") or 0.0                            # 净额占成交额% (主力强弱)
        pct = f.get("pct_change") or 0.0                                  # 当日涨跌%
        intent = zhuli_intent(net_yi, pct, rate, cfg)
        row = {"主力净额": round(net_yi, 4), "净额占比": round(rate, 2), "涨跌": round(pct, 2),
               "主力意图": intent["主力意图"], "意图解读": intent["解读"], "量价背离": intent["量价背离"]}
        seg = [nets[j] for j in range(max(0, i - 2), i + 1) if nets[j] is not None]   # 3日主力净额累计 (PIT)
        if seg:
            row["三日主力净额"] = round(sum(seg) / unit_div, 4)
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
            "主力净额": None if np.isnan(net) else float(net),         # 东财 net_amount (大单净, 万元)
            "主力净额20日累计": cum, "换手率": None if np.isnan(tr) else float(tr),
            "换手分位": tpct, "capital_state": cap, "turnover_state": hot,
        }
    return out
