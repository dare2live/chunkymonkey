"""akshare source — 真兜底, 仅保留 tdxhub 和妙想都没有的能力."""
from __future__ import annotations

import time
from typing import Any

from services.akshare_client import disable_proxy_env

from ..base import BaseDataSource, Capability, Health, register_source


def _call_with_retry(func, *args, retries: int = 2, delay_s: float = 1.0, **kwargs):
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # akshare/requests/JSON parse can all be transient
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(delay_s * (attempt + 1))
    assert last_exc is not None
    raise last_exc


@register_source
class AkshareSource(BaseDataSource):
    name = "akshare"
    display_name = "akshare (兜底)"
    priority = 99
    repo_url = "https://github.com/akfamily/akshare"

    @property
    def capabilities(self) -> list[Capability]:
        return [
            # ===== akshare 唯一来源 (没替代源) =====
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
            Capability(
                "individual_fund_flow",
                description="个股资金流 (近期历史窗口)",
                freshness="daily",
                notes="ak.stock_individual_fund_flow — need_027 probe candidate",
            ),
            Capability(
                "individual_fund_flow_rank",
                description="个股资金流排行",
                freshness="daily",
                notes="ak.stock_individual_fund_flow_rank — need_027 supplementary probe",
            ),
            Capability(
                "individual_fund_flow_rank_snapshot",
                description="个股资金流排行快照 (同花顺)",
                freshness="daily",
                notes="ak.stock_fund_flow_individual — research-only, snapshot rank, not need_027 exact flow",
            ),

            # ===== 妙想故障时 fallback (P0.3) =====
            Capability(
                "lhb_daily",
                description="龙虎榜 (fallback)",
                freshness="daily",
                notes="ak.stock_lhb_detail_em — 仅当妙想故障时 fallback, 接受反爬风险",
            ),
            Capability(
                "qfii_holding_quarterly",
                description="QFII 持仓 (fallback)",
                freshness="quarterly",
                notes="ak.stock_gdfx_holding_detail_em — fallback only",
            ),
            Capability(
                "institution_survey",
                description="机构调研 (fallback)",
                freshness="daily",
                notes="ak.stock_jgdy_tj_em — fallback only",
            ),
        ]

    def fetch(self, capability: str, **kwargs) -> Any:
        disable_proxy_env()

        if capability == "trading_calendar":
            try:
                import akshare as ak
            except ImportError as exc:
                raise RuntimeError(f"akshare 未安装: {exc}")
            return _call_with_retry(ak.tool_trade_date_hist_sina)

        if capability == "etf_spot_ths":
            try:
                import akshare as ak
            except ImportError as exc:
                raise RuntimeError(f"akshare 未安装: {exc}")
            return _call_with_retry(ak.fund_etf_spot_ths)

        if capability == "individual_fund_flow":
            import akshare as ak
            stock = kwargs.get("stock") or kwargs.get("symbol")
            if not stock:
                raise ValueError("individual_fund_flow requires stock or symbol")
            market = kwargs.get("market", "sh")
            df = _call_with_retry(ak.stock_individual_fund_flow, stock=str(stock), market=str(market))
            return df.to_dict("records") if df is not None and not df.empty else []

        if capability == "individual_fund_flow_rank":
            import akshare as ak
            indicator = kwargs.get("indicator", "5日")
            df = _call_with_retry(ak.stock_individual_fund_flow_rank, indicator=str(indicator))
            return df.to_dict("records") if df is not None and not df.empty else []

        if capability == "individual_fund_flow_rank_snapshot":
            import akshare as ak
            symbol = kwargs.get("symbol") or kwargs.get("indicator") or "即时"
            df = _call_with_retry(ak.stock_fund_flow_individual, symbol=str(symbol))
            return df.to_dict("records") if df is not None and not df.empty else []

        # fallback 路径 (P0.3): 妙想主源故障时来这里
        if capability == "lhb_daily":
            import akshare as ak
            start = kwargs.get("start_date") or kwargs.get("start")
            end = kwargs.get("end_date") or kwargs.get("end")
            df = _call_with_retry(ak.stock_lhb_detail_em, start_date=start, end_date=end)
            return df.to_dict("records") if df is not None and not df.empty else []

        if capability == "qfii_holding_quarterly":
            import akshare as ak
            report_date = kwargs.get("report_date")
            symbol = kwargs.get("symbol", "QFII")
            df = _call_with_retry(ak.stock_gdfx_holding_detail_em, date=report_date, indicator=symbol)
            return df.to_dict("records") if df is not None and not df.empty else []

        if capability == "institution_survey":
            import akshare as ak
            date_str = kwargs.get("date") or kwargs.get("start_date")
            df = _call_with_retry(ak.stock_jgdy_tj_em, date=date_str)
            return df.to_dict("records") if df is not None and not df.empty else []

        raise NotImplementedError(f"akshare 不实现 capability '{capability}'")

    def healthcheck(self) -> Health:
        disable_proxy_env()

        try:
            import akshare as ak  # noqa: F401
        except ImportError as exc:
            return Health(state="down", notes=f"akshare 未安装: {exc}")
        return Health(
            state="ok",
            last_check_ts=time.time(),
            notes="兜底用, 仅 trading_calendar + etf_spot_ths",
        )
