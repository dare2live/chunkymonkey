"""technical_states.chips — 筹码分布 + 筹码胜率维度 (档案维度④, 用户点名接入)。

owner=backend/services/technical_states/ + config/technical_states.yaml 筹码 段。
真相源: raw_tushare_cyq_perf (winner_rate 获利盘 + cost_5/50/95pct 成本分位 + weight_avg 平均成本 + his_low/high)。
描述: 获利盘%(winner_rate) + 集中度((cost95-cost5)/cost50, 小=单峰集中) + 价位(收盘 vs 平均成本=获利/套牢) + 获利盘趋势。
**鱼尾出场用** (评审/goal): 高位获利盘高 + 集中度由单峰转多峰/分散 = 派发预警 (CYQ 出货信号)。
PIT: cyq 盘后, 决策侧 JOIN t-1。winner_rate 量纲 0-100 (注: 旧反例 0-1 阈值=100x误判, §4.5)。纯函数。
"""
from __future__ import annotations

import numpy as np


def chip_signals(dates, cyq_by_date: dict, close_by_date: dict,
                 window: int = 20, cfg=None) -> dict:
    """筹码分布 + 胜率 (PIT ≤t)。cyq_by_date={date:{winner_rate, cost_5pct, cost_50pct, cost_95pct, weight_avg}}。
    返回 {date:{获利盘, 集中度, 价位状态, 获利盘20日变化, chip_state}}。winner_rate 量纲 0-100。
    """
    c = (cfg or {}).get("筹码") or {}
    hi_win = c.get("高获利盘", 85.0)        # winner_rate > 此(0-100) = 高位派发压力
    lo_win = c.get("低获利盘", 15.0)        # winner_rate < 此 = 低位惜售
    conc_thr = c.get("集中门", 0.5)         # (cost95-cost5)/cost50 < 此 = 单峰集中
    wins = {str(d): (cyq_by_date.get(str(d), {}) or {}) for d in dates}
    out = {}
    ds = [str(d) for d in dates]
    for i in range(window, len(ds)):
        d = ds[i]
        cy = wins[d]
        wr = cy.get("winner_rate")
        c50 = cy.get("cost_50pct"); c5 = cy.get("cost_5pct"); c95 = cy.get("cost_95pct")
        wavg = cy.get("weight_avg"); px = close_by_date.get(d)
        if wr is None and c50 is None:
            continue
        conc = ((c95 - c5) / c50) if (c5 is not None and c95 is not None and c50) else None
        prev = wins.get(ds[i - window], {}).get("winner_rate")
        wr_chg = (wr - prev) if (wr is not None and prev is not None) else None
        price_pos = None
        if px is not None and wavg:
            price_pos = "获利" if px > wavg else "套牢"
        chip = "筹码中性"
        if wr is not None:
            if wr > hi_win:
                chip = "高获利盘(派发压力)"
            elif wr < lo_win:
                chip = "低获利盘(惜售)"
        conc_state = ("单峰集中" if (conc is not None and conc < conc_thr)
                      else "多峰分散" if conc is not None else None)
        out[d] = {"获利盘": wr, "集中度": conc, "价位状态": price_pos,
                  "获利盘20日变化": wr_chg, "chip_state": chip, "集中状态": conc_state}
    return out
