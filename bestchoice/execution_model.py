"""Shared execution model for BestChoice backtests.

The model is intentionally conservative:
- signals are actionable from T+1 only;
- buy uses guarded same-day VWAP and is blocked by suspension/limit-up;
- sell uses guarded same-day VWAP and is delayed by suspension/limit-down;
- all dates/prices returned here are the single source for charts and metrics.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np


EXECUTION_MODEL_VERSION = "vwap_tradable_v1"
BP_TOLERANCE = 0.0001


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if np.isnan(f):
            return default
        return f
    except Exception:
        return default


def limit_pct_for_code(code: str) -> tuple[float, float]:
    code = str(code or "")
    if code.startswith(("60", "00")):
        return 0.10, -0.10
    if code.startswith(("30", "301", "688", "689")):
        return 0.20, -0.20
    if code.startswith(("4", "8", "9")):
        return 0.30, -0.30
    return 0.10, -0.10


def guarded_vwap(
    amount: float,
    volume: float,
    close: float,
    low: float | None = None,
    high: float | None = None,
) -> tuple[float, str]:
    """Return a guarded VWAP price and method tag.

    `amount / (volume * 100)` is preferred for A-share lot volume. Some
    sources store volume as shares, so `amount / volume` is also tested.
    """
    amount = _to_float(amount)
    volume = _to_float(volume)
    close = _to_float(close)
    low = _to_float(low, close)
    high = _to_float(high, close)
    if amount <= 0 or volume <= 0:
        return close, "close_fallback_zero_amount_volume"

    lot = amount / (volume * 100.0)
    raw = amount / volume
    candidates: list[tuple[float, str]] = []

    if close > 0:
        for price, method in ((lot, "vwap_lot"), (raw, "vwap_raw")):
            ratio = price / close
            if 0.5 <= ratio <= 1.5:
                candidates.append((price, method))

    if not candidates and low > 0 and high > 0:
        lo_b = low * 0.95
        hi_b = high * 1.05
        for price, method in ((lot, "vwap_lot_range"), (raw, "vwap_raw_range")):
            if lo_b <= price <= hi_b:
                candidates.append((price, method))

    if not candidates:
        return close, "close_fallback_vwap_guard"
    if len(candidates) == 1 or close <= 0:
        return candidates[0]
    return min(candidates, key=lambda x: abs(x[0] - close))


def is_suspended(volume: float, amount: float, close: float) -> bool:
    return _to_float(volume) <= 0 or _to_float(amount) <= 0 or _to_float(close) <= 0


def is_limit_up(close: float, prev_close: float, up_pct: float) -> bool:
    close = _to_float(close)
    prev_close = _to_float(prev_close)
    return prev_close > 0 and close >= prev_close * (1.0 + up_pct - BP_TOLERANCE)


def is_limit_down(close: float, prev_close: float, down_pct: float) -> bool:
    close = _to_float(close)
    prev_close = _to_float(prev_close)
    return prev_close > 0 and close <= prev_close * (1.0 + down_pct + BP_TOLERANCE)


def is_one_word(open_: float, high: float, low: float, close: float) -> bool:
    vals = [_to_float(open_), _to_float(high), _to_float(low), _to_float(close)]
    return all(v > 0 for v in vals) and vals[0] == vals[1] == vals[2] == vals[3]


def tradability_flags(
    code: str,
    i: int,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    amounts: np.ndarray,
) -> dict[str, Any]:
    prev_close = _to_float(closes[i - 1]) if i > 0 else 0.0
    open_ = _to_float(opens[i], _to_float(closes[i]))
    high = _to_float(highs[i], _to_float(closes[i]))
    low = _to_float(lows[i], _to_float(closes[i]))
    close = _to_float(closes[i])
    volume = _to_float(volumes[i])
    amount = _to_float(amounts[i])
    up_pct, down_pct = limit_pct_for_code(code)
    suspended = is_suspended(volume, amount, close)
    limit_up = (not suspended) and is_limit_up(close, prev_close, up_pct)
    limit_down = (not suspended) and is_limit_down(close, prev_close, down_pct)
    one_word = is_one_word(open_, high, low, close)
    return {
        "suspended": suspended,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "one_word_limit_up": one_word and limit_up,
        "one_word_limit_down": one_word and limit_down,
        "can_buy": (not suspended) and (not limit_up),
        "can_sell": (not suspended) and (not limit_down),
    }


def _find_buy_idx(
    code: str,
    planned_idx: int,
    n: int,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    amounts: np.ndarray,
    delay_days: int,
) -> tuple[int | None, str | None, int]:
    last_reason = None
    for i in range(planned_idx, min(n, planned_idx + delay_days + 1)):
        flags = tradability_flags(code, i, opens, highs, lows, closes, volumes, amounts)
        if flags["can_buy"]:
            return i, None, i - planned_idx
        if flags["suspended"]:
            last_reason = "buy_blocked_suspended"
        elif flags["limit_up"]:
            last_reason = "buy_blocked_limit_up"
        else:
            last_reason = "buy_blocked_untradable"
    return None, last_reason or "buy_blocked_no_bar", delay_days


def _find_sell_idx(
    code: str,
    planned_idx: int,
    n: int,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    amounts: np.ndarray,
) -> tuple[int | None, str | None, int]:
    last_reason = None
    for i in range(planned_idx, n):
        flags = tradability_flags(code, i, opens, highs, lows, closes, volumes, amounts)
        if flags["can_sell"]:
            return i, None, i - planned_idx
        if flags["suspended"]:
            last_reason = "sell_blocked_suspended"
        elif flags["limit_down"]:
            last_reason = "sell_blocked_limit_down"
        else:
            last_reason = "sell_blocked_untradable"
    return None, last_reason or "sell_blocked_no_bar", max(0, n - planned_idx)


def build_fixed_holding_trades(
    *,
    code: str,
    dates: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    amounts: np.ndarray,
    signal_indices: Iterable[int],
    holding_periods: Iterable[int],
    buy_delay_days: int = 3,
    include_open: bool = True,
) -> dict[int, list[dict[str, Any]]]:
    """Build fixed-holding trades for signal indices.

    Returned lists include blocked/open trades; metric callers should only use
    rows whose `ret` is not None.
    """
    n = len(closes)
    periods = sorted({int(h) for h in holding_periods if int(h) > 0})
    out: dict[int, list[dict[str, Any]]] = {h: [] for h in periods}

    for signal_i in signal_indices:
        signal_i = int(signal_i)
        planned_buy_i = signal_i + 1
        if planned_buy_i >= n:
            for h in periods:
                out[h].append(
                    {
                        "signal_idx": signal_i,
                        "signal_date": str(dates[signal_i]),
                        "planned_buy_date": None,
                        "actual_buy_date": None,
                        "buy_date": None,
                        "sell_date": None,
                        "pending_buy_idx": signal_i,
                        "holding_days": h,
                        "ret": None,
                        "max_dd": None,
                        "pending_buy": True,
                        "buy_block_reason": "waiting_next_bar",
                        "execution_model": EXECUTION_MODEL_VERSION,
                    }
                )
            continue

        buy_i, buy_block_reason, delay_buy_days = _find_buy_idx(
            code, planned_buy_i, n, opens, highs, lows, closes, volumes, amounts, buy_delay_days
        )
        if buy_i is None:
            for h in periods:
                out[h].append(
                    {
                        "signal_idx": signal_i,
                        "signal_date": str(dates[signal_i]),
                        "planned_buy_date": str(dates[planned_buy_i]),
                        "actual_buy_date": None,
                        "buy_date": None,
                        "sell_date": None,
                        "holding_days": h,
                        "ret": None,
                        "max_dd": None,
                        "skipped": True,
                        "buy_block_reason": buy_block_reason,
                        "delay_buy_days": delay_buy_days,
                        "execution_model": EXECUTION_MODEL_VERSION,
                    }
                )
            continue

        buy_price, buy_method = guarded_vwap(
            amounts[buy_i], volumes[buy_i], closes[buy_i], lows[buy_i], highs[buy_i]
        )
        if buy_price <= 0:
            continue

        for h in periods:
            planned_sell_i = buy_i + h
            if planned_sell_i >= n:
                if include_open:
                    latest_i = n - 1
                    latest_price, latest_method = guarded_vwap(
                        amounts[latest_i], volumes[latest_i], closes[latest_i], lows[latest_i], highs[latest_i]
                    )
                    low_slice = lows[buy_i : latest_i + 1]
                    max_dd = min(0.0, (_to_float(np.min(low_slice), buy_price) - buy_price) / buy_price) if len(low_slice) else 0.0
                    out[h].append(
                        {
                            "signal_idx": signal_i,
                            "buy_idx": buy_i,
                            "sell_idx": None,
                            "signal_date": str(dates[signal_i]),
                            "planned_buy_date": str(dates[planned_buy_i]),
                            "actual_buy_date": str(dates[buy_i]),
                            "buy_date": str(dates[buy_i]),
                            "buy_price": round(float(buy_price), 3),
                            "buy_price_method": buy_method,
                            "latest_date": str(dates[latest_i]),
                            "latest_price": round(float(latest_price), 3),
                            "latest_price_method": latest_method,
                            "sell_date": None,
                            "sell_price": None,
                            "ret": None,
                            "latest_ret": round(float((latest_price - buy_price) / buy_price), 4) if latest_price > 0 else None,
                            "max_dd": round(float(max_dd), 4),
                            "holding_days": h,
                            "holding_days_actual": int(latest_i - buy_i),
                            "remaining_days": int(planned_sell_i - latest_i),
                            "open": True,
                            "delay_buy_days": delay_buy_days,
                            "execution_model": EXECUTION_MODEL_VERSION,
                        }
                    )
                continue

            sell_i, sell_block_reason, delay_sell_days = _find_sell_idx(
                code, planned_sell_i, n, opens, highs, lows, closes, volumes, amounts
            )
            if sell_i is None:
                if include_open:
                    out[h].append(
                        {
                            "signal_idx": signal_i,
                            "buy_idx": buy_i,
                            "sell_idx": None,
                            "signal_date": str(dates[signal_i]),
                            "planned_buy_date": str(dates[planned_buy_i]),
                            "actual_buy_date": str(dates[buy_i]),
                            "buy_date": str(dates[buy_i]),
                            "buy_price": round(float(buy_price), 3),
                            "buy_price_method": buy_method,
                            "planned_sell_date": str(dates[planned_sell_i]),
                            "actual_sell_date": None,
                            "sell_date": None,
                            "sell_price": None,
                            "ret": None,
                            "max_dd": None,
                            "holding_days": h,
                            "open": True,
                            "sell_block_reason": sell_block_reason,
                            "delay_buy_days": delay_buy_days,
                            "delay_sell_days": delay_sell_days,
                            "execution_model": EXECUTION_MODEL_VERSION,
                        }
                    )
                continue

            sell_price, sell_method = guarded_vwap(
                amounts[sell_i], volumes[sell_i], closes[sell_i], lows[sell_i], highs[sell_i]
            )
            low_slice = lows[buy_i : sell_i + 1]
            max_dd = min(0.0, (_to_float(np.min(low_slice), buy_price) - buy_price) / buy_price) if len(low_slice) else 0.0
            ret = (sell_price - buy_price) / buy_price
            out[h].append(
                {
                    "signal_idx": signal_i,
                    "buy_idx": buy_i,
                    "sell_idx": sell_i,
                    "signal_date": str(dates[signal_i]),
                    "planned_buy_date": str(dates[planned_buy_i]),
                    "actual_buy_date": str(dates[buy_i]),
                    "buy_date": str(dates[buy_i]),
                    "buy_price": round(float(buy_price), 3),
                    "buy_price_method": buy_method,
                    "buy_block_reason": buy_block_reason,
                    "planned_sell_date": str(dates[planned_sell_i]),
                    "actual_sell_date": str(dates[sell_i]),
                    "sell_date": str(dates[sell_i]),
                    "sell_price": round(float(sell_price), 3),
                    "sell_price_method": sell_method,
                    "sell_block_reason": sell_block_reason,
                    "ret": round(float(ret), 4),
                    "max_dd": round(float(max_dd), 4),
                    "holding_days": h,
                    "holding_days_actual": int(sell_i - buy_i),
                    "delay_buy_days": delay_buy_days,
                    "delay_sell_days": delay_sell_days,
                    "execution_model": EXECUTION_MODEL_VERSION,
                }
            )

    return out


def build_sell_rule_trades(
    *,
    code: str,
    dates: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    amounts: np.ndarray,
    signal_indices: Iterable[int],
    sell_rule: str,
    exit_signals: np.ndarray | None = None,
    buy_delay_days: int = 3,
    include_open: bool = True,
) -> list[dict[str, Any]]:
    """Build trades for a single selected sell rule.

    Supported rules:
    - `fixed_N`: sell after N trading days.
    - `formula_exit_or_N`: sell on the first formula exit after buy, capped at N days.
    """
    rule = str(sell_rule or "").strip().lower()
    if rule.startswith("fixed_"):
        try:
            holding_days = int(rule.split("_", 1)[1])
        except Exception:
            holding_days = 0
        if holding_days <= 0:
            return []
        trades = build_fixed_holding_trades(
            code=code,
            dates=dates,
            opens=opens,
            highs=highs,
            lows=lows,
            closes=closes,
            volumes=volumes,
            amounts=amounts,
            signal_indices=signal_indices,
            holding_periods=[holding_days],
            buy_delay_days=buy_delay_days,
            include_open=include_open,
        ).get(holding_days, [])
        for trade in trades:
            trade["sell_rule"] = rule
        return trades

    if not rule.startswith("formula_exit_or_"):
        return []

    try:
        max_holding_days = int(rule.rsplit("_", 1)[1])
    except Exception:
        max_holding_days = 0
    if max_holding_days <= 0:
        return []

    n = len(closes)
    exits = np.asarray(exit_signals if exit_signals is not None else np.zeros(n, dtype=bool), dtype=bool)
    exit_idxs = np.flatnonzero(exits)
    trades: list[dict[str, Any]] = []

    for signal_i in signal_indices:
        signal_i = int(signal_i)
        planned_buy_i = signal_i + 1
        if planned_buy_i >= n:
            if include_open:
                trades.append(
                    {
                        "signal_idx": signal_i,
                        "signal_date": str(dates[signal_i]),
                        "planned_buy_date": None,
                        "actual_buy_date": None,
                        "buy_date": None,
                        "sell_date": None,
                        "pending_buy_idx": signal_i,
                        "holding_days": max_holding_days,
                        "sell_rule": rule,
                        "ret": None,
                        "max_dd": None,
                        "pending_buy": True,
                        "buy_block_reason": "waiting_next_bar",
                        "execution_model": EXECUTION_MODEL_VERSION,
                    }
                )
            continue

        buy_i, buy_block_reason, delay_buy_days = _find_buy_idx(
            code, planned_buy_i, n, opens, highs, lows, closes, volumes, amounts, buy_delay_days
        )
        if buy_i is None:
            trades.append(
                {
                    "signal_idx": signal_i,
                    "signal_date": str(dates[signal_i]),
                    "planned_buy_date": str(dates[planned_buy_i]),
                    "actual_buy_date": None,
                    "buy_date": None,
                    "sell_date": None,
                    "holding_days": max_holding_days,
                    "sell_rule": rule,
                    "ret": None,
                    "max_dd": None,
                    "skipped": True,
                    "buy_block_reason": buy_block_reason,
                    "delay_buy_days": delay_buy_days,
                    "execution_model": EXECUTION_MODEL_VERSION,
                }
            )
            continue

        buy_price, buy_method = guarded_vwap(
            amounts[buy_i], volumes[buy_i], closes[buy_i], lows[buy_i], highs[buy_i]
        )
        if buy_price <= 0:
            continue

        exit_pos = int(np.searchsorted(exit_idxs, buy_i + 1, side="left"))
        formula_sell_i = int(exit_idxs[exit_pos]) if exit_pos < len(exit_idxs) else None
        max_sell_i = buy_i + max_holding_days
        planned_sell_i = min(formula_sell_i, max_sell_i) if formula_sell_i is not None else max_sell_i
        if planned_sell_i <= buy_i:
            planned_sell_i = buy_i + 1

        if planned_sell_i >= n:
            if include_open:
                latest_i = n - 1
                latest_price, latest_method = guarded_vwap(
                    amounts[latest_i], volumes[latest_i], closes[latest_i], lows[latest_i], highs[latest_i]
                )
                low_slice = lows[buy_i : latest_i + 1]
                max_dd = min(0.0, (_to_float(np.min(low_slice), buy_price) - buy_price) / buy_price) if len(low_slice) else 0.0
                trades.append(
                    {
                        "signal_idx": signal_i,
                        "buy_idx": buy_i,
                        "sell_idx": None,
                        "signal_date": str(dates[signal_i]),
                        "planned_buy_date": str(dates[planned_buy_i]),
                        "actual_buy_date": str(dates[buy_i]),
                        "buy_date": str(dates[buy_i]),
                        "buy_price": round(float(buy_price), 3),
                        "buy_price_method": buy_method,
                        "latest_date": str(dates[latest_i]),
                        "latest_price": round(float(latest_price), 3),
                        "latest_price_method": latest_method,
                        "sell_date": None,
                        "sell_price": None,
                        "ret": None,
                        "latest_ret": round(float((latest_price - buy_price) / buy_price), 4) if latest_price > 0 else None,
                        "max_dd": round(float(max_dd), 4),
                        "holding_days": max_holding_days,
                        "holding_days_actual": int(latest_i - buy_i),
                        "remaining_days": int(planned_sell_i - latest_i),
                        "open": True,
                        "sell_rule": rule,
                        "delay_buy_days": delay_buy_days,
                        "execution_model": EXECUTION_MODEL_VERSION,
                    }
                )
            continue

        sell_i, sell_block_reason, delay_sell_days = _find_sell_idx(
            code, planned_sell_i, n, opens, highs, lows, closes, volumes, amounts
        )
        if sell_i is None:
            if include_open:
                trades.append(
                    {
                        "signal_idx": signal_i,
                        "buy_idx": buy_i,
                        "sell_idx": None,
                        "signal_date": str(dates[signal_i]),
                        "planned_buy_date": str(dates[planned_buy_i]),
                        "actual_buy_date": str(dates[buy_i]),
                        "buy_date": str(dates[buy_i]),
                        "buy_price": round(float(buy_price), 3),
                        "buy_price_method": buy_method,
                        "planned_sell_date": str(dates[planned_sell_i]),
                        "actual_sell_date": None,
                        "sell_date": None,
                        "sell_price": None,
                        "ret": None,
                        "max_dd": None,
                        "holding_days": max_holding_days,
                        "open": True,
                        "sell_rule": rule,
                        "sell_block_reason": sell_block_reason,
                        "delay_buy_days": delay_buy_days,
                        "delay_sell_days": delay_sell_days,
                        "execution_model": EXECUTION_MODEL_VERSION,
                    }
                )
            continue

        sell_price, sell_method = guarded_vwap(
            amounts[sell_i], volumes[sell_i], closes[sell_i], lows[sell_i], highs[sell_i]
        )
        low_slice = lows[buy_i : sell_i + 1]
        max_dd = min(0.0, (_to_float(np.min(low_slice), buy_price) - buy_price) / buy_price) if len(low_slice) else 0.0
        ret = (sell_price - buy_price) / buy_price
        trades.append(
            {
                "signal_idx": signal_i,
                "buy_idx": buy_i,
                "sell_idx": sell_i,
                "signal_date": str(dates[signal_i]),
                "planned_buy_date": str(dates[planned_buy_i]),
                "actual_buy_date": str(dates[buy_i]),
                "buy_date": str(dates[buy_i]),
                "buy_price": round(float(buy_price), 3),
                "buy_price_method": buy_method,
                "buy_block_reason": buy_block_reason,
                "planned_sell_date": str(dates[planned_sell_i]),
                "actual_sell_date": str(dates[sell_i]),
                "sell_date": str(dates[sell_i]),
                "sell_price": round(float(sell_price), 3),
                "sell_price_method": sell_method,
                "sell_block_reason": sell_block_reason,
                "ret": round(float(ret), 4),
                "max_dd": round(float(max_dd), 4),
                "holding_days": max_holding_days,
                "holding_days_actual": int(sell_i - buy_i),
                "sell_rule": rule,
                "exit_triggered": formula_sell_i is not None and formula_sell_i <= max_sell_i,
                "delay_buy_days": delay_buy_days,
                "delay_sell_days": delay_sell_days,
                "execution_model": EXECUTION_MODEL_VERSION,
            }
        )

    return trades
