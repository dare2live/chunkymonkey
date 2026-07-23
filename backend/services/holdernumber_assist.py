"""Shareholder-count (stk_holdernumber) decision-assist — concentration slow variable.

Reads only via DataAccess entity ``holder_number`` (documented raw_evidence,
ann_date PIT). Optional price overlay via ``kline_qfq``. Fail-closed: missing /
invalid → typed empty|unavailable, never invent signals. Not Optuna / not a
trading signal — dossier / feature-assist only.
"""
from __future__ import annotations

from typing import Any


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _yyyymmdd(s: str | None) -> str | None:
    if not s:
        return None
    d = str(s).replace("-", "")
    return d if len(d) == 8 and d.isdigit() else None


def _concentration(prev_n: float | None, cur_n: float | None) -> dict[str, Any]:
    if prev_n is None or cur_n is None or prev_n <= 0:
        return {
            "direction": "unknown",
            "holder_num_chg": None,
            "holder_num_chg_pct": None,
            "periods_compared": 0,
        }
    chg = cur_n - prev_n
    pct = chg / prev_n
    if abs(pct) < 1e-6:
        direction = "flat"
    elif chg < 0:
        direction = "concentrating"  # fewer holders → more concentrated
    else:
        direction = "diluting"
    return {
        "direction": direction,
        "holder_num_chg": chg,
        "holder_num_chg_pct": pct,
        "periods_compared": 2,
    }


def _vs_price(
    code: str,
    start_ann: str | None,
    end_ann: str | None,
    *,
    da: Any,
) -> dict[str, Any]:
    """Price change over the same ann window — assist overlay, fail-closed."""
    start = _yyyymmdd(start_ann)
    end = _yyyymmdd(end_ann)
    if not start or not end or start > end:
        return {
            "status": "unavailable",
            "reason": "ann_window_invalid",
            "price_chg_pct": None,
            "note": "assist only; not a signal",
        }
    try:
        # ISO as_of for DataAccess PIT; start lower-bounds the window.
        start_iso = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
        end_iso = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
        res = da.get("kline_qfq", codes=[code], start=start_iso, as_of=end_iso)
    except Exception as exc:  # noqa: BLE001 — price overlay optional
        return {
            "status": "unavailable",
            "reason": f"kline_qfq_error:{type(exc).__name__}",
            "price_chg_pct": None,
            "note": "assist only; not a signal",
        }
    rows = list(res.rows or [])
    if len(rows) < 2:
        return {
            "status": "unavailable",
            "reason": "kline_qfq_insufficient",
            "price_chg_pct": None,
            "note": "assist only; not a signal",
        }
    c0 = _to_float(rows[0].get("close"))
    c1 = _to_float(rows[-1].get("close"))
    if c0 is None or c1 is None or c0 <= 0:
        return {
            "status": "unavailable",
            "reason": "kline_qfq_invalid_close",
            "price_chg_pct": None,
            "note": "assist only; not a signal",
        }
    return {
        "status": "ok",
        "reason": None,
        "price_chg_pct": (c1 - c0) / c0,
        "start_date": rows[0].get("date"),
        "end_date": rows[-1].get("date"),
        "note": "assist only; not a signal",
    }


def load_holdernumber_assist(
    code: str,
    as_of: str | None = None,
    *,
    lookback_periods: int = 8,
    da: Any | None = None,
) -> dict[str, Any]:
    """Load concentration series + optional vs-price assist for one 沪深A code.

    ``as_of``: YYYYMMDD or ISO decision day (PIT on ann_date). None → DataAccess
    default (latest closed trade date).
    """
    from services.data_access import DataAccess

    code = str(code or "").strip()
    if len(code) != 6 or not code.isdigit():
        return {
            "status": "empty",
            "reason": "invalid_code",
            "latest": None,
            "series": [],
            "concentration": _concentration(None, None),
            "vs_price": {
                "status": "unavailable",
                "reason": "invalid_code",
                "price_chg_pct": None,
                "note": "assist only; not a signal",
            },
            "provenance": None,
        }

    access = da or DataAccess()
    as_of_arg = None
    if as_of:
        d = _yyyymmdd(as_of)
        if d is None:
            return {
                "status": "empty",
                "reason": "invalid_as_of",
                "latest": None,
                "series": [],
                "concentration": _concentration(None, None),
                "vs_price": {
                    "status": "unavailable",
                    "reason": "invalid_as_of",
                    "price_chg_pct": None,
                    "note": "assist only; not a signal",
                },
                "provenance": None,
            }
        as_of_arg = f"{d[:4]}-{d[4:6]}-{d[6:8]}"

    try:
        res = access.get("holder_number", codes=[code], as_of=as_of_arg)
    except Exception as exc:  # noqa: BLE001 — fail-closed typed empty
        return {
            "status": "empty",
            "reason": f"holder_number_error:{type(exc).__name__}",
            "latest": None,
            "series": [],
            "concentration": _concentration(None, None),
            "vs_price": {
                "status": "unavailable",
                "reason": f"holder_number_error:{type(exc).__name__}",
                "price_chg_pct": None,
                "note": "assist only; not a signal",
            },
            "provenance": None,
        }

    # One row per end_date: keep latest ann_date revision within PIT window.
    by_end: dict[str, dict[str, Any]] = {}
    for r in res.rows or []:
        end = _yyyymmdd(r.get("end_date")) or str(r.get("end_date") or "")
        ann = _yyyymmdd(r.get("ann_date")) or str(r.get("ann_date") or "")
        n = _to_float(r.get("holder_num"))
        if not end or n is None:
            continue
        prev = by_end.get(end)
        if prev is None or ann >= str(prev.get("ann_date") or ""):
            by_end[end] = {
                "ann_date": ann,
                "end_date": end,
                "holder_num": n,
            }
    series = sorted(by_end.values(), key=lambda x: x["end_date"])
    if lookback_periods > 0:
        series = series[-int(lookback_periods) :]
    if not series:
        return {
            "status": "empty",
            "reason": "holder_number_empty",
            "latest": None,
            "series": [],
            "concentration": _concentration(None, None),
            "vs_price": {
                "status": "unavailable",
                "reason": "holder_number_empty",
                "price_chg_pct": None,
                "note": "assist only; not a signal",
            },
            "provenance": res.provenance,
        }

    latest = series[-1]
    prev = series[-2] if len(series) >= 2 else None
    conc = _concentration(
        prev["holder_num"] if prev else None,
        latest["holder_num"],
    )
    vs = _vs_price(
        code,
        series[0]["ann_date"] if len(series) >= 2 else latest["ann_date"],
        latest["ann_date"],
        da=access,
    )
    return {
        "status": "ok",
        "reason": None,
        "latest": latest,
        "series": series,
        "concentration": conc,
        "vs_price": vs,
        "provenance": res.provenance,
    }
