import logging
import time
from datetime import datetime
from typing import Optional

from pydantic import TypeAdapter, ValidationError

from services.api_schemas import KLineDailyRow

logger = logging.getLogger("cm-api")


def looks_like_empty_payload_error(err: Exception) -> bool:
    text = str(err or "")
    markers = [
        "Length mismatch",
        "Expected axis has 0 elements",
        "new values have 6 elements",
        "Columns must be same length as key",
        "No tables found",
    ]
    return any(marker in text for marker in markers)


def market_symbol(code: str) -> str:
    text = str(code or "").strip()
    return f"sh{text}" if text.startswith("6") else f"sz{text}"


def payload_is_empty(payload) -> bool:
    if payload is None:
        return True
    empty = getattr(payload, "empty", None)
    if empty is not None:
        try:
            return bool(empty)
        except Exception:
            pass
    try:
        return len(payload) == 0
    except Exception:
        return False


def records_from_payload(payload) -> list[dict]:
    """Convert tabular or records-like payloads into plain dict rows."""
    if payload is None:
        return []
    if isinstance(payload, dict):
        return [dict(payload)]

    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
        except TypeError:
            records = None
        if records is not None:
            index_values = []
            try:
                index_attr = getattr(payload, "index", None)
                index_values = list(index_attr) if index_attr is not None else []
            except Exception:
                index_values = []
            rows = []
            for idx, row in enumerate(records):
                item = dict(row)
                if "date" not in item and "datetime" not in item and idx < len(index_values):
                    item["datetime"] = index_values[idx]
                rows.append(item)
            return rows

    if isinstance(payload, (str, bytes)):
        return []

    try:
        iterator = iter(payload)
    except TypeError:
        return []

    rows = []
    for row in iterator:
        if isinstance(row, dict):
            rows.append(dict(row))
            continue
        if hasattr(row, "_asdict"):
            rows.append(dict(row._asdict()))
            continue
        try:
            rows.append(dict(row))
        except Exception:
            continue
    return rows


def normalize_date_value(value) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "strftime") and not isinstance(value, str):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            pass
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "nat"}:
        return None
    if len(text) >= 10 and text[4] in {"-", "/"}:
        return text[:10].replace("/", "-")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def _compact_keys(row: dict) -> dict:
    return {str(key).replace(" ", ""): value for key, value in row.items()}


def normalize_price_rows(payload, source: str) -> list[dict]:
    """Normalize upstream price payloads into shared daily-schema records."""
    source = str(source or "")
    column_map = {}
    if source == "eastmoney":
        column_map = {
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        }

    rows = []
    for raw in records_from_payload(payload):
        compact = _compact_keys(raw)
        row = {column_map.get(key, key): value for key, value in compact.items()}
        if "volume" not in row and "vol" in row:
            row["volume"] = row["vol"]

        date = normalize_date_value(row.get("date") or row.get("datetime"))
        if not date or not all(key in row for key in ("open", "high", "low", "close")):
            continue

        rows.append({
            "date": date,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "volume": row.get("volume"),
            "amount": row.get("amount"),
        })
    return rows


def _number_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_or_none(values):
    numbers = [_number_or_none(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return sum(numbers) if numbers else None


def _max_or_none(values):
    numbers = [_number_or_none(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return max(numbers) if numbers else None


def _min_or_none(values):
    numbers = [_number_or_none(value) for value in values]
    numbers = [value for value in numbers if value is not None]
    return min(numbers) if numbers else None


def aggregate_monthly_from_daily(rows) -> list[dict]:
    """Aggregate daily records into monthly OHLCV records."""
    normalized = normalize_price_rows(rows, "normalized")
    if not normalized:
        return []

    normalized.sort(key=lambda row: row["date"])
    groups: dict[str, list[dict]] = {}
    for row in normalized:
        try:
            datetime.strptime(row["date"], "%Y-%m-%d")
        except ValueError:
            continue
        groups.setdefault(row["date"][:7], []).append(row)

    monthly = []
    for month in sorted(groups):
        group = groups[month]
        monthly.append({
            "date": f"{month}-01",
            "open": group[0].get("open"),
            "high": _max_or_none(row.get("high") for row in group),
            "low": _min_or_none(row.get("low") for row in group),
            "close": group[-1].get("close"),
            "volume": _sum_or_none(row.get("volume") for row in group),
            "amount": _sum_or_none(row.get("amount") for row in group),
        })
    return monthly


async def fetch_daily_akshare_fallbacks(code: str, start_date: str, end_date: str, *, safe_call):
    import akshare as ak

    attempts = [
        (
            "eastmoney",
            ak.stock_zh_a_hist,
            {
                "symbol": code,
                "period": "daily",
                "start_date": start_date,
                "end_date": end_date,
                "adjust": "qfq",
            },
        ),
        (
            "sina",
            ak.stock_zh_a_daily,
            {
                "symbol": market_symbol(code),
                "start_date": start_date,
                "end_date": end_date,
                "adjust": "qfq",
            },
        ),
        (
            "tx",
            ak.stock_zh_a_hist_tx,
            {
                "symbol": market_symbol(code),
                "start_date": start_date,
                "end_date": end_date,
                "adjust": "qfq",
            },
        ),
    ]

    diagnostics = {
        "ok": False,
        "attempts": [],
        "all_empty": False,
        "last_error": None,
    }
    empty_sources = []
    last_err = None

    for source, func, kwargs in attempts:
        attempt = {"source": source, "ok": False}
        started_at = time.time()
        try:
            payload = await safe_call(func, timeout=30, retries=1, **kwargs)
            rows = normalize_price_rows(payload, source)
            if rows:
                try:
                    TypeAdapter(list[KLineDailyRow]).validate_python(rows)
                    attempt["ok"] = True
                    attempt["rows"] = len(rows)
                    attempt["elapsed_sec"] = round(time.time() - started_at, 3)
                    diagnostics["attempts"].append(attempt)
                    diagnostics["ok"] = True
                    diagnostics["effective_source"] = source
                    return rows, source, diagnostics
                except ValidationError as err:
                    logger.error(f"[日K fallback] {source} 防腐层截断 - Schema校验失败: {err}")
                    last_err = ValueError(f"{source}: schema validation failed")
                    empty_sources.append(source)
                    attempt["error_type"] = "ValidationError"
                    attempt["error"] = str(err)
            else:
                empty_sources.append(source)
                last_err = ValueError(f"{source}: empty")
                attempt["error_type"] = "empty"
                attempt["error"] = "empty"
        except Exception as err:
            if looks_like_empty_payload_error(err):
                empty_sources.append(source)
                last_err = ValueError(f"{source}: empty")
                attempt["error_type"] = "empty"
                attempt["error"] = "empty"
            else:
                last_err = err
                attempt["error_type"] = type(err).__name__
                attempt["error"] = str(err)
            logger.debug(f"[日K fallback] {code} {source} 失败: {err}")

        attempt["elapsed_sec"] = round(time.time() - started_at, 3)
        diagnostics["attempts"].append(attempt)

    diagnostics["all_empty"] = bool(empty_sources and len(empty_sources) == len(attempts))
    if diagnostics["all_empty"]:
        diagnostics["last_error"] = "all_sources_empty(eastmoney/sina/tx)"
    elif last_err:
        diagnostics["last_error"] = str(last_err)
    return None, "", diagnostics
