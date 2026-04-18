"""
北向持仓同步服务

职责：
- 拉取东方财富北向持股日级快照
- 规范化到 fact_northbound_daily 事实表
- 基于历史持股数量补齐 change_shares
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Iterable, Optional

import pandas as pd

from services.utils import normalize_ymd, safe_float

logger = logging.getLogger("cm-api")

_NORTHBOUND_SOURCE = "akshare_stock_hsgt_stock_statistics_em"
_NORTHBOUND_REQUIRED_COLUMNS = {
    "持股日期": "trade_date",
    "股票代码": "stock_code",
    "股票简称": "stock_name",
    "持股数量": "hold_shares",
    "持股市值": "hold_market_cap",
    "持股数量占发行股百分比": "hold_ratio",
}


def _disable_proxy_env() -> None:
    for key in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"


async def fetch_northbound_statistics(
    start_date: str,
    end_date: str,
    *,
    retries: int = 2,
    timeout: int = 45,
) -> pd.DataFrame:
    """拉取指定日期区间的北向持股明细。"""
    _disable_proxy_env()
    import akshare as ak

    last_error = None
    for attempt in range(retries + 1):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    ak.stock_hsgt_stock_statistics_em,
                    symbol="北向持股",
                    start_date=start_date.replace("-", ""),
                    end_date=end_date.replace("-", ""),
                ),
                timeout=timeout,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            logger.warning(
                f"[北向] 拉取失败，重试 {attempt + 1}/{retries}: {exc}"
            )
    raise RuntimeError(f"northbound_source_failed: {last_error}")


def _normalize_northbound_rows(
    frame: Optional[pd.DataFrame],
    *,
    active_codes: Optional[Iterable[str]] = None,
) -> list[dict]:
    if frame is None or frame.empty:
        return []

    missing = [column for column in _NORTHBOUND_REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise RuntimeError(f"northbound_columns_missing: {missing}")

    normalized = frame[list(_NORTHBOUND_REQUIRED_COLUMNS.keys())].copy()
    normalized = normalized.rename(columns=_NORTHBOUND_REQUIRED_COLUMNS)
    normalized["stock_code"] = normalized["stock_code"].astype(str).str.strip().str.zfill(6)
    normalized["stock_name"] = normalized["stock_name"].where(
        normalized["stock_name"].notna(),
        None,
    )
    normalized["trade_date"] = normalized["trade_date"].map(normalize_ymd)
    normalized = normalized[normalized["stock_code"].str.fullmatch(r"\d{6}", na=False)]
    normalized = normalized[normalized["trade_date"].notna()]

    if active_codes is not None:
        active_set = {str(code).strip().zfill(6) for code in active_codes if code}
        normalized = normalized[normalized["stock_code"].isin(active_set)]

    for column in ("hold_shares", "hold_market_cap", "hold_ratio"):
        normalized[column] = normalized[column].map(safe_float)

    normalized = normalized.drop_duplicates(
        subset=["stock_code", "trade_date"],
        keep="last",
    ).sort_values(["stock_code", "trade_date"])

    records = []
    for row in normalized.to_dict("records"):
        stock_name = row.get("stock_name")
        if stock_name is not None:
            stock_name = str(stock_name).strip() or None
        records.append(
            {
                "stock_code": row["stock_code"],
                "stock_name": stock_name,
                "hold_shares": row.get("hold_shares"),
                "hold_market_cap": row.get("hold_market_cap"),
                "hold_ratio": row.get("hold_ratio"),
                "trade_date": row["trade_date"],
            }
        )
    return records


def _load_previous_hold_shares(conn, stock_codes: list[str], before_date: str) -> dict[str, Optional[float]]:
    if not stock_codes:
        return {}
    placeholders = ",".join("?" for _ in stock_codes)
    rows = conn.execute(
        f"SELECT stock_code, trade_date, hold_shares FROM fact_northbound_daily "
        f"WHERE stock_code IN ({placeholders}) AND trade_date < ? "
        f"ORDER BY stock_code, trade_date DESC",
        [*stock_codes, before_date],
    ).fetchall()

    previous: dict[str, Optional[float]] = {}
    for row in rows:
        stock_code = str(row["stock_code"] if hasattr(row, "keys") else row[0]).strip().zfill(6)
        if stock_code in previous:
            continue
        hold_shares = row["hold_shares"] if hasattr(row, "keys") else row[2]
        previous[stock_code] = safe_float(hold_shares)
    return previous


def _attach_change_shares(conn, records: list[dict]) -> list[dict]:
    if not records:
        return []

    previous = _load_previous_hold_shares(
        conn,
        sorted({row["stock_code"] for row in records}),
        min(row["trade_date"] for row in records),
    )

    enriched = []
    for row in records:
        prior_shares = previous.get(row["stock_code"])
        hold_shares = row.get("hold_shares")
        change_shares = (
            hold_shares - prior_shares
            if hold_shares is not None and prior_shares is not None
            else None
        )
        next_row = dict(row)
        next_row["change_shares"] = change_shares
        enriched.append(next_row)
        previous[row["stock_code"]] = hold_shares
    return enriched


def _upsert_northbound_rows(conn, rows: list[dict]) -> int:
    if not rows:
        return 0

    now = datetime.now().isoformat()
    payload = [
        (
            row["stock_code"],
            row.get("stock_name"),
            row.get("hold_shares"),
            row.get("hold_market_cap"),
            row.get("hold_ratio"),
            row.get("change_shares"),
            row["trade_date"],
            now,
        )
        for row in rows
    ]

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(
            """
            INSERT INTO fact_northbound_daily (
                stock_code,
                stock_name,
                hold_shares,
                hold_market_cap,
                hold_ratio,
                change_shares,
                trade_date,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, trade_date) DO UPDATE SET
                stock_name = excluded.stock_name,
                hold_shares = excluded.hold_shares,
                hold_market_cap = excluded.hold_market_cap,
                hold_ratio = excluded.hold_ratio,
                change_shares = excluded.change_shares,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(payload)


async def sync_northbound_daily(
    conn,
    *,
    start_date: str,
    end_date: str,
    active_codes: Optional[Iterable[str]] = None,
) -> dict:
    """同步北向持仓日级事实表。"""
    normalized_start = normalize_ymd(start_date)
    normalized_end = normalize_ymd(end_date)
    if not normalized_start or not normalized_end:
        raise ValueError("invalid_northbound_date_range")

    try:
        frame = await fetch_northbound_statistics(normalized_start, normalized_end)
    except Exception as exc:
        logger.warning(
            f"[北向] 区间 {normalized_start} ~ {normalized_end} 拉取失败: {exc}"
        )
        return {
            "status": "source_unavailable",
            "source": _NORTHBOUND_SOURCE,
            "requested_start_date": normalized_start,
            "requested_end_date": normalized_end,
            "input_rows": 0,
            "written_rows": 0,
            "trade_dates": [],
            "error": str(exc),
        }

    input_rows = 0 if frame is None else len(frame)
    records = _normalize_northbound_rows(frame, active_codes=active_codes)
    if not records:
        logger.warning(
            f"[北向] 区间 {normalized_start} ~ {normalized_end} 无可写入数据"
        )
        return {
            "status": "empty",
            "source": _NORTHBOUND_SOURCE,
            "requested_start_date": normalized_start,
            "requested_end_date": normalized_end,
            "input_rows": input_rows,
            "written_rows": 0,
            "trade_dates": [],
        }

    enriched_records = _attach_change_shares(conn, records)
    written_rows = _upsert_northbound_rows(conn, enriched_records)
    trade_dates = sorted({row["trade_date"] for row in enriched_records})
    logger.info(
        f"[北向] 同步完成: {written_rows} 条, {len(trade_dates)} 个交易日 ({trade_dates[0]} ~ {trade_dates[-1]})"
    )
    return {
        "status": "success",
        "source": _NORTHBOUND_SOURCE,
        "requested_start_date": normalized_start,
        "requested_end_date": normalized_end,
        "input_rows": input_rows,
        "written_rows": written_rows,
        "trade_dates": trade_dates,
    }