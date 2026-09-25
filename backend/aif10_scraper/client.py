"""妙想 F10 HTTP client.

工程要点 (相对 akshare 直接 requests.get):
- Session(trust_env=False): 避代理 (Surge/Clash)
- timeout / retry / Referer / UA / rate-limit
- v1 接口为主, v0 接口 (财报老 endpoint) 兼容
"""
from __future__ import annotations

import logging
import time
from types import MappingProxyType
from typing import Any, Mapping

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

# 东财 v1 顶层 code 的闭合枚举 (2026-09-25 实测, spec_holders_pagination.md §1.1 P1~P5):
#   0    正常, result 携带 {pages, data, count}
#   9201 "返回数据为空" —— 真空 (第 1 页) 与"翻过末页" (第 ≥2 页) 同码, 只能靠页码区分
#   9501 结构性错误 (排序列/过滤列/报表不存在等)
# 新增成员必须同时在 tests/test_aif10_client.py::K12 登记 (「新成员必须有裁决」)。
KNOWN_RESPONSE_CODES: Mapping[int, str] = MappingProxyType(
    {0: "ok", 9201: "empty_result", 9501: "structural_error"}
)


class AIF10Error(Exception):
    """妙想 F10 接口错误."""


class AIF10BlockedError(AIF10Error):
    """HTTP 403 / 429 —— 被墙。不重试、不退避、立即抛 (§3.1)。"""


class AIF10NonJsonError(AIF10Error):
    """HTTP 2xx/3xx 但响应体不是合法 JSON (如 WAF 返回一页 HTML)。

    与 ``AIF10BlockedError`` 分开是修订 1 的 N7: 一页 WAF/HTML 页面只应让这一域
    STRUCTURAL 失败, 不必像真被封那样停整条 drain。立即抛, 不进 5xx 那条瞬态重试路径
    (现行会被 ``except ValueError`` 收进 3 次重试, 这里改掉)。
    """


class AIF10ApiError(AIF10Error):
    """``code == 9501``: 排序列 / 过滤列 / 报表不存在等结构性错误。不重试。"""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"aif10 api error code={code}: {message}")


class AIF10UnknownCodeError(AIF10Error):
    """``code`` 不在 ``KNOWN_RESPONSE_CODES`` 里 —— fail-closed: 不猜它是空还是错。"""

    def __init__(self, code: Any, message: str):
        self.code = code
        self.message = message
        super().__init__(f"aif10 unknown response code={code!r}: {message}")


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
        """判定顺序写死 (§3.1，不得改动):

        ① ``status in (403, 429)`` → ``AIF10BlockedError`` (不重试、不退避、立即抛)。
        ② ``500 <= status`` → 现行瞬态重试路径 (耗尽后 ``AIF10Error``)。
        ③ ``400 <= status < 500`` → 现行 ``AIF10Error`` 立即抛 (不改)。
        ④ 其它 → ``resp.json()``, 解析失败 → ``AIF10NonJsonError``。

        **禁止**读 ``Content-Type`` 头做判定 —— 东财把 JSON 用 ``text/plain`` 头
        返回 (2026-09-25 P1~P9 实测)。
        """
        self._rate_limit_wait()
        last_exc: Exception | None = None
        for attempt in range(self.retry + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                self._last_request_at = time.time()
                status = resp.status_code
                if status in (403, 429):
                    raise AIF10BlockedError(f"HTTP {status} {url}: {resp.text[:200]}")
                if status >= 500:
                    last_exc = AIF10Error(f"HTTP {status}: {resp.text[:200]}")
                elif 400 <= status < 500:
                    raise AIF10Error(f"HTTP {status} {url}: {resp.text[:200]}")
                else:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise AIF10NonJsonError(
                            f"GET {url}: 响应体不是合法 JSON: {resp.text[:200]}"
                        ) from exc
            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.HTTPError,
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
        return self._parse_v1_envelope(resp)

    @staticmethod
    def _parse_v1_envelope(resp: Any) -> dict[str, Any]:
        """判定顺序写死 (§3.1):

        顶层不是 dict 或缺 ``code`` → ``AIF10Error("envelope_missing_code ...")``
        (不造 0)；``code == 9501`` → ``AIF10ApiError``；``code`` 不在
        ``KNOWN_RESPONSE_CODES`` → ``AIF10UnknownCodeError``；``code in {0, 9201}``
        → 返回 dict, 固定带 ``code``/``message``/``success``, ``result`` 为 null
        时 ``pages``/``data``/``count`` 仍为 ``0``/``[]``/``0``。**只有 0 与 9201
        会以返回值形态离开客户端** —— 不经引擎的调用方 (``_provider_newest_update_date``
        / ``probe_period_count`` / ``_probe_v1``) 因此自动 fail-closed: 9501/未知码
        不再折叠成 ``count=0``。
        """
        if not isinstance(resp, dict) or resp.get("code") is None:
            raise AIF10Error(f"aif10 envelope_missing_code: {resp!r}")
        code = resp["code"]
        message = str(resp.get("message") or "")
        if code == 9501:
            raise AIF10ApiError(code, message)
        if code not in KNOWN_RESPONSE_CODES:
            raise AIF10UnknownCodeError(code, message)
        result = resp.get("result")
        if not isinstance(result, dict):
            result = {}
        return {
            "code": int(code),
            "message": message,
            "success": bool(resp.get("success")),
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
