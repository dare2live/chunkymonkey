"""akshare source — 真兜底, 仅保留 tdxhub 和妙想都没有的 2 类.

注意: lhb / qfii / margin / 机构调研 / 资金流 这些"项目自建 datacenter-web 直连"
是过渡产物, 不归入此 source. 见 data_routes.py 中 status='transitional' 的条目,
P6 迁移目标是 miaoxiang (妙想 RPT_BILLBOARD_DAILYDETAILS 等已能覆盖).
"""
from __future__ import annotations

import time
from typing import Any

from ..base import BaseDataSource, Capability, Health, register_source


@register_source
class AkshareSource(BaseDataSource):
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
                notes="ak.tool_trade_date_hist_sina, dim_trading_calendar 唯一来源",
            ),
            Capability(
                "etf_spot_ths",
                description="ETF 实时行情 (同花顺源)",
                freshness="t-0",
                notes="ak.fund_etf_spot_ths, ETF 模块独家",
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
        except ImportError as exc:
            return Health(state="down", notes=f"akshare 未安装: {exc}")
        return Health(
            state="ok",
            last_check_ts=time.time(),
            notes="兜底用, 仅 trading_calendar + etf_spot_ths",
        )
