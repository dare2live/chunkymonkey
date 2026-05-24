"""Multi-timeframe synergy formulas (Phase 2.4 category 4/7).

Confluence across daily + weekly + monthly = stronger signal vs single timeframe.
Resample daily kline up to weekly/monthly internally.

Formulas:
- weekly_macd_daily_macd_bull: both timeframes MACD DIF > DEA + > 0
- weekly_higher_low_daily_break: weekly forming higher-low + daily breaks prior 5d high
- monthly_uptrend_daily_pullback_buy: monthly above MA12 + daily 5-bar pullback to MA20
- multi_tf_rsi_alignment: daily RSI 30-50 (cool) AND weekly RSI > 50 (trending up)
- weekly_breakout_daily_confirm: weekly closes above 20w high + daily confirm next bar
- monthly_stage2_daily_volume_confirm: monthly higher-highs + daily vol spike
- weekly_dragon_daily_pullback: weekly +N consecutive up + daily small pullback
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _resample_close(close: np.ndarray, factor: int) -> np.ndarray:
    """Resample daily close to factor-day bar close (last close of each window)."""
    n = len(close)
    out = []
    for i in range(0, n, factor):
        end = min(i + factor, n)
        out.append(close[end - 1])
    return np.array(out)


def _macd_dif_dea(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_f = pd.Series(close).ewm(span=fast, adjust=False).mean().values
    ema_s = pd.Series(close).ewm(span=slow, adjust=False).mean().values
    dif = ema_f - ema_s
    dea = pd.Series(dif).ewm(span=signal, adjust=False).mean().values
    return dif, dea


def weekly_macd_daily_macd_bull(close: np.ndarray, **params: Any) -> tuple[np.ndarray, dict]:
    """Daily MACD DIF > DEA > 0 AND weekly same."""
    daily_dif, daily_dea = _macd_dif_dea(close)
    weekly_close = _resample_close(close, 5)
    if len(weekly_close) < 30:
        return np.zeros(len(close), dtype=bool), {"name": "weekly_macd_daily_macd_bull", "entry_count": 0}
    w_dif, w_dea = _macd_dif_dea(weekly_close)
    # Map weekly signals back to daily timeline
    weekly_bull = (w_dif > w_dea) & (w_dif > 0) & (w_dea > 0)
    weekly_map = np.repeat(weekly_bull, 5)[:len(close)]
    if len(weekly_map) < len(close):
        weekly_map = np.concatenate([weekly_map, np.zeros(len(close) - len(weekly_map), dtype=bool)])
    daily_bull = (daily_dif > daily_dea) & (daily_dif > 0)
    entry = weekly_map & daily_bull
    return entry, {"name": "weekly_macd_daily_macd_bull", "entry_count": int(entry.sum())}


def weekly_higher_low_daily_break(close: np.ndarray, low: np.ndarray, *, lookback: int = 5,
                                   **params: Any) -> tuple[np.ndarray, dict]:
    """Weekly forms higher-low + daily breaks prior lookback-day high."""
    weekly_low = _resample_close(low, 5)
    if len(weekly_low) < 4:
        return np.zeros(len(close), dtype=bool), {"name": "weekly_higher_low_daily_break", "entry_count": 0}
    weekly_hl = np.zeros(len(weekly_low), dtype=bool)
    for i in range(2, len(weekly_low)):
        if weekly_low[i] > weekly_low[i - 1] and weekly_low[i - 1] < weekly_low[i - 2]:
            weekly_hl[i] = True
    weekly_map = np.repeat(weekly_hl, 5)[:len(close)]
    if len(weekly_map) < len(close):
        weekly_map = np.concatenate([weekly_map, np.zeros(len(close) - len(weekly_map), dtype=bool)])
    daily_high_lookback = pd.Series(close).rolling(lookback).max().shift(1).values
    daily_break = close > daily_high_lookback
    daily_break = np.nan_to_num(daily_break, nan=False).astype(bool)
    entry = weekly_map & daily_break
    return entry, {"name": "weekly_higher_low_daily_break", "entry_count": int(entry.sum())}


def monthly_uptrend_daily_pullback_buy(close: np.ndarray, **params: Any) -> tuple[np.ndarray, dict]:
    """Monthly above 12m MA (uptrend) + daily 5-bar pullback to MA20."""
    monthly_close = _resample_close(close, 22)
    if len(monthly_close) < 13:
        return np.zeros(len(close), dtype=bool), {"name": "monthly_uptrend_daily_pullback_buy", "entry_count": 0}
    m_ma12 = pd.Series(monthly_close).rolling(12).mean().values
    monthly_up = monthly_close > m_ma12
    monthly_map = np.repeat(monthly_up, 22)[:len(close)]
    if len(monthly_map) < len(close):
        monthly_map = np.concatenate([monthly_map, np.zeros(len(close) - len(monthly_map), dtype=bool)])
    ma20 = pd.Series(close).rolling(20).mean().values
    near_ma20 = np.abs(close - ma20) / np.where(ma20 > 0, ma20, 1) < 0.01
    pullback = (close < pd.Series(close).rolling(5).max().shift(1).values * 0.97)
    pullback = np.nan_to_num(pullback, nan=False).astype(bool)
    entry = monthly_map & near_ma20 & pullback
    return entry, {"name": "monthly_uptrend_daily_pullback_buy", "entry_count": int(entry.sum())}


def multi_tf_rsi_alignment(close: np.ndarray, **params: Any) -> tuple[np.ndarray, dict]:
    """Daily RSI 30-50 (cool reset) AND weekly RSI > 50 (trending up)."""
    def _rsi(s, period=14):
        delta = np.diff(s, prepend=s[0])
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_g = pd.Series(gain).rolling(period).mean().values
        avg_l = pd.Series(loss).rolling(period).mean().values
        rs = np.where(avg_l > 0, avg_g / avg_l, 100)
        return 100 - 100 / (1 + rs)
    daily_rsi = _rsi(close)
    weekly_close = _resample_close(close, 5)
    if len(weekly_close) < 20:
        return np.zeros(len(close), dtype=bool), {"name": "multi_tf_rsi_alignment", "entry_count": 0}
    w_rsi = _rsi(weekly_close)
    w_map = np.repeat(w_rsi > 50, 5)[:len(close)]
    if len(w_map) < len(close):
        w_map = np.concatenate([w_map, np.zeros(len(close) - len(w_map), dtype=bool)])
    daily_cool = (daily_rsi > 30) & (daily_rsi < 50)
    entry = w_map & daily_cool
    return entry, {"name": "multi_tf_rsi_alignment", "entry_count": int(entry.sum())}


def weekly_breakout_daily_confirm(close: np.ndarray, *, weekly_window: int = 20, **params: Any) -> tuple[np.ndarray, dict]:
    """Weekly closes above 20-week high + daily confirms with up close."""
    weekly_close = _resample_close(close, 5)
    if len(weekly_close) < weekly_window + 1:
        return np.zeros(len(close), dtype=bool), {"name": "weekly_breakout_daily_confirm", "entry_count": 0}
    w_high = pd.Series(weekly_close).rolling(weekly_window).max().shift(1).values
    w_break = weekly_close > w_high
    w_map = np.repeat(w_break, 5)[:len(close)]
    if len(w_map) < len(close):
        w_map = np.concatenate([w_map, np.zeros(len(close) - len(w_map), dtype=bool)])
    daily_up = np.concatenate([[False], close[1:] > close[:-1]])
    entry = w_map & daily_up
    return entry, {"name": "weekly_breakout_daily_confirm", "entry_count": int(entry.sum())}


def monthly_stage2_daily_volume_confirm(close: np.ndarray, volume: np.ndarray,
                                          **params: Any) -> tuple[np.ndarray, dict]:
    """Monthly higher-highs (uptrend) + daily vol spike 2x MA20."""
    monthly_close = _resample_close(close, 22)
    if len(monthly_close) < 4:
        return np.zeros(len(close), dtype=bool), {"name": "monthly_stage2_daily_volume_confirm", "entry_count": 0}
    m_uptrend = np.zeros(len(monthly_close), dtype=bool)
    for i in range(2, len(monthly_close)):
        if monthly_close[i] > monthly_close[i - 1] > monthly_close[i - 2]:
            m_uptrend[i] = True
    m_map = np.repeat(m_uptrend, 22)[:len(close)]
    if len(m_map) < len(close):
        m_map = np.concatenate([m_map, np.zeros(len(close) - len(m_map), dtype=bool)])
    vol_ma = pd.Series(volume).rolling(20).mean().values
    vol_spike = volume > vol_ma * 2.0
    entry = m_map & vol_spike
    return entry, {"name": "monthly_stage2_daily_volume_confirm", "entry_count": int(entry.sum())}


def weekly_dragon_daily_pullback(close: np.ndarray, *, weekly_streak: int = 3,
                                   **params: Any) -> tuple[np.ndarray, dict]:
    """Weekly N consecutive up + daily small pullback."""
    weekly_close = _resample_close(close, 5)
    if len(weekly_close) < weekly_streak + 1:
        return np.zeros(len(close), dtype=bool), {"name": "weekly_dragon_daily_pullback", "entry_count": 0}
    w_streak = np.zeros(len(weekly_close), dtype=bool)
    for i in range(weekly_streak, len(weekly_close)):
        if all(weekly_close[i - j] > weekly_close[i - j - 1] for j in range(weekly_streak)):
            w_streak[i] = True
    w_map = np.repeat(w_streak, 5)[:len(close)]
    if len(w_map) < len(close):
        w_map = np.concatenate([w_map, np.zeros(len(close) - len(w_map), dtype=bool)])
    daily_pullback = np.zeros(len(close), dtype=bool)
    daily_pullback[1:] = (close[1:] < close[:-1]) & (close[1:] > close[:-1] * 0.98)
    entry = w_map & daily_pullback
    return entry, {"name": "weekly_dragon_daily_pullback", "entry_count": int(entry.sum())}


MULTI_TF_FORMULAS = {
    "weekly_macd_daily_macd_bull": weekly_macd_daily_macd_bull,
    "weekly_higher_low_daily_break": weekly_higher_low_daily_break,
    "monthly_uptrend_daily_pullback_buy": monthly_uptrend_daily_pullback_buy,
    "multi_tf_rsi_alignment": multi_tf_rsi_alignment,
    "weekly_breakout_daily_confirm": weekly_breakout_daily_confirm,
    "monthly_stage2_daily_volume_confirm": monthly_stage2_daily_volume_confirm,
    "weekly_dragon_daily_pullback": weekly_dragon_daily_pullback,
}
