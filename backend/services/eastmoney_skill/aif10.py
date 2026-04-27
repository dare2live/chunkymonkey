"""东财妙想 F10 (datacenter.eastmoney.com/securities/api/data/v1/get) 通用 client.

与 Phase 1 的 datacenter-web 区别:
- 子域: datacenter (vs datacenter-web)
- Path: /securities/api/data/v1/get (vs /api/data/v1/get)
- source=HSF10&client=PC (vs source=WEB&client=WEB)
- 主键过滤: SECUCODE='600519.SH' (vs SECURITY_CODE='600519')
- Referer: emweb.eastmoney.com (vs data.eastmoney.com)

适用场景: 单股 F10 16 个模块 (财务/股东/估值/事件等纵向 F10 数据).
完整 spec: docs/eastmoney-aif10-spec.md

参考: 用户 2026-04-27 浏览器抓包调研报告 (14 个一级模块全覆盖).
"""
from __future__ import annotations

from typing import Any, Iterator, Literal

from .client import EastMoneyClient, default_client


# v1 接口 (主用): 标准 result.{pages, data, count} 包裹
HSF10_URL_V1 = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
# v0 接口 (财报老接口): 直接返回 list, 字段名不同 (type vs reportName, sty 必传)
HSF10_URL_V0 = "https://datacenter.eastmoney.com/securities/api/data/get"

# 妙想 F10 必需的 referer + UA 防反爬
_HSF10_HEADERS = {
    "Referer": "https://emweb.eastmoney.com/",
}

# 交易所后缀映射 (6 位 code → secucode)
_MARKET_TO_SUFFIX = {
    "sh": "SH",
    "sz": "SZ",
    "bj": "BJ",
    "hk": "HK",
}


def to_secucode(stock_code: str, market: str | None = None) -> str:
    """6 位代码 + 市场 → 带后缀 SECUCODE (e.g. '600519.SH').

    market 为 None 时按代码推断:
        6/5/9 开头 → SH
        其他 → SZ
    """
    code = str(stock_code).strip()
    if "." in code:
        return code.upper()
    if market:
        suffix = _MARKET_TO_SUFFIX.get(market.lower())
        if suffix:
            return f"{code}.{suffix}"
    # 推断
    if code.startswith(("5", "6", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def call_hsf10(
    report_name: str,
    *,
    secucode: str | None = None,
    page: int = 1,
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "",
    columns: str = "ALL",
    extra_filters: list[str] | None = None,
    filter_expr: str | None = None,
    extra_params: dict[str, Any] | None = None,
    client: EastMoneyClient | None = None,
) -> dict[str, Any]:
    """调用妙想 F10 v1 接口, 返回 {pages, data, count}.

    Args:
        report_name: reportName (如 RPT_F10_EH_HOLDERS)
        secucode: 主键 (e.g. '600519.SH'), None 则不加 SECUCODE 过滤 (按 reportName 决定)
        page: 1-based
        page_size: 默认 50 (HSF10 单页通常较小)
        sort_columns / sort_types: 排序
        columns: 字段, 默认 'ALL'
        extra_filters: 额外 filter 条件 list[str], 如 ['(REPORT_DATE>"2024-01-01")']
        filter_expr: 完整 filter 字符串 (覆盖 secucode + extra_filters), 高级用法
        extra_params: 额外 query params

    Returns: {pages: int, data: list[dict], count: int}
    """
    cli = client or default_client

    # 构造 filter
    if filter_expr is None:
        parts = []
        if secucode:
            parts.append(f'(SECUCODE="{secucode}")')
        if extra_filters:
            parts.extend(extra_filters)
        filter_expr = "".join(parts) if parts else ""

    params: dict[str, Any] = {
        "reportName": report_name,
        "columns": columns,
        "pageNumber": page,
        "pageSize": page_size,
        "source": "HSF10",
        "client": "PC",
    }
    if filter_expr:
        params["filter"] = filter_expr
    if sort_columns:
        params["sortColumns"] = sort_columns
    if sort_types:
        params["sortTypes"] = sort_types
    if extra_params:
        params.update(extra_params)

    resp = cli.get_json(HSF10_URL_V1, params=params, headers=_HSF10_HEADERS)
    result = resp.get("result")
    if not result or not isinstance(result, dict):
        return {"pages": 0, "data": [], "count": 0}
    return {
        "pages": int(result.get("pages") or 0),
        "data": list(result.get("data") or []),
        "count": int(result.get("count") or 0),
    }


def call_hsf10_v0(
    type_name: str,
    sty: str,
    *,
    secucode: str | None = None,
    page: int = 1,
    page_size: int = 200,
    sort_columns: str = "REPORT_DATE",
    sort_types: int = -1,
    extra_filters: list[str] | None = None,
    filter_expr: str | None = None,
    extra_params: dict[str, Any] | None = None,
    client: EastMoneyClient | None = None,
) -> list[dict]:
    """调用妙想 F10 v0 接口 (财报老接口), 返回 list[dict].

    用于 type=RPT_F10_FINANCE_MAINFINADATA 这类老接口, 必须配套 sty 参数.
    返回直接是数据 list, 不再包 result.{pages,data,count}.
    """
    cli = client or default_client

    if filter_expr is None:
        parts = []
        if secucode:
            parts.append(f'(SECUCODE="{secucode}")')
        if extra_filters:
            parts.extend(extra_filters)
        filter_expr = "".join(parts) if parts else ""

    params: dict[str, Any] = {
        "type": type_name,
        "sty": sty,
        "p": page,
        "ps": page_size,
        "sr": sort_types,
        "st": sort_columns,
        "source": "HSF10",
        "client": "PC",
    }
    if filter_expr:
        params["filter"] = filter_expr
    if extra_params:
        params.update(extra_params)

    resp = cli.get_json(HSF10_URL_V0, params=params, headers=_HSF10_HEADERS)
    # v0 接口返回结构: {data: [...], success: bool, ...} 或直接 list
    if isinstance(resp, list):
        return resp
    return list(resp.get("data") or [])


def fetch_all_pages(
    report_name: str,
    *,
    secucode: str | None = None,
    page_size: int = 50,
    max_pages: int = 0,
    sort_columns: str = "",
    sort_types: str = "",
    columns: str = "ALL",
    extra_filters: list[str] | None = None,
    extra_params: dict[str, Any] | None = None,
    client: EastMoneyClient | None = None,
) -> list[dict]:
    """分页拉全部数据 (v1 接口)."""
    rows: list[dict] = []
    page = 1
    while True:
        result = call_hsf10(
            report_name,
            secucode=secucode,
            page=page,
            page_size=page_size,
            sort_columns=sort_columns,
            sort_types=sort_types,
            columns=columns,
            extra_filters=extra_filters,
            extra_params=extra_params,
            client=client,
        )
        rows.extend(result["data"])
        total = result["pages"]
        if total <= page or page >= total:
            break
        if max_pages and page >= max_pages:
            break
        page += 1
    return rows


# ---------------------------------------------------------------------------
# 高频接口快捷封装 (Phase 2 第一批: 5 个最关键的)
# ---------------------------------------------------------------------------


def fetch_company_type(secucode: str, *, client: EastMoneyClient | None = None) -> dict | None:
    """财报模板分类 (一般/银行/保险/券商).

    入库前必查, 决定字段集. 返回 None 表示未匹配.
    """
    rows = fetch_all_pages(
        report_name="RPT_F10_PUBLIC_COMPANYTPYE",
        secucode=secucode,
        page_size=1,
        client=client,
    )
    return rows[0] if rows else None


def fetch_main_finance_indicators(
    secucode: str,
    *,
    page_size: int = 8,
    client: EastMoneyClient | None = None,
) -> list[dict]:
    """主要财务指标 (按报告期, 最近 page_size 期).

    含 EPS / ROE / 毛利率 / 营收同比 / 归母净利同比 等数十项.
    服务端通常保留 8 期历史.
    """
    return fetch_all_pages(
        report_name="RPT_PCF10_FINANCEMAINFINADATA",
        secucode=secucode,
        page_size=page_size,
        sort_columns="REPORT_DATE",
        sort_types="-1",
        client=client,
    )


def fetch_top_holders(
    secucode: str,
    *,
    end_date: str | None = None,
    page_size: int = 10,
    client: EastMoneyClient | None = None,
) -> list[dict]:
    """十大股东 (RPT_F10_EH_HOLDERS).

    end_date: 'YYYY-MM-DD' 报告期, 默认最新.
    """
    extra = [f'(END_DATE="{end_date}")'] if end_date else None
    return fetch_all_pages(
        report_name="RPT_F10_EH_HOLDERS",
        secucode=secucode,
        page_size=page_size,
        sort_columns="HOLDER_RANK",
        sort_types="1",
        extra_filters=extra,
        client=client,
    )


def fetch_top_free_holders(
    secucode: str,
    *,
    end_date: str | None = None,
    page_size: int = 10,
    client: EastMoneyClient | None = None,
) -> list[dict]:
    """十大流通股东 (RPT_F10_EH_FREEHOLDERS).

    end_date: 'YYYY-MM-DD' 报告期, 默认最新.
    """
    extra = [f'(END_DATE="{end_date}")'] if end_date else None
    return fetch_all_pages(
        report_name="RPT_F10_EH_FREEHOLDERS",
        secucode=secucode,
        page_size=page_size,
        sort_columns="HOLDER_RANK",
        sort_types="1",
        extra_filters=extra,
        client=client,
    )


def fetch_valuation_quantile(
    secucode: str,
    *,
    client: EastMoneyClient | None = None,
) -> list[dict]:
    """估值分位 (PE/PB 30/50/70 分位)."""
    return fetch_all_pages(
        report_name="RPT_STOCKVALUATIONTANTILE",
        secucode=secucode,
        page_size=10,
        client=client,
    )


def fetch_basic_org_info(
    secucode: str,
    *,
    client: EastMoneyClient | None = None,
) -> dict | None:
    """公司基本资料 (董事长/办公地址/经营范围/律所等)."""
    rows = fetch_all_pages(
        report_name="RPT_F10_BASIC_ORGINFO",
        secucode=secucode,
        page_size=1,
        client=client,
    )
    return rows[0] if rows else None
