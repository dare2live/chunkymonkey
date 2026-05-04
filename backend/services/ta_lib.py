"""Shared technical indicator helpers.

Inputs and outputs are plain Python sequences. Missing numeric values are
represented as ``None``.
"""
from __future__ import annotations

import math
from typing import Optional


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _values(series) -> list[Optional[float]]:
    try:
        iterator = iter(series)
    except TypeError:
        return [_safe_float(series)]
    return [_safe_float(value) for value in iterator]


def _truthy(value) -> bool:
    number = _safe_float(value)
    return number is not None and bool(number)


def ma(series, n: int) -> list[Optional[float]]:
    values = _values(series)
    out = []
    for idx in range(len(values)):
        chunk = values[idx - n + 1:idx + 1]
        valid = [value for value in chunk if value is not None]
        out.append(sum(valid) / n if len(valid) == n else None)
    return out


def ema(series, n: int) -> list[Optional[float]]:
    values = _values(series)
    alpha = 2 / (n + 1)
    out = []
    ema_value = None
    valid_count = 0
    for value in values:
        if value is None:
            out.append(None)
            continue
        valid_count += 1
        ema_value = value if ema_value is None else alpha * value + (1 - alpha) * ema_value
        out.append(ema_value if valid_count >= n else None)
    return out


def ref(series, n: int) -> list:
    values = list(series)
    return [None] * n + values[:-n] if n > 0 else values


def hhv(series, n: int) -> list[Optional[float]]:
    values = _values(series)
    out = []
    for idx in range(len(values)):
        chunk = values[idx - n + 1:idx + 1]
        valid = [value for value in chunk if value is not None]
        out.append(max(valid) if len(valid) == n else None)
    return out


def llv(series, n: int) -> list[Optional[float]]:
    values = _values(series)
    out = []
    for idx in range(len(values)):
        chunk = values[idx - n + 1:idx + 1]
        valid = [value for value in chunk if value is not None]
        out.append(min(valid) if len(valid) == n else None)
    return out


def barslast(condition) -> list[Optional[int]]:
    out = []
    counter = None
    for value in condition:
        if _truthy(value):
            counter = 0
        elif counter is not None:
            counter += 1
        out.append(counter)
    return out


def barslastcount(condition) -> list[int]:
    out = []
    count_value = 0
    for value in condition:
        count_value = count_value + 1 if _truthy(value) else 0
        out.append(count_value)
    return out


def barscount(series) -> list[int]:
    values = _values(series)
    first = None
    for idx, value in enumerate(values):
        if value is not None:
            first = idx
            break
    if first is None:
        return [0 for _value in values]
    return [0 if idx < first else idx - first for idx in range(len(values))]


def cross(left, right) -> list[bool]:
    left_values = _values(left)
    right_values = _values(right)
    if len(right_values) == 1 and len(left_values) != 1:
        right_values = right_values * len(left_values)
    out = [False]
    for idx in range(1, len(left_values)):
        out.append(
            left_values[idx] is not None
            and right_values[idx] is not None
            and left_values[idx - 1] is not None
            and right_values[idx - 1] is not None
            and left_values[idx] > right_values[idx]
            and left_values[idx - 1] <= right_values[idx - 1]
        )
    return out


def count(condition, n: int) -> list[Optional[int]]:
    values = [1 if _truthy(value) else 0 for value in condition]
    return rolling_sum(values, n)


def rolling_sum(series, n: int) -> list[Optional[float]]:
    values = _values(series)
    out = []
    for idx in range(len(values)):
        chunk = values[idx - n + 1:idx + 1]
        valid = [value for value in chunk if value is not None]
        out.append(sum(valid) if len(valid) == n else None)
    return out


def std(series, n: int) -> list[Optional[float]]:
    values = _values(series)
    out = []
    for idx in range(len(values)):
        chunk = values[idx - n + 1:idx + 1]
        valid = [value for value in chunk if value is not None]
        if len(valid) != n or n < 2:
            out.append(None)
            continue
        mean = sum(valid) / n
        out.append(math.sqrt(sum((value - mean) ** 2 for value in valid) / (n - 1)))
    return out


def islastbar(series) -> list[bool]:
    values = list(series)
    return [idx == len(values) - 1 for idx in range(len(values))]


def float_market_cap(close, float_shares: float) -> list[Optional[float]]:
    return [value * float_shares if value is not None else None for value in _values(close)]


def macd(close, fast: int = 12, slow: int = 26, signal: int = 9):
    fast_values = ema(close, fast)
    slow_values = ema(close, slow)
    dif = [
        fast_value - slow_value if fast_value is not None and slow_value is not None else None
        for fast_value, slow_value in zip(fast_values, slow_values)
    ]
    dea = ema(dif, signal)
    hist = [
        (dif_value - dea_value) * 2 if dif_value is not None and dea_value is not None else None
        for dif_value, dea_value in zip(dif, dea)
    ]
    return dif, dea, hist


def compute_alpha_factors(rows: list[dict]) -> list[dict]:
    closes = [_safe_float(row.get("close")) for row in rows]
    highs = [_safe_float(row.get("high")) for row in rows]
    lows = [_safe_float(row.get("low")) for row in rows]
    volumes = [_safe_float(row.get("volume")) for row in rows]
    roc_5 = [
        close / closes[idx - 5] - 1
        if idx >= 5 and close is not None and closes[idx - 5] not in (None, 0)
        else None
        for idx, close in enumerate(closes)
    ]
    macd_line, signal_line, hist = macd(closes)
    vol_ma20 = ma(volumes, 20)
    atr_14 = []
    true_ranges = []
    prev_close = None
    for high, low, close in zip(highs, lows, closes):
        if high is None or low is None:
            true_ranges.append(None)
            prev_close = close
            continue
        candidates = [high - low]
        if prev_close is not None:
            candidates.append(abs(high - prev_close))
            candidates.append(abs(low - prev_close))
        true_ranges.append(max(candidates))
        prev_close = close
    atr_raw = ma(true_ranges, 14)
    for close, atr_value in zip(closes, atr_raw):
        atr_14.append(atr_value / close if close not in (None, 0) and atr_value is not None else None)

    return [
        {
            "ROC_5": roc_5[idx],
            "ATR_14": atr_14[idx],
            "MACD": (
                macd_line[idx] / closes[idx]
                if closes[idx] not in (None, 0) and macd_line[idx] is not None
                else None
            ),
            "MACD_SIGNAL": (
                signal_line[idx] / closes[idx]
                if closes[idx] not in (None, 0) and signal_line[idx] is not None
                else None
            ),
            "MACD_HIST": hist[idx] / closes[idx] if closes[idx] not in (None, 0) and hist[idx] is not None else None,
            "VOL_RATIO_20": (
                volumes[idx] / vol_ma20[idx]
                if volumes[idx] is not None and vol_ma20[idx] not in (None, 0)
                else None
            ),
        }
        for idx in range(len(rows))
    ]
