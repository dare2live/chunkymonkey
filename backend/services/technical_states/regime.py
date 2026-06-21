"""technical_states.regime — 市场 regime 门 (档案 L3 属性背景, ⑥; 横切非单股因子)。

真相源 (PIT 锚 trade_date, 盘后即知):
  raw_tushare_index_daily (大盘指数 000300.SH HS300 close — 趋势) — 锚 trade_date
  raw_tushare_limit_list_d (每日涨跌停统计: limit U涨停/D跌停/Z炸板 — 涨停情绪/风险偏好) — 锚 trade_date
市场 regime = stage-conditional 策略最外层横切门 (大盘环境决定 form/factor 权重; 牛市进攻/熊市防御)。
**横切非单股**: 同一交易日所有股共享同一 regime (不依赖个股), 档案展示为市场背景。
大盘趋势复用真 MA 斜率口径 (同 sector_regime: 均线变化率非 price-vs-MA)。描述性档案维度。纯函数。
"""
from __future__ import annotations

import numpy as np


def market_regime(idx_dates, idx_close, sentiment_by_date: dict, *, cfg=None) -> dict:
    """市场 regime: 大盘指数趋势(真MA斜率 + 价vs均线位置) + 涨停情绪(净涨停 + 炸板率) → 牛市/震荡市/熊市 + 情绪强弱。
    idx_dates/idx_close = 大盘指数时序; sentiment_by_date = {date: {"up":涨停家数, "down":跌停家数, "zha":炸板家数}}。
    返回 {date: {大盘趋势, regime, 涨停家数, 跌停家数, 净涨停, 炸板率, 情绪}}。
    """
    c = (cfg or {}).get("regime") or {}
    ma_win = int(c.get("趋势窗口", 20))          # 大盘均线窗 (真MA斜率)
    net_strong = c.get("情绪强门", 30)           # 净涨停(涨-跌) > 此 = 情绪强
    net_weak = c.get("情绪弱门", 0)              # < 此 = 情绪弱
    zha_high = c.get("炸板率高门", 0.4)          # 炸板率 > 此 = 封板质量差(情绪退潮)
    ds = [str(x) for x in idx_dates]
    ic = np.array([float(x) if x else np.nan for x in idx_close], float)
    out = {}
    start = 2 * ma_win                            # MA 斜率需 2×ma_win 回看
    for i in range(start, len(ds)):
        d = ds[i]
        if np.isnan(ic[i]):
            continue
        ma_now = np.nanmean(ic[i - ma_win + 1:i + 1])                  # 当前均线
        ma_prev = np.nanmean(ic[i - 2 * ma_win + 1:i - ma_win + 1])    # ma_win 前均线
        slope = ((ma_now / ma_prev - 1.0) * 100.0) if (np.isfinite(ma_now) and np.isfinite(ma_prev) and ma_prev > 0) else None
        above = bool(np.isfinite(ma_now) and ic[i] > ma_now)          # 价在均线上
        trend = ("上行" if (slope is not None and slope > 0) else "下行" if slope is not None else "?")
        if slope is None:
            regime = "未知"
        elif trend == "上行" and above:
            regime = "牛市"          # 均线上行 + 价在均线上 = 进攻
        elif trend == "下行" and not above:
            regime = "熊市"          # 均线下行 + 价在均线下 = 防御
        else:
            regime = "震荡市"        # 趋势/位置背离 = 震荡
        s = sentiment_by_date.get(d) or {}
        up, down, zha = s.get("up"), s.get("down"), s.get("zha")
        net = (up - down) if (up is not None and down is not None) else None
        zha_rate = (zha / (up + zha)) if (up is not None and zha is not None and (up + zha) > 0) else None
        if net is None:
            senti = "未知"
        elif net > net_strong and (zha_rate is None or zha_rate < zha_high):
            senti = "情绪强(风险偏好高)"
        elif net <= net_weak or (zha_rate is not None and zha_rate >= zha_high):
            senti = "情绪弱(风险偏好低)"
        else:
            senti = "情绪中性"
        out[d] = {"大盘趋势": trend, "regime": regime, "涨停家数": up, "跌停家数": down,
                  "净涨停": net, "炸板率": round(zha_rate, 2) if zha_rate is not None else None, "情绪": senti}
    return out
