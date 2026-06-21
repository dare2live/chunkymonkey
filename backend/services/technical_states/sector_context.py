"""technical_states.sector_context — 板块/概念/行业维度 (档案 L3 属性背景 + L2 个股vs板块相对)。

owner=backend/services/technical_states/ + config/technical_states.yaml 板块 段。
真相源 (口径红线 J6: 行业=申万 / 概念=东财 / flow vendor=membership vendor):
  v_sw_industry_pit (申万 PIT 行业归属, l1_code 直接=sw_daily ts_code) + raw_tushare_sw_daily (申万行业指数K线)
  + raw_tushare_dc_member (东财概念成分) + raw_tushare_dc_index (东财概念热度)。
- **L2 切面**: 个股 vs 所属申万行业指数 领涨/落后 (复用 rs.relative_strength, 基准换板块指数; 见 dossier)。
- **L3 切面**: 板块自身 regime (申万行业指数趋势 + vs HS300 超额 = 风口在不在) + 概念归属/热度。
PIT: 行业/概念归属 as-of ≤t (latest-snapshot 陷阱); 板块K线 t-1 盘后。纯函数 (不连DB, 输入=服务层load好的dict)。
注: 行业资金 (moneyflow_ind_dc 东财口径) + 板块横截面rotation = 后续增强 (东财行业↔申万映射待对齐)。
"""
from __future__ import annotations

import numpy as np


def sector_regime(sector_dates, sector_close, bench_by_date: dict, *, cfg=None) -> dict:
    """L3 板块自身 regime: 申万行业指数趋势(MA斜率) + vs HS300 超额(窗口收益差) → regime_label。
    sector_dates/sector_close = 板块指数K线时序; bench_by_date = {date: HS300 close}。
    返回 {date:{板块超额, 板块趋势, regime}}。'风口在不在' = 解释为什么涨。
    """
    c = (cfg or {}).get("板块") or {}
    win = int(c.get("超额窗口", 60))        # 超额回看窗 (~3月)
    ma_win = int(c.get("趋势窗口", 20))     # 板块趋势窗 (均线变化率窗长)
    strong = c.get("板块强势超额门", 5.0)   # 窗口超额% > 此 = 板块强势(风口在)
    weak = c.get("板块弱势超额门", -5.0)    # < 此 = 板块走弱
    ds = [str(x) for x in sector_dates]
    sc = np.array([float(x) if x else np.nan for x in sector_close], float)
    out = {}
    start = max(win, 2 * ma_win)            # MA 斜率需 2×ma_win 回看 (当前 MA vs ma_win 前 MA)
    for i in range(start, len(ds)):
        d = ds[i]
        if np.isnan(sc[i]) or np.isnan(sc[i - win]) or not sc[i - win]:
            continue
        sec_ret = (sc[i] / sc[i - win] - 1.0) * 100.0                  # 板块窗口收益%
        bc, bc0 = bench_by_date.get(d), bench_by_date.get(ds[i - win])
        excess = (sec_ret - (bc / bc0 - 1.0) * 100.0) if (bc and bc0) else None   # vs HS300 超额
        ma_now = np.nanmean(sc[i - ma_win + 1:i + 1])                  # 当前 ma_win 均线
        ma_prev = np.nanmean(sc[i - 2 * ma_win + 1:i - ma_win + 1])    # ma_win 根前的均线
        # 真 MA 斜率 = 均线变化率 (非 price-vs-MA: 避免'高位回落但仍在均线上'误判上行, 复审 MEDIUM)
        slope = ((ma_now / ma_prev - 1.0) * 100.0) if (np.isfinite(ma_now) and np.isfinite(ma_prev) and ma_prev > 0) else None
        trend = ("上行" if (slope is not None and slope > 0) else "下行" if slope is not None else "?")
        if excess is None:
            regime = "未知"
        elif excess > strong and trend == "上行":
            regime = "板块强势(风口在)"
        elif excess < weak:
            regime = "板块走弱"
        else:
            regime = "板块中性"
        out[d] = {"板块超额": round(excess, 2) if excess is not None else None,
                  "板块趋势": trend, "regime": regime}
    return out


def concept_labels(concepts: list, hot_by_concept: dict, *, cfg=None) -> dict:
    """L3 概念标签 (纯描述): concepts=[(concept_code, concept_name)] as-of 归属; hot_by_concept={code: 近期涨幅%}。
    取热度 top-N 概念展示, 标 is_hot。返回 {概念:[{名称, 热度, is_hot}], 热门概念数}。
    """
    c = (cfg or {}).get("板块") or {}
    topn = int(c.get("概念展示数", 3))
    hot_thr = c.get("概念热度门", 8.0)       # 概念近期涨幅% > 此 = 热门
    items = []
    for code, name in concepts:
        h = hot_by_concept.get(code)
        items.append({"名称": name, "热度": round(h, 1) if h is not None else None,
                      "is_hot": bool(h is not None and h > hot_thr)})
    # 显式 None 检查 (非 falsy-or: 真实 0.0% 热度概念不被误折成 -1e9 排到最底, 复审 LOW)
    items.sort(key=lambda x: (x["热度"] is not None, x["热度"] if x["热度"] is not None else -1e9), reverse=True)
    return {"概念": items[:topn], "热门概念数": sum(1 for x in items if x["is_hot"])}
