"""akshare 兜底 source — 仅留 tdxhub/妙想/东财都没有的 2 类.

经过 P1-P3 退役后, akshare 在 chunky-monkey-v2 里只剩这两个角色.
"""
from __future__ import annotations

import time
from typing import Any

from ..base import BaseDataSource, Capability, Health, register_source


@register_source
class AkshareFallbackSource(BaseDataSource):
    name = "akshare"
    display_name = "akshare (兜底)"
    priority = 99
    repo_url = "https://github.com/akfamily/akshare"

    @property
    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                "trading_calendar",
                description="A 股交易日历 (新浪源)",
                freshness="static",
                notes="ak.tool_trade_date_hist_sina, 项目 dim_trading_calendar 用",
            ),
            Capability(
                "etf_spot_ths",
                description="ETF 实时行情 (同花顺源, 唯一)",
                freshness="t-0",
                notes="ak.fund_etf_spot_ths",
            ),
        ]

    def fetch(self, capability: str, **kwargs) -> Any:
        if capability == "trading_calendar":
            try:
                import akshare as ak
            except ImportError as exc:
                raise RuntimeError(f"akshare 未安装: {exc}")
            return ak.tool_trade_date_hist_sina()

        if capability == "etf_spot_ths":
            try:
                import akshare as ak
            except ImportError as exc:
                raise RuntimeError(f"akshare 未安装: {exc}")
            return ak.fund_etf_spot_ths()

        raise NotImplementedError(f"akshare 不实现 capability '{capability}'")

    def healthcheck(self) -> Health:
        try:
            import akshare as ak  # noqa: F401
        except ImportError:
            return Health(state="down", notes="akshare 未安装")

        # 只检 import 通, 不真实调用 (akshare 第一次调用很慢, 影响 UI)
        return Health(
            state="ok",
            last_check_ts=time.time(),
            notes="import 通, 兜底用, 未实测网络",
        )
