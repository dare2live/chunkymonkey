"""Price pattern formulas for bc_absorbed bank (Phase 2.4 category 2/7).

Academic + retail-popular price patterns with high empirical win rate.

Formulas:
- cup_and_handle: William O'Neil CANSLIM cup-shape consolidation + handle dip
- double_bottom_w: W-shape reversal (two equal lows + neckline break)
- ascending_triangle: flat resistance + rising support, breakout up
- bull_flag_continuation: strong rally then narrow consolidation + breakout
- rounded_bottom: saucer-shape gradual reversal
- inverse_head_shoulders: 3 lows with middle deepest + neckline break
- box_breakout: tight 20-day range break upward
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def cup_and_handle(close: np.ndarray, *, cup_period: int = 60, handle_period: int = 15,
                   tolerance: float = 0.03, **params: Any) -> tuple[np.ndarray, dict]:
    """Cup-and-handle: U-shape over cup_period + small dip (handle) + breakout.

    Simplified detection: lowest point in cup_period at center +- 10, close
    near prior high (within tolerance), then short pullback (handle), break high.
    """
    n = len(close)
    entry = np.zeros(n, dtype=bool)
    for i in range(cup_period + handle_period, n):
        cup_close = close[i - cup_period - handle_period:i - handle_period]
        if len(cup_close) < cup_period: continue
        cup_high = float(cup_close[:5].max())  # left rim
        cup_low_idx = int(np.argmin(cup_close))
        # U-shape: low in middle of cup
        if not (cup_period * 0.3 <= cup_low_idx <= cup_period * 0.7): continue
        # Right rim close to left rim
        right_rim = float(cup_close[-5:].max())
        if abs(right_rim - cup_high) / cup_high > tolerance: continue
        # Handle: small pullback
        handle = close[i - handle_period:i + 1]
        handle_low = float(handle.min())
        handle_drop = (cup_high - handle_low) / cup_high
        if not (0.05 <= handle_drop <= 0.20): continue
        # Current bar breaks cup_high
        if close[i] > cup_high * 1.005:
            entry[i] = True
    return entry, {"name": "cup_and_handle", "entry_count": int(entry.sum())}


def double_bottom_w(close: np.ndarray, low: np.ndarray, *, period: int = 30,
                    tolerance: float = 0.02, **params: Any) -> tuple[np.ndarray, dict]:
    """Double-bottom W: two equal lows separated by intermediate high + neckline break."""
    n = len(close)
    entry = np.zeros(n, dtype=bool)
    for i in range(period, n):
        window_low = low[i - period:i + 1]
        window_close = close[i - period:i + 1]
        if len(window_low) < period: continue
        # Find two lowest local minima
        sorted_lows = np.argsort(window_low)
        if len(sorted_lows) < 2: continue
        idx1, idx2 = sorted_lows[0], sorted_lows[1]
        if abs(idx1 - idx2) < 5: continue  # Need separation
        # Two lows roughly equal
        l1, l2 = window_low[idx1], window_low[idx2]
        if abs(l1 - l2) / min(l1, l2) > tolerance: continue
        # Neckline = high between two lows
        lo_idx_start, lo_idx_end = sorted([idx1, idx2])
        between_high = float(window_close[lo_idx_start:lo_idx_end + 1].max())
        # Current bar breaks neckline
        if close[i] > between_high * 1.005:
            entry[i] = True
    return entry, {"name": "double_bottom_w", "entry_count": int(entry.sum())}


def ascending_triangle(close: np.ndarray, high: np.ndarray, low: np.ndarray, *,
                       period: int = 30, **params: Any) -> tuple[np.ndarray, dict]:
    """Ascending triangle: flat resistance (R) + rising support, breakout R."""
    n = len(close)
    entry = np.zeros(n, dtype=bool)
    for i in range(period, n):
        window_high = high[i - period:i]
        window_low = low[i - period:i]
        # Flat top: high range tight
        top_range = float(window_high.max() - window_high.min()) / float(window_high.mean())
        if top_range > 0.04: continue
        # Rising bottom: lows form upward slope
        slope = np.polyfit(np.arange(period), window_low, 1)[0]
        if slope <= 0: continue
        resistance = float(window_high.max())
        if close[i] > resistance * 1.005:
            entry[i] = True
    return entry, {"name": "ascending_triangle", "entry_count": int(entry.sum())}


def bull_flag_continuation(close: np.ndarray, *, flagpole_period: int = 10,
                            flag_period: int = 8, pole_min_pct: float = 0.10,
                            **params: Any) -> tuple[np.ndarray, dict]:
    """Strong rally (flagpole) then narrow consolidation (flag) + breakout."""
    n = len(close)
    entry = np.zeros(n, dtype=bool)
    for i in range(flagpole_period + flag_period, n):
        pole = close[i - flagpole_period - flag_period:i - flag_period + 1]
        flag = close[i - flag_period:i + 1]
        if len(pole) < flagpole_period: continue
        pole_gain = (pole[-1] - pole[0]) / pole[0]
        if pole_gain < pole_min_pct: continue
        flag_range = (flag.max() - flag.min()) / flag.mean()
        if flag_range > 0.06: continue
        flag_high = float(flag[:-1].max())
        if close[i] > flag_high * 1.005:
            entry[i] = True
    return entry, {"name": "bull_flag_continuation", "entry_count": int(entry.sum())}


def rounded_bottom(close: np.ndarray, *, period: int = 60, **params: Any) -> tuple[np.ndarray, dict]:
    """Rounded saucer bottom: smooth U-shape with gradual recovery."""
    n = len(close)
    entry = np.zeros(n, dtype=bool)
    for i in range(period, n):
        window = close[i - period:i + 1]
        if len(window) < period: continue
        # Polynomial fit degree 2, check if U-shape (concave up)
        x = np.arange(len(window))
        coef = np.polyfit(x, window, 2)
        if coef[0] <= 0: continue  # not U-shape
        # Current near right rim and rising
        if close[i] > window[period // 2] and close[i] > close[i - 5]:
            entry[i] = True
    return entry, {"name": "rounded_bottom", "entry_count": int(entry.sum())}


def inverse_head_shoulders(close: np.ndarray, low: np.ndarray, *, period: int = 40,
                           **params: Any) -> tuple[np.ndarray, dict]:
    """Inverse H&S: 3 lows with middle (head) deepest + neckline break."""
    n = len(close)
    entry = np.zeros(n, dtype=bool)
    for i in range(period, n):
        window_low = low[i - period:i]
        if len(window_low) < period: continue
        # Divide into 3 equal sections
        s_len = period // 3
        ls = window_low[:s_len]
        hd = window_low[s_len:2 * s_len]
        rs = window_low[2 * s_len:]
        ls_min, hd_min, rs_min = float(ls.min()), float(hd.min()), float(rs.min())
        if not (hd_min < ls_min and hd_min < rs_min): continue
        # Shoulders roughly equal
        if abs(ls_min - rs_min) / min(ls_min, rs_min) > 0.05: continue
        # Neckline = max between shoulders
        neckline = float(close[i - period + s_len // 2:i - period + 2 * s_len + s_len // 2].max())
        if close[i] > neckline * 1.005:
            entry[i] = True
    return entry, {"name": "inverse_head_shoulders", "entry_count": int(entry.sum())}


def box_breakout(close: np.ndarray, *, period: int = 20, range_tolerance: float = 0.05,
                 **params: Any) -> tuple[np.ndarray, dict]:
    """20-day tight range box + upward break."""
    n = len(close)
    entry = np.zeros(n, dtype=bool)
    for i in range(period, n):
        window = close[i - period:i]
        if len(window) < period: continue
        rng = (window.max() - window.min()) / window.mean()
        if rng > range_tolerance: continue
        if close[i] > window.max() * 1.005:
            entry[i] = True
    return entry, {"name": "box_breakout", "entry_count": int(entry.sum())}


PATTERN_FORMULAS = {
    "cup_and_handle": cup_and_handle,
    "double_bottom_w": double_bottom_w,
    "ascending_triangle": ascending_triangle,
    "bull_flag_continuation": bull_flag_continuation,
    "rounded_bottom": rounded_bottom,
    "inverse_head_shoulders": inverse_head_shoulders,
    "box_breakout": box_breakout,
}
