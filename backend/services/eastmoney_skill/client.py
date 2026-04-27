"""东财通用 HTTP 客户端 (BaseClient).

工程改进相对于 akshare 直接 requests.get():
- Session(trust_env=False): 避免被系统代理 (Surge/ClashX) 接管
- timeout 默认 15s
- 默认 User-Agent + Referer (东财部分接口要 Referer 才放行)
- 内置 retry (exp backoff, 默认 3 次)
- ConnectionError / Timeout / 5xx 自动 retry
- 4xx 直接抛 (反爬规则触发, retry 没意义)
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger("eastmoney_skill")


_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class EastMoneyError(Exception):
    """东财接口错误."""


class EastMoneyClient:
    """东方财富 HTTP 客户端.

    用法:
        client = EastMoneyClient(timeout=15, retry=3)
        data = client.get_json("https://datacenter-web.eastmoney.com/api/data/v1/get",
                               params={"reportName": "RPT_ORG_SURVEYNEW", ...})
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
        self._session.headers.update(_DEFAULT_HEADERS)
        if extra_headers:
            self._session.headers.update(extra_headers)
        self._last_request_at = 0.0

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> requests.Response:
        """GET with retry. 5xx / 网络异常 retry, 4xx 直接抛."""
        if self.rate_limit > 0:
            elapsed = time.time() - self._last_request_at
            if elapsed < self.rate_limit:
                time.sleep(self.rate_limit - elapsed)

        last_exc: Exception | None = None
        eff_timeout = timeout if timeout is not None else self.timeout
        for attempt in range(self.retry + 1):
            try:
                resp = self._session.get(
                    url, params=params, headers=headers, timeout=eff_timeout
                )
                self._last_request_at = time.time()
                if resp.status_code < 400:
                    return resp
                if 400 <= resp.status_code < 500:
                    raise EastMoneyError(
                        f"HTTP {resp.status_code} {url}: {resp.text[:200]}"
                    )
                last_exc = EastMoneyError(
                    f"HTTP {resp.status_code} {url}: {resp.text[:200]}"
                )
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
                last_exc = exc
            if attempt < self.retry:
                sleep_s = self.retry_backoff ** attempt
                logger.debug(
                    "[em_skill] %s 第 %d 次重试 (sleep %.1fs): %s",
                    url, attempt + 1, sleep_s, str(last_exc)[:120],
                )
                time.sleep(sleep_s)
        raise EastMoneyError(
            f"GET {url} 失败 (重试 {self.retry} 次): {last_exc}"
        ) from last_exc

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """GET + JSON parse."""
        resp = self.get(url, params=params, headers=headers, timeout=timeout)
        try:
            return resp.json()
        except ValueError as exc:
            raise EastMoneyError(
                f"GET {url} JSON 解析失败: {str(exc)[:120]}, body={resp.text[:200]}"
            ) from exc

    def close(self) -> None:
        self._session.close()


# 模块级共享实例 (项目内大部分调用复用同一 session 即可)
default_client = EastMoneyClient()
