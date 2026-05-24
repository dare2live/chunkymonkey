"""Technical indicator formulas for bc_absorbed bank (Phase 2.4 category 1/7).

Each formula returns (entry_signal: np.ndarray[bool], meta: dict).

Formulas:
- macd_golden_cross_above_zero: DIF crosses above DEA, both > 0
- macd_zero_axis_bullish: DIF/DEA both turn positive (zero-axis crossing)
- rsi_oversold_bounce: RSI(14) crosses up through 30
- bollinger_squeeze_breakout: BB width contracts then expands with price > upper
- kdj_golden_cross: K crosses above D from below 20
- atr_breakout: close breaks above prior 20-day high + 0.5*ATR
- macd_divergence_bottom: price lower low + MACD higher low (bullish divergence)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _ema(series: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(series).ewm(span=span, adjust=False).mean().values


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(period).mean().values
    avg_loss = pd.Series(loss).rolling(period).mean().values
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100)
    return 100 - (100 / (1 + rs))


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_f = _ema(close, fast)
    ema_s = _ema(close, slow)
    dif = ema_f - ema_s
    dea = _ema(dif, signal)
    macd = (dif - dea) * 2
    return dif, dea, macd


def macd_golden_cross_above_zero(close: np.ndarray, **params: Any) -> tuple[np.ndarray, dict]:
    """DIF crosses above DEA, both > 0."""
    dif, dea, _ = _macd(close)
    cross_up = (dif[:-1] <= dea[:-1]) & (dif[1:] > dea[1:])
    above_zero = (dif[1:] > 0) & (dea[1:] > 0)
    entry = np.zeros(len(close), dtype=bool)
    entry[1:] = cross_up & above_zero
    return entry, {"name": "macd_golden_cross_above_zero", "entry_count": int(entry.sum())}


def macd_zero_axis_bullish(close: np.ndarray, **params: Any) -> tuple[np.ndarray, dict]:
    """DIF and DEA both turn positive in same bar (zero-axis crossing bullish)."""
    dif, dea, _ = _macd(close)
    dif_up = (dif[:-1] <= 0) & (dif[1:] > 0)
    dea_up = (dea[:-1] <= 0) & (dea[1:] > 0)
    entry = np.zeros(len(close), dtype=bool)
    entry[1:] = dif_up | dea_up
    return entry, {"name": "macd_zero_axis_bullish", "entry_count": int(entry.sum())}


def rsi_oversold_bounce(close: np.ndarray, *, threshold: float = 30.0, **params: Any) -> tuple[np.ndarray, dict]:
    """RSI(14) crosses up through threshold (default 30 = oversold)."""
    rsi = _rsi(close, 14)
    entry = np.zeros(len(close), dtype=bool)
    entry[1:] = (rsi[:-1] <= threshold) & (rsi[1:] > threshold)
    return entry, {"name": "rsi_oversold_bounce", "threshold": threshold, "entry_count": int(entry.sum())}


def bollinger_squeeze_breakout(close: np.ndarray, *, period: int = 20, std_mult: float = 2.0,
                                squeeze_threshold: float = 0.05, **params: Any) -> tuple[np.ndarray, dict]:
    """BB width < squeeze_threshold then price breaks above upper band."""
    ma = pd.Series(close).rolling(period).mean().values
    std = pd.Series(close).rolling(period).std().values
    upper = ma + std_mult * std
    width = np.where(ma > 0, (std_mult * 2 * std) / ma, np.inf)
    squeeze = width <= squeeze_threshold
    breakout = close > upper
    entry = np.zeros(len(close), dtype=bool)
    # squeeze in prior bar AND breakout current bar
    entry[1:] = squeeze[:-1] & breakout[1:]
    return entry, {"name": "bollinger_squeeze_breakout", "entry_count": int(entry.sum())}


def kdj_golden_cross(close: np.ndarray, high: np.ndarray, low: np.ndarray, *,
                     k_period: int = 9, **params: Any) -> tuple[np.ndarray, dict]:
    """K crosses above D from below 20 (oversold golden cross)."""
    lowest = pd.Series(low).rolling(k_period).min().values
    highest = pd.Series(high).rolling(k_period).max().values
    rsv = np.where(highest > lowest, 100 * (close - lowest) / (highest - lowest), 50)
    k = pd.Series(rsv).ewm(alpha=1/3, adjust=False).mean().values
    d = pd.Series(k).ewm(alpha=1/3, adjust=False).mean().values
    cross_up = (k[:-1] <= d[:-1]) & (k[1:] > d[1:])
    oversold = k[:-1] < 20
    entry = np.zeros(len(close), dtype=bool)
    entry[1:] = cross_up & oversold
    return entry, {"name": "kdj_golden_cross", "entry_count": int(entry.sum())}


def atr_breakout(close: np.ndarray, high: np.ndarray, low: np.ndarray, *,
                 period: int = 20, atr_mult: float = 0.5, **params: Any) -> tuple[np.ndarray, dict]:
    """Close breaks above prior period high + atr_mult * ATR."""
    prev_close = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    atr = pd.Series(tr).rolling(period).mean().values
    prior_high = pd.Series(high).rolling(period).max().shift(1).values
    entry = close > (prior_high + atr_mult * atr)
    entry = np.nan_to_num(entry, nan=False).astype(bool)
    return entry, {"name": "atr_breakout", "atr_mult": atr_mult, "entry_count": int(entry.sum())}


def macd_divergence_bottom(close: np.ndarray, *, lookback: int = 20, **params: Any) -> tuple[np.ndarray, dict]:
    """Bullish divergence: price makes lower low but MACD makes higher low.

    Simplified: in `lookback` window, current low vs prior low.
    """
    dif, _, macd = _macd(close)
    entry = np.zeros(len(close), dtype=bool)
    for i in range(lookback, len(close)):
        window_close = close[i - lookback:i + 1]
        window_macd = macd[i - lookback:i + 1]
        price_low_idx = int(np.argmin(window_close))
        macd_low_idx = int(np.argmin(window_macd))
        # current is lower low in price but MACD already turned up from earlier low
        if price_low_idx == lookback and macd_low_idx < lookback - 3:
            if close[i] < close[i - lookback + price_low_idx] and macd[i] > macd[i - lookback + macd_low_idx]:
                entry[i] = True
    return entry, {"name": "macd_divergence_bottom", "lookback": lookback, "entry_count": int(entry.sum())}


# Registry for dispatch
TECHNICAL_FORMULAS = {
    "macd_golden_cross_above_zero": macd_golden_cross_above_zero,
    "macd_zero_axis_bullish": macd_zero_axis_bullish,
    "rsi_oversold_bounce": rsi_oversold_bounce,
    "bollinger_squeeze_breakout": bollinger_squeeze_breakout,
    "kdj_golden_cross": kdj_golden_cross,
    "atr_breakout": atr_breakout,
    "macd_divergence_bottom": macd_divergence_bottom,
}
