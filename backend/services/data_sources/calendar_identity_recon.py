"""Read-only calendar / ST / identity recon vs accepted tables.

Baseline is accepted ``canonical_sse_trading_calendar_generation`` (open days)
and ``canonical_stock_st_daily``. ``raw_tushare_trade_cal`` /
``raw_tushare_stock_st`` are compatibility fills, not recon truth.
``raw_tushare_suspend_d`` has no accepted publication (S7 blocked); it is
rejected as a suspend baseline. This module does not change primaries.

Fuyao REST calendar is a trailing ~1 year of open days (not a closed-day
calendar). ST from ticker *names* is a current snapshot, not daily history.
TDX ``get_security_list`` does not support BJ.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

ACCEPTED_CAL_TABLE = "canonical_sse_trading_calendar_generation"
ACCEPTED_ST_TABLE = "canonical_stock_st_daily"
BANNED_CAL_BASELINE = frozenset({"raw_tushare_trade_cal"})
BANNED_ST_BASELINE = frozenset({"raw_tushare_stock_st"})
BANNED_SUSPEND_BASELINE = frozenset({"raw_tushare_suspend_d"})
SAMPLE_LIMIT = 20
_ST_NAME_RE = re.compile(r"^(?:S)?\*ST|^ST", re.IGNORECASE)


def _table_name(table: str) -> str:
    return str(table).split(".")[-1].strip('"')


def reject_banned_baseline(table: str, *, banned: frozenset[str], accepted: str) -> str:
    name = _table_name(table)
    if name in banned:
        raise ValueError(
            f"banned baseline {table!r}; use {accepted} "
            f"(legacy_raw_plane: {name} is not recon truth)"
        )
    return table


_SHANGHAI = timezone(timedelta(hours=8))


def compact_yyyymmdd(value: Any) -> str | None:
    if isinstance(value, datetime):
        value = value.astimezone(_SHANGHAI).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    if isinstance(value, (int, float)):
        n = int(value)
        if n >= 10_000_000_000:  # epoch ms
            return datetime.fromtimestamp(n / 1000.0, tz=_SHANGHAI).date().strftime("%Y%m%d")
        if 19_000_101 <= n <= 21_123_131:
            value = str(n)
        else:
            return None
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 8:
        return None
    return digits[:8]


def as_date(value: Any) -> date | None:
    compact = compact_yyyymmdd(value)
    if compact is None:
        return None
    return date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))


def name_flags_st(name: str) -> bool:
    """Current-name ST marker. Not the accepted type/type_name membership."""
    text = str(name or "").strip().replace(" ", "")
    return bool(_ST_NAME_RE.match(text))


def set_diff(
    source: Iterable[Any],
    accepted: Iterable[Any],
    *,
    sample_limit: int = SAMPLE_LIMIT,
) -> dict[str, Any]:
    src = {x for x in source if x is not None}
    acc = {x for x in accepted if x is not None}
    only_source = sorted(src - acc)
    only_accepted = sorted(acc - src)
    both = src & acc
    return {
        "source_n": len(src),
        "accepted_n": len(acc),
        "intersection": len(both),
        "only_source": len(only_source),
        "only_accepted": len(only_accepted),
        "only_source_sample": [str(x) for x in only_source[:sample_limit]],
        "only_accepted_sample": [str(x) for x in only_accepted[:sample_limit]],
    }


def compare_open_days(
    source_days: Iterable[Any],
    accepted_days: Iterable[Any],
) -> dict[str, Any]:
    src = {d for d in (as_date(x) for x in source_days) if d is not None}
    acc = {d for d in (as_date(x) for x in accepted_days) if d is not None}
    if not src or not acc:
        window = None
        src_w, acc_w = src, acc
    else:
        lo = max(min(src), min(acc))
        hi = min(max(src), max(acc))
        window = {"start": lo.isoformat(), "end": hi.isoformat()}
        src_w = {d for d in src if lo <= d <= hi}
        acc_w = {d for d in acc if lo <= d <= hi}
    out = set_diff(src_w, acc_w)
    out["window"] = window
    out["source_span"] = (
        {"start": min(src).isoformat(), "end": max(src).isoformat()} if src else None
    )
    out["accepted_span"] = (
        {"start": min(acc).isoformat(), "end": max(acc).isoformat()} if acc else None
    )
    out["note"] = (
        "Fuyao trading-days is open days only, trailing ~1y; compare overlap window"
    )
    return out


def compare_st_names(
    source_rows: Sequence[Mapping[str, Any]],
    accepted_codes: Iterable[str],
) -> dict[str, Any]:
    flagged = []
    for row in source_rows:
        code = str(row.get("ts_code") or row.get("thscode") or "").strip().upper()
        name = str(row.get("name") or "")
        if code and name_flags_st(name):
            flagged.append(code)
    acc = {str(c).strip().upper() for c in accepted_codes if c}
    out = set_diff(flagged, acc)
    out["note"] = (
        "name-prefix ST is a live snapshot, not accepted type/type_name history; "
        "Fuyao limit-up is_st is not a universe ST membership"
    )
    return out


def suspend_recon_status(*, baseline: str | None = None) -> dict[str, Any]:
    if baseline:
        reject_banned_baseline(
            baseline,
            banned=BANNED_SUSPEND_BASELINE,
            accepted="accepted suspend publication (none)",
        )
    return {
        "status": "blocked_no_publication",
        "baseline": None,
        "reason": (
            "S7 suspend_d has no accepted writer; raw_tushare_suspend_d is not recon "
            "truth. Fuyao auction data_status=suspended is intraday, not suspend_d. "
            "TDX listing has no daily suspend calendar."
        ),
    }


def load_accepted_open_days(con: Any, *, table: str = ACCEPTED_CAL_TABLE) -> list[date]:
    reject_banned_baseline(table, banned=BANNED_CAL_BASELINE, accepted=ACCEPTED_CAL_TABLE)
    rows = con.execute(
        f'SELECT cal_date FROM "{_table_name(table)}" WHERE is_open = 1'
    ).fetchall()
    out: list[date] = []
    for row in rows:
        day = as_date(row[0])
        if day is not None:
            out.append(day)
    return out


def load_accepted_st_codes(
    con: Any,
    trade_date: Any,
    *,
    table: str = ACCEPTED_ST_TABLE,
) -> list[str]:
    reject_banned_baseline(table, banned=BANNED_ST_BASELINE, accepted=ACCEPTED_ST_TABLE)
    day = as_date(trade_date)
    if day is None:
        raise ValueError("trade_date required")
    rows = con.execute(
        f'SELECT ts_code FROM "{_table_name(table)}" WHERE CAST(trade_date AS DATE) = ?',
        [day],
    ).fetchall()
    return [str(r[0]).strip().upper() for r in rows if r and r[0]]


def fuyao_calendar_days(items: Sequence[Mapping[str, Any]]) -> list[date]:
    out: list[date] = []
    for item in items:
        day = as_date(item.get("date") or item.get("date_ms"))
        if day is not None:
            out.append(day)
    return out


def fuyao_ticker_rows(items: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        thscode = str(item.get("thscode") or "").strip().upper()
        ticker = str(item.get("ticker") or "").strip()
        exch = str(item.get("exchange") or "").strip().upper()
        if thscode:
            code = thscode
        elif ticker and exch:
            code = f"{ticker}.{exch}"
        else:
            continue
        rows.append({"ts_code": code, "name": str(item.get("name") or "")})
    return rows


def tdx_stock_rows(records: Sequence[Mapping[str, Any]], *, market: int) -> list[dict[str, str]]:
    suffix = {0: "SZ", 1: "SH"}.get(int(market))
    if suffix is None:
        raise ValueError(f"TDX listing recon only supports market 0/1, got {market}")
    prefixes = {
        "SZ": ("000", "001", "002", "003", "300", "301", "302"),
        "SH": ("600", "601", "603", "605", "688", "689"),
    }[suffix]
    rows: list[dict[str, str]] = []
    for rec in records:
        code = str(rec.get("code") or rec.get("symbol") or "").strip().zfill(6)
        name = str(rec.get("name") or "")
        if len(code) != 6 or not code.isdigit() or not code.startswith(prefixes):
            continue
        rows.append({"ts_code": f"{code}.{suffix}", "name": name})
    return rows


__all__ = [
    "ACCEPTED_CAL_TABLE",
    "ACCEPTED_ST_TABLE",
    "BANNED_CAL_BASELINE",
    "BANNED_ST_BASELINE",
    "BANNED_SUSPEND_BASELINE",
    "compare_open_days",
    "compare_st_names",
    "fuyao_calendar_days",
    "fuyao_ticker_rows",
    "load_accepted_open_days",
    "load_accepted_st_codes",
    "name_flags_st",
    "reject_banned_baseline",
    "suspend_recon_status",
    "tdx_stock_rows",
]
