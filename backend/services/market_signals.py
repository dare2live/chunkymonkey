import logging
import math
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger("cm-api")

_CACHE_TTL_SEC = 600
_CACHE: dict[tuple, tuple[float, object]] = {}
_FETCH_RETRY_DELAYS_SEC = (0.6, 1.2)


def _cache_get(key: tuple, *, allow_stale: bool = False):
    cached = _CACHE.get(key)
    if not cached:
        return None
    cached_at, value = cached
    if allow_stale or time.time() - cached_at <= _CACHE_TTL_SEC:
        return value
    if time.time() - cached_at > _CACHE_TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: tuple, value):
    _CACHE[key] = (time.time(), value)
    return value


def _normalize_stock_code(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits.zfill(6) if digits else ""


def _normalize_date(value: object) -> Optional[str]:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())[:8]
    if len(digits) != 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _datetime_filter(value: object) -> Optional[str]:
    date_text = _normalize_date(value)
    if not date_text:
        return None
    return f"{date_text} 00:00:00"


def _safe_number(value: object) -> Optional[float]:
    if value in (None, "", "None"):
        return None
    try:
        number = float(value)
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 2)


def _sample_points(points: list[dict], max_points: int) -> list[dict]:
    if len(points) <= max_points:
        return points
    step = max(1, (len(points) + max_points - 1) // max_points)
    sampled = points[::step]
    if sampled and sampled[-1]["date"] != points[-1]["date"]:
        sampled.append(points[-1])
    return sampled


def _lookback_point(points: list[dict], offset: int) -> Optional[dict]:
    if len(points) <= offset:
        return None
    return points[-(offset + 1)]


def _format_change_amount(value: Optional[float]) -> str:
    if value is None:
        return "-"
    amount = abs(float(value))
    if amount >= 10000:
        return f"{amount / 10000:.2f}亿股"
    return f"{amount:.2f}万股"


def _eastmoney_rows(
    report_name: str,
    *,
    columns: str,
    filter_expr: Optional[str],
    sort_columns: str,
    sort_types: str,
    source: str,
    client_name: str,
    page_size: int = 1000,
    max_pages: Optional[int] = None,
) -> list[dict]:
    cache_key = (
        report_name,
        columns,
        filter_expr,
        sort_columns,
        sort_types,
        source,
        client_name,
        page_size,
        max_pages,
    )
    # P6.5 (2026-04-28): 走 miaoxiang/aif10-scraper, 不保留直连抓取路径.
    # source/client 参数通过 extra_params 传, 兼容 reportName 对协议参数的需求差异.
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    stale_cached = _cache_get(cache_key, allow_stale=True)

    from aif10_scraper import AIF10Client

    last_error = None
    for attempt in range(len(_FETCH_RETRY_DELAYS_SEC) + 1):
        rows: list[dict] = []
        try:
            cli = AIF10Client(retry=1, timeout=20.0)
            try:
                page = 1
                total_pages = 1
                while page <= total_pages:
                    result_obj = cli.get_v1(
                        report_name,
                        page=page,
                        page_size=page_size,
                        sort_columns=sort_columns,
                        sort_types=sort_types,
                        columns=columns,
                        filter_expr=filter_expr if filter_expr else None,
                        extra_params={"source": source, "client": client_name},
                    )
                    page_rows = result_obj.get("data") or []
                    if page == 1:
                        total_pages = int(result_obj.get("pages") or 0)
                        if not total_pages:
                            count = int(result_obj.get("count") or 0)
                            total_pages = max(1, math.ceil(count / page_size)) if count else 1
                        if max_pages:
                            total_pages = min(total_pages, max_pages)
                    rows.extend(page_rows)
                    if not page_rows:
                        break
                    page += 1
            finally:
                cli.close()
            return _cache_put(cache_key, rows)
        except Exception as exc:
            last_error = exc
            if attempt < len(_FETCH_RETRY_DELAYS_SEC):
                time.sleep(_FETCH_RETRY_DELAYS_SEC[attempt])

    if stale_cached is not None:
        logger.warning("aif10 fetch failed for %s after retries, using stale cache: %s", report_name, last_error)
        return stale_cached

    logger.warning("aif10 fetch failed for %s after retries: %s", report_name, last_error)
    return []


def load_margin_balance_overlay(stock_code: str, years: int = 3, max_points: int = 220) -> dict:
    code = _normalize_stock_code(stock_code)
    if not code:
        return {"points": [], "point_count": 0, "raw_point_count": 0}

    since_dt = datetime.utcnow() - timedelta(days=max(years, 1) * 370)
    rows = _eastmoney_rows(
        "RPT_MARGIN_STATISTICS_STOCKS",
        columns=(
            "SECURITY_CODE,TRADE_DATE,SECURITY_NAME_ABBR,FIN_BALANCE,FIN_BALANCE_RATIO,"
            "LOAN_BALANCE,LOAN_BALANCE_RATIO,FIN_NETBUY_AMT,LOAN_NETSELL_AMT"
        ),
        filter_expr=f"(SECURITY_CODE=\"{code}\")(TRADE_DATE>='{since_dt.strftime('%Y-%m-%d 00:00:00')}')",
        sort_columns="TRADE_DATE",
        sort_types="-1",
        source="DataCenter",
        client_name="WAP",
        page_size=1000,
    )

    points: list[dict] = []
    for row in reversed(rows):
        date_text = _normalize_date(row.get("TRADE_DATE"))
        if not date_text:
            continue
        points.append({
            "date": date_text,
            "fin_balance": _safe_number(row.get("FIN_BALANCE")),
            "fin_balance_ratio": _safe_number(row.get("FIN_BALANCE_RATIO")),
            "loan_balance": _safe_number(row.get("LOAN_BALANCE")),
            "loan_balance_ratio": _safe_number(row.get("LOAN_BALANCE_RATIO")),
            "fin_netbuy_amt": _safe_number(row.get("FIN_NETBUY_AMT")),
            "loan_netsell_amt": _safe_number(row.get("LOAN_NETSELL_AMT")),
        })

    latest = points[-1] if points else None
    lookback_20 = _lookback_point(points, 20)
    lookback_60 = _lookback_point(points, 60)
    return {
        "source": "eastmoney_margin_history",
        "note": "两融曲线来自东财单股历史接口；图中展示融资余额与融券余额，联动摘要使用余额占比做横截面对比。",
        "point_count": len(_sample_points(points, max_points)),
        "raw_point_count": len(points),
        "latest_trade_date": latest["date"] if latest else None,
        "latest_fin_balance": latest.get("fin_balance") if latest else None,
        "latest_fin_balance_ratio": latest.get("fin_balance_ratio") if latest else None,
        "latest_loan_balance": latest.get("loan_balance") if latest else None,
        "latest_loan_balance_ratio": latest.get("loan_balance_ratio") if latest else None,
        "fin_balance_change_20d_pct": _pct_change(latest.get("fin_balance") if latest else None, lookback_20.get("fin_balance") if lookback_20 else None),
        "fin_balance_change_60d_pct": _pct_change(latest.get("fin_balance") if latest else None, lookback_60.get("fin_balance") if lookback_60 else None),
        "loan_balance_change_20d_pct": _pct_change(latest.get("loan_balance") if latest else None, lookback_20.get("loan_balance") if lookback_20 else None),
        "loan_balance_change_60d_pct": _pct_change(latest.get("loan_balance") if latest else None, lookback_60.get("loan_balance") if lookback_60 else None),
        "points": _sample_points(points, max_points),
    }


def load_shareholder_change_payload(stock_code: str, years: int = 3) -> dict:
    code = _normalize_stock_code(stock_code)
    if not code:
        return {"events": [], "recent_180d": {}}

    since_dt = datetime.utcnow() - timedelta(days=max(years, 1) * 370)
    recent_cutoff = datetime.utcnow() - timedelta(days=180)
    rows = _eastmoney_rows(
        "RPT_SHARE_HOLDER_INCREASE",
        columns=(
            "SECURITY_CODE,SECURITY_NAME_ABBR,HOLDER_NAME,DIRECTION,CHANGE_NUM_SYMBOL,CHANGE_RATE,"
            "TRADE_AVERAGE_PRICE,MARKET,START_DATE,END_DATE,NOTICE_DATE,TRADE_DATE"
        ),
        filter_expr=f"(SECURITY_CODE=\"{code}\")(END_DATE>='{since_dt.strftime('%Y-%m-%d 00:00:00')}')",
        sort_columns="END_DATE,SECURITY_CODE,EITIME",
        sort_types="-1,-1,-1",
        source="WEB",
        client_name="WEB",
        page_size=1000,
    )

    events: list[dict] = []
    summary = {
        "window_days": 180,
        "event_count": 0,
        "increase_count": 0,
        "decrease_count": 0,
        "net_event_count": 0,
        "net_change_num": 0.0,
        "latest_notice_date": None,
    }
    for row in reversed(rows):
        event_date = _normalize_date(row.get("TRADE_DATE") or row.get("END_DATE") or row.get("NOTICE_DATE"))
        if not event_date:
            continue
        notice_date = _normalize_date(row.get("NOTICE_DATE"))
        holder_name = str(row.get("HOLDER_NAME") or "").strip()
        direction = str(row.get("DIRECTION") or "").strip()
        change_num = _safe_number(row.get("CHANGE_NUM_SYMBOL"))
        if not direction:
            direction = "增持" if (change_num or 0) > 0 else "减持" if (change_num or 0) < 0 else "变动"
        tone = "increase" if direction == "增持" else "decrease" if direction == "减持" else "change"
        change_rate = _safe_number(row.get("CHANGE_RATE"))
        avg_price = _safe_number(row.get("TRADE_AVERAGE_PRICE"))
        body_parts = [part for part in [holder_name, str(row.get("MARKET") or "").strip()] if part]
        if change_num is not None:
            body_parts.append(f"{direction} {_format_change_amount(change_num)}")
        else:
            body_parts.append(direction)
        if change_rate is not None:
            body_parts.append(f"占总股本 {change_rate:+.2f}%")
        if avg_price is not None:
            body_parts.append(f"均价 {avg_price:.2f}")
        events.append({
            "date": event_date,
            "notice_date": notice_date,
            "lane": "change",
            "tone": tone,
            "title": f"高管/股东{direction}",
            "body": " · ".join(body_parts) or f"高管/股东{direction}",
            "shortLabel": direction,
        })

        event_dt = datetime.strptime(event_date, "%Y-%m-%d")
        if event_dt >= recent_cutoff:
            summary["event_count"] += 1
            if direction == "增持":
                summary["increase_count"] += 1
            elif direction == "减持":
                summary["decrease_count"] += 1
            summary["net_change_num"] += change_num or 0.0
            if notice_date and (not summary["latest_notice_date"] or notice_date > summary["latest_notice_date"]):
                summary["latest_notice_date"] = notice_date

    summary["net_event_count"] = summary["increase_count"] - summary["decrease_count"]
    return {
        "source": "eastmoney_shareholder_change",
        "note": "高管/股东增减持来自东财个股增减持明细；事件轴按交易/截止日落点，联动摘要统计最近180天。",
        "events": events,
        "recent_180d": summary,
    }


def load_margin_market_snapshot(trade_date: str) -> dict[str, dict]:
    trade_dt = _datetime_filter(trade_date)
    if not trade_dt:
        return {}
    rows = _eastmoney_rows(
        "RPT_MARGIN_STATISTICS_STOCKS",
        columns="SECURITY_CODE,TRADE_DATE,FIN_BALANCE,FIN_BALANCE_RATIO,LOAN_BALANCE,LOAN_BALANCE_RATIO",
        filter_expr=f"(TRADE_DATE='{trade_dt}')",
        sort_columns="FIN_BALANCE",
        sort_types="-1",
        source="DataCenter",
        client_name="WAP",
        page_size=5000,
    )
    result = {}
    for row in rows:
        code = _normalize_stock_code(row.get("SECURITY_CODE"))
        if not code:
            continue
        result[code] = {
            "fin_balance": _safe_number(row.get("FIN_BALANCE")),
            "fin_balance_ratio": _safe_number(row.get("FIN_BALANCE_RATIO")),
            "loan_balance": _safe_number(row.get("LOAN_BALANCE")),
            "loan_balance_ratio": _safe_number(row.get("LOAN_BALANCE_RATIO")),
        }
    return result


def load_shareholder_change_universe_summary(window_days: int = 180) -> dict:
    since_dt = datetime.utcnow() - timedelta(days=max(window_days, 30))
    rows = _eastmoney_rows(
        "RPT_SHARE_HOLDER_INCREASE",
        columns="SECURITY_CODE,DIRECTION,CHANGE_NUM_SYMBOL,END_DATE,NOTICE_DATE,TRADE_DATE",
        filter_expr=f"(END_DATE>='{since_dt.strftime('%Y-%m-%d 00:00:00')}')",
        sort_columns="END_DATE,SECURITY_CODE,EITIME",
        sort_types="-1,-1,-1",
        source="WEB",
        client_name="WEB",
        page_size=5000,
    )
    stocks: dict[str, dict] = {}
    for row in rows:
        code = _normalize_stock_code(row.get("SECURITY_CODE"))
        if not code:
            continue
        direction = str(row.get("DIRECTION") or "").strip()
        change_num = _safe_number(row.get("CHANGE_NUM_SYMBOL"))
        if not direction:
            direction = "增持" if (change_num or 0) > 0 else "减持" if (change_num or 0) < 0 else "变动"
        item = stocks.setdefault(code, {
            "event_count": 0,
            "increase_count": 0,
            "decrease_count": 0,
            "net_event_count": 0,
            "net_change_num": 0.0,
        })
        item["event_count"] += 1
        if direction == "增持":
            item["increase_count"] += 1
        elif direction == "减持":
            item["decrease_count"] += 1
        item["net_change_num"] += change_num or 0.0
        item["net_event_count"] = item["increase_count"] - item["decrease_count"]
    return {"window_days": window_days, "stocks": stocks}
