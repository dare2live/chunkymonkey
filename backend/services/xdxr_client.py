"""TDX xdxr 同步客户端。

职责：
- 通过共享 tdxhub / tdxhub 入口抓取除权除息与股本变动事件
- 规范化为项目内统一字段
- 按股票全量替换写入 market.duckdb.price_xdxr
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
from datetime import datetime, timedelta
from typing import Optional

from services.market_db import (
    get_all_xdxr_sync_states,
    replace_xdxr_rows,
    update_xdxr_sync_state,
)
from services.tdx_source import call_tdx_quotes_with_retry, iter_tdx_servers


logger = logging.getLogger("cm-api")
_XDXR_EXECUTOR = ThreadPoolExecutor(max_workers=64, thread_name_prefix="xdxr-sync")

_XDXR_COLUMNS = (
    "year",
    "month",
    "day",
    "category",
    "name",
    "fenhong",
    "peigujia",
    "songzhuangu",
    "peigu",
    "suogu",
    "panqianliutong",
    "panhouliutong",
    "qianzongguben",
    "houzongguben",
    "fenshu",
    "xingquanjia",
)


def _parse_sync_time(value: str):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _is_recent_successful_sync(state: dict, cooldown_hours: int = 24) -> bool:
    if not state or state.get("last_error"):
        return False
    synced_at = _parse_sync_time(
        state.get("last_success_at") or state.get("last_attempt_at") or ""
    )
    if not synced_at:
        return False
    return datetime.now() - synced_at < timedelta(hours=cooldown_hours)


def _optional_float(value):
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return None


def _optional_int(value):
    parsed = _optional_float(value)
    return int(parsed) if parsed is not None else None


def _normalize_xdxr_records(code: str, records: list[dict]) -> list[dict]:
    if not records:
        return []

    rows = []
    for row in records:
        year = _optional_int(row.get("year"))
        month = _optional_int(row.get("month"))
        day = _optional_int(row.get("day"))
        category = _optional_int(row.get("category"))
        if year is None or month is None or day is None or category is None:
            continue
        rows.append({
            "code": code,
            "date": f"{year:04d}-{month:02d}-{day:02d}",
            "category": category,
            "name": str(row.get("name") or "").strip() or None,
            "fenhong": _optional_float(row.get("fenhong")),
            "peigujia": _optional_float(row.get("peigujia")),
            "songzhuangu": _optional_float(row.get("songzhuangu")),
            "peigu": _optional_float(row.get("peigu")),
            "suogu": _optional_float(row.get("suogu")),
            "panqianliutong": _optional_float(row.get("panqianliutong")),
            "panhouliutong": _optional_float(row.get("panhouliutong")),
            "qianzongguben": _optional_float(row.get("qianzongguben")),
            "houzongguben": _optional_float(row.get("houzongguben")),
            "fenshu": _optional_float(row.get("fenshu")),
            "xingquanjia": _optional_float(row.get("xingquanjia")),
        })

    rows.sort(key=lambda item: (item["date"], item["category"]))
    return rows


async def fetch_stock_xdxr(code: str) -> tuple[list[dict], str]:
    """从共享 TDX 入口抓取单只股票的 xdxr 事件。"""
    try:
        loop = asyncio.get_running_loop()
        records, source = await loop.run_in_executor(
            _XDXR_EXECUTOR,
            lambda: call_tdx_quotes_with_retry(
                lambda client: client.xdxr_records(symbol=code),
                action_name=f"xdxr[{code}]",
            ),
        )
    except Exception as exc:
        logger.debug(f"[xdxr] {code} 失败: {exc}")
        raise

    return _normalize_xdxr_records(code, records), source


async def sync_xdxr_for_codes(mkt_conn, codes: list[str], *,
                              cooldown_hours: int = 24,
                              should_stop=None,
                              progress_callback=None,
                              concurrency: Optional[int] = None,
                              progress_every: Optional[int] = None) -> dict:
    """同步 tracked 股票的 xdxr 事件到 market.duckdb。"""
    existing = {row["code"]: row for row in get_all_xdxr_sync_states(mkt_conn)}
    to_fetch = [
        code for code in codes
        if not _is_recent_successful_sync(existing.get(code), cooldown_hours=cooldown_hours)
    ]
    if concurrency is None:
        concurrency = max(32, min(64, max(1, len(iter_tdx_servers())) * 8))
    if progress_every is None:
        progress_every = 10 if len(to_fetch) >= 10 else 1

    result = {
        "status": "running" if to_fetch else "skipped",
        "done_codes": 0,
        "total_codes": len(to_fetch),
        "success_codes": 0,
        "rows": 0,
        "failed_count": 0,
        "failed_codes": [],
        "skipped_recent": len(codes) - len(to_fetch),
        "concurrency": concurrency,
    }

    def _emit_progress(force: bool = False):
        if not progress_callback:
            return
        if not force and result["done_codes"] != result["total_codes"] and result["done_codes"] % progress_every != 0:
            return
        progress_callback({
            **result,
            "failed_codes": result["failed_codes"][:20],
        })

    if not to_fetch:
        _emit_progress(force=True)
        return result

    sem = asyncio.Semaphore(concurrency)

    async def _fetch_one(code: str) -> dict:
        async with sem:
            if should_stop:
                should_stop()
            try:
                rows, source = await fetch_stock_xdxr(code)
                return {
                    "code": code,
                    "rows": rows,
                    "source": source,
                    "error": None,
                }
            except Exception as exc:
                if should_stop:
                    should_stop()
                return {
                    "code": code,
                    "rows": [],
                    "source": None,
                    "error": str(exc)[:200],
                }

    tasks = [asyncio.create_task(_fetch_one(code)) for code in to_fetch]
    try:
        for task in asyncio.as_completed(tasks):
            payload = await task
            code = payload["code"]
            rows = payload["rows"]
            source = payload["source"]
            error = payload["error"]
            if error:
                result["failed_codes"].append(code)
                update_xdxr_sync_state(
                    mkt_conn,
                    code,
                    error=error,
                )
                logger.warning(f"[xdxr] {code} 同步失败: {error}")
            else:
                replace_xdxr_rows(mkt_conn, code, rows, source=source)
                dates = [row["date"] for row in rows]
                update_xdxr_sync_state(
                    mkt_conn,
                    code,
                    source=source,
                    min_date=min(dates) if dates else None,
                    max_date=max(dates) if dates else None,
                    row_count=len(rows),
                )
                result["success_codes"] += 1
                result["rows"] += len(rows)

            result["done_codes"] += 1
            result["failed_count"] = len(result["failed_codes"])
            _emit_progress()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    if result["failed_count"]:
        result["status"] = "partial"
    else:
        result["status"] = "success"
    result["failed_codes"] = result["failed_codes"][:20]
    _emit_progress(force=True)
    return result
