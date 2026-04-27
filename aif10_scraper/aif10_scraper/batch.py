"""批量分页 + 并发抓取.

利用妙想 F10 的"全市场天然支持分页"特性, 不需要逐 SECUCODE 拉.

实测 (2026-04-27):
- RPT_STOCKVALUATIONTANTILE: 187 页 × 500 = 93285 行 / page=0.3s → 顺序 60s
- RPT_F10_EH_HOLDERNUM: 1473 页 × 500 = 736323 行 → 顺序 7 min
- RPT_PCF10_INDUSTRY_CVALUE: 90 页 × 500 = 44651 行 → 顺序 30s

并发能加速但单 IP 有限流, 见 stress/concurrency_test.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Iterator, Callable

from .client import AIF10Client, default_client
from .registry import ReportSpec, get_report

logger = logging.getLogger("aif10_scraper")


def fetch_all_pages(
    report_name: str,
    *,
    secucode: str | None = None,
    page_size: int = 500,
    max_pages: int = 0,
    sort_columns: str = "",
    sort_types: str = "",
    columns: str = "ALL",
    extra_filters: list[str] | None = None,
    extra_params: dict[str, Any] | None = None,
    client: AIF10Client | None = None,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> list[dict]:
    """单线程顺序分页拉全量 (v1 接口).

    progress_callback(page, total_pages, rows_so_far): 每页回调.
    """
    cli = client or default_client
    spec = None
    try:
        spec = get_report(report_name)
        if not sort_columns:
            sort_columns = spec.sort_columns
        if not sort_types:
            sort_types = spec.sort_types
    except KeyError:
        pass

    rows: list[dict] = []
    page = 1
    while True:
        result = cli.get_v1(
            report_name,
            page=page, page_size=page_size,
            sort_columns=sort_columns, sort_types=sort_types,
            columns=columns,
            secucode=secucode,
            extra_filters=extra_filters,
            extra_params=extra_params,
        )
        rows.extend(result["data"])
        total = result["pages"]
        if progress_callback:
            try:
                progress_callback(page, total, len(rows))
            except Exception:
                pass
        if total <= page or page >= total:
            break
        if max_pages and page >= max_pages:
            break
        page += 1
    return rows


def iter_pages(
    report_name: str,
    *,
    secucode: str | None = None,
    page_size: int = 500,
    sort_columns: str = "",
    sort_types: str = "",
    columns: str = "ALL",
    extra_filters: list[str] | None = None,
    extra_params: dict[str, Any] | None = None,
    client: AIF10Client | None = None,
) -> Iterator[list[dict]]:
    """generator: 一页一页 yield, 流式入库省内存."""
    cli = client or default_client
    page = 1
    while True:
        result = cli.get_v1(
            report_name,
            page=page, page_size=page_size,
            sort_columns=sort_columns, sort_types=sort_types,
            columns=columns,
            secucode=secucode,
            extra_filters=extra_filters,
            extra_params=extra_params,
        )
        if result["data"]:
            yield result["data"]
        total = result["pages"]
        if total <= page or page >= total:
            break
        page += 1


# ---------------------------------------------------------------------------
# 异步并发版本
# ---------------------------------------------------------------------------

async def fetch_page_async(
    semaphore: asyncio.Semaphore,
    sync_client: AIF10Client,
    report_name: str,
    page: int,
    *,
    page_size: int,
    sort_columns: str,
    sort_types: str,
    columns: str,
    secucode: str | None,
    extra_filters: list[str] | None,
    extra_params: dict[str, Any] | None,
) -> tuple[int, list[dict]]:
    """单页 fetch (asyncio + thread executor)."""
    async with semaphore:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: sync_client.get_v1(
                report_name,
                page=page, page_size=page_size,
                sort_columns=sort_columns, sort_types=sort_types,
                columns=columns,
                secucode=secucode,
                extra_filters=extra_filters,
                extra_params=extra_params,
            ),
        )
    return page, result["data"]


async def _fetch_all_pages_concurrent_async(
    report_name: str,
    *,
    secucode: str | None,
    page_size: int,
    max_pages: int,
    sort_columns: str,
    sort_types: str,
    columns: str,
    extra_filters: list[str] | None,
    extra_params: dict[str, Any] | None,
    concurrency: int,
    rate_limit_per_sec: float,
    client: AIF10Client | None,
    progress_callback: Callable[[int, int, int], None] | None,
) -> list[dict]:
    cli = client or default_client

    # 探针拿 total pages
    head = cli.get_v1(
        report_name, page=1, page_size=page_size,
        sort_columns=sort_columns, sort_types=sort_types,
        columns=columns, secucode=secucode,
        extra_filters=extra_filters, extra_params=extra_params,
    )
    total = head["pages"]
    if total <= 1:
        return head["data"]
    if max_pages and max_pages < total:
        total = max_pages

    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        fetch_page_async(
            semaphore, cli, report_name, p,
            page_size=page_size, sort_columns=sort_columns, sort_types=sort_types,
            columns=columns, secucode=secucode,
            extra_filters=extra_filters, extra_params=extra_params,
        )
        for p in range(2, total + 1)
    ]

    # 收集结果, 按 page 排序合并
    all_pages = {1: head["data"]}
    completed = 0
    for coro in asyncio.as_completed(tasks):
        page, rows = await coro
        all_pages[page] = rows
        completed += 1
        if progress_callback:
            try:
                progress_callback(completed + 1, total, sum(len(r) for r in all_pages.values()))
            except Exception:
                pass
        if rate_limit_per_sec > 0:
            await asyncio.sleep(1.0 / rate_limit_per_sec)

    out: list[dict] = []
    for p in sorted(all_pages.keys()):
        out.extend(all_pages[p])
    return out


def fetch_all_pages_concurrent(
    report_name: str,
    *,
    secucode: str | None = None,
    page_size: int = 500,
    max_pages: int = 0,
    sort_columns: str = "",
    sort_types: str = "",
    columns: str = "ALL",
    extra_filters: list[str] | None = None,
    extra_params: dict[str, Any] | None = None,
    concurrency: int = 5,
    rate_limit_per_sec: float = 0.0,
    client: AIF10Client | None = None,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> list[dict]:
    """并发分页拉全量.

    concurrency: 同时多少个请求 in-flight
    rate_limit_per_sec: 整体 QPS 上限 (>0 时启用), 0 = 不限
    """
    spec = None
    try:
        spec = get_report(report_name)
        if not sort_columns:
            sort_columns = spec.sort_columns
        if not sort_types:
            sort_types = spec.sort_types
    except KeyError:
        pass

    return asyncio.run(
        _fetch_all_pages_concurrent_async(
            report_name,
            secucode=secucode, page_size=page_size, max_pages=max_pages,
            sort_columns=sort_columns, sort_types=sort_types,
            columns=columns,
            extra_filters=extra_filters, extra_params=extra_params,
            concurrency=concurrency,
            rate_limit_per_sec=rate_limit_per_sec,
            client=client,
            progress_callback=progress_callback,
        )
    )


def fetch_report(
    report_name: str,
    *,
    mode: str = "auto",   # "sync" / "concurrent" / "auto"
    concurrency: int = 5,
    page_size: int = 500,
    secucode: str | None = None,
    max_pages: int = 0,
    extra_filters: list[str] | None = None,
    client: AIF10Client | None = None,
    progress_callback: Callable[[int, int, int], None] | None = None,
) -> dict:
    """高层封装: 一行代码拉某 reportName 全量.

    mode='auto': total pages > 10 自动用并发, 否则同步.

    返回: {report_name, total_rows, elapsed_s, rows: list[dict]}
    """
    cli = client or default_client
    spec = None
    try:
        spec = get_report(report_name)
    except KeyError:
        pass

    t0 = time.time()
    if mode == "sync":
        rows = fetch_all_pages(
            report_name, secucode=secucode, page_size=page_size,
            max_pages=max_pages, extra_filters=extra_filters,
            client=cli, progress_callback=progress_callback,
        )
    elif mode == "concurrent":
        rows = fetch_all_pages_concurrent(
            report_name, secucode=secucode, page_size=page_size,
            max_pages=max_pages, extra_filters=extra_filters,
            concurrency=concurrency, client=cli,
            progress_callback=progress_callback,
        )
    else:  # auto
        # 探针
        head = cli.get_v1(
            report_name, page=1, page_size=page_size,
            secucode=secucode, extra_filters=extra_filters,
        )
        total = head["pages"]
        if total <= 10 or (max_pages and max_pages <= 10):
            rows = fetch_all_pages(
                report_name, secucode=secucode, page_size=page_size,
                max_pages=max_pages, extra_filters=extra_filters,
                client=cli, progress_callback=progress_callback,
            )
        else:
            rows = fetch_all_pages_concurrent(
                report_name, secucode=secucode, page_size=page_size,
                max_pages=max_pages, extra_filters=extra_filters,
                concurrency=concurrency, client=cli,
                progress_callback=progress_callback,
            )

    elapsed = time.time() - t0
    return {
        "report_name": report_name,
        "total_rows": len(rows),
        "elapsed_s": round(elapsed, 2),
        "rows": rows,
    }
