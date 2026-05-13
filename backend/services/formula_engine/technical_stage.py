"""Stan Weinstein 4-stage 技术形态分类 — 纯规则版 v1。

判定规则 (开发手册 §4.7):
  Stage 1 底部基础:  价格在 60 周低位 ±15% + 30/50 周线走平 + 量能枯竭
  Stage 1.5 突破中:  突破 30 周线 + 量比 > 1.5 + 持续 1-10 日
  Stage 2 上升趋势:  MA10 > MA30 > MA50 (周线) + 价 > MA30 + 回撤 < 15%
  Stage 3 顶部分布:  价创新高但量背离 OR MA10 死叉 MA30 OR 距 MA30 偏离过大
  Stage 4 下跌趋势:  MA10 < MA30 < MA50 + 价 < MA30
  unknown: 数据不足
"""
from __future__ import annotations

import numpy as np

from services.formula_engine.base import sma


# 周线 = 5 个交易日聚合; 实际我们直接在日线上用 5x 长度近似 (50日 ≈ 10周)
# 简化: 直接用日线参数 50/150/250 近似周线 10/30/50
MA_FAST_DAYS = 50    # ≈ 10 周
MA_MID_DAYS = 150    # ≈ 30 周
MA_SLOW_DAYS = 250   # ≈ 50 周
RANGE_LOOKBACK = 300  # ≈ 60 周
BREAKOUT_RECENT_DAYS = 10
DRAWDOWN_MAX_STAGE2 = 0.15


def classify_technical_stage(
    closes: np.ndarray,
    volumes: np.ndarray,
) -> np.ndarray:
    """对单股 K 线计算每个日期的 technical_stage 标签。

    返回字符串 numpy array, 元素为: '1','1.5','2','3','4','unknown'
    """
    n = len(closes)
    out = np.full(n, "unknown", dtype="<U8")
    if n < MA_SLOW_DAYS:
        return out

    ma_fast = sma(closes, MA_FAST_DAYS)   # 10 周
    ma_mid = sma(closes, MA_MID_DAYS)     # 30 周
    ma_slow = sma(closes, MA_SLOW_DAYS)   # 50 周

    # 用 MA_MID 斜率近似走平判定
    slope_mid = np.full(n, np.nan)
    for i in range(MA_MID_DAYS + 20, n):
        slope_mid[i] = (ma_mid[i] - ma_mid[i - 20]) / max(ma_mid[i - 20], 1e-9)

    # 60 周高低区间位置
    range_pos = np.full(n, np.nan)
    for i in range(RANGE_LOOKBACK, n):
        window = closes[i - RANGE_LOOKBACK:i]
        lo, hi = window.min(), window.max()
        if hi > lo:
            range_pos[i] = (closes[i] - lo) / (hi - lo)

    # 量比 (20 日均量)
    vol_ma20 = sma(volumes, 20)
    vol_ratio = np.where((vol_ma20 > 0) & ~np.isnan(vol_ma20), volumes / vol_ma20, 1.0)

    # 60 日回撤
    drawdown_60d = np.full(n, 0.0)
    for i in range(60, n):
        window = closes[i - 60:i + 1]
        peak = window.max()
        drawdown_60d[i] = (closes[i] - peak) / peak

    for i in range(MA_SLOW_DAYS, n):
        c = closes[i]
        mf, mm, ms = ma_fast[i], ma_mid[i], ma_slow[i]
        if np.isnan(mf) or np.isnan(mm) or np.isnan(ms):
            continue
        slope = slope_mid[i] if not np.isnan(slope_mid[i]) else 0.0
        pos = range_pos[i] if not np.isnan(range_pos[i]) else 0.5

        # Stage 4 下跌趋势: MA10 < MA30 < MA50, 价 < MA30, 斜率明显向下
        if mf < mm < ms and c < mm and slope < -0.02:
            out[i] = "4"
            continue
        # Stage 1 底部基础: 60 周低位 + MA30 走平 + 量能枯竭
        if pos < 0.30 and abs(slope) < 0.02 and vol_ratio[i] < 0.8:
            out[i] = "1"
            continue
        # Stage 1.5 突破中: 突破 MA30 + 量比 > 1.5 + 最近 10 日内 (从下方上穿)
        below_ma30_recent = False
        if i >= BREAKOUT_RECENT_DAYS:
            recent_below = np.sum(closes[i - BREAKOUT_RECENT_DAYS:i] < ma_mid[i - BREAKOUT_RECENT_DAYS:i])
            below_ma30_recent = recent_below >= 2
        if c > mm and below_ma30_recent and vol_ratio[i] > 1.5:
            out[i] = "1.5"
            continue
        # Stage 2 上升趋势: MA10>MA30>MA50, 价>MA30, 回撤<15%
        if mf > mm > ms and c > mm and drawdown_60d[i] > -DRAWDOWN_MAX_STAGE2:
            out[i] = "2"
            continue
        # Stage 3 顶部分布: 距 MA30 偏离大 + MA10 开始下穿 MA30
        if c > mm * 1.15 or (mf < mm and slope > 0):
            out[i] = "3"
            continue
        # 其他默认 unknown (兜底)

    return out
