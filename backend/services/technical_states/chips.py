"""technical_states.chips — 筹码分布 + 筹码胜率维度 (档案维度④, 用户点名接入)。

owner=backend/services/technical_states/ + config/technical_states.yaml 筹码 段。
真相源: raw_tushare_cyq_perf (winner_rate 获利盘 + cost_5/50/95pct 成本分位 + weight_avg 平均成本 + his_low/high)。
描述: 获利盘%(winner_rate) + 集中度((cost95-cost5)/cost50, 小=单峰集中) + 价位(收盘 vs 平均成本=获利/套牢) + 获利盘趋势。
**鱼尾出场用** (评审/goal): 高位获利盘高 + 集中度由单峰转多峰/分散 = 派发预警 (CYQ 出货信号)。
PIT: cyq 盘后, 决策侧 JOIN t-1。winner_rate 量纲 0-100 (注: 旧反例 0-1 阈值=100x误判, §4.5)。纯函数。
"""
from __future__ import annotations

import numpy as np


def _conc_of(cy: dict):
    """集中度 (cost95-cost5)/cost50 (小=单峰集中); 缺值 None。"""
    c5 = cy.get("cost_5pct"); c95 = cy.get("cost_95pct"); c50 = cy.get("cost_50pct")
    return ((c95 - c5) / c50) if (c5 is not None and c95 is not None and c50) else None


def chip_signals(dates, cyq_by_date: dict, close_by_date: dict,
                 window: int = 20, cfg=None) -> dict:
    """筹码分布 + 胜率 + **分盈亏精细化** (PIT ≤t)。cyq_by_date={date:{winner_rate, cost_5/50/95pct, weight_avg}}。
    返回 {date:{获利盘, 套牢盘, 集中度, 集中度20日变化, 成本偏度, 价位状态, 获利盘20日变化, chip_state, 集中状态, 派发预警}}。
    **筹码精细化①** (2026-06-22, 长江《筹码分布因子》分盈亏 + goal CYQ鱼尾出货预警; cyq_perf第一手衍生, 无重建误差):
      套牢盘=亏损筹码(论文: 亏损筹码预测力更强); 成本偏度=weight_avg vs cost_50 分布偏向; 集中度20日变化=单峰→多峰派发;
      **派发预警(鱼尾)**=高获利盘+集中度转分散+价位获利 (主升浪顶部出货信号)。winner_rate 量纲 0-100 (旧反例 0-1=100x误判, §4.5)。
    注: 华泰VWAP三角+换手递推重建筹码分布(筹码龄分层/精细分盈亏统计量)POC已验证(spearman0.826, sandbox/chip_rebuild),
        留作未来增强 — 需 cyq_perf 没有的筹码龄/完整分布时启用; 当前用 cyq_perf 第一手避重建误差。
    """
    c = (cfg or {}).get("筹码") or {}
    hi_win = c.get("高获利盘", 85.0)        # winner_rate > 此(0-100) = 高位派发压力
    lo_win = c.get("低获利盘", 15.0)        # winner_rate < 此 = 低位惜售
    conc_thr = c.get("集中门", 0.5)         # (cost95-cost5)/cost50 < 此 = 单峰集中
    loosen = c.get("集中松动门", 0.1)       # 集中度20日变化 > 此 = 单峰转多峰(派发松动)
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
        conc = _conc_of(cy)
        prev_cy = wins.get(ds[i - window], {})
        prev_wr = prev_cy.get("winner_rate")
        wr_chg = (wr - prev_wr) if (wr is not None and prev_wr is not None) else None
        prev_conc = _conc_of(prev_cy)
        conc_chg = (conc - prev_conc) if (conc is not None and prev_conc is not None) else None
        sink = (100.0 - wr) if wr is not None else None      # 套牢盘 = 亏损筹码 (论文: 预测力更强)
        skew = (((wavg - c50) / ((c95 - c5) / 2.0))          # 成本偏度: 均值vs中位, 正=上方套牢多(右偏)/负=下方获利多
                if (wavg is not None and c50 is not None and c5 is not None and c95 is not None and (c95 - c5)) else None)
        price_pos = ("获利" if px > wavg else "套牢") if (px is not None and wavg) else None
        chip = "筹码中性"
        if wr is not None:
            if wr > hi_win:
                chip = "高获利盘(派发压力)"
            elif wr < lo_win:
                chip = "低获利盘(惜售)"
        conc_state = ("单峰集中" if (conc is not None and conc < conc_thr)
                      else "多峰分散" if conc is not None else None)
        distrib_warn = bool(wr is not None and wr > hi_win and conc_chg is not None
                            and conc_chg > loosen and price_pos == "获利")   # 鱼尾派发预警 (CYQ 出货)
        out[d] = {"获利盘": wr, "套牢盘": sink, "集中度": conc, "集中度20日变化": conc_chg,
                  "成本偏度": skew, "价位状态": price_pos, "获利盘20日变化": wr_chg,
                  "chip_state": chip, "集中状态": conc_state, "派发预警": distrib_warn}
    return out
