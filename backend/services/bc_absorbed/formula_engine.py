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
    "pullback_doji": FormulaDefinition(
        "pullback_doji",
        "回调十字星",
        "放量大涨后凌厉缩量回调收十字星，次日买入。按板块涨停阈值适配。",
    ),
    "consolidation_breakout": FormulaDefinition(
        "consolidation_breakout",
        "底部首涨",
        "长期横盘后MA突破+量扩+不追涨停。300616 W1/W3起涨点。",
    ),
    "continuation": FormulaDefinition(
        "continuation",
        "主涨续涨",
        "趋势确认(MA20/60上方)+量能持续+不过度延伸。300616 主涨段。",
    ),
    "pullback_doji_enhanced": FormulaDefinition(
        "pullback_doji_enhanced",
        "增强十字星",
        "原始十字星+gain_retained(>50%)+pb_depth(<7%)过滤弱信号。",
    ),
}


def _register_bank_definitions() -> None:
    try:
        from bank import ALL_FORMULAS
    except ImportError:
        return
    for fid in ALL_FORMULAS:
        if fid not in FORMULA_DEFINITIONS:
            FORMULA_DEFINITIONS[fid] = FormulaDefinition(fid, fid, f"bank formula: {fid}")


_register_bank_definitions()


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


def _load_gs_yaml() -> dict[str, Any]:
    import yaml
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / "formula_gs.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def gs_raw_buy_signals(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}
    ycfg = _load_gs_yaml().get("gs_raw_buy", {})
    core = _gs_core(
        open_,
        high,
        low,
        close,
        ma_windows=tuple(params.get("x3_ma_windows",
                                     ycfg.get("x3_ma_windows", [3, 7, 13, 27]))),
        ema_fallback_span=int(params.get("ema_fallback_span",
                                          ycfg.get("ema_fallback_span", 5))),
        down_adjust=float(params.get("down_adjust",
                                      ycfg.get("down_adjust", 0.98))),
        up_adjust=float(params.get("up_adjust",
                                    ycfg.get("up_adjust", 1.02))),
        iterations=int(params.get("iterations",
                                   ycfg.get("iterations", 10))),
    )
    entry = _apply_cooldown(core["gsb"], int(params.get("signal_cooldown_days",
                                                         ycfg.get("signal_cooldown_days", 0))))
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
    full_cfg = _load_gs_yaml()
    ycfg = full_cfg.get("gs_pullback_confirm", {})
    core_cfg = full_cfg.get("gs_raw_buy", {})
    core = _gs_core(
        open_, high, low, close,
        ma_windows=tuple(params.get("x3_ma_windows",
                                     core_cfg.get("x3_ma_windows", [3, 7, 13, 27]))),
        ema_fallback_span=int(params.get("ema_fallback_span",
                                          core_cfg.get("ema_fallback_span", 5))),
        down_adjust=float(params.get("down_adjust",
                                      core_cfg.get("down_adjust", 0.98))),
        up_adjust=float(params.get("up_adjust",
                                    core_cfg.get("up_adjust", 1.02))),
        iterations=int(params.get("iterations",
                                   core_cfg.get("iterations", 10))),
    )
    gsb = core["gsb"]
    gss = core["gss"]
    ls = _barslast(gss)
    lb = _barslast(gsb)
    insell_days = int(params.get("insell_days", ycfg.get("insell_days", 3)))
    insell = (ls < lb) & (ls <= insell_days)

    hist_window = int(params.get("hist_window", ycfg.get("hist_window", 90)))
    fastb = _rolling_sum_bool(gsb & (_barslast(gss) > 0) & (_barslast(gss) <= 3), hist_window)
    totb = _rolling_sum_bool(gsb & (_barslast(gss) > 0), hist_window)
    rate = np.divide(fastb * 100.0, totb, out=np.zeros(len(totb), dtype=np.float64), where=totb > 0)
    histok = (totb >= 1) & (fastb >= 1) & (rate >= float(params.get("rate_min", ycfg.get("rate_min", 40))))

    sellstate = ls < lb
    maxrun = _rolling_max(_barslastcount(sellstate).astype(np.float64), 45)
    sellpct = _rolling_sum_bool(sellstate, 45) * 100.0 / 45.0
    greenok = (maxrun <= float(params.get("maxrun_max", ycfg.get("maxrun_max", 8)))) & (sellpct <= float(params.get("sellpct_max", ycfg.get("sellpct_max", 60))))

    maxlen = _rolling_max(np.where(gsb, _barslast(gss), 0).astype(np.float64), 90)
    longb = _rolling_sum_bool(gsb & (_barslast(gss) > 10), 90)
    sellqual = (maxlen <= float(params.get("maxlen_max", ycfg.get("maxlen_max", 20)))) & (longb <= float(params.get("longb_max", ycfg.get("longb_max", 2))))

    m20 = _ma(close, 20)
    m60 = _ma(close, 60)
    m90 = _ma(close, 90)
    up = (
        (m20 > m60)
        & (m60 > m90)
        & (m60 > _ref(m60, 20))
        & (m90 > _ref(m90, 20))
        & (close > m90)
        & (close > m60 * float(params.get("m60_buffer", ycfg.get("m60_buffer", 0.97))))
    )
    ret = _safe_div(close, _rolling_max(high, 20), 1.0)
    pull = (ret <= float(params.get("ma_pull_high", ycfg.get("ma_pull_high", 0.995)))) & (ret >= float(params.get("ma_pull_low", ycfg.get("ma_pull_low", 0.78))))
    entry = insell & histok & sellqual & greenok & up & pull
    return {"entry": entry, "exit": gss, "indicators": {**core, "rate": rate, "sellpct": sellpct}}


def _load_formula_yaml(name: str) -> dict[str, Any]:
    import yaml
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / f"formula_{name}.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            return yaml.safe_load(f) or {}
    return {}


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
    ycfg = _load_formula_yaml("ma_base_breakout")
    ma_cfg = ycfg.get("ma", {})
    base_cfg = ycfg.get("base", {})
    brk_cfg = ycfg.get("breakout", {})
    price_cfg = ycfg.get("price", {})
    short = int(params.get("short_ma", ma_cfg.get("short", 5)))
    mid = int(params.get("mid_ma", ma_cfg.get("mid", 90)))
    long = int(params.get("long_ma", ma_cfg.get("long", 145)))
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
    rising = _rolling_sum_bool(ma_s > _ref(ma_s, 1), int(params.get("ma5_rising_count_window", base_cfg.get("ma5_rising_count_window", 10))))
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

    tj1 = (ls >= int(params.get("below_days_min", base_cfg.get("below_days_min", 45)))) & (ma_s < ma_m)
    tj2 = rising >= int(params.get("ma5_rising_min", base_cfg.get("ma5_rising_min", 7)))
    tj3 = (_rolling_sum_bool(cross_l, int(params.get("breakout_lookback", brk_cfg.get("breakout_lookback", 11)))) >= 1) & (b145 <= int(params.get("breakout_recent_days", brk_cfg.get("breakout_recent_days", 10)))) & (close > ma_l)
    tj4 = since_break_count_below == 0
    tj5 = _rolling_sum_bool(close > ma_l, 45) == (b145 + 1)
    tj6 = (close <= top * float(params.get("price_top_buffer", price_cfg.get("price_top_buffer", 1.06)))) & (close <= ma_l * float(params.get("price_long_ma_buffer", price_cfg.get("price_long_ma_buffer", 1.10))))
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
    ycfg = _load_formula_yaml("activity_breakout")
    bull_cfg = ycfg.get("bull_line", {})
    ret_cfg = ycfg.get("return_filter", {})
    sig_cfg = ycfg.get("signal", {})
    limit_cfg = ycfg.get("limit_adapt", {})
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
    x15 = np.maximum.reduce([x12, x3, x11, x10, x2, x7, x4]) * float(params.get("x15_multiplier", bull_cfg.get("x15_multiplier", 1.2)))
    base_bull = float(params.get("big_bull_line", params.get("大牛线", bull_cfg.get("big_bull_line", 6.0))))
    limit_pct = float(params.get("limit_up_pct", 0.0))
    if limit_pct > 0 and limit_cfg.get("enabled", False):
        base_limit = float(limit_cfg.get("base_limit_pct", 0.10))
        base_bull = base_bull * (limit_pct / base_limit) if base_limit > 0 else base_bull
    big = np.full(len(close), base_bull, dtype=np.float64)
    raw_entry = _cross(x15, big)
    prev_close = _ref(close, 1, close[0] if len(close) else 1.0)
    close_ret = _safe_div(close - prev_close, prev_close) * 100.0
    entry = raw_entry & (close_ret >= float(params.get("min_close_ret", ret_cfg.get("min_close_ret", -100.0)))) & (close_ret <= float(params.get("max_close_ret", ret_cfg.get("max_close_ret", 100.0))))
    entry = _apply_cooldown(entry, int(params.get("signal_cooldown_days", sig_cfg.get("signal_cooldown_days", 0))))
    base_strong = float(params.get("strong_line", bull_cfg.get("strong_line", 3.0)))
    if limit_pct > 0 and limit_cfg.get("enabled", False):
        base_limit = float(limit_cfg.get("base_limit_pct", 0.10))
        base_strong = base_strong * (limit_pct / base_limit) if base_limit > 0 else base_strong
    return {"entry": entry, "exit": x15 < base_strong, "indicators": {"x15": x15}}


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
    ycfg = _load_formula_yaml("volume_base_breakout")
    spk_cfg = ycfg.get("spike", {})
    bas_cfg = ycfg.get("base", {})
    dry_cfg = ycfg.get("dry", {})
    wrm_cfg = ycfg.get("warm", {})
    brk_cfg = ycfg.get("breakout", {})
    sig_cfg = ycfg.get("signal", {})
    n = len(close)
    spike_lookback = int(params.get("spike_lookback", spk_cfg.get("spike_lookback", 30)))
    spike_ratio = float(params.get("spike_ratio", spk_cfg.get("spike_ratio", 2.0)))
    amount_spike_ratio = float(params.get("amount_spike_ratio", spk_cfg.get("amount_spike_ratio", 2.0)))
    base_min_days = int(params.get("base_min_days", bas_cfg.get("base_min_days", 8)))
    base_max_days = int(params.get("base_max_days", bas_cfg.get("base_max_days", 90)))
    base_range_max = float(params.get("base_range_max", bas_cfg.get("base_range_max", 0.35)))
    base_floor = float(params.get("base_floor", bas_cfg.get("base_floor", 0.75)))
    base_ceiling = float(params.get("base_ceiling", bas_cfg.get("base_ceiling", 1.60)))
    dry_ratio = float(params.get("dry_ratio", dry_cfg.get("dry_ratio", 0.30)))
    warm_vol_ratio = float(params.get("warm_vol_ratio", wrm_cfg.get("warm_vol_ratio", 0.60)))
    warm_ret_min = float(params.get("warm_ret_min", wrm_cfg.get("warm_ret_min", 0.0)))
    warm_ret_max = float(params.get("warm_ret_max", wrm_cfg.get("warm_ret_max", 0.60)))
    breakout_window = int(params.get("breakout_window", brk_cfg.get("breakout_window", 20)))
    breakout_near_high = float(params.get("breakout_near_high", brk_cfg.get("breakout_near_high", 0.0)))
    breakout_max_extension = float(params.get("breakout_max_extension", brk_cfg.get("breakout_max_extension", 9.99)))
    dry_spike_ratio = float(params.get("dry_spike_ratio", dry_cfg.get("dry_spike_ratio", 0.55)))
    signal_cooldown_days = int(params.get("signal_cooldown_days", sig_cfg.get("signal_cooldown_days", 0)))
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


def pullback_doji_signals(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    amount: np.ndarray,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """回调十字星: 放量大涨→缩量回调→十字星→买入."""
    del amount
    params = params or {}
    import yaml
    from pathlib import Path
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / "formula_limit_up_pullback.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            ycfg = yaml.safe_load(f) or {}
    else:
        ycfg = {}

    from services.universe import get_limit_up_pct
    limit_pct = float(params.get("limit_up_pct", 0.10))
    breakout_ratio = float(params.get("breakout_limit_ratio",
                                       ycfg.get("breakout", {}).get("limit_ratio", 0.7)))

    det_params = {
        "breakout_pct_min": breakout_ratio * limit_pct * 100,
        "breakout_vol_ratio": float(params.get("breakout_vol_ratio",
                                                ycfg.get("breakout", {}).get("vol_ratio", 1.5))),
        "breakout_close_eq_high": bool(params.get("breakout_close_eq_high", False)),
        "pullback_min_days": int(params.get("pullback_min_days",
                                             ycfg.get("pullback", {}).get("min_days", 3))),
        "pullback_max_days": int(params.get("pullback_max_days",
                                             ycfg.get("pullback", {}).get("max_days", 5))),
        "pullback_vol_shrink": float(params.get("pullback_vol_shrink",
                                                 ycfg.get("pullback", {}).get("vol_shrink", 0.7))),
        "pullback_above_breakout_low": True,
        "doji_body_ratio_max": float(params.get("doji_body_ratio_max",
                                                  ycfg.get("doji", {}).get("body_ratio_max", 0.3))),
        "doji_range_min": float(params.get("doji_range_min",
                                            ycfg.get("doji", {}).get("range_min", 0.005))),
        "buy_offset": int(params.get("buy_offset", ycfg.get("entry", {}).get("buy_offset", 1))),
        "pre_pattern": bool(params.get("pre_pattern", False)),
    }

    n = len(close)
    dates = list(range(n))
    import importlib, sys
    from pathlib import Path
    scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from formula_limit_up_pullback import detect_signals
    sigs = detect_signals(dates, open_, high, low, close, volume, det_params, limit_up_pct=limit_pct)

    entry = np.zeros(n, dtype=bool)
    exit_arr = np.zeros(n, dtype=bool)
    for s in sigs:
        bi = s["buy_idx"]
        if 0 <= bi < n:
            entry[bi] = True

    cooldown = int(params.get("signal_cooldown_days", 0))
    if cooldown > 0:
        entry = _apply_cooldown(entry, cooldown)

    return {"entry": entry, "exit": exit_arr, "indicators": {"n_signals": len(sigs)}}


from derived_formulas import consolidation_breakout_signals, continuation_signals, pullback_doji_enhanced_signals

_OHLCV_FORMULAS = {
    "gs_raw_buy": lambda o, h, l, c, v, a, p: gs_raw_buy_signals(o, h, l, c, p),
    "gs_pullback_confirm": lambda o, h, l, c, v, a, p: gs_pullback_confirm_signals(o, h, l, c, v, a, p),
    "ma_base_breakout": lambda o, h, l, c, v, a, p: ma_base_breakout_signals(o, h, l, c, v, a, p),
    "activity_breakout": lambda o, h, l, c, v, a, p: activity_breakout_signals(o, h, l, c, v, a, p),
    "volume_base_breakout": lambda o, h, l, c, v, a, p: volume_base_breakout_signals(o, h, l, c, v, a, p),
    "pullback_doji": lambda o, h, l, c, v, a, p: pullback_doji_signals(o, h, l, c, v, a, p),
    "consolidation_breakout": lambda o, h, l, c, v, a, p: consolidation_breakout_signals(o, h, l, c, v, a, p),
    "continuation": lambda o, h, l, c, v, a, p: continuation_signals(o, h, l, c, v, a, p),
    "pullback_doji_enhanced": lambda o, h, l, c, v, a, p: pullback_doji_enhanced_signals(o, h, l, c, v, a, p),
}

_BANK_OHLCV_PARAMS = {"close", "high", "low", "volume", "open_", "open"}


_SMARTMONEY_PARAM_MAP = {
    "lhb_inst_seats": "lhb_inst_seats",
    "insider_buy_count": "insider_buy_count",
    "hsgt_net": "hsgt_net",
    "ex_dividend_flag": "ex_dividend_flag",
    "sector_ret": "sector_ret",
    "diffusion_score": "diffusion_score",
    "is_leader": "diffusion_score",
    "context_score": "context_score",
    "under_reaction_score": "under_reaction_score",
    "crowding_score": "context_score",
    "theme_score": "diffusion_score",
    "theme_member_since": "diffusion_score",
}

_smartmoney_cache: dict[str, dict[str, np.ndarray]] = {}


def _get_smartmoney_feature(param_name: str, stock_code: str, dates: np.ndarray) -> np.ndarray | None:
    adapter_key = _SMARTMONEY_PARAM_MAP.get(param_name)
    if adapter_key is None:
        return None
    cache_key = f"{stock_code}:{adapter_key}"
    if cache_key in _smartmoney_cache:
        return _smartmoney_cache[cache_key]
    try:
        import duckdb
        from pathlib import Path
        db_path = Path(__file__).resolve().parents[3] / "data" / "smartmoney.duckdb"
        if not db_path.exists():
            return None
        from smartmoney_adapter import SmartMoneyAdapter
        conn = duckdb.connect(str(db_path), read_only=True)
        adapter = SmartMoneyAdapter(conn)
        features = adapter.load_stock_features(stock_code, dates, required=[adapter_key])
        conn.close()
        result = features.get(adapter_key)
        if result is not None:
            _smartmoney_cache[cache_key] = result
        return result
    except Exception:
        return None


def _call_bank_formula(
    func,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    params: dict[str, Any],
    stock_code: str = "",
    dates: np.ndarray | None = None,
) -> dict[str, Any]:
    import inspect
    sig = inspect.signature(func)
    available = {"close": close, "high": high, "low": low, "volume": volume, "open_": open_, "open": open_}
    positional = []
    kwargs: dict[str, Any] = {}
    for name, p in sig.parameters.items():
        if name in available:
            positional.append(available[name])
        elif name == "params":
            kwargs["params"] = params
        elif p.kind == p.VAR_KEYWORD:
            kwargs.update(params)
        elif name in params:
            kwargs[name] = params[name]
        elif name not in _BANK_OHLCV_PARAMS and p.default is inspect.Parameter.empty:
            sm_data = _get_smartmoney_feature(name, stock_code, dates if dates is not None else np.arange(len(close)))
            if sm_data is not None and len(sm_data) == len(close):
                positional.append(sm_data)
            else:
                return {"entry": np.zeros(len(close), dtype=bool), "exit": np.zeros(len(close), dtype=bool), "indicators": {"skipped": f"missing required param: {name}"}}
    entry_arr, meta = func(*positional, **kwargs)
    return {"entry": entry_arr, "exit": np.zeros(len(close), dtype=bool), "indicators": meta}


def _get_bank_formulas() -> dict[str, Any]:
    try:
        from bank import ALL_FORMULAS
        return ALL_FORMULAS
    except ImportError:
        return {}


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
    stock_code: str = "",
    dates: np.ndarray | None = None,
) -> dict[str, Any]:
    params = params or {}
    ohlcv_fn = _OHLCV_FORMULAS.get(formula_id)
    if ohlcv_fn is not None:
        return ohlcv_fn(open_, high, low, close, volume, amount, params)
    bank = _get_bank_formulas()
    if formula_id in bank:
        return _call_bank_formula(bank[formula_id], open_, high, low, close, volume, params,
                                  stock_code=stock_code, dates=dates)
    raise ValueError(f"unknown formula_id: {formula_id}")
