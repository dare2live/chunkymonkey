"""衍生公式 — 从 300616 三波实测 + Codex 设计, 基于核心公式组合.

3 个信号类型:
  1. consolidation_breakout: 底部首涨 (长期横盘 → MA突破 → 多公式共振)
  2. continuation: 主涨续涨 (趋势确认 + 3+ 公式共振)
  3. pullback_doji_enhanced: 增强十字星 (加 gain_retained + pb_depth 过滤)

这些公式不是独立的 OHLCV 计算, 而是组合核心公式信号 + 画像条件.
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


def _slope(ma_arr: np.ndarray, i: int, w: int = 10) -> float:
    if i < w:
        return 0.0
    seg = ma_arr[i - w + 1:i + 1]
    valid = seg[np.isfinite(seg)]
    if len(valid) < 2:
        return 0.0
    return float((valid[-1] - valid[0]) / (len(valid) * (valid[0] if valid[0] != 0 else 1)))


def consolidation_breakout_signals(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """底部首涨: 长期横盘后 MA 突破 + 量扩 + 不追涨停."""
    params = params or {}
    n = len(close)
    entry = np.zeros(n, dtype=bool)

    base_min_days = int(params.get("base_min_days", 45))
    ma_long = int(params.get("ma_long", 120))
    vol_expand_ratio = float(params.get("vol_expand_ratio", 1.5))
    pre_limit_cap = float(params.get("pre_limit_cap", 0.08))
    breakout_ext_max = float(params.get("breakout_ext_max", 1.06))

    ma_l = _ma(close, ma_long)
    ma20 = _ma(close, 20)
    vma20 = _ma(volume, 20)

    for i in range(max(base_min_days, ma_long), n):
        if not np.isfinite(ma_l[i]):
            continue
        below_count = int(np.sum(close[i - base_min_days:i] < ma_l[i - base_min_days:i]))
        if below_count < base_min_days * 0.6:
            continue
        if close[i] <= ma_l[i]:
            continue
        prev_close = close[i - 1] if i > 0 else close[i]
        daily_ret = (close[i] - prev_close) / prev_close if prev_close > 0 else 0
        if daily_ret > pre_limit_cap:
            continue
        if vma20[i] > 0 and volume[i] < vma20[i] * vol_expand_ratio:
            continue
        recent_high = np.max(high[max(0, i - 60):i + 1])
        if close[i] > recent_high * breakout_ext_max:
            continue
        entry[i] = True

    return {"entry": entry, "exit": close < ma_l, "indicators": {"type": "consolidation_breakout"}}


def continuation_signals(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """主涨续涨: 趋势确认 + 量能持续 + 不过度延伸."""
    params = params or {}
    n = len(close)
    entry = np.zeros(n, dtype=bool)

    lookback = int(params.get("lookback_days", 20))
    vol_confirm = float(params.get("vol_confirm_ratio", 1.0))
    max_extension = float(params.get("max_extension_from_ma20", 1.15))
    min_recent_gain = float(params.get("min_recent_gain", 0.05))

    ma20 = _ma(close, 20)
    ma60 = _ma(close, 60)
    vma20 = _ma(volume, 20)

    for i in range(max(60, lookback), n):
        if not (np.isfinite(ma20[i]) and np.isfinite(ma60[i])):
            continue
        if close[i] <= ma20[i] or close[i] <= ma60[i]:
            continue
        if _slope(ma20, i) <= 0:
            continue
        if ma20[i] > 0 and close[i] > ma20[i] * max_extension:
            continue
        recent_gain = (close[i] - close[i - lookback]) / close[i - lookback] if close[i - lookback] > 0 else 0
        if recent_gain < min_recent_gain:
            continue
        if vma20[i] > 0 and volume[i] < vma20[i] * vol_confirm:
            continue
        entry[i] = True

    return {"entry": entry, "exit": close < ma20, "indicators": {"type": "continuation"}}


def pullback_doji_enhanced_signals(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """增强十字星: 原始 pullback_doji + gain_retained + pb_depth 过滤.

    Codex review 发现: Signal 2 (300616 W2) 亏损因为 gain_retained=0.35 太低,
    pb_depth=-9.5% 太深. 加过滤后只保留强信号.
    """
    params = params or {}
    n = len(close)
    entry = np.zeros(n, dtype=bool)

    gain_retained_min = float(params.get("gain_retained_min", 0.5))
    pb_depth_max = float(params.get("pb_depth_max", -0.07))
    doji_body_max = float(params.get("doji_body_max", 0.25))
    pullback_min_days = int(params.get("pullback_min_days", 2))
    pullback_max_days = int(params.get("pullback_max_days", 7))
    breakout_pct_min = float(params.get("breakout_pct_min", 0.05))
    vol_shrink = float(params.get("vol_shrink", 0.7))
    buy_offset = int(params.get("buy_offset", 1))

    ma20 = _ma(close, 20)
    ma60 = _ma(close, 60)
    vma20 = _ma(volume, 20)

    for i in range(30, n):
        if not (np.isfinite(ma20[i]) and np.isfinite(ma60[i])):
            continue
        if close[i] < ma20[i] or close[i] < ma60[i]:
            continue

        body_ratio = abs(close[i] - open_[i]) / (high[i] - low[i]) if high[i] > low[i] else 1.0
        if body_ratio > doji_body_max:
            continue

        local_high_idx = i - 1 - pullback_min_days
        if local_high_idx < 1:
            continue
        search_start = max(0, local_high_idx - 20)
        local_high = np.max(high[search_start:local_high_idx + 1])
        local_high_i = search_start + np.argmax(high[search_start:local_high_idx + 1])

        pb_days = i - local_high_i
        if pb_days < pullback_min_days or pb_days > pullback_max_days:
            continue

        pb_depth = (close[i] - local_high) / local_high if local_high > 0 else 0
        if pb_depth < pb_depth_max:
            continue

        pre_breakout_close = close[max(0, local_high_i - 5)]
        breakout_gain = (local_high - pre_breakout_close) / pre_breakout_close if pre_breakout_close > 0 else 0
        if breakout_gain < breakout_pct_min:
            continue

        gain_retained = (close[i] - pre_breakout_close) / (local_high - pre_breakout_close) if (local_high - pre_breakout_close) > 0 else 0
        if gain_retained < gain_retained_min:
            continue

        pb_vol = np.mean(volume[local_high_i + 1:i + 1]) if i > local_high_i else 0
        breakout_vol = volume[local_high_i] if local_high_i < n else 0
        if breakout_vol > 0 and pb_vol > breakout_vol * vol_shrink:
            continue

        buy_i = min(i + buy_offset, n - 1)
        entry[buy_i] = True

    return {"entry": entry, "exit": close < ma20, "indicators": {"type": "pullback_doji_enhanced"}}
