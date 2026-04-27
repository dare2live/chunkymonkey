"""东财 datacenter-web source — pytdx/妙想都没有的 5 类业务数据.

走 backend/services/eastmoney_skill/datacenter.py 已有的封装.
"""
from __future__ import annotations

import time
from typing import Any

from ..base import BaseDataSource, Capability, Health, register_source


@register_source
class EmDatacenterSource(BaseDataSource):
    name = "em_datacenter"
    display_name = "东财 datacenter-web"
    priority = 30
    repo_url = "https://datacenter-web.eastmoney.com"

    @property
    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                "lhb_daily",
                description="龙虎榜每日明细 (营业部)",
                freshness="daily",
                notes="走 lhb_client.sync_lhb_range",
            ),
            Capability(
                "qfii_holding_quarterly",
                description="QFII 持仓季度",
                freshness="quarterly",
                notes="走 qfii_client.sync_qfii_holding_quarterly",
            ),
            Capability(
                "margin_daily",
                description="融资融券日明细",
                freshness="daily",
                notes="走 margin_client.sync_margin_day",
            ),
            Capability(
                "institution_survey",
                description="机构调研事件",
                freshness="daily",
                notes="走 institution_survey_client",
            ),
            Capability(
                "money_flow_history",
                description="个股资金流历史 (反爬高发, 需 retry)",
                freshness="daily",
                cost="high",
                notes="走 eastmoney_skill datacenter-web",
            ),
            Capability(
                "money_flow_latest",
                description="个股资金流最新 1 日",
                freshness="t-0",
                notes="走 eastmoney_skill datacenter-web",
            ),
        ]

    def fetch(self, capability: str, **kwargs) -> Any:
        # 全部走现有 client. 这里只做 capability → client 映射, 不重新实现.
        if capability == "lhb_daily":
            from services import lhb_client
            start = kwargs.get("start") or kwargs.get("trade_date")
            end = kwargs.get("end") or start
            return lhb_client.sync_lhb_range(start, end)

        if capability == "qfii_holding_quarterly":
            from services import qfii_client
            return qfii_client.sync_qfii_holding_quarterly(**kwargs)

        if capability == "margin_daily":
            from services import margin_client
            trade_date = kwargs.get("trade_date") or kwargs.get("date")
            return margin_client.sync_margin_day(trade_date)

        if capability == "institution_survey":
            from services import institution_survey_client
            return institution_survey_client.sync_institution_surveys(**kwargs)

        if capability in ("money_flow_history", "money_flow_latest"):
            # 项目里有 datacenter-web 适配, 直接走
            try:
                from services.eastmoney_skill import call_datacenter
            except ImportError:
                raise RuntimeError("eastmoney_skill 未配置")
            # 这里给个最小实现, 让调用方补具体 reportName
            report_name = kwargs.get("report_name", "RPT_INDIVIDUAL_FUND_FLOW")
            return call_datacenter(report_name, **kwargs)

        raise NotImplementedError(f"em_datacenter 不实现 capability '{capability}'")

    def healthcheck(self) -> Health:
        """ping 一次 datacenter-web 看是否能拿到数据."""
        try:
            from services.eastmoney_skill import call_datacenter
        except Exception as exc:
            return Health(state="down", notes=f"eastmoney_skill 未配置: {exc}")

        t0 = time.time()
        try:
            # 用最便宜的接口: 龙虎榜近 1 条
            result = call_datacenter(
                "RPT_DAILYBILLBOARD_DETAILSNEW",
                page=1, page_size=1,
                sort_columns="TRADE_DATE",
                sort_types="-1",
            )
            latency = (time.time() - t0) * 1000
            ok = bool(result and result.get("data"))
            return Health(
                state="ok" if ok else "degraded",
                last_success_ts=time.time() if ok else None,
                avg_latency_ms=round(latency, 1),
                notes=f"{len(result.get('data') or [])} 行" if ok else "返回空",
            )
        except Exception as exc:
            return Health(
                state="down",
                consecutive_failures=1,
                notes=f"{type(exc).__name__}: {str(exc)[:80]}",
            )
