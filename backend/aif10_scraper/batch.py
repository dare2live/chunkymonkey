"""批量分页 + 并发抓取.

利用妙想 F10 的"全市场天然支持分页"特性, 不需要逐 SECUCODE 拉.

实测 (2026-04-27):
- RPT_STOCKVALUATIONTANTILE: 187 页 × 500 = 93285 行 / page=0.3s → 顺序 60s
- RPT_F10_EH_HOLDERNUM: 1473 页 × 500 = 736323 行 → 顺序 7 min
- RPT_PCF10_INDUSTRY_CVALUE: 90 页 × 500 = 44651 行 → 顺序 30s

并发实测(上游 dare2live/aif10-scraper@a1501e3 ``STRESS_TEST_RESULTS.md``, 未并入):
单 IP、concurrency 1/2/5/10/20/30/50/100 全部 200 OK, **没有触发 rc=100 / rate-limit,
服务端不限流**; 甜蜜点 20(26.2 页/秒, 13K 行/秒), 30+ 反而变慢 —— 天花板是客户端 TCP
连接池 + 服务端处理排队, 不是限流。datacenter 子域不在东财反爬黑名单(与 push2his 相反)。

并入本仓时改: 原文写「并发能加速但**单 IP 有限流**, 见 上游@a1501e3 stress/concurrency_test.py」——
那是压测**前**的假设(那个脚本的验收标准就是「最高 QPS 不触发 rc=100」), 而压测结论正好
相反。指向的 stress/ 目录也没并入。一句被自己的证据否掉的话留在这里, 下一个人会据此
不敢开并发。

注: 本仓生产路径只走 ``fetch_all_pages`` / ``fetch_all_pages_sharded``, 实测
``fetch_all_pages_concurrent`` 在 backend/ 里零调用方, 所以 sync_registry.yaml 的
sources.miaoxiang 不声明 max_concurrency(没有 loader 读的 typed 键是假执法)。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Iterator, Callable

from .client import AIF10Client, default_client
from .pagination import (
    DEFAULT_MAX_PAGES_PER_QUERY,
    PaginationPolicy,
    STRICT_POLICY_FOR,
    fetch_pages_for_filters,
    fetch_pages_strict,
    plan_security_code_shards,
)
from .registry import ReportSpec, get_report

logger = logging.getLogger("aif10_scraper")


def _resolve_sort(report_name: str, sort_columns: str, sort_types: str) -> tuple[str, str]:
    try:
        spec = get_report(report_name)
        if not sort_columns:
            sort_columns = spec.sort_columns
        if not sort_types:
            sort_types = spec.sort_types
    except KeyError as exc:
        # 并入本仓时改: 原上游是 ``except KeyError: pass``。get_report 失败 -> spec 为 None
        # -> sort_columns/sort_types 落空 -> 分页**无序**, 同一行可能重复落在两页、另一行
        # 一页不落。下游按 grain 去重后表现为「静默少数据」而非报错, 撞红线「缺失只能传播
        # 为缺失」。实测本仓在用的 3 个 report 全部已注册, 这条分支走不到; 走到就该响。
        raise KeyError(
            f"report {report_name!r} 未注册: 取不到 sort_columns/sort_types, "
            f"分页会无序 -> 静默重复/漏行。先在 aif10_scraper.registry 里登记它。"
        ) from exc
    return sort_columns, sort_types


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
    policy: PaginationPolicy | None = None,
) -> list[dict]:
    """单线程顺序分页拉全量 (v1 接口), 严格判据 (2026-09-25 刀 A)。

    ``policy`` 缺省 (``None``) 时用 ``STRICT_POLICY_FOR(registry 排序键)``
    (容差 0、无身份列、重复即错、不重取) —— 不读 ``aif10_pagination.yaml``,
    未登记报表的调用方 (org / qfii / Phase A 取数器) 不受影响。任何完整性判据
    失败都原样抛出 ``PaginationIntegrityError`` (旧版"只打 warning"的那段已删除,
    静默截断不该只留一行日志)。

    progress_callback(page, total_pages, rows_so_far): 每页回调.
    """
    cli = client or default_client
    sort_columns, sort_types = _resolve_sort(report_name, sort_columns, sort_types)
    if policy is None:
        policy = STRICT_POLICY_FOR(sort_columns, sort_types)

    rows, _ledger = fetch_pages_strict(
        cli,
        report_name,
        page_size=page_size,
        policy=policy,
        columns=columns,
        secucode=secucode,
        extra_filters=extra_filters,
        extra_params=extra_params,
        max_pages=max_pages or 0,
        max_pages_per_query=DEFAULT_MAX_PAGES_PER_QUERY,
        progress_callback=progress_callback,
    )
    return rows


def fetch_all_pages_sharded(
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
    max_pages_per_query: int = DEFAULT_MAX_PAGES_PER_QUERY,
    shard_field: str = "SECURITY_CODE",
    policy: PaginationPolicy | None = None,
) -> dict[str, Any]:
    """Paginated fetch with SECURITY_CODE sharding when page-1 count exceeds cap.

    每片走 ``fetch_pages_for_filters`` (薄壳, 固定 STRICT 容差 —— 每片内部必须
    精确)；``policy`` (缺省 ``STRICT_POLICY_FOR(registry 排序键)``, 不读
    ``aif10_pagination.yaml``) 只用于合并后的总量比对，容差来自 ``policy.
    row_tolerance_rows`` 而不是旧版硬编码的 ``0.002``/``500`` 字面量 (已随
    ``assess_pagination_land`` 一起删除)。

    Returns:
        rows, provider_count, fetched_rows, truncated, shard_count, land_reasons
    """
    cli = client or default_client
    sort_columns, sort_types = _resolve_sort(report_name, sort_columns, sort_types)

    base = list(extra_filters or [])
    if shard_field != "SECURITY_CODE":
        raise ValueError(f"unsupported shard_field={shard_field!r}")

    if policy is None:
        policy = STRICT_POLICY_FOR(sort_columns, sort_types)

    shard_plans = plan_security_code_shards(
        cli,
        report_name,
        base_filters=base,
        page_size=page_size,
        max_pages_per_query=max_pages_per_query,
        sort_columns=sort_columns,
        sort_types=sort_types,
        columns=columns,
        secucode=secucode,
        extra_params=extra_params,
    )
    all_rows: list[dict] = []
    expected_total = 0
    truncated = False
    reasons: list[str] = []
    for plan in shard_plans:
        shard_rows, land = fetch_pages_for_filters(
            cli,
            report_name,
            page_size=page_size,
            max_pages=max_pages or 0,
            sort_columns=sort_columns,
            sort_types=sort_types,
            columns=columns,
            secucode=secucode,
            extra_filters=plan,
            extra_params=extra_params,
            progress_callback=progress_callback,
            max_pages_per_query=max_pages_per_query,
        )
        all_rows.extend(shard_rows)
        expected_total += land.expected_count
        if land.truncated:
            truncated = True
            reasons.extend(land.reasons)

    tolerance = policy.row_tolerance_rows
    if abs(len(all_rows) - expected_total) > tolerance:
        truncated = True
        reasons.append(
            f"sharded_raw_rows_ne_count landed={len(all_rows)} "
            f"expected={expected_total} tolerance={tolerance}"
        )

    return {
        "rows": all_rows,
        "provider_count": expected_total,
        "fetched_rows": len(all_rows),
        "truncated": truncated,
        "shard_count": len(shard_plans),
        "land_reasons": sorted(set(reasons)),
    }


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
            except Exception:  # noqa: BLE001 — 进度回调是显示层, 挂了不该中断取数
                # 并入本仓时改: 原上游是 pass。行已经收在 all_pages 里, 这里吞的只是显示,
                # 不影响完整性; 但吞掉不记 = 回调坏了没人知道。
                logger.warning("progress_callback 抛异常, 已忽略(不影响已取到的行)", exc_info=True)
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
    except KeyError as exc:
        # 并入本仓时改: 原上游是 ``except KeyError: pass``。get_report 失败 -> spec 为 None
        # -> sort_columns/sort_types 落空 -> 分页**无序**, 同一行可能重复落在两页、另一行
        # 一页不落。下游按 grain 去重后表现为「静默少数据」而非报错, 撞红线「缺失只能传播
        # 为缺失」。实测本仓在用的 3 个 report 全部已注册, 这条分支走不到; 走到就该响。
        raise KeyError(
            f"report {report_name!r} 未注册: 取不到 sort_columns/sort_types, "
            f"分页会无序 -> 静默重复/漏行。先在 aif10_scraper.registry 里登记它。"
        ) from exc

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
    except KeyError as exc:
        # 并入本仓时改: 原上游是 ``except KeyError: pass``。get_report 失败 -> spec 为 None
        # -> sort_columns/sort_types 落空 -> 分页**无序**, 同一行可能重复落在两页、另一行
        # 一页不落。下游按 grain 去重后表现为「静默少数据」而非报错, 撞红线「缺失只能传播
        # 为缺失」。实测本仓在用的 3 个 report 全部已注册, 这条分支走不到; 走到就该响。
        raise KeyError(
            f"report {report_name!r} 未注册: 取不到 sort_columns/sort_types, "
            f"分页会无序 -> 静默重复/漏行。先在 aif10_scraper.registry 里登记它。"
        ) from exc

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
