"""东财数据中心通用 RPC 客户端 (datacenter-web.eastmoney.com).

东财数据中心是 RESTful + reportName 模式. 所有接口都通过同一个 URL:
    https://datacenter-web.eastmoney.com/api/data/v1/get
不同 reportName 对应不同数据 (机构调研 / 龙虎榜 / QFII / 十大股东 / 财务等).

response 标准结构:
    {
        "result": {
            "pages": int,           # 总页数
            "data": [...],          # 当前页数据 (list[dict])
            "count": int            # 总条数
        }
    }

参考: akshare/stock_feature/stock_jgdy_em.py:stock_jgdy_tj_em
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

from .client import EastMoneyClient, default_client

logger = logging.getLogger("eastmoney_skill")

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def call_datacenter(
    report_name: str,
    *,
    page: int = 1,
    page_size: int = 500,
    sort_columns: str = "",
    sort_types: str = "",
    columns: str = "ALL",
    filter_expr: str | None = None,
    quote_columns: str | None = None,
    extra_params: dict[str, Any] | None = None,
    client: EastMoneyClient | None = None,
) -> dict[str, Any]:
    """调用 datacenter-web 单页, 返回原始 result dict.

    参数:
        report_name: 报表名 (如 RPT_ORG_SURVEYNEW)
        page: 页码 (1-based)
        page_size: 每页条数, 默认 500
        sort_columns: 排序列, 逗号分隔 (如 "NOTICE_DATE,SECURITY_CODE")
        sort_types: 排序方向, 逗号分隔 (-1=降序, 1=升序), 与 sort_columns 一一对应
        columns: 返回字段, 默认 "ALL"
        filter_expr: SQL-like 过滤 (如 '(NOTICE_DATE>"2026-01-01")')
        quote_columns: 关联行情列 (如 "f2~01~SECURITY_CODE~CLOSE_PRICE")
        extra_params: 额外 query params (覆盖默认)

    返回: {"pages": int, "data": [...], "count": int}
    """
    cli = client or default_client
    params: dict[str, Any] = {
        "reportName": report_name,
        "pageNumber": page,
        "pageSize": page_size,
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "columns": columns,
        "source": "WEB",
        "client": "WEB",
    }
    if filter_expr:
        params["filter"] = filter_expr
    if quote_columns:
        params["quoteColumns"] = quote_columns
    if extra_params:
        params.update(extra_params)

    resp = cli.get_json(DATACENTER_URL, params=params)
    result = resp.get("result")
    if not result or not isinstance(result, dict):
        # 东财在过滤无结果时 result 可能为 null
        return {"pages": 0, "data": [], "count": 0}
    return {
        "pages": int(result.get("pages") or 0),
        "data": list(result.get("data") or []),
        "count": int(result.get("count") or 0),
    }


def fetch_all_pages(
    report_name: str,
    *,
    page_size: int = 500,
    max_pages: int = 0,
    sort_columns: str = "",
    sort_types: str = "",
    columns: str = "ALL",
    filter_expr: str | None = None,
    quote_columns: str | None = None,
    extra_params: dict[str, Any] | None = None,
    client: EastMoneyClient | None = None,
    progress_callback=None,
) -> list[dict[str, Any]]:
    """分页拉全部数据, 返回拼接后的 list[dict].

    适用场景: 调研列表 / 龙虎榜区间 / QFII 季度持仓 等批量查询.
    page_size 默认 500 (东财上限通常 500 / 1000).
    max_pages > 0 时限制页数 (debug 用).
    progress_callback(page, total_pages) 每页回调一次.
    """
    cli = client or default_client
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        result = call_datacenter(
            report_name,
            page=page,
            page_size=page_size,
            sort_columns=sort_columns,
            sort_types=sort_types,
            columns=columns,
            filter_expr=filter_expr,
            quote_columns=quote_columns,
            extra_params=extra_params,
            client=cli,
        )
        rows.extend(result["data"])
        total = result["pages"]
        if progress_callback:
            try:
                progress_callback(page, total)
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
    page_size: int = 500,
    sort_columns: str = "",
    sort_types: str = "",
    columns: str = "ALL",
    filter_expr: str | None = None,
    quote_columns: str | None = None,
    extra_params: dict[str, Any] | None = None,
    client: EastMoneyClient | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """generator 版: 一页一页 yield, 节省内存."""
    cli = client or default_client
    page = 1
    while True:
        result = call_datacenter(
            report_name,
            page=page,
            page_size=page_size,
            sort_columns=sort_columns,
            sort_types=sort_types,
            columns=columns,
            filter_expr=filter_expr,
            quote_columns=quote_columns,
            extra_params=extra_params,
            client=cli,
        )
        if result["data"]:
            yield result["data"]
        total = result["pages"]
        if total <= page or page >= total:
            break
        page += 1
