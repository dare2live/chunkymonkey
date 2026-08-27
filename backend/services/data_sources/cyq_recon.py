"""Read-only chip recon. Does not cut primaries or feed formulas.

Rulers:
- Bars = ``canonical_nominal_ohlcv_daily`` (unadjusted). qfq kline and
  stopped ``raw_tushare_daily`` are not inputs.
- Turnover = ``raw_tushare_daily_basic.turnover_rate_f`` (percent → fraction).
  That is a vendor derived field, labeled as such — not observed holdings.
- Challenger = ``raw_tushare_cyq_perf`` (DataAccess L0 declared, not accepted
  publication). C0 20260612 already FAIL vs local qfq CYQ; this overlay is
  nominal, so a matching number is still not identity.
- Known-empty vendor days are empty recon, not 0=0. No formula winner_rate.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

CANONICAL_K = "canonical_nominal_ohlcv_daily"
DAILY_BASIC = "raw_tushare_daily_basic"
CYQ_LANDING = "raw_tushare_cyq_perf"
METHOD_ID = "turnover_overlay_v1"
COORDINATE = "nominal_unadjusted"
VENDOR_COORDINATE = "vendor_qfq_or_unknown"
KNOWN_EMPTY_DAYS = frozenset({"20260615"})
BANNED_K_INPUT = frozenset(
    {
        "raw_tushare_daily",
        "price_kline_qfq_tushare",
        "v_price_kline_qfq",
    }
)
_TABLE_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHANGHAI = timezone(timedelta(hours=8))
_QFQ_NEEDLE = re.compile(r"qfq", re.IGNORECASE)


def _table_leaf(table: str) -> str:
    return str(table).split(".")[-1].strip('"')


def sql_table(table: str) -> str:
    parts = [p.strip('"') for p in str(table).split(".") if p.strip('"')]
    if not parts or any(not _TABLE_PART.fullmatch(p) for p in parts):
        raise ValueError(f"bad table identifier: {table!r}")
    return ".".join(f'"{p}"' for p in parts)


def compact_yyyymmdd(value: Any) -> str | None:
    if isinstance(value, datetime):
        value = value.astimezone(_SHANGHAI).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    if isinstance(value, (int, float)):
        n = int(value)
        if 19_000_101 <= n <= 21_123_131:
            value = str(n)
        else:
            return None
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[:8]


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def reject_qfq_bars(table: str) -> str:
    name = _table_leaf(table)
    if name in BANNED_K_INPUT or _QFQ_NEEDLE.search(name):
        raise ValueError(f"banned qfq/raw-daily bars as chip input: {table!r}")
    return table


def reject_cyq_as_accepted(table: str) -> str:
    name = _table_leaf(table)
    if name == CYQ_LANDING:
        raise ValueError("raw_tushare_cyq_perf is not accepted publication")
    return table


def is_known_empty_day(day: Any) -> bool:
    compact = compact_yyyymmdd(day)
    return compact in KNOWN_EMPTY_DAYS if compact else False


def cyq_publication_status() -> dict[str, Any]:
    return {
        "status": "serve_l0_declared",
        "baseline": None,
        "landing": CYQ_LANDING,
        "accepted": False,
        "formula_winner_rate": False,
        "primary_cut": False,
        "reason": (
            "raw_tushare_cyq_perf is DataAccess L0 on raw, not accepted "
            "publication. C0 20260612 FAIL vs local qfq CYQ; this overlay "
            "uses nominal bars so a numeric match is still not identity."
        ),
    }


def _percentile(mass: Mapping[float, float], q: float) -> float | None:
    total = sum(mass.values())
    if total <= 0:
        return None
    acc = 0.0
    last = None
    for price, weight in sorted(mass.items()):
        acc += weight
        last = price
        if acc / total >= q:
            return price
    return last


def overlay_chips(bars: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Turnover-decay histogram on unadjusted close. Not observed holdings."""
    mass: dict[float, float] = {}
    out: list[dict[str, Any]] = []
    for bar in bars:
        close = _as_float(bar.get("close"))
        day = compact_yyyymmdd(bar.get("trade_date"))
        t_pct = _as_float(bar.get("turnover_rate_f"))
        if close is None or day is None:
            continue
        close = round(close, 2)
        if t_pct is None:
            out.append(
                {
                    "trade_date": day,
                    "ts_code": bar.get("ts_code"),
                    "close": close,
                    "status": "skip_missing_turnover",
                    "method": METHOD_ID,
                    "coordinate": COORDINATE,
                }
            )
            continue
        turnover = min(max(t_pct / 100.0, 0.0), 1.0)
        if not mass:
            mass[close] = 1.0
        else:
            for price in list(mass):
                mass[price] *= 1.0 - turnover
                if mass[price] < 1e-15:
                    del mass[price]
            mass[close] = mass.get(close, 0.0) + turnover
        total = sum(mass.values())
        if total <= 0:
            continue
        winner = 100.0 * sum(w for p, w in mass.items() if p < close) / total
        out.append(
            {
                "trade_date": day,
                "ts_code": bar.get("ts_code"),
                "close": close,
                "turnover_f": turnover,
                "winner_rate": winner,
                "cost_5pct": _percentile(mass, 0.05),
                "cost_50pct": _percentile(mass, 0.50),
                "cost_95pct": _percentile(mass, 0.95),
                "weight_avg": sum(p * w for p, w in mass.items()) / total,
                "method": METHOD_ID,
                "coordinate": COORDINATE,
                "available_at": "t+1",
                "status": "ok",
            }
        )
    return out


def compare_chip_day(
    model: Mapping[str, Any] | None,
    vendor: Mapping[str, Any] | None,
) -> dict[str, Any]:
    model_ok = isinstance(model, Mapping) and model.get("status") == "ok"
    left = _as_float((model or {}).get("cost_50pct")) if model_ok else None
    right = _as_float((vendor or {}).get("cost_50pct")) if vendor else None
    if left is None or right is None:
        return {
            "status": "empty_recon",
            "identity": False,
            "jaccard": None,
            "relation": "nominal_overlay_is_not_vendor_cyq_perf",
            "coordinate_left": COORDINATE,
            "coordinate_right": VENDOR_COORDINATE,
        }
    winner_l = _as_float(model.get("winner_rate")) if model_ok else None
    winner_r = _as_float(vendor.get("winner_rate")) if vendor else None
    return {
        "status": "compared",
        "identity": False,
        "relation": "nominal_overlay_is_not_vendor_cyq_perf",
        "coordinate_left": COORDINATE,
        "coordinate_right": VENDOR_COORDINATE,
        "method": METHOD_ID,
        "cost_50pct": {
            "left": left,
            "right": right,
            "abs_diff": abs(left - right),
            "numeric_near": abs(left - right) <= 0.05,
        },
        "winner_rate": {
            "left": winner_l,
            "right": winner_r,
            "abs_diff": (
                abs(winner_l - winner_r)
                if winner_l is not None and winner_r is not None
                else None
            ),
        },
        "formula_winner_rate": False,
    }


def compare_chip_sample(
    model_rows: Sequence[Mapping[str, Any]],
    vendor_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    models = {
        compact_yyyymmdd(r.get("trade_date")): r
        for r in model_rows
        if compact_yyyymmdd(r.get("trade_date"))
    }
    vendors = {
        compact_yyyymmdd(r.get("trade_date")): r
        for r in vendor_rows
        if compact_yyyymmdd(r.get("trade_date"))
    }
    days = sorted(set(models) | set(vendors))
    if not days:
        return {
            "status": "empty_recon",
            "identity": False,
            "periods": 0,
            "relation": "nominal_overlay_is_not_vendor_cyq_perf",
            "formula_winner_rate": False,
        }
    per_day = []
    near = 0
    compared = 0
    for day in days:
        body = compare_chip_day(models.get(day), vendors.get(day))
        body["trade_date"] = day
        if is_known_empty_day(day) and day not in vendors:
            body["status"] = "known_empty_day"
            body["identity"] = False
        per_day.append(body)
        if body.get("status") == "compared":
            compared += 1
            if (body.get("cost_50pct") or {}).get("numeric_near"):
                near += 1
    return {
        "status": "compared" if compared else "empty_recon",
        "identity": False,
        "periods": len(days),
        "compared": compared,
        "cost_50_numeric_near": near,
        "relation": "nominal_overlay_is_not_vendor_cyq_perf",
        "formula_winner_rate": False,
        "primary_cut": False,
        "per_day": per_day,
    }


def latest_canonical_k_day(con: Any, table: str = CANONICAL_K) -> str | None:
    reject_qfq_bars(table)
    row = con.execute(
        f"SELECT max(trade_date) FROM {sql_table(table)}"
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return compact_yyyymmdd(row[0])


def load_nominal_bars(
    con: Any,
    ts_code: str,
    *,
    start: str,
    end: str,
    table: str = CANONICAL_K,
) -> list[dict[str, Any]]:
    reject_qfq_bars(table)
    rows = con.execute(
        f"""
        SELECT trade_date, ts_code, close, vol
        FROM {sql_table(table)}
        WHERE ts_code = ?
        """,
        [ts_code],
    ).fetchall()
    out = []
    for row in rows:
        day = compact_yyyymmdd(row[0])
        if day is None or day < start or day > end:
            continue
        out.append(
            {
                "trade_date": day,
                "ts_code": row[1],
                "close": _as_float(row[2]),
                "vol": _as_float(row[3]),
            }
        )
    out.sort(key=lambda r: r["trade_date"])
    return out


def load_daily_basic(
    con: Any,
    ts_code: str,
    *,
    start: str,
    end: str,
    table: str = DAILY_BASIC,
) -> list[dict[str, Any]]:
    name = _table_leaf(table)
    if name != DAILY_BASIC:
        raise ValueError(f"daily_basic input must be {DAILY_BASIC}, got {table!r}")
    rows = con.execute(
        f"""
        SELECT trade_date, ts_code, float_share, turnover_rate, turnover_rate_f
        FROM {sql_table(table)}
        WHERE ts_code = ?
        """,
        [ts_code],
    ).fetchall()
    out = []
    for row in rows:
        day = compact_yyyymmdd(row[0])
        if day is None or day < start or day > end:
            continue
        out.append(
            {
                "trade_date": day,
                "ts_code": row[1],
                "float_share": _as_float(row[2]),
                "turnover_rate": _as_float(row[3]),
                "turnover_rate_f": _as_float(row[4]),
            }
        )
    out.sort(key=lambda r: r["trade_date"])
    return out


def load_cyq_perf(
    con: Any,
    ts_code: str,
    *,
    start: str,
    end: str,
    table: str = CYQ_LANDING,
) -> list[dict[str, Any]]:
    name = _table_leaf(table)
    if name != CYQ_LANDING:
        raise ValueError(f"cyq challenger must be {CYQ_LANDING}, got {table!r}")
    rows = con.execute(
        f"""
        SELECT trade_date, ts_code, winner_rate, cost_5pct, cost_50pct,
               cost_95pct, weight_avg
        FROM {sql_table(table)}
        WHERE ts_code = ?
        """,
        [ts_code],
    ).fetchall()
    out = []
    for row in rows:
        day = compact_yyyymmdd(row[0])
        if day is None or day < start or day > end:
            continue
        if is_known_empty_day(day):
            continue
        out.append(
            {
                "trade_date": day,
                "ts_code": row[1],
                "winner_rate": _as_float(row[2]),
                "cost_5pct": _as_float(row[3]),
                "cost_50pct": _as_float(row[4]),
                "cost_95pct": _as_float(row[5]),
                "weight_avg": _as_float(row[6]),
            }
        )
    out.sort(key=lambda r: r["trade_date"])
    return out


def join_bars_and_basic(
    bars: Sequence[Mapping[str, Any]],
    basic: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_day = {r["trade_date"]: r for r in basic}
    out = []
    for bar in bars:
        day = bar["trade_date"]
        extra = by_day.get(day) or {}
        item = dict(bar)
        item["float_share"] = extra.get("float_share")
        item["turnover_rate"] = extra.get("turnover_rate")
        item["turnover_rate_f"] = extra.get("turnover_rate_f")
        out.append(item)
    return out
