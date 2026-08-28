"""Read-only TDX unadjusted daily K vs accepted nominal OHLCV.

Uses vipdoc ``.day`` files when ``TDXDIR`` exists; otherwise the TDX protocol
``get_security_bars`` (frequency=9). ``adjust=qfq/hfq`` is rejected.

BJ listing APIs in tdxhub warn unsupported; BJ bars still go through
``get_security_bars`` with market=2 derived from ``ts_code`` suffix, never from
``get_stock_market('920…')`` (that helper maps 9* to SH).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping

from services.data_sources.fuyao_kline_recon import (
    ACCEPTED_K_TABLE,
    compare_kline,
    reject_banned_baseline,
)

TDX_K_TABLE = "tdx_k"
TDX_VOL_SCALE = 1.0  # protocol vol vs TuShare lot; confirmed live, not assumed
TDX_AMOUNT_SCALE = 1000.0  # protocol amount CNY vs TuShare CNY_thousand
TDX_VOL_CONFIRM = (0.8, 1.2)
TDX_AMOUNT_CONFIRM = (800.0, 1200.0)
BANNED_ADJUST = frozenset({"qfq", "hfq", "01", "02", "before", "after"})
# Both labeled daily in tdxhub.consts; live HQ may answer only one.
DAILY_BAR_CATEGORIES = (9, 4)
# Protocol max (tdxhub.consts.MAX_KLINE_COUNT). Page size, not a history estimate.
MAX_BARS_PER_PAGE = 800
MAX_KLINE_PAGES = 50  # 50*800 >> A-share listed daily history; fail closed on runaway HQ
_CLIENT_CATEGORY_ATTR = "_cm_daily_category"


def reject_tdx_adjust(adjust: str | None) -> None:
    raw = str(adjust or "").strip().lower()
    if raw in BANNED_ADJUST:
        raise ValueError(
            f"banned tdx adjust={adjust!r}; this recon only accepts unadjusted bars"
        )


def protocol_market(ts_code: str) -> tuple[int, str]:
    """Return (tdx_market, six_digit_code) from ``000001.SZ`` style codes."""
    code, _, exch = str(ts_code).upper().partition(".")
    code = code.zfill(6)
    exch = exch or ""
    if exch == "SZ":
        return 0, code
    if exch == "SH":
        return 1, code
    if exch == "BJ":
        return 2, code
    raise ValueError(f"unsupported ts_code {ts_code!r}")


def lday_stem_ts_code(stem: str) -> str | None:
    """Map ``sz000001`` / ``sh600519`` vipdoc stems to ts_code; drop indexes."""
    name = stem.lower()
    if name.startswith("sz"):
        code = name[2:]
        if code.startswith(("000", "001", "002", "003", "300", "301", "302")):
            return f"{code}.SZ"
        return None
    if name.startswith("sh"):
        code = name[2:]
        if code.startswith(("600", "601", "603", "605", "688", "689")):
            return f"{code}.SH"
        return None
    if name.startswith("bj"):
        code = name[2:]
        return f"{code}.BJ"
    return None


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if " " in text:
        text = text.split(" ", 1)[0]
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return date.fromisoformat(text[:10])


def bar_trade_date(item: Mapping[str, Any] | None) -> date | None:
    payload = item or {}
    got = _as_date(payload.get("datetime") or payload.get("date"))
    if got is not None:
        return got
    year, month, day = payload.get("year"), payload.get("month"), payload.get("day")
    if year in (None, "") or month in (None, "") or day in (None, ""):
        return None
    try:
        return date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def bars_page_size(offset: int | None = None) -> int:
    """Protocol ``count`` per ``get_security_bars`` call. Cap 800; never a holiday guess."""
    if offset is None:
        return MAX_BARS_PER_PAGE
    n = int(offset)
    if n <= 0:
        return MAX_BARS_PER_PAGE
    return min(n, MAX_BARS_PER_PAGE)


def bars_as_records(raw: Any) -> list[dict[str, Any]]:
    """Normalize protocol / pandas payloads without ``if dataframe`` truthiness."""
    if raw is None:
        return []
    empty = getattr(raw, "empty", None)
    if empty is True:
        return []
    to_dict = getattr(raw, "to_dict", None)
    if callable(to_dict):
        try:
            rows = to_dict("records")
        except TypeError:
            rows = None
        if isinstance(rows, list):
            return [dict(item) for item in rows]
    if isinstance(raw, list):
        return [dict(item) if not isinstance(item, dict) else item for item in raw]
    if isinstance(raw, dict):
        return [raw]
    return []


def records_to_rows(
    records: Iterable[dict[str, Any]],
    ts_code: str,
    *,
    start: date,
    end: date,
) -> list[tuple]:
    rows = []
    for item in records or []:
        trade_date = bar_trade_date(item)
        if trade_date is None or trade_date < start or trade_date > end:
            continue
        rows.append(
            (
                ts_code,
                trade_date,
                float(item.get("open") or 0),
                float(item.get("high") or 0),
                float(item.get("low") or 0),
                float(item.get("close") or 0),
                float(item.get("vol") if item.get("vol") is not None else item.get("volume") or 0),
                float(item.get("amount") or 0),
            )
        )
    return rows


def load_tdx_kline(con: Any, rows: Iterable[tuple], *, table: str = TDX_K_TABLE) -> int:
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(
        f"""
        CREATE TABLE {table} (
            ts_code VARCHAR,
            trade_date DATE,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume_share DOUBLE,
            turnover_cny DOUBLE
        )
        """
    )
    payload = list(rows)
    if payload:
        con.executemany(
            f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            payload,
        )
    return len(payload)


def compare_tdx_kline(
    con: Any,
    *,
    source_table: str = TDX_K_TABLE,
    accepted_table: str = ACCEPTED_K_TABLE,
) -> dict[str, Any]:
    reject_banned_baseline(accepted_table)
    report = compare_kline(
        con,
        fuyao_table=source_table,
        accepted_table=accepted_table,
        documented_vol_scale=TDX_VOL_SCALE,
        documented_amount_scale=TDX_AMOUNT_SCALE,
        vol_ratio_confirm=TDX_VOL_CONFIRM,
        amount_ratio_confirm=TDX_AMOUNT_CONFIRM,
    )
    report["source"] = "tdxhub_unadjusted"
    report["only_source"] = report["only_fuyao"]
    report["source_rows"] = report["fuyao_rows"]
    report["only_source_samples"] = report["only_fuyao_samples"]
    return report


def _page_oldest(records: Iterable[dict[str, Any]]) -> date | None:
    dates = [d for d in (bar_trade_date(item) for item in records) if d is not None]
    return min(dates) if dates else None


def dedup_kline_rows(rows: Iterable[tuple]) -> list[tuple]:
    seen: dict[tuple[Any, Any], tuple] = {}
    for row in rows:
        key = (row[0], row[1])
        if key not in seen:
            seen[key] = row
    return sorted(seen.values(), key=lambda r: (r[0], r[1]))


def live_hq_env_set() -> bool:
    import os

    return bool(
        os.environ.get("TDXHUB_CONNECT_CFG", "").strip()
        or os.environ.get("TDXHUB_HQ", "").strip()
    )


def live_history_probe(*, ts_code: str = "000001.SZ") -> dict[str, Any]:
    """Optional live HQ probe. Unset env is ``live_unprobed``, not a skip of fake tests."""
    if not live_hq_env_set():
        return {
            "status": "live_unprobed",
            "bars": "live_unprobed",
            "xdxr": "live_unprobed",
            "block": "live_unprobed",
            "reason": "TDXHUB_CONNECT_CFG and TDXHUB_HQ unset",
        }
    from services.calendar import latest_closed_or_raise, parse_day
    from services.data_sources.sources.tdxhub import block, quotes_client, xdxr

    client = None
    try:
        client = quotes_client()
        end = parse_day(latest_closed_or_raise())
        if end is None:
            raise RuntimeError("calendar latest closed day unparseable")
        try:
            start = date(end.year - 10, end.month, end.day)
        except ValueError:
            start = date(end.year - 10, 2, 28)
        rows = fetch_unadjusted_bars(client, ts_code, start=start, end=end)
        xdxr_payload = xdxr(client, ts_code)
        block_payload = block(client, tofile="block_gn.dat")
        bar_status = "ok" if len(rows) > MAX_BARS_PER_PAGE else "shallow_or_empty"
        return {
            "status": bar_status,
            "bars": bar_status,
            "bar_rows": len(rows),
            "xdxr": xdxr_payload.get("status"),
            "xdxr_events": len(xdxr_payload.get("events") or []),
            "block": block_payload.get("status"),
            "block_n": len(block_payload.get("blocks") or []),
            "reason": None,
        }
    except Exception as exc:  # noqa: BLE001 — probe must not pretend success
        return {
            "status": "live_failed",
            "bars": "live_failed",
            "xdxr": "live_failed",
            "block": "live_failed",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # rule-compliance: ok evidence=tdx-socket-close-best-effort
                pass


def fetch_unadjusted_bars(
    client: Any,
    ts_code: str,
    *,
    start: date,
    end: date,
    offset: int | None = None,
    adjust: str | None = None,
) -> list[tuple]:
    """Full-history unadjusted daily bars via paginated ``get_security_bars``.

    ``start``/``count`` are protocol offsets (0 = newest page), not calendar
    arithmetic. Stop on empty page, short page, or a page whose oldest bar is
    before the requested start date. Do not estimate trading-day span.
    """
    reject_tdx_adjust(adjust)
    market, code = protocol_market(ts_code)
    page_size = bars_page_size(offset)
    preferred = getattr(client, _CLIENT_CATEGORY_ATTR, None)
    categories: list[int] = []
    if preferred is not None:
        categories.append(int(preferred))
    for cat in DAILY_BAR_CATEGORIES:
        if cat not in categories:
            categories.append(int(cat))
    api = client.client
    chosen: int | None = None
    collected: list[dict[str, Any]] = []
    for cat in categories:
        page = bars_as_records(api.get_security_bars(int(cat), market, code, 0, page_size))
        if not page:
            continue
        chosen = int(cat)
        setattr(client, _CLIENT_CATEGORY_ATTR, chosen)
        collected.extend(page)
        break
    if chosen is None:
        return []

    def _stop(page: list[dict[str, Any]]) -> bool:
        if len(page) < page_size:
            return True
        oldest = _page_oldest(page)
        return oldest is not None and oldest < start

    seen_dates = {d for d in (bar_trade_date(item) for item in collected) if d is not None}
    if _stop(collected):
        return dedup_kline_rows(records_to_rows(collected, ts_code, start=start, end=end))

    page_start = page_size
    while True:
        page = bars_as_records(
            api.get_security_bars(chosen, market, code, page_start, page_size)
        )
        if not page:
            break
        new_dates = {d for d in (bar_trade_date(item) for item in page) if d is not None}
        collected.extend(page)
        if not (new_dates - seen_dates):
            break
        seen_dates |= new_dates
        if _stop(page):
            break
        page_start += page_size
        if page_start >= page_size * MAX_KLINE_PAGES:
            raise RuntimeError(
                f"tdx get_security_bars exceeded {MAX_KLINE_PAGES} pages "
                f"without reaching start={start}"
            )
    return dedup_kline_rows(records_to_rows(collected, ts_code, start=start, end=end))
