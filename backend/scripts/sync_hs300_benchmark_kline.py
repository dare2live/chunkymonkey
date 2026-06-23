#!/usr/bin/env python3
"""Sync HS300 benchmark K-line with explicit source lineage.

The stock feature panel uses the real HS300 index (000300) as the preferred
market-regime benchmark and falls back to the tradable ETF proxy (510300) only
when index data is unavailable. This script keeps 000300 available in the
canonical K-line view:

1. try TDXHub index bars first (仅用于 staleness 判断, 不再落库);
2. if TDXHub is missing or stale, fetch a bounded fallback from CSIndex/Sina;
3. write fallback rows to price_kline (akshare 备援链).

2026-06-23 M3: tdx-write 到 price_kline_tdxhub 已 neuter (该表退役物删)。benchmark 000300 主源
已切 raw_tushare_index_daily (registry 同步 2005~now); 本脚本仅留 price_kline 备援, 待 benchmark
消费侧 (paper_engine/return_engine) 全切 tushare index_daily 后整体退役。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import multiprocessing as mp
import os
import queue
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.akshare_client import fetch_index_kline
from services.db import get_conn as get_business_conn
from services.kline_source import records_from_payload
from services.market_db import (
    get_market_conn,
    init_market_db,
    upsert_price_rows,
)
from services.utils import latest_completed_trade_date

logger = logging.getLogger("sync_hs300_benchmark")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


def _date_yyyymmdd(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-":
        return text[:10].replace("-", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    raise ValueError(f"invalid date: {value!r}")


def _date_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "strftime") and not isinstance(value, str):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] in {"-", "/"}:
        return text[:10].replace("/", "-")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_fallback_rows(payload: Any, *, code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    start_iso = _date_iso(start_date)
    end_iso = _date_iso(end_date)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in records_from_payload(payload):
        compact = {str(key).replace(" ", ""): value for key, value in raw.items()}
        day = _date_iso(compact.get("日期") or compact.get("date") or compact.get("datetime"))
        if not day or day in seen:
            continue
        if start_iso and day < start_iso:
            continue
        if end_iso and day > end_iso:
            continue
        open_ = _float_or_none(compact.get("开盘") or compact.get("open"))
        high = _float_or_none(compact.get("最高") or compact.get("high"))
        low = _float_or_none(compact.get("最低") or compact.get("low"))
        close = _float_or_none(compact.get("收盘") or compact.get("close"))
        if open_ is None or high is None or low is None or close is None:
            continue
        volume = _float_or_none(compact.get("成交量") or compact.get("volume"))
        amount = _float_or_none(compact.get("成交金额") or compact.get("amount"))
        if amount is None:
            amount = volume
        rows.append({
            "code": code,
            "date": day,
            "freq": "daily",
            "adjust": "qfq",
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
        })
        seen.add(day)
    rows.sort(key=lambda row: row["date"])
    return rows


def _fallback_worker(queue: Any, source_name: str, code: str, start_date: str, end_date: str) -> None:
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

    import akshare as ak

    try:
        if source_name == "csindex":
            payload = ak.stock_zh_index_hist_csindex(
                symbol=code,
                start_date=start_date,
                end_date=end_date,
            )
        elif source_name == "sina":
            payload = ak.stock_zh_index_daily(symbol=f"sh{code}")
        else:
            raise ValueError(f"unsupported fallback source: {source_name}")
        queue.put({"ok": True, "source": source_name, "records": records_from_payload(payload)})
    except Exception as exc:
        queue.put({"ok": False, "source": source_name, "error": f"{type(exc).__name__}: {exc}"})


def _fetch_fallback_bounded(
    *,
    code: str,
    start_date: str,
    end_date: str,
    timeout_s: float,
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
    ctx = mp.get_context(method)
    for source_name in ("csindex", "sina"):
        result_queue = ctx.Queue()
        started = time.monotonic()
        logger.info("fallback stage start: source=%s timeout_s=%.1f", source_name, timeout_s)
        proc = ctx.Process(target=_fallback_worker, args=(result_queue, source_name, code, start_date, end_date))
        proc.start()
        result = None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                result = result_queue.get(timeout=0.2)
                break
            except queue.Empty:
                if not proc.is_alive():
                    break
        proc.join(timeout=1.0)
        if result is None and proc.is_alive():
            proc.terminate()
            proc.join()
            attempts.append({
                "source": source_name,
                "ok": False,
                "elapsed_sec": round(time.monotonic() - started, 3),
                "error": f"timeout after {timeout_s}s",
            })
            logger.warning("fallback stage timeout: source=%s elapsed=%.3fs", source_name, time.monotonic() - started)
            continue
        if result is None:
            result = {"ok": False, "source": source_name, "error": "empty child result"}
        rows = _normalize_fallback_rows(
            result.get("records") if result.get("ok") else [],
            code=code,
            start_date=start_date,
            end_date=end_date,
        )
        attempts.append({
            "source": source_name,
            "ok": bool(rows),
            "elapsed_sec": round(time.monotonic() - started, 3),
            "rows": len(rows),
            "error": None if rows else result.get("error") or "empty",
        })
        logger.info(
            "fallback stage done: source=%s rows=%d elapsed=%.3fs",
            source_name,
            len(rows),
            time.monotonic() - started,
        )
        if rows:
            return rows, source_name, attempts
    return [], "", attempts


def _resolve_end_date(end_date: str | None) -> str:
    if end_date:
        return _date_yyyymmdd(end_date)
    conn = get_business_conn(timeout=30)
    try:
        latest = latest_completed_trade_date(conn)
    finally:
        conn.close()
    if not latest:
        raise RuntimeError("dim_trading_calendar has no completed trading day")
    return _date_yyyymmdd(latest)


async def run_sync(args: argparse.Namespace) -> dict[str, Any]:
    start_date = _date_yyyymmdd(args.start)
    end_date = _resolve_end_date(args.end)
    batch_id = args.batch_id or f"hs300_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    init_market_db()
    market_conn = get_market_conn(timeout=120)
    t0 = time.monotonic()
    logger.info("tdx stage start: code=%s start=%s end=%s", args.code, start_date, end_date)
    tdx_rows, tdx_source = await fetch_index_kline(args.code, start_date, end_date)
    logger.info(
        "tdx stage done: source=%s rows=%d elapsed=%.3fs",
        tdx_source,
        0 if not tdx_rows else len(tdx_rows),
        time.monotonic() - t0,
    )
    tdx_rows = tdx_rows or []
    tdx_written = 0
    fallback_rows: list[dict[str, Any]] = []
    fallback_source = ""
    fallback_attempts: list[dict[str, Any]] = []
    try:
        # 2026-06-23 M3: 已 neuter tdx-write 到 price_kline_tdxhub (该表退役物删, 0 serving 读者)。
        # tdx_rows 仍取数 → 仅驱动下方 staleness 判断决定是否触发 akshare→price_kline 备援。
        # benchmark 主源已切 raw_tushare_index_daily (registry 同步 2005~now); 此脚本仅留 price_kline
        # 备援链, 待 benchmark 消费侧 (paper_engine/return_engine) 全切 tushare index_daily 后整体退役。
        tdx_written = 0  # rule-compliance: ok evidence=M3 neuter, price_kline_tdxhub 退役不再写
        latest_tdx = max((row["date"] for row in tdx_rows), default=None)
        if not latest_tdx or latest_tdx.replace("-", "") < end_date:
            fallback_rows, fallback_source, fallback_attempts = _fetch_fallback_bounded(
                code=args.code,
                start_date=start_date,
                end_date=end_date,
                timeout_s=args.fallback_timeout,
            )
            if fallback_rows:
                upsert_price_rows(
                    market_conn,
                    fallback_rows,
                    source=f"akshare_{fallback_source}_hs300",
                    batch_id=batch_id,
                )
    finally:
        market_conn.close()

    latest = max(
        [row["date"] for row in tdx_rows]
        + [row["date"] for row in fallback_rows],
        default=None,
    )
    result = {
        "status": "ok" if latest and latest.replace("-", "") >= end_date else "stale_or_missing",
        "code": args.code,
        "start_date": start_date,
        "end_date": end_date,
        "latest_date": latest,
        "tdx_source": tdx_source,
        "tdx_rows": len(tdx_rows),
        "tdx_written": tdx_written,
        "fallback_source": fallback_source,
        "fallback_rows": len(fallback_rows),
        "fallback_attempts": fallback_attempts,
        "batch_id": batch_id,
        "elapsed_sec": round(time.monotonic() - t0, 3),
    }
    logger.info("sync result: %s", json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync HS300 benchmark K-line into canonical market DB")
    parser.add_argument("--code", default="000300")
    parser.add_argument("--start", default="20220101")
    parser.add_argument("--end", default=None)
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--fallback-timeout", type=float, default=15.0)
    return parser.parse_args()


def main() -> int:
    result = asyncio.run(run_sync(parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
