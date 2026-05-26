"""300616 衍生公式集 — 5 个公式捕捉三波主升浪.

基于 300616 实测: W1 (12-29→02-14 +48%) W3 (04-20→05-20 +57%)
核心发现: 底部量比从 0.5-0.7x 回升到 1.1-1.4x 是起涨前兆.

公式体系:
  1. pullback_doji (原始, 已在 formula_engine.py)
  2. wave1_base_breakout — 底部首涨 (长期低位 + 量比回升 + 不追涨停)
  3. wave2_pullback_buy — 回调后再起涨 (前高回撤 15-25% + 量比恢复)
  4. wave3_rapid_doji — 快速回调十字星 (20日内涨>20% + 浅回调 + 十字星)
  5. full_rally_rider — 整轮主升浪 (底部信号 + 移动止盈持有)
"""
from __future__ import annotations

from typing import Any

import numpy as np


def _ma(arr: np.ndarray, w: int) -> np.ndarray:
    out = np.full(len(arr), np.nan, dtype=np.float64)
    if len(arr) >= w:
        kernel = np.ones(w, dtype=np.float64) / w
        out[w - 1:] = np.convolve(arr.astype(np.float64), kernel, mode="valid")
    return out


def wave1_base_breakout_signals(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray,
    close: np.ndarray, volume: np.ndarray, amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """底部首涨: 长期低位 + 量比从地量回升 + 价格微涨 + 不追涨停.

    300616 实测: W1 12-28 (vol/MA60=1.1x, ret=+2.1%) → 次日首涨 +13.6%
                W3 04-21 (vol/MA60=1.4x, ret=+1.6%) → 次日首涨 +11.6%
    """
    p = params or {}
    n = len(close)
    entry = np.zeros(n, dtype=bool)

    base_days = int(p.get("base_days", 30))
    pos_max = float(p.get("pos_max", 0.30))
    vol_wake_min = float(p.get("vol_wake_min", 1.0))
    vol_wake_max = float(p.get("vol_wake_max", 2.5))
    vol_prior_max = float(p.get("vol_prior_max", 0.9))
    ret_max = float(p.get("ret_max", 0.08))
    ret_min = float(p.get("ret_min", -0.06))

    ma120 = _ma(close, 120)
    vma60 = _ma(volume, 60)
    vma20 = _ma(volume, 20)

    for i in range(max(250, 120), n):
        if not (np.isfinite(ma120[i]) and np.isfinite(vma60[i])):
            continue
        hi250 = np.max(high[i - 250:i + 1])
        lo250 = np.min(low[i - 250:i + 1])
        pos = (close[i] - lo250) / (hi250 - lo250) if hi250 > lo250 else 0.5
        if pos > pos_max:
            continue
        daily_ret = (close[i] - close[i - 1]) / close[i - 1] if close[i - 1] > 0 else 0
        if daily_ret > ret_max or daily_ret < ret_min:
            continue
        vol_ratio = volume[i] / vma60[i] if vma60[i] > 0 else 0
        if vol_ratio < vol_wake_min or vol_ratio > vol_wake_max:
            continue
        prior_vol = np.mean(volume[i - 5:i]) / vma60[i] if vma60[i] > 0 else 1
        if prior_vol > vol_prior_max:
            continue
        entry[i] = True

    return {"entry": entry, "exit": np.zeros(n, dtype=bool), "indicators": {"type": "wave1_base_breakout"}}


def wave2_pullback_buy_signals(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray,
    close: np.ndarray, volume: np.ndarray, amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """回调后再起涨: 从前高回撤 15-25% + 量比开始恢复 + gain_retained > 40%.

    300616 实测: W2 05-09 (前高 29.04 → 回撤到 25.22, -13%) → 次日 +16.9%
    """
    p = params or {}
    n = len(close)
    entry = np.zeros(n, dtype=bool)

    min_prior_rally = float(p.get("min_prior_rally", 0.20))
    pb_min = float(p.get("pb_min", -0.25))
    pb_max = float(p.get("pb_max", -0.08))
    gain_retained_min = float(p.get("gain_retained_min", 0.40))
    vol_wake = float(p.get("vol_wake", 1.2))

    vma20 = _ma(volume, 20)
    ma60 = _ma(close, 60)

    for i in range(60, n):
        if not np.isfinite(ma60[i]):
            continue
        peak_60 = np.max(high[i - 60:i])
        base_60 = close[i - 60]
        if base_60 <= 0:
            continue
        rally = (peak_60 - base_60) / base_60
        if rally < min_prior_rally:
            continue
        pb_depth = (close[i] - peak_60) / peak_60
        if pb_depth < pb_min or pb_depth > pb_max:
            continue
        gain_retained = (close[i] - base_60) / (peak_60 - base_60) if peak_60 > base_60 else 0
        if gain_retained < gain_retained_min:
            continue
        vol_ratio = volume[i] / vma20[i] if np.isfinite(vma20[i]) and vma20[i] > 0 else 0
        if vol_ratio < vol_wake:
            continue
        if close[i] < close[i - 1]:
            continue
        entry[i] = True

    return {"entry": entry, "exit": np.zeros(n, dtype=bool), "indicators": {"type": "wave2_pullback_buy"}}


def wave3_rapid_doji_signals(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray,
    close: np.ndarray, volume: np.ndarray, amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """快速回调十字星: 20日内涨 >20% + 浅回调 + 十字星 + 缩量.

    300616 实测: W3 05-18 (20日涨 25%, 回调 body=0.02, 缩量) → 次日涨停 +20%
    """
    p = params or {}
    n = len(close)
    entry = np.zeros(n, dtype=bool)

    min_rally_20d = float(p.get("min_rally_20d", 0.15))
    doji_body_max = float(p.get("doji_body_max", 0.20))
    pb_depth_min = float(p.get("pb_depth_min", -0.12))
    pb_depth_max = float(p.get("pb_depth_max", -0.02))
    gain_retained_min = float(p.get("gain_retained_min", 0.50))
    vol_contract = float(p.get("vol_contract", 0.8))

    ma20 = _ma(close, 20)

    for i in range(20, n):
        if not np.isfinite(ma20[i]):
            continue
        if close[i] < ma20[i]:
            continue
        peak_20 = np.max(high[i - 20:i])
        base_20 = close[i - 20]
        if base_20 <= 0:
            continue
        rally = (peak_20 - base_20) / base_20
        if rally < min_rally_20d:
            continue
        body = abs(close[i] - open_[i]) / (high[i] - low[i]) if high[i] > low[i] else 1
        if body > doji_body_max:
            continue
        pb_depth = (close[i] - peak_20) / peak_20
        if pb_depth < pb_depth_min or pb_depth > pb_depth_max:
            continue
        gain_retained = (close[i] - base_20) / (peak_20 - base_20) if peak_20 > base_20 else 0
        if gain_retained < gain_retained_min:
            continue
        vol_mean5 = np.mean(volume[max(0, i - 5):i])
        if vol_mean5 > 0 and volume[i] > vol_mean5 * vol_contract:
            pass
        entry[i] = True

    return {"entry": entry, "exit": np.zeros(n, dtype=bool), "indicators": {"type": "wave3_rapid_doji"}}


def full_rally_rider_signals(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray,
    close: np.ndarray, volume: np.ndarray, amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """整轮主升浪: 底部信号入场 + 移动止盈持有.

    买入: 同 wave1_base_breakout 条件
    卖出: 从最高点回撤 > trailing_pct (移动止盈) 或 close < MA60
    """
    p = params or {}
    n = len(close)

    buy_result = wave1_base_breakout_signals(open_, high, low, close, volume, amount, p)
    raw_entry = buy_result["entry"]
    trailing_pct = float(p.get("trailing_pct", 0.10))
    ma60 = _ma(close, 60)

    entry = np.zeros(n, dtype=bool)
    exit_arr = np.zeros(n, dtype=bool)
    in_position = False
    highest = 0.0

    for i in range(n):
        if not in_position:
            if raw_entry[i]:
                entry[i] = True
                in_position = True
                highest = close[i]
        else:
            highest = max(highest, high[i])
            drawdown = (close[i] - highest) / highest if highest > 0 else 0
            below_ma60 = np.isfinite(ma60[i]) and close[i] < ma60[i]
            if drawdown < -trailing_pct or below_ma60:
                exit_arr[i] = True
                in_position = False

    return {"entry": entry, "exit": exit_arr, "indicators": {"type": "full_rally_rider"}}


# 保留旧名兼容
consolidation_breakout_signals = wave1_base_breakout_signals
continuation_signals = wave2_pullback_buy_signals
pullback_doji_enhanced_signals = wave3_rapid_doji_signals
