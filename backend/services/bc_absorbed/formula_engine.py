"""Local TDX-style formula signal engine.

The formulas here produce daily entry/exit signals from OHLCV arrays. They are
independent from MACD unless a formula explicitly encodes MACD itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


@dataclass(frozen=True)
class FormulaDefinition:
    formula_id: str
    display_name: str
    description: str


FORMULA_DEFINITIONS: dict[str, FormulaDefinition] = {
    "gs_pullback_confirm": FormulaDefinition(
        "gs_pullback_confirm",
        "GS回调确认",
        "GS买点叠加历史质量、卖出状态、均线多头和回撤约束。",
    ),
    "gs_raw_buy": FormulaDefinition(
        "gs_raw_buy",
        "GS原始买点",
        "原始GS买点 CROSS(X36, X3)，更敏感。",
    ),
    "ma_base_breakout": FormulaDefinition(
        "ma_base_breakout",
        "均线筑底突破",
        "MA5长期低于MA90后突破并站稳MA145。",
    ),
    "activity_breakout": FormulaDefinition(
        "activity_breakout",
        "活跃度大牛突破",
        "K线活跃度 X15 向上突破大牛线。",
    ),
    "volume_base_breakout": FormulaDefinition(
        "volume_base_breakout",
        "巨量蓄势启动",
        "巨量后缩量横盘，再温和放量突破平台。",
    ),
}


def _ma(arr: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(arr), np.nan, dtype=np.float64)
    if window <= 0 or len(arr) < window:
        return out
    kernel = np.ones(window, dtype=np.float64) / window
    out[window - 1 :] = np.convolve(arr.astype(np.float64), kernel, mode="valid")
    return out


def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    out = np.empty(len(arr), dtype=np.float64)
    if len(arr) == 0:
        return out
    alpha = 2.0 / (span + 1)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rolling_sum_bool(arr: np.ndarray, window: int) -> np.ndarray:
    out = np.zeros(len(arr), dtype=np.float64)
    if window <= 0 or len(arr) == 0:
        return out
    vals = arr.astype(np.float64)
    csum = np.cumsum(vals)
    out[:] = csum
    if len(arr) > window:
        out[window:] = csum[window:] - csum[:-window]
    return out


def _rolling_max(arr: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(arr), np.nan, dtype=np.float64)
    if window <= 0 or len(arr) == 0:
        return out
    limit = min(window - 1, len(arr))
    for i in range(limit):
        out[i] = np.nanmax(arr[: i + 1])
    if len(arr) >= window:
        out[window - 1 :] = np.nanmax(sliding_window_view(arr, window), axis=1)
    return out


def _rolling_min(arr: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(arr), np.nan, dtype=np.float64)
    if window <= 0 or len(arr) == 0:
        return out
    limit = min(window - 1, len(arr))
    for i in range(limit):
        out[i] = np.nanmin(arr[: i + 1])
    if len(arr) >= window:
        out[window - 1 :] = np.nanmin(sliding_window_view(arr, window), axis=1)
    return out


class _RangeExtrema:
    def __init__(self, arr: np.ndarray, op: str):
        self.arr = arr.astype(np.float64, copy=False)
        self.op = op
        self.tables: list[np.ndarray] = [self.arr]
        step = 1
        while step * 2 <= len(self.arr):
            prev = self.tables[-1]
            if op == "max":
                self.tables.append(np.maximum(prev[:-step], prev[step:]))
            else:
                self.tables.append(np.minimum(prev[:-step], prev[step:]))
            step *= 2

    def query(self, start: int, end: int) -> float:
        if end <= start:
            return np.nan
        length = end - start
        level = length.bit_length() - 1
        span = 1 << level
        left = self.tables[level][start]
        right = self.tables[level][end - span]
        if self.op == "max":
            return float(max(left, right))
        return float(min(left, right))


def _prefix_sum(arr: np.ndarray) -> np.ndarray:
    return np.concatenate(([0.0], np.cumsum(arr.astype(np.float64, copy=False))))


def _range_mean(csum: np.ndarray, start: int, end: int) -> float:
    if end <= start:
        return np.nan
    return float((csum[end] - csum[start]) / (end - start))


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out = np.zeros(len(a), dtype=bool)
    if len(a) < 2:
        return out
    out[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return out


def _ref(arr: np.ndarray, n: int, fill: float | None = None) -> np.ndarray:
    out = np.full(len(arr), np.nan if fill is None else fill, dtype=np.float64)
    if n <= 0:
        return arr.astype(np.float64)
    if len(arr) > n:
        out[n:] = arr[:-n]
    return out


def _barslast(cond: np.ndarray) -> np.ndarray:
    out = np.full(len(cond), 1_000_000, dtype=np.int64)
    last = -1
    for i, hit in enumerate(cond):
        if hit:
            last = i
            out[i] = 0
        elif last >= 0:
            out[i] = i - last
    return out


def _barslastcount(cond: np.ndarray) -> np.ndarray:
    out = np.zeros(len(cond), dtype=np.int64)
    run = 0
    for i, hit in enumerate(cond):
        run = run + 1 if hit else 0
        out[i] = run
    return out


def _safe_div(a: np.ndarray, b: np.ndarray, default: float = 0.0) -> np.ndarray:
    out = np.full(len(a), default, dtype=np.float64)
    mask = np.isfinite(a) & np.isfinite(b) & (b != 0)
    out[mask] = a[mask] / b[mask]
    return out


def _apply_cooldown(entry: np.ndarray, cooldown_days: int) -> np.ndarray:
    if cooldown_days <= 0:
        return entry
    out = np.zeros(len(entry), dtype=bool)
    last_i = -10_000
    for i, hit in enumerate(entry):
        if hit and i - last_i >= cooldown_days:
            out[i] = True
            last_i = i
    return out


def _gs_core(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    ma_windows: tuple[int, int, int, int] = (3, 7, 13, 27),
    ema_fallback_span: int = 5,
    down_adjust: float = 0.98,
    up_adjust: float = 1.02,
    iterations: int = 10,
) -> dict[str, np.ndarray]:
    ma_parts = [_ma(close, w) for w in ma_windows]
    ma_stack = np.vstack(ma_parts)
    valid_counts = np.sum(np.isfinite(ma_stack), axis=0)
    x1 = np.divide(
        np.nansum(ma_stack, axis=0),
        valid_counts,
        out=np.full(len(close), np.nan, dtype=np.float64),
        where=valid_counts > 0,
    )
    x2 = _ema(close, ema_fallback_span)
    x3 = np.where(np.isfinite(x1), x1, x2)
    x4 = (high + low + 2 * open_ + 6 * close) / 10.0
    prev_high = _ref(high, 1, close[0] if len(close) else 0.0)
    prev_close = _ref(close, 1, close[0] if len(close) else 0.0)

    x5 = (
        (close < open_)
        | ((close < prev_high) & (close > open_))
        | ((close >= open_) & ((high - close) >= (close - open_)) & (_safe_div(close, prev_close, 1.0) < 1.02))
        | ((close == open_) & ((high - close) >= (close - low)) & (_safe_div(close, prev_close, 1.0) < 1.05))
    )
    x6 = (
        ((close > open_) & (_safe_div(close, prev_close, 1.0) > 0.94))
        | ((close > _ref(low, 1, low[0] if len(low) else 0.0)) & (close < open_))
        | ((close <= open_) & ((close - low) >= (open_ - close)) & (_safe_div(close, prev_close, 1.0) > 0.98))
        | ((close == open_) & ((close - low) >= (high - close)) & (_safe_div(close, prev_close, 1.0) > 0.95))
    )

    x = x4.copy()
    for _ in range(iterations):
        down_cross = _cross(x, x3) & x5
        up_cross = _cross(x3, x) & x6
        x = np.where(down_cross, x3 * down_adjust, np.where(up_cross, x3 * up_adjust, x))

    gsb = _cross(x, x3)
    gss = _cross(x3, x)
    return {"x3": x3, "x36": x, "gsb": gsb, "gss": gss}


def gs_raw_buy_signals(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    core = _gs_core(
        open_,
        high,
        low,
        close,
        ma_windows=tuple(params.get("x3_ma_windows", (3, 7, 13, 27))),
        ema_fallback_span=int(params.get("ema_fallback_span", 5)),
        down_adjust=float(params.get("down_adjust", 0.98)),
        up_adjust=float(params.get("up_adjust", 1.02)),
        iterations=int(params.get("iterations", 10)),
    )
    entry = _apply_cooldown(core["gsb"], int(params.get("signal_cooldown_days", 0)))
    return {"entry": entry, "exit": core["gss"], "indicators": core}


def gs_pullback_confirm_signals(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del volume, amount
    params = params or {}
    core = _gs_core(open_, high, low, close)
    gsb = core["gsb"]
    gss = core["gss"]
    ls = _barslast(gss)
    lb = _barslast(gsb)
    insell_days = int(params.get("insell_days", 3))
    insell = (ls < lb) & (ls <= insell_days)

    hist_window = int(params.get("hist_window", 90))
    fastb = _rolling_sum_bool(gsb & (_barslast(gss) > 0) & (_barslast(gss) <= 3), hist_window)
    totb = _rolling_sum_bool(gsb & (_barslast(gss) > 0), hist_window)
    rate = np.divide(fastb * 100.0, totb, out=np.zeros(len(totb), dtype=np.float64), where=totb > 0)
    histok = (totb >= 1) & (fastb >= 1) & (rate >= float(params.get("rate_min", 40)))

    sellstate = ls < lb
    maxrun = _rolling_max(_barslastcount(sellstate).astype(np.float64), 45)
    sellpct = _rolling_sum_bool(sellstate, 45) * 100.0 / 45.0
    greenok = (maxrun <= float(params.get("maxrun_max", 8))) & (sellpct <= float(params.get("sellpct_max", 60)))

    maxlen = _rolling_max(np.where(gsb, _barslast(gss), 0).astype(np.float64), 90)
    longb = _rolling_sum_bool(gsb & (_barslast(gss) > 10), 90)
    sellqual = (maxlen <= float(params.get("maxlen_max", 20))) & (longb <= float(params.get("longb_max", 2)))

    m20 = _ma(close, 20)
    m60 = _ma(close, 60)
    m90 = _ma(close, 90)
    up = (
        (m20 > m60)
        & (m60 > m90)
        & (m60 > _ref(m60, 20))
        & (m90 > _ref(m90, 20))
        & (close > m90)
        & (close > m60 * float(params.get("m60_buffer", 0.97)))
    )
    ret = _safe_div(close, _rolling_max(high, 20), 1.0)
    pull = (ret <= float(params.get("ma_pull_high", 0.995))) & (ret >= float(params.get("ma_pull_low", 0.78)))
    entry = insell & histok & sellqual & greenok & up & pull
    return {"entry": entry, "exit": gss, "indicators": {**core, "rate": rate, "sellpct": sellpct}}


def ma_base_breakout_signals(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del open_, high, low, volume, amount
    params = params or {}
    short = int(params.get("short_ma", 5))
    mid = int(params.get("mid_ma", 90))
    long = int(params.get("long_ma", 145))
    ma_s = _ma(close, short)
    ma_m = _ma(close, mid)
    ma_l = _ma(close, long)
    top = np.maximum(
        np.where(np.isfinite(ma_m), ma_m, -np.inf),
        np.where(np.isfinite(ma_l), ma_l, -np.inf),
    )
    top = np.where(np.isfinite(top), top, np.nan)
    ls = _barslast(ma_s >= ma_m)
    cross_l = _cross(close, ma_l)
    b145 = _barslast(cross_l)
    rising = _rolling_sum_bool(ma_s > _ref(ma_s, 1), int(params.get("ma5_rising_count_window", 10)))
    since_break_count_below = np.zeros(len(close), dtype=np.float64)
    since_break_count_above = np.zeros(len(close), dtype=np.float64)
    for i in range(len(close)):
        b = int(b145[i])
        if b >= 1_000_000:
            since_break_count_below[i] = 1_000_000
            since_break_count_above[i] = 0
        else:
            start = max(0, i - b)
            since_break_count_below[i] = np.sum(close[start : i + 1] < ma_l[start : i + 1])
            since_break_count_above[i] = np.sum(close[start : i + 1] > ma_l[start : i + 1])

    tj1 = (ls >= int(params.get("below_days_min", 45))) & (ma_s < ma_m)
    tj2 = rising >= int(params.get("ma5_rising_min", 7))
    tj3 = (_rolling_sum_bool(cross_l, int(params.get("breakout_lookback", 11))) >= 1) & (b145 <= int(params.get("breakout_recent_days", 10))) & (close > ma_l)
    tj4 = since_break_count_below == 0
    tj5 = _rolling_sum_bool(close > ma_l, 45) == (b145 + 1)
    tj6 = (close <= top * float(params.get("price_top_buffer", 1.06))) & (close <= ma_l * float(params.get("price_long_ma_buffer", 1.10)))
    tj7 = _rolling_sum_bool(_cross(ma_m, ma_l) | _cross(ma_l, ma_m), 45) == 0
    entry = tj1 & tj2 & tj3 & tj4 & tj5 & tj6 & tj7
    return {"entry": entry, "exit": close < ma_l, "indicators": {"ma_short": ma_s, "ma_mid": ma_m, "ma_long": ma_l}}


def activity_breakout_signals(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del volume, amount
    params = params or {}
    x1 = np.where(close <= open_, close, open_)
    x2 = _safe_div(x1 - low, low) * 100.0
    x3 = _safe_div(close - _ref(close, 1, close[0] if len(close) else 0.0), _ref(close, 1, close[0] if len(close) else 1.0)) * 100.0
    x4 = _safe_div(open_ - _ref(close, 1, close[0] if len(close) else 0.0), _ref(close, 1, close[0] if len(close) else 1.0)) * 100.0
    x5 = _safe_div(close - open_, open_) * 100.0
    x6 = np.where(close >= open_, close, open_)
    x7 = _safe_div(high - x6, x6) * 100.0
    x12 = x7 + x2
    x10 = x5 + x7
    x11 = x5 + x2
    x15 = np.maximum.reduce([x12, x3, x11, x10, x2, x7, x4]) * float(params.get("x15_multiplier", 1.2))
    big = np.full(len(close), float(params.get("big_bull_line", params.get("大牛线", 6.0))), dtype=np.float64)
    raw_entry = _cross(x15, big)
    prev_close = _ref(close, 1, close[0] if len(close) else 1.0)
    close_ret = _safe_div(close - prev_close, prev_close) * 100.0
    entry = raw_entry & (close_ret >= float(params.get("min_close_ret", -100.0))) & (close_ret <= float(params.get("max_close_ret", 100.0)))
    entry = _apply_cooldown(entry, int(params.get("signal_cooldown_days", 0)))
    return {"entry": entry, "exit": x15 < float(params.get("strong_line", 3.0)), "indicators": {"x15": x15}}


def volume_base_breakout_signals(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del open_
    params = params or {}
    n = len(close)
    spike_lookback = int(params.get("spike_lookback", 30))
    spike_ratio = float(params.get("spike_ratio", 2.0))
    amount_spike_ratio = float(params.get("amount_spike_ratio", 2.0))
    base_min_days = int(params.get("base_min_days", 8))
    base_max_days = int(params.get("base_max_days", 90))
    base_range_max = float(params.get("base_range_max", 0.35))
    base_floor = float(params.get("base_floor", 0.75))
    base_ceiling = float(params.get("base_ceiling", 1.60))
    dry_ratio = float(params.get("dry_ratio", 0.30))
    warm_vol_ratio = float(params.get("warm_vol_ratio", 0.60))
    warm_ret_min = float(params.get("warm_ret_min", 0.0))
    warm_ret_max = float(params.get("warm_ret_max", 0.60))
    breakout_window = int(params.get("breakout_window", 20))
    breakout_near_high = float(params.get("breakout_near_high", 0.0))
    breakout_max_extension = float(params.get("breakout_max_extension", 9.99))
    dry_spike_ratio = float(params.get("dry_spike_ratio", 0.55))
    signal_cooldown_days = int(params.get("signal_cooldown_days", 0))
    latest_only = bool(params.get("__latest_only", False)) and signal_cooldown_days <= 0
    min_scan_i = max(25, base_min_days)
    eval_start_i = max(min_scan_i, int(params.get("__eval_start_index", min_scan_i)))
    loop_start_i = max(min_scan_i, eval_start_i - max(1, signal_cooldown_days))

    vma20 = _ma(volume, 20)
    ama20 = _ma(amount, 20)
    vma10 = _ma(volume, 10)
    ma20 = _ma(close, 20)
    spike = (volume >= vma20 * spike_ratio) | (amount >= ama20 * amount_spike_ratio)
    entry = np.zeros(n, dtype=bool)
    platform_low = np.full(n, np.nan)
    platform_high = np.full(n, np.nan)
    spike_idxs = np.flatnonzero(spike)
    high_range = _RangeExtrema(high, "max")
    low_range = _RangeExtrema(low, "min")
    volume_sum = _prefix_sum(volume)

    def evaluate_condition(i: int) -> tuple[bool, float, float]:
        start = max(0, i - spike_lookback, i - base_max_days)
        end = i - base_min_days + 1
        if end <= start:
            return False, np.nan, np.nan
        left = int(np.searchsorted(spike_idxs, start, side="left"))
        right = int(np.searchsorted(spike_idxs, end, side="left"))
        if right <= left:
            return False, np.nan, np.nan
        for spike_i in spike_idxs[left:right][::-1]:
            spike_i = int(spike_i)
            base_high = high_range.query(spike_i + 1, i)
            base_low = low_range.query(spike_i + 1, i)
            if base_low <= 0 or (base_high - base_low) / base_low > base_range_max:
                continue
            if close[i] < close[spike_i] * base_floor or close[i] > high[spike_i] * base_ceiling:
                continue
            base_volume_start = spike_i + 1
            base_volume_end = i + 1
            if base_volume_end - base_volume_start < base_min_days:
                continue
            back_start = base_volume_start + (base_volume_end - base_volume_start) // 2
            back_mean = _range_mean(volume_sum, back_start, base_volume_end)
            pre_avg = _range_mean(volume_sum, max(0, spike_i - 20), spike_i + 1)
            spike_vol = float(volume[spike_i])
            dry_up = (
                (pre_avg > 0 and back_mean <= pre_avg * dry_ratio)
                or (spike_vol > 0 and back_mean <= spike_vol * dry_spike_ratio)
                or (vma10[i] < vma20[i])
            )
            if not dry_up:
                continue
            recent_start = max(0, i - 4)
            recent_vol_ok = _range_mean(volume_sum, recent_start, i + 1) > vma20[i] * warm_vol_ratio if vma20[i] > 0 else False
            recent_ret = close[i] / close[recent_start] - 1.0 if close[recent_start] > 0 else 0.0
            breakout_ref_start = max(0, i - breakout_window)
            ref_high = high_range.query(breakout_ref_start, i)
            box_breakout = (
                base_high > 0
                and close[i] >= base_high * breakout_near_high
                and close[i] <= base_high * breakout_max_extension
            )
            breakout = close[i] >= ref_high and box_breakout
            trend = close[i] > ma20[i] and ma20[i] > ma20[i - 1]
            condition = recent_vol_ok and warm_ret_min <= recent_ret <= warm_ret_max and (breakout or (trend and box_breakout))
            if condition:
                return True, base_low, base_high
        return False, np.nan, np.nan

    if latest_only:
        for i in range(n - 1, eval_start_i - 1, -1):
            condition, matched_base_low, matched_base_high = evaluate_condition(i)
            if not condition:
                continue
            prev_condition = evaluate_condition(i - 1)[0] if i - 1 >= min_scan_i else False
            if not prev_condition:
                entry[i] = True
                platform_low[i] = matched_base_low
                platform_high[i] = matched_base_high
                break
        return {"entry": entry, "exit": close < ma20, "indicators": {"platform_low": platform_low, "platform_high": platform_high}}

    prev_condition = False
    last_entry_i = -10_000
    for i in range(loop_start_i, n):
        condition, matched_base_low, matched_base_high = evaluate_condition(i)
        if condition and not prev_condition:
            if i - last_entry_i >= signal_cooldown_days:
                if i >= eval_start_i:
                    entry[i] = True
                    platform_low[i] = matched_base_low
                    platform_high[i] = matched_base_high
                last_entry_i = i
        prev_condition = condition

    return {"entry": entry, "exit": close < ma20, "indicators": {"platform_low": platform_low, "platform_high": platform_high}}


def compute_formula_signals(
    formula_id: str,
    *,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if formula_id == "gs_raw_buy":
        return gs_raw_buy_signals(open_, high, low, close, params)
    if formula_id == "gs_pullback_confirm":
        return gs_pullback_confirm_signals(open_, high, low, close, volume, amount, params)
    if formula_id == "ma_base_breakout":
        return ma_base_breakout_signals(open_, high, low, close, volume, amount, params)
    if formula_id == "activity_breakout":
        return activity_breakout_signals(open_, high, low, close, volume, amount, params)
    if formula_id == "volume_base_breakout":
        return volume_base_breakout_signals(open_, high, low, close, volume, amount, params)
    raise ValueError(f"unknown formula_id: {formula_id}")
