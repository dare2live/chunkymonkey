"""裸K线公式 -> 连续 PIT 特征提取器 (L0 Tier-1, owner=analysis/l0_bare_kline_baseline_spec_20260614.md)。

active 4 公式 (formula_candidates.yaml) 从其核心机制派生连续特征供 RankIC:
  macd_golden_cross  -> MACD 柱 (EMA_fast - EMA_slow - signal): 正=多头动能
  ma_base_breakout   -> close/MA_long - 1: 站上长均线距离
  turtle_breakout    -> Donchian 通道位置 (close 在 N 日高低区间的相对位置)
  reversal_short_term-> -(close/close[i-lb] - 1): 近期跌幅 (超卖反弹预期, 取负使越跌特征越高)

PIT 铁律 (死亡条款泄漏死): feature[i] 只用 bars[:i+1]。EMA 递归只含过去; rolling max/min 用
[i-N+1:i+1] 过去窗; pct_change 用 close[i-lb]。warmup 不足 -> None (标 unknown, 不报假值)。
本模块零未来引用; pit_guard.assert_pit_clean 行为门自动核证 (追加未来 bar 不改过去特征)。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def ema(values: list[float], period: int) -> list[float | None]:
    """PIT EMA: ema[i]=a*x[i]+(1-a)*ema[i-1], a=2/(period+1)。只用过去, warmup<period-1 -> None。"""
    if period < 1:
        raise ValueError(f"period 必须 >=1, got {period}")
    a = 2.0 / (period + 1)
    out: list[float | None] = []
    prev: float | None = None
    for i, x in enumerate(values):
        if x is None:
            out.append(None)
            continue
        prev = x if prev is None else a * x + (1 - a) * prev
        out.append(prev if i >= period - 1 else None)
    return out


def feature_macd_hist(closes: list[float], *, fast: int = 12, slow: int = 26,
                      signal_period: int = 9) -> list[float | None]:
    """MACD 柱 = (EMA_fast - EMA_slow) - signal_EMA。正 = 多头动能。"""
    ef, es = ema(closes, fast), ema(closes, slow)
    dif = [(f - s) if (f is not None and s is not None) else None for f, s in zip(ef, es)]
    dif_filled = [d if d is not None else 0.0 for d in dif]
    sig = ema(dif_filled, signal_period)
    return [(d - s) if (d is not None and s is not None) else None for d, s in zip(dif, sig)]


def feature_ma_distance(closes: list[float], *, long_period: int = 145) -> list[float | None]:
    """close/MA_long - 1: 站上长均线的相对距离。MA_long 用过去 long_period 个 close。"""
    out: list[float | None] = []
    for i in range(len(closes)):
        if i < long_period - 1 or closes[i] in (None, 0):
            out.append(None)
            continue
        window = closes[i - long_period + 1: i + 1]
        if any(c is None for c in window):
            out.append(None)
            continue
        ma = float(np.mean(window))
        out.append(closes[i] / ma - 1.0 if ma != 0 else None)
    return out


def feature_turtle_position(highs: list[float], lows: list[float], closes: list[float],
                            *, channel: int = 20) -> list[float | None]:
    """Donchian 通道位置: (close - low_N)/(high_N - low_N), 用过去 channel 日 (含当日) 高低。"""
    out: list[float | None] = []
    for i in range(len(closes)):
        if i < channel - 1 or closes[i] is None:
            out.append(None)
            continue
        hw = highs[i - channel + 1: i + 1]
        lw = lows[i - channel + 1: i + 1]
        if any(h is None for h in hw) or any(l is None for l in lw):
            out.append(None)
            continue
        hi, lo = max(hw), min(lw)
        out.append((closes[i] - lo) / (hi - lo) if hi != lo else None)
    return out


def feature_reversal(closes: list[float], *, lookback: int = 20) -> list[float | None]:
    """短期反转: -(close[i]/close[i-lb]-1)。近期跌幅大 -> 特征高 (超卖反弹预期)。"""
    out: list[float | None] = []
    for i in range(len(closes)):
        j = i - lookback
        if j < 0 or closes[i] is None or closes[j] in (None, 0):
            out.append(None)
        else:
            out.append(-(closes[i] / closes[j] - 1.0))
    return out


def _load_params(formula_id: str) -> dict:
    p = CONFIG_DIR / f"formula_{formula_id}.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {} if p.exists() else {}


def extract_feature(formula_id: str, bars: dict[str, list[float]],
                    params: dict | None = None) -> list[float | None]:
    """formula_id -> 连续 PIT 特征序列。bars 含 close (+ high/low for turtle)。params 缺省读 yaml。

    params 由调用方 (寻参时 Optuna) 覆盖; 默认读 formula_*.yaml (config 驱动, 不 hardcode 轴)。
    """
    p = params if params is not None else _load_params(formula_id)
    closes = bars["close"]
    if formula_id == "macd_golden_cross":
        return feature_macd_hist(closes, fast=p.get("fast_period", 12),
                                 slow=p.get("slow_period", 26),
                                 signal_period=p.get("signal_period", 9))
    if formula_id == "ma_base_breakout":
        long_p = (p.get("ma") or {}).get("long", 145)
        return feature_ma_distance(closes, long_period=long_p)
    if formula_id == "turtle_breakout":
        # turtle_breakout_20 / _55: 默认 20 日通道
        return feature_turtle_position(bars["high"], bars["low"], closes,
                                       channel=p.get("channel", 20))
    if formula_id == "reversal_short_term":
        lb = (p.get("reversal_1m_mild") or {}).get("lookback_days", 20) if "reversal_1m_mild" in p \
            else p.get("lookback", 20)
        return feature_reversal(closes, lookback=lb)
    raise ValueError(f"未知 formula_id (非 active 池): {formula_id}")


ACTIVE_FORMULAS = ("macd_golden_cross", "ma_base_breakout", "turtle_breakout", "reversal_short_term")
