import logging
import time

import pandas as pd
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


def normalize_price_frame(df, source: str):
    """Normalize upstream price frames into the shared daily schema."""
    if df is None or df.empty:
        return None
    frame = df.copy()

    if source == "eastmoney":
        frame = frame.rename(columns={
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        })
    elif source == "tx":
        if "volume" not in frame.columns:
            frame["volume"] = None

    required = ["date", "open", "high", "low", "close"]
    if not all(col in frame.columns for col in required):
        return None

    for col in ["volume", "amount"]:
        if col not in frame.columns:
            frame[col] = None

    frame = frame[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    frame["date"] = frame["date"].astype(str).str[:10]
    return frame


def aggregate_monthly_from_daily(df: pd.DataFrame):
    """Aggregate daily rows into monthly OHLCV rows."""
    if df is None or df.empty:
        return None
    frame = df.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date")
    frame["month"] = frame["date"].dt.to_period("M")

    rows = []
    for _, group in frame.groupby("month", sort=True):
        group = group.sort_values("date")
        rows.append({
            "date": group.iloc[0]["date"].strftime("%Y-%m-01"),
            "open": group.iloc[0]["open"],
            "high": group["high"].max(),
            "low": group["low"].min(),
            "close": group.iloc[-1]["close"],
            "volume": group["volume"].sum(min_count=1),
            "amount": group["amount"].sum(min_count=1),
        })
    return pd.DataFrame(rows)


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
            df = await safe_call(func, timeout=30, retries=1, **kwargs)
            norm = normalize_price_frame(df, source)
            if norm is not None and not norm.empty:
                try:
                    TypeAdapter(list[KLineDailyRow]).validate_python(norm.to_dict("records"))
                    attempt["ok"] = True
                    attempt["rows"] = len(norm)
                    attempt["elapsed_sec"] = round(time.time() - started_at, 3)
                    diagnostics["attempts"].append(attempt)
                    diagnostics["ok"] = True
                    diagnostics["effective_source"] = source
                    return norm, source, diagnostics
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