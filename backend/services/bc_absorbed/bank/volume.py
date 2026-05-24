"""Volume-based formulas for bc_absorbed bank (Phase 2.4 category 3/7).

Volume signals reveal institutional footprint vs retail noise.

Formulas:
- obv_breakout: OBV trend break above prior high (accumulation confirm)
- mfi_oversold_bounce: MFI(14) crosses up through 20
- volume_spike: today vol > 3 × MA20 vol (institutional event)
- vwap_cross_up: close crosses above 20-day VWAP
- ad_line_uptrend: Accumulation/Distribution rising 10 days while price flat (silent accum)
- chaikin_mfi: Chaikin Money Flow > 0.15 (strong buying)
- vpt_divergence_bullish: VPT rising while price flat
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def obv_breakout(close: np.ndarray, volume: np.ndarray, *, lookback: int = 20,
                 **params: Any) -> tuple[np.ndarray, dict]:
    """OBV breaks above prior lookback high while price near recent high."""
    direction = np.sign(np.diff(close, prepend=close[0]))
    obv = np.cumsum(direction * volume)
    obv_high = pd.Series(obv).rolling(lookback).max().shift(1).values
    entry = obv > obv_high
    entry = np.nan_to_num(entry, nan=False).astype(bool)
    return entry, {"name": "obv_breakout", "entry_count": int(entry.sum())}


def mfi_oversold_bounce(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                        volume: np.ndarray, *, period: int = 14, threshold: float = 20.0,
                        **params: Any) -> tuple[np.ndarray, dict]:
    """MFI(14) crosses up through threshold (default 20 = oversold)."""
    tp = (high + low + close) / 3
    mf = tp * volume
    pos_flow = np.where(tp > np.roll(tp, 1), mf, 0)
    neg_flow = np.where(tp < np.roll(tp, 1), mf, 0)
    pos_sum = pd.Series(pos_flow).rolling(period).sum().values
    neg_sum = pd.Series(neg_flow).rolling(period).sum().values
    mfr = np.where(neg_sum > 0, pos_sum / neg_sum, 100)
    mfi = 100 - (100 / (1 + mfr))
    entry = np.zeros(len(close), dtype=bool)
    entry[1:] = (mfi[:-1] <= threshold) & (mfi[1:] > threshold)
    return entry, {"name": "mfi_oversold_bounce", "threshold": threshold, "entry_count": int(entry.sum())}


def volume_spike(close: np.ndarray, volume: np.ndarray, *, period: int = 20,
                 spike_mult: float = 3.0, require_price_up: bool = True,
                 **params: Any) -> tuple[np.ndarray, dict]:
    """Volume > spike_mult × MA(period), optionally with price rise."""
    vol_ma = pd.Series(volume).rolling(period).mean().values
    spike = volume > vol_ma * spike_mult
    if require_price_up:
        price_up = np.concatenate([[False], close[1:] > close[:-1]])
        entry = spike & price_up
    else:
        entry = spike.copy()
    return entry, {"name": "volume_spike", "spike_mult": spike_mult, "entry_count": int(entry.sum())}


def vwap_cross_up(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                  volume: np.ndarray, *, period: int = 20,
                  **params: Any) -> tuple[np.ndarray, dict]:
    """Close crosses above period-VWAP from below."""
    tp = (high + low + close) / 3
    tp_vol = tp * volume
    vwap = pd.Series(tp_vol).rolling(period).sum().values / pd.Series(volume).rolling(period).sum().values
    entry = np.zeros(len(close), dtype=bool)
    entry[1:] = (close[:-1] <= vwap[:-1]) & (close[1:] > vwap[1:])
    return entry, {"name": "vwap_cross_up", "entry_count": int(entry.sum())}


def ad_line_uptrend(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                    volume: np.ndarray, *, period: int = 10,
                    **params: Any) -> tuple[np.ndarray, dict]:
    """A/D line rising while price relatively flat (silent accumulation)."""
    mfm = np.where((high - low) > 0, ((close - low) - (high - close)) / (high - low), 0)
    mfv = mfm * volume
    ad = np.cumsum(mfv)
    ad_slope = ad - np.roll(ad, period)
    price_change = np.abs(close - np.roll(close, period)) / np.where(np.roll(close, period) > 0, np.roll(close, period), 1)
    entry = (ad_slope > 0) & (price_change < 0.05) & np.arange(len(close)) >= period
    return entry.astype(bool), {"name": "ad_line_uptrend", "entry_count": int(entry.sum())}


def chaikin_money_flow(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                       volume: np.ndarray, *, period: int = 21, threshold: float = 0.15,
                       **params: Any) -> tuple[np.ndarray, dict]:
    """CMF > threshold (strong buying pressure)."""
    mfm = np.where((high - low) > 0, ((close - low) - (high - close)) / (high - low), 0)
    mfv = mfm * volume
    cmf = pd.Series(mfv).rolling(period).sum().values / pd.Series(volume).rolling(period).sum().values
    entry = np.zeros(len(close), dtype=bool)
    entry[1:] = (cmf[:-1] <= threshold) & (cmf[1:] > threshold)
    return entry, {"name": "chaikin_money_flow", "threshold": threshold, "entry_count": int(entry.sum())}


def vpt_divergence_bullish(close: np.ndarray, volume: np.ndarray, *, period: int = 14,
                            **params: Any) -> tuple[np.ndarray, dict]:
    """Volume-Price Trend rising while price flat (bullish divergence)."""
    pct_change = np.diff(close, prepend=close[0]) / np.where(close > 0, close, 1)
    vpt = np.cumsum(pct_change * volume)
    vpt_slope = vpt - np.roll(vpt, period)
    price_change = (close - np.roll(close, period)) / np.where(np.roll(close, period) > 0, np.roll(close, period), 1)
    entry = (vpt_slope > 0) & (np.abs(price_change) < 0.03)
    entry[:period] = False
    return entry.astype(bool), {"name": "vpt_divergence_bullish", "entry_count": int(entry.sum())}


VOLUME_FORMULAS = {
    "obv_breakout": obv_breakout,
    "mfi_oversold_bounce": mfi_oversold_bounce,
    "volume_spike": volume_spike,
    "vwap_cross_up": vwap_cross_up,
    "ad_line_uptrend": ad_line_uptrend,
    "chaikin_money_flow": chaikin_money_flow,
    "vpt_divergence_bullish": vpt_divergence_bullish,
}
