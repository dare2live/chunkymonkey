"""Kelly sizing for MSAF Scheme 6 Sniper trades.

Sizing uses only historical confluence-triggered outcomes dated before the
current signal date.  Fractions are half-Kelly and capped per trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any, Iterable, Mapping

from services.strategies.sniper.confluence import evaluate_confluence


DEFAULT_KELLY_CAP = 0.30
DEFAULT_LOOKBACK_DAYS = 252


@dataclass(frozen=True)
class KellyEstimate:
    win_rate: float
    avg_win: float
    avg_loss: float
    win_loss_ratio: float
    expectancy: float
    kelly_fraction: float
    n_trades: int


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _date_of(row: Mapping[str, Any]) -> date | None:
    for key in ("exit_date", "close_date", "entry_date", "signal_date", "date", "trade_date"):
        if key in row:
            parsed = _parse_date(row.get(key))
            if parsed is not None:
                return parsed
    return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(out):
        return None
    return out


def _pnl_pct(row: Mapping[str, Any]) -> float | None:
    for key in ("pnl_pct", "return", "ret", "trade_return", "forward_ret_20d", "forward_ret_10d"):
        if key in row:
            return _as_float(row.get(key))
    pnl = _as_float(row.get("pnl"))
    cost = _as_float(row.get("buy_cost"))
    if pnl is not None and cost and cost > 0:
        return pnl / cost
    return None


def kelly_fraction_from_win_loss(
    win_rate: float,
    win_loss_ratio: float,
    *,
    half_kelly: bool = True,
    cap: float = DEFAULT_KELLY_CAP,
) -> float:
    """Binary-outcome Kelly fraction, clipped to ``[0, cap]``.

    ``win_loss_ratio`` is average winning return divided by absolute average
    losing return.  Full Kelly is ``p - (1-p)/b``; production sizing uses
    half-Kelly and a hard 30% default cap.
    """
    p = _as_float(win_rate)
    b = _as_float(win_loss_ratio)
    cap_value = max(0.0, _as_float(cap) or 0.0)
    if p is None or b is None or b <= 0:
        return 0.0
    p = max(0.0, min(1.0, p))
    full = p - (1.0 - p) / b
    if half_kelly:
        full *= 0.5
    return max(0.0, min(cap_value, full))


def kelly_fraction_from_ev(
    ev: float,
    *,
    win_loss_ratio: float = 1.0,
    half_kelly: bool = True,
    cap: float = DEFAULT_KELLY_CAP,
) -> float:
    """Convert EV in loss units to a capped Kelly fraction.

    EV is interpreted as ``p * b - (1-p)`` where ``b`` is win/loss ratio.
    """
    ev_value = _as_float(ev)
    b = _as_float(win_loss_ratio)
    if ev_value is None or b is None or b <= 0:
        return 0.0
    inferred_p = (ev_value + 1.0) / (b + 1.0)
    return kelly_fraction_from_win_loss(
        inferred_p, b, half_kelly=half_kelly, cap=cap,
    )


def normalize_kelly_weights(
    fractions: Mapping[str, float],
    *,
    max_total_weight: float = 1.0,
) -> dict[str, float]:
    """Scale simultaneous trade weights so the portfolio sum never exceeds 1."""
    cap_total = max(0.0, _as_float(max_total_weight) or 0.0)
    clean = {
        key: max(0.0, value)
        for key, raw in fractions.items()
        if (value := (_as_float(raw) or 0.0)) > 0
    }
    total = sum(clean.values())
    if total <= 0:
        return {key: 0.0 for key in fractions}
    scale = min(1.0, cap_total / total)
    out = {key: clean.get(key, 0.0) * scale for key in fractions}
    drift = sum(out.values()) - cap_total
    if drift > 1e-12:
        last = next(reversed(out))
        out[last] = max(0.0, out[last] - drift)
    return out


def _pit_window(
    signal_date: str | date | datetime,
    rows: Iterable[Mapping[str, Any]],
    *,
    lookback_days: int,
) -> list[Mapping[str, Any]]:
    signal = _parse_date(signal_date)
    if signal is None:
        return list(rows)[-lookback_days:]
    floor = signal - timedelta(days=max(lookback_days * 2, 370))
    dated: list[tuple[date, Mapping[str, Any]]] = []
    for row in rows:
        row_date = _date_of(row)
        if row_date is not None and floor <= row_date < signal:
            dated.append((row_date, row))
    if dated:
        selected_dates = set(sorted({d for d, _ in dated})[-lookback_days:])
        return [row for row_date, row in dated if row_date in selected_dates]
    return []


def estimate_ev_from_trades(
    signal_date: str | date | datetime,
    trade_history: Iterable[Mapping[str, Any]],
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_trades: int = 5,
    cap: float = DEFAULT_KELLY_CAP,
) -> KellyEstimate:
    """Estimate EV from historical confluence-triggered trade outcomes.

    Rows with ``confluence_triggered=False`` are ignored.  Date filtering is
    strict: only rows dated before ``signal_date`` enter the estimate.
    """
    rows = _pit_window(signal_date, trade_history, lookback_days=lookback_days)
    returns: list[float] = []
    for row in rows:
        if row.get("confluence_triggered") is False:
            continue
        ret = _pnl_pct(row)
        if ret is not None:
            returns.append(ret)
    return estimate_ev_from_returns(returns, min_trades=min_trades, cap=cap)


def estimate_ev_from_returns(
    returns: Iterable[float],
    *,
    min_trades: int = 5,
    cap: float = DEFAULT_KELLY_CAP,
) -> KellyEstimate:
    clean = [r for raw in returns if (r := _as_float(raw)) is not None]
    if len(clean) < min_trades:
        return KellyEstimate(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, len(clean))
    wins = [r for r in clean if r > 0]
    losses = [-r for r in clean if r < 0]
    if not wins or not losses:
        win_rate = len(wins) / len(clean)
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
        return KellyEstimate(
            win_rate,
            avg_win,
            avg_loss,
            ratio,
            sum(clean) / len(clean),
            kelly_fraction_from_win_loss(win_rate, ratio, cap=cap),
            len(clean),
        )
    win_rate = len(wins) / len(clean)
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    ratio = avg_win / avg_loss
    expectancy = win_rate * avg_win - (1.0 - win_rate) * avg_loss
    fraction = kelly_fraction_from_win_loss(win_rate, ratio, cap=cap)
    return KellyEstimate(
        win_rate,
        avg_win,
        avg_loss,
        ratio,
        expectancy,
        fraction,
        len(clean),
    )


def estimate_ev_from_confluence_history(
    signal_date: str | date | datetime,
    feature_history: Iterable[Mapping[str, Any]],
    *,
    confluence_threshold: int = 5,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_trades: int = 5,
    cap: float = DEFAULT_KELLY_CAP,
) -> KellyEstimate:
    """PIT-safe EV estimate by replaying historical confluence verdicts.

    Each candidate historical row is scored with only rows before its own date,
    then its realized forward return is included if the historical verdict
    triggered.
    """
    rows = list(feature_history)
    window = _pit_window(signal_date, rows, lookback_days=lookback_days)
    returns: list[float] = []
    for row in window:
        row_date = _date_of(row)
        if row_date is None:
            continue
        verdict = evaluate_confluence(
            row_date,
            row,
            history=rows,
            threshold=confluence_threshold,
            lookback_days=lookback_days,
        )
        if verdict.triggered:
            ret = _pnl_pct(row)
            if ret is not None:
                returns.append(ret)
    return estimate_ev_from_returns(returns, min_trades=min_trades, cap=cap)

