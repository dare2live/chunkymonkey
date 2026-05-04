"""Event-driven follow-trade simulator.

The simulator accepts event records plus strategy parameters and returns
event-level performance metrics. It intentionally reports per-position drawdown
statistics, not portfolio-level compound drawdown.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from services.market_db import get_market_conn


def _normalize_date(d: Optional[str]) -> Optional[str]:
    if d is None:
        return None
    text = str(d).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] in {"-", "/"}:
        return text[:10].replace("/", "-")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def _records_from_payload(payload) -> list[dict]:
    if payload is None:
        return []
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            return [dict(row) for row in to_dict("records")]
        except TypeError:
            pass
    if isinstance(payload, dict):
        return [dict(payload)]
    try:
        iterator = iter(payload)
    except TypeError:
        return []
    rows = []
    for row in iterator:
        try:
            rows.append(dict(row))
        except Exception:
            continue
    return rows


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _normalize_price_rows(payload) -> list[dict]:
    rows = []
    for row in _records_from_payload(payload):
        date_value = _normalize_date(row.get("date"))
        if not date_value:
            continue
        rows.append({
            "date": date_value,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
        })
    rows.sort(key=lambda item: item["date"])
    return rows


def load_price_panel(
    stock_codes: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, list[dict]]:
    """Load daily qfq price rows grouped by stock code."""
    if not stock_codes:
        return {}
    conn = get_market_conn()
    try:
        placeholders = ",".join("?" for _ in stock_codes)
        rows = conn.execute(
            f"""
            SELECT code, date, open, high, low, close
            FROM price_kline
            WHERE freq='daily' AND adjust='qfq'
              AND code IN ({placeholders})
              AND date BETWEEN ? AND ?
            ORDER BY code, date
            """,
            (*stock_codes, start_date, end_date),
        ).fetchall()
    finally:
        conn.close()

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        code = str(item.pop("code"))
        item["date"] = _normalize_date(item.get("date"))
        if item["date"]:
            grouped[code].append(item)
    return {
        code: _normalize_price_rows(rows)
        for code, rows in grouped.items()
    }


def _simulate_one(
    entry_date: str,
    code_prices: list[dict],
    max_hold_days: int,
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> Optional[dict]:
    if not code_prices:
        return None
    dates = [row["date"] for row in code_prices]
    try:
        entry_pos = dates.index(entry_date)
    except ValueError:
        return None

    entry_price = _safe_float(code_prices[entry_pos].get("close"))
    if entry_price is None or entry_price <= 0:
        return None

    end_pos = min(entry_pos + max_hold_days, len(dates) - 1)
    if end_pos <= entry_pos:
        return None

    lowest_seen = entry_price
    for idx in range(entry_pos + 1, end_pos + 1):
        row = code_prices[idx]
        day_low = _safe_float(row.get("low"))
        day_high = _safe_float(row.get("high"))
        if day_low is None or day_high is None:
            continue
        lowest_seen = min(lowest_seen, day_low)

        sl_hit = stop_loss is not None and (day_low / entry_price - 1) <= stop_loss
        tp_hit = take_profit is not None and (day_high / entry_price - 1) >= take_profit
        intra = lowest_seen / entry_price - 1

        if sl_hit and tp_hit:
            return {
                "entry_date": entry_date,
                "exit_date": dates[idx],
                "entry_price": entry_price,
                "exit_price": entry_price * (1 + stop_loss),
                "pnl": stop_loss,
                "hold_days": idx - entry_pos,
                "intra_maxdd": float(intra),
                "exit_reason": "stop_loss_conservative",
            }
        if sl_hit:
            return {
                "entry_date": entry_date,
                "exit_date": dates[idx],
                "entry_price": entry_price,
                "exit_price": entry_price * (1 + stop_loss),
                "pnl": stop_loss,
                "hold_days": idx - entry_pos,
                "intra_maxdd": float(intra),
                "exit_reason": "stop_loss",
            }
        if tp_hit:
            return {
                "entry_date": entry_date,
                "exit_date": dates[idx],
                "entry_price": entry_price,
                "exit_price": entry_price * (1 + take_profit),
                "pnl": take_profit,
                "hold_days": idx - entry_pos,
                "intra_maxdd": float(intra),
                "exit_reason": "take_profit",
            }

    last_close = _safe_float(code_prices[end_pos].get("close"))
    if last_close is None or last_close <= 0:
        return None
    intra = lowest_seen / entry_price - 1
    return {
        "entry_date": entry_date,
        "exit_date": dates[end_pos],
        "entry_price": entry_price,
        "exit_price": last_close,
        "pnl": last_close / entry_price - 1,
        "hold_days": end_pos - entry_pos,
        "intra_maxdd": float(intra),
        "exit_reason": "max_hold",
    }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    weight = pos - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _normalize_events(events) -> list[dict]:
    rows = []
    for row in _records_from_payload(events):
        notice = _normalize_date(row.get("notice_date"))
        stock_code = str(row.get("stock_code") or "").strip()
        if not notice or not stock_code:
            continue
        item = dict(row)
        item["_notice_norm"] = notice
        item["stock_code"] = stock_code
        rows.append(item)
    return rows


def simulate_events(
    events,
    params: dict,
    prices_by_code: Optional[dict[str, list[dict]]] = None,
) -> dict:
    """Simulate follow-trades for event records.

    Event rows require institution_id, stock_code, and notice_date. Params:
    entry_lag, max_hold_days, stop_loss, and take_profit.
    """
    entry_lag = int(params.get("entry_lag", 0))
    max_hold = int(params.get("max_hold_days", 20))
    stop_loss = params.get("stop_loss")
    take_profit = params.get("take_profit")

    event_rows = _normalize_events(events)
    if prices_by_code is None:
        codes = sorted({row["stock_code"] for row in event_rows})
        if not codes:
            return {"n_events": 0, "n_filled": 0}
        start = min(row["_notice_norm"] for row in event_rows)
        latest_notice = max(row["_notice_norm"] for row in event_rows)
        end = (
            datetime.strptime(latest_notice, "%Y-%m-%d")
            + timedelta(days=int(max_hold) * 2 + 30)
        ).strftime("%Y-%m-%d")
        prices_by_code = load_price_panel(codes, start, end)

    positions: list[dict] = []
    for event in event_rows:
        code = event["stock_code"]
        notice = event["_notice_norm"]
        code_prices = _normalize_price_rows(prices_by_code.get(code) or [])
        if not code_prices:
            continue
        dates = [row["date"] for row in code_prices]
        target = bisect_left(dates, notice) + entry_lag
        if target >= len(dates):
            continue
        pos = _simulate_one(dates[target], code_prices, max_hold, stop_loss, take_profit)
        if pos is None:
            continue
        pos["institution_id"] = event.get("institution_id")
        pos["stock_code"] = code
        pos["notice_date"] = notice
        positions.append(pos)

    if not positions:
        return {"n_events": len(event_rows), "n_filled": 0}

    pnls = [float(row["pnl"]) for row in positions]
    hold_days = [float(row["hold_days"]) for row in positions]
    avg_hold = _mean(hold_days) or 1.0
    avg_pnl = _mean(pnls)

    annual_return = (1.0 + avg_pnl) ** (252.0 / max(avg_hold, 1.0)) - 1.0 if avg_pnl > -1 else -1.0
    pnl_std = _sample_std(pnls)
    sharpe = (avg_pnl / pnl_std) * math.sqrt(252.0 / max(avg_hold, 1.0)) if pnl_std > 0 else 0.0

    intra_dd = [float(row["intra_maxdd"]) for row in positions]
    exit_reason_counts = dict(Counter(row["exit_reason"] for row in positions))

    return {
        "n_events": int(len(event_rows)),
        "n_filled": int(len(positions)),
        "avg_pnl": avg_pnl,
        "avg_hold_days": avg_hold,
        "win_rate": _mean([1.0 if pnl > 0 else 0.0 for pnl in pnls]),
        "annual_return": float(annual_return),
        "sharpe": float(sharpe),
        "avg_position_maxdd": _mean(intra_dd),
        "p95_position_maxdd": _quantile(intra_dd, 0.05),
        "exit_reason_counts": exit_reason_counts,
        "positions": positions,
    }
