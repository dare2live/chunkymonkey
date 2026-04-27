"""妙想 F10 HTTP client.

工程要点 (相对 akshare 直接 requests.get):
- Session(trust_env=False): 避代理 (Surge/Clash)
- timeout / retry / Referer / UA / rate-limit
- v1 接口为主, v0 接口 (财报老 endpoint) 兼容
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger("aif10_scraper")


# v1: 标准 result.{pages, data, count} 包裹
URL_V1 = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
# v0: 财报老接口, 直接返回 list, 用 type/sty/p/ps 参数
URL_V0 = "https://datacenter.eastmoney.com/securities/api/data/get"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://emweb.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class AIF10Error(Exception):
    """妙想 F10 接口错误."""


class AIF10Client:
    """同步客户端.

    用法:
        client = AIF10Client()
        result = client.get_v1('RPT_STOCKVALUATIONTANTILE', page=1, page_size=500)
    """

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        retry: int = 3,
        retry_backoff: float = 1.5,
        rate_limit: float = 0.0,
        trust_env: bool = False,
        extra_headers: dict[str, str] | None = None,
    ):
        self.timeout = timeout
        self.retry = retry
        self.retry_backoff = retry_backoff
        self.rate_limit = rate_limit
        self._session = requests.Session()
        self._session.trust_env = trust_env
        self._session.headers.update(DEFAULT_HEADERS)
        if extra_headers:
            self._session.headers.update(extra_headers)
        self._last_request_at = 0.0

    def _rate_limit_wait(self):
        if self.rate_limit > 0:
            elapsed = time.time() - self._last_request_at
            if elapsed < self.rate_limit:
                time.sleep(self.rate_limit - elapsed)

    def _request_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        self._rate_limit_wait()
        last_exc: Exception | None = None
        for attempt in range(self.retry + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                self._last_request_at = time.time()
                if resp.status_code < 400:
                    return resp.json()
                if 400 <= resp.status_code < 500:
                    raise AIF10Error(
                        f"HTTP {resp.status_code} {url}: {resp.text[:200]}"
                    )
                last_exc = AIF10Error(
                    f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.HTTPError,
                ValueError,
            ) as exc:
                last_exc = exc
            if attempt < self.retry:
                sleep_s = self.retry_backoff ** attempt
                logger.debug(f"[aif10] {url} retry {attempt+1} (sleep {sleep_s:.1f}s)")
                time.sleep(sleep_s)
        raise AIF10Error(f"GET {url} 失败 (重试 {self.retry} 次): {last_exc}") from last_exc

    def get_v1(
        self,
        report_name: str,
        *,
        page: int = 1,
        page_size: int = 500,
        sort_columns: str = "",
        sort_types: str = "",
        columns: str = "ALL",
        secucode: str | None = None,
        extra_filters: list[str] | None = None,
        filter_expr: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """v1 通用调用.

        返回: {pages: int, data: list[dict], count: int}
        """
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

        resp = self._request_json(URL_V1, params)
        result = resp.get("result")
        if not result or not isinstance(result, dict):
            return {"pages": 0, "data": [], "count": 0}
        return {
            "pages": int(result.get("pages") or 0),
            "data": list(result.get("data") or []),
            "count": int(result.get("count") or 0),
        }

    def get_v0(
        self,
        type_name: str,
        sty: str,
        *,
        page: int = 1,
        page_size: int = 200,
        sort_columns: str = "REPORT_DATE",
        sort_types: int = -1,
        secucode: str | None = None,
        extra_filters: list[str] | None = None,
        filter_expr: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> list[dict]:
        """v0 老接口调用 (财报历史).

        返回: list[dict] (无 pagination, 服务端直接返回数据 list)
        """
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

        resp = self._request_json(URL_V0, params)
        if isinstance(resp, list):
            return resp
        return list(resp.get("data") or [])

    def close(self):
        self._session.close()


# 模块级共享实例
default_client = AIF10Client()
