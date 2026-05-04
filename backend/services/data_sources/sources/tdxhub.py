"""tdxhub source adapter — 包装 dare2live/tdxhub 的 14 类 capability.

tdxhub 已通过 `pip install -e ../tdxhub` 引入, 入口 `import tdxhub.X`.
chunky-monkey-v2 的 tdx_source.py 已封了连接池/circuit breaker, 我们继续走它.

P7 (2026-04-28): 新增 holders_top10_float — F10「股东研究」结构化解析,
99.6% 全市场覆盖, A/H 拆分, 含退出表. 替代 miaoxiang RPT_F10_EH_FREEHOLDERS.
"""
from __future__ import annotations

import time
from typing import Any

from ..base import BaseDataSource, Capability, Health, register_source


@register_source
class TdxhubSource(BaseDataSource):
    name = "tdxhub"
    display_name = "通达信 (tdxhub)"
    priority = 10
    repo_url = "https://github.com/dare2live/tdxhub"

    @property
    def capabilities(self) -> list[Capability]:
        return [
            # ===== 已用 (8 类) =====
            Capability(
                "kline_daily",
                description="日 K 线 (含分钟/周/月)",
                freshness="t-0",
                fields=["datetime", "open", "high", "low", "close", "vol", "amount"],
                notes="tdxhub.quotes.bars(symbol, frequency=9, offset=N)",
            ),
            Capability(
                "kline_index",
                description="指数 K 线",
                freshness="t-0",
                fields=["datetime", "open", "high", "low", "close", "vol", "amount", "up_count", "down_count"],
                notes="tdxhub.quotes.index_bars",
            ),
            Capability(
                "kline_minute",
                description="分钟级 K 线 (1m/5m/15m/30m/60m)",
                freshness="t-0",
                cost="medium",
                notes="同 kline_daily, frequency=0/1/2/3/7",
            ),
            Capability(
                "xdxr",
                description="除权除息历史 (送转配/分红)",
                freshness="t-1",
                fields=["category", "name", "fenhong", "songzhuangu", "peigu", "year"],
                notes="tdxhub 独家, 项目用作 K 线复权",
            ),
            Capability(
                "financial_summary",
                description="财务摘要 (单次摘要值)",
                freshness="quarterly",
                notes="tdxhub.quotes.finance",
            ),
            Capability(
                "financial_gpcw_8q",
                description="财务 gpcw 二进制解析 (585 字段, 8 期)",
                freshness="quarterly",
                cost="medium",
                fields=["secucode", "report_date", "company_type", "三大表全字段"],
                notes="tdxhub.affair.Affair: files/fetch/parse",
            ),
            Capability(
                "industry_sw",
                description="申万行业分类",
                freshness="static",
                notes="tdxhub.quotes.block (block_zhishu)",
            ),
            Capability(
                "stock_blocks",
                description="板块/概念归属",
                freshness="static",
                notes="tdxhub.quotes.block (含 block_gn / block_dy)",
            ),
            Capability(
                "stock_list",
                description="全市场代码列表 (沪/深/北)",
                freshness="static",
                notes="tdxhub.quotes.stocks(market=0/1/2)",
            ),
            Capability(
                "holders_top10_float",
                description="十大流通股东 + 十大股东 + 退出表 (A/H 拆分 + 控股股东 + 增减持计划 + 单笔变动)",
                freshness="quarterly",
                fields=[
                    "stock_code", "report_date", "holder_set", "holder_rank",
                    "holder_name", "share_class", "shares_approx",
                    "hold_ratio_float", "hold_ratio_total",
                    "change_status", "is_exit_row", "is_secondary_class",
                    "raw_hash", "fetched_at",
                ],
                cost="low",
                notes=(
                    "tdxhub.holders.HolderFetcher: 解析 F10 「股东研究」 段 1-4, "
                    "返回 fact_top10_holder_period / fact_controlling_shareholder / "
                    "fact_shareholder_plan / fact_shareholder_trade. "
                    "Format A (灵通V9.0) + Format B (通达信沪深京F10) 双格式. "
                    "替代 miaoxiang RPT_F10_EH_FREEHOLDERS (P7 起退役)."
                ),
            ),

            # ===== 已实现可用但 chunky-monkey 暂未接 (5 类) =====
            Capability(
                "quote_realtime",
                description="实时五档盘口 (≤80 只/次)",
                freshness="t-0",
                cost="low",
                notes="tdxhub.quotes.quotes - 工作台股票卡片可实时刷",
            ),
            Capability(
                "minute_today",
                description="当日实时分时",
                freshness="t-0",
                notes="tdxhub.quotes.minute",
            ),
            Capability(
                "minute_history",
                description="历史分时 (按日期)",
                freshness="t-1",
                cost="medium",
                notes="tdxhub.quotes.minutes(date)",
            ),
            Capability(
                "tick_today",
                description="当日分笔成交",
                freshness="t-0",
                cost="medium",
                notes="tdxhub.quotes.transaction",
            ),
            Capability(
                "tick_history",
                description="历史分笔 (按日期)",
                freshness="t-1",
                cost="high",
                notes="tdxhub.quotes.transactions(date)",
            ),
        ]

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------
    def fetch(self, capability: str, **kwargs) -> Any:
        # 走现有的 tdx_source.call_tdx_quotes_with_retry, 不重新写连接池
        from services.tdx_source import call_tdx_quotes_with_retry, get_tdx_affair_class

        if capability == "kline_daily":
            symbol = kwargs.get("code") or kwargs.get("symbol")
            freq = kwargs.get("frequency", 9)  # 9=日
            offset = kwargs.get("offset", 800)
            return call_tdx_quotes_with_retry(
                lambda c: c.bars_records(symbol=symbol, frequency=freq, offset=offset),
                action_name=f"tdxhub.kline_daily({symbol})",
            )

        if capability == "kline_index":
            symbol = kwargs.get("code") or kwargs.get("symbol")
            offset = kwargs.get("offset", 800)
            return call_tdx_quotes_with_retry(
                lambda c: c.index_bars_records(symbol=symbol, frequency=9, offset=offset),
                action_name=f"tdxhub.kline_index({symbol})",
            )

        if capability == "kline_minute":
            symbol = kwargs.get("code") or kwargs.get("symbol")
            freq = kwargs.get("frequency", 7)  # 7=1h
            offset = kwargs.get("offset", 800)
            return call_tdx_quotes_with_retry(
                lambda c: c.bars_records(symbol=symbol, frequency=freq, offset=offset),
                action_name=f"tdxhub.kline_minute({symbol},{freq})",
            )

        if capability == "xdxr":
            symbol = kwargs.get("code") or kwargs.get("symbol")
            return call_tdx_quotes_with_retry(
                lambda c: c.xdxr_records(symbol=symbol),
                action_name=f"tdxhub.xdxr({symbol})",
            )

        if capability == "financial_summary":
            symbol = kwargs.get("code") or kwargs.get("symbol")
            return call_tdx_quotes_with_retry(
                lambda c: c.finance_records(symbol=symbol),
                action_name=f"tdxhub.financial_summary({symbol})",
            )

        if capability == "financial_gpcw_8q":
            Affair = get_tdx_affair_class()
            if Affair is None:
                raise RuntimeError("tdxhub.affair 未安装")
            return Affair.files()

        if capability == "industry_sw" or capability == "stock_blocks":
            group = kwargs.get("group", False)
            return call_tdx_quotes_with_retry(
                lambda c: c.block_records(group=group, custom=False),
                action_name=f"tdxhub.{capability}",
            )

        if capability == "stock_list":
            market = kwargs.get("market", 0)
            return call_tdx_quotes_with_retry(
                lambda c: c.stocks_records(market=market),
                action_name=f"tdxhub.stock_list({market})",
            )

        if capability == "holders_top10_float":
            # 走 tdxhub.holders.HolderFetcher (cooldown 软挂起 + HQ_HOSTS 自动 resync).
            # 不复用 services/tdx_source 的 quotes 池, 因为 holders 用的是
            # company_info_category/content 协议, 由 HolderFetcher 自己管.
            from tdxhub.holders import HolderFetcher, parse_research_records

            symbol = kwargs.get("code") or kwargs.get("symbol")
            stock_name = kwargs.get("stock_name", "")
            if not symbol:
                raise ValueError("holders_top10_float requires symbol/code")
            fetcher = kwargs.get("_shared_fetcher")
            owns_fetcher = fetcher is None
            if owns_fetcher:
                fetcher = HolderFetcher(timeout=15, max_attempts_per_call=6)
            try:
                text = fetcher.fetch_text(symbol)
                if not text:
                    return None
                return parse_research_records(text, symbol=symbol, stock_name=stock_name)
            finally:
                if owns_fetcher:
                    fetcher.close()

        if capability == "quote_realtime":
            symbols = kwargs.get("symbols") or kwargs.get("code") or kwargs.get("symbol")
            return call_tdx_quotes_with_retry(
                lambda c: c.quotes_records(symbol=symbols),
                action_name=f"tdxhub.quote_realtime",
            )

        if capability == "minute_today":
            symbol = kwargs.get("code") or kwargs.get("symbol")
            return call_tdx_quotes_with_retry(
                lambda c: c.minute_records(symbol=symbol),
                action_name=f"tdxhub.minute_today({symbol})",
            )

        if capability == "minute_history":
            symbol = kwargs.get("code") or kwargs.get("symbol")
            date = kwargs.get("date")
            return call_tdx_quotes_with_retry(
                lambda c: c.minutes_records(symbol=symbol, date=date),
                action_name=f"tdxhub.minute_history({symbol},{date})",
            )

        if capability == "tick_today":
            symbol = kwargs.get("code") or kwargs.get("symbol")
            return call_tdx_quotes_with_retry(
                lambda c: c.transaction_records(symbol=symbol),
                action_name=f"tdxhub.tick_today({symbol})",
            )

        if capability == "tick_history":
            symbol = kwargs.get("code") or kwargs.get("symbol")
            date = kwargs.get("date")
            return call_tdx_quotes_with_retry(
                lambda c: c.transactions_records(symbol=symbol, date=date),
                action_name=f"tdxhub.tick_history({symbol},{date})",
            )

        raise NotImplementedError(f"tdxhub 不实现 capability '{capability}'")

    def healthcheck(self) -> Health:
        """轻量 health: import + ping 一次 stock_count(沪) ≤ 5s 算 ok."""
        try:
            from services.tdx_source import call_tdx_quotes_with_retry, tdxhub_circuit_open
        except Exception as exc:
            return Health(state="down", notes=f"无法 import tdx_source: {exc}")

        if tdxhub_circuit_open():
            return Health(state="degraded", notes="circuit breaker open, 暂停服务")

        t0 = time.time()
        try:
            count = call_tdx_quotes_with_retry(
                lambda c: c.stock_count(market=1),
                action_name="tdxhub.healthcheck",
            )
            latency = (time.time() - t0) * 1000
            return Health(
                state="ok",
                last_success_ts=time.time(),
                avg_latency_ms=round(latency, 1),
                notes=f"{count} 只沪股 (健康)",
            )
        except Exception as exc:
            return Health(
                state="down",
                consecutive_failures=1,
                notes=f"{type(exc).__name__}: {str(exc)[:80]}",
            )
