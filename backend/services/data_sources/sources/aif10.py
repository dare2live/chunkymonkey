"""aif10 (妙想 F10) source adapter — 包装 dare2live/aif10-scraper.

参考 miaoxiang/600519_F10_data_source_report.md 的字段标准化建议:
- 主键: SECUCODE (含 .SH/.SZ)
- 报告期型: 联合 (SECUCODE, REPORT_DATE/END_DATE), 服务端保留约 8 期
- 事件型: (SECUCODE, NOTICE_DATE/TRADE_DATE, [事件子类型])

12 个 capability 覆盖 6 个主题域:
- stock_master, fin_period, shareholder_period, event_log, peer_snapshot, forecast
"""
from __future__ import annotations

import time
from typing import Any

from ..base import BaseDataSource, Capability, Health, register_source


# capability_name → reportName (项目实际用得到的)
CAPABILITY_TO_REPORT = {
    # ===== 股东周期 (shareholder_period) =====
    "top_free_holders":         "RPT_F10_EH_FREEHOLDERS",       # 十大流通股东 ⭐ 项目主用
    "top_holders":              "RPT_F10_EH_HOLDERS",           # 十大股东
    "holder_count":             "RPT_F10_EH_HOLDERNUM",         # 股东人数 (独家)
    "shareholder_change":       "RPT_F10_SHAREHOLDER_CHANGE",   # 持股变动 (季差分)
    "main_org_holding":         "RPT_F10_MAIN_ORGHOLDDETAILS",  # 机构持仓 ORG_TYPE 分桶 (含 QFII=02)
    "fund_org_holding":         "RPT_MAIN_ORGHOLDDETAIL",       # 基金/机构持仓明细
    "northbound_holding":       "RPT_MUTUAL_STOCK_HOLDRANKN_NEW",  # 沪深港通持股
    # ===== 替代 datacenter-web 直连 (P6 迁移目标) =====
    "lhb_daily":                "RPT_BILLBOARD_DAILYDETAILS",   # 龙虎榜 (P6: 替代 lhb_client datacenter-web)
    "lhb_operatedept":          "RPT_OPERATEDEPT_TRADE",        # 龙虎榜营业部
    "block_trade":              "RPT_DATA_BLOCKTRADE",          # 大宗交易
    "margin_stocks":            "RPT_MARGIN_STATISTICS_STOCKS", # 融资融券 (P6: 替代 margin_client)
    "margin_trend":             "RPT_STOCK_MARGINTRENDEXPLAIN", # 融券趋势

    # ===== 估值/同行 (peer_snapshot) =====
    "valuation_quantile":       "RPT_STOCKVALUATIONTANTILE",    # PE/PB/PEG 分位 (独家)
    "peer_valuation":           "RPT_PCF10_INDUSTRY_CVALUE",    # 同行估值排名
    "peer_growth":              "RPT_PCF10_INDUSTRY_GROWTH",    # 同行成长性
    "peer_dupont":              "RPT_PCF10_INDUSTRY_DBFX",      # 杜邦同行

    # ===== 财务 (fin_period) =====
    "financial_main_finadata":  "RPT_PCF10_FINANCEMAINFINADATA",  # 最新主指标
    "financial_history_200q":   "RPT_F10_FINANCE_MAINFINADATA",   # 历史 200 期 (v0)
    "financial_dupont":         "RPT_F10_FINANCE_DUPONT",         # 杜邦
    "company_type":             "RPT_F10_PUBLIC_COMPANYTPYE",     # 公司类型 (财报模板)

    # ===== 主题/经营 =====
    "themes":                   "RPT_F10_CORETHEME_BOARDTYPE",    # 概念题材
    "main_business":            "RPT_F10_FN_MAINOP",              # 主营构成
    "business_analysis":        "RPT_F10_OP_BUSINESSANALYSIS",    # 经营评述

    # ===== 公司大事 (event_log) =====
    "lift_future":              "RPTA_APP_LIFTFUTURE",            # 限售解禁
    "executive_holding":        "RPT_EXECUTIVE_HOLD_DETAILS",     # 高管持股变动

    # ===== 盈利预测 (forecast) =====
    "forecast_consensus":       "RPT_HSF10_RES_ORGRATING",        # 评级统计
    "forecast_predict_avg":     "RPT_HSF10_RESPREDICT_STATISTICS",  # 预测均值

    # ===== 公司概况 (stock_master) =====
    "company_basic":            "RPT_F10_BASIC_ORGINFO",          # 基本资料
    "company_executives":       "RPT_F10_ORGINFO_MANAINTRO",      # 高管列表

    # ===== 分红融资 =====
    "dividend_history":         "RPT_F10_DIVIDEND_MAIN",          # 分红明细
    "dividend_3year":           "RPT_F10_DIVIDEND_3YEAR",         # 近 3 年汇总
}


@register_source
class Aif10Source(BaseDataSource):
    name = "aif10"
    display_name = "妙想 F10 (datacenter)"
    priority = 20
    repo_url = "https://github.com/dare2live/aif10-scraper"

    # 频率桶 (来自调研报告 §4)
    _FRESHNESS_MAP = {
        "valuation_quantile": "daily",
        "northbound_holding": "daily",
        "forecast_consensus": "weekly",
        "forecast_predict_avg": "weekly",
        "shareholder_change": "weekly",
        "lift_future": "daily",  # 解禁日期表, 每日更新预告
        "executive_holding": "daily",  # 事件型, 日级
        "dividend_history": "daily",   # 事件型
        "company_basic": "static",
        "company_executives": "static",
        "company_type": "static",
        "themes": "static",
        "main_business": "quarterly",
        "business_analysis": "quarterly",
    }

    @property
    def capabilities(self) -> list[Capability]:
        out = []
        for cap_name, report_name in CAPABILITY_TO_REPORT.items():
            freshness = self._FRESHNESS_MAP.get(cap_name, "quarterly")
            out.append(Capability(
                name=cap_name,
                description=f"妙想 F10 / {report_name}",
                freshness=freshness,
                notes=f"reportName={report_name}",
            ))
        return out

    def fetch(self, capability: str, **kwargs) -> Any:
        report_name = CAPABILITY_TO_REPORT.get(capability)
        if not report_name:
            raise NotImplementedError(f"aif10 不实现 capability '{capability}'")

        from aif10_scraper import fetch_report

        secucode = kwargs.get("secucode") or kwargs.get("code")
        page_size = kwargs.get("page_size", 500)
        max_pages = kwargs.get("max_pages", 0)
        mode = kwargs.get("mode", "auto")

        # 单股查询: secucode 必须形如 600519.SH
        if secucode and "." not in secucode:
            # chunky-monkey 内部一般传 6 位代码, 转成 SECUCODE
            if secucode.startswith(("60", "68", "5")):
                secucode = f"{secucode}.SH"
            elif secucode.startswith(("0", "3")):
                secucode = f"{secucode}.SZ"
            elif secucode.startswith(("4", "8")):
                secucode = f"{secucode}.BJ"

        result = fetch_report(
            report_name,
            mode=mode,
            secucode=secucode,
            page_size=page_size,
            max_pages=max_pages,
        )
        return result["rows"]

    def healthcheck(self) -> Health:
        """ping 一次 RPT_F10_BASIC_ORGINFO with limit=1 看是否能 200."""
        try:
            from aif10_scraper import AIF10Client
        except ImportError as exc:
            return Health(state="down", notes=f"aif10_scraper 未安装: {exc}")

        t0 = time.time()
        try:
            cli = AIF10Client(retry=1, timeout=8.0)
            result = cli.get_v1(
                "RPT_F10_BASIC_ORGINFO",
                page=1, page_size=1,
                secucode="600519.SH",
            )
            cli.close()
            latency = (time.time() - t0) * 1000
            if result.get("data"):
                return Health(
                    state="ok",
                    last_success_ts=time.time(),
                    avg_latency_ms=round(latency, 1),
                    notes=f"{len(result['data'])} 行返回 (健康)",
                )
            return Health(state="degraded", notes="返回空数据")
        except Exception as exc:
            return Health(
                state="down",
                consecutive_failures=1,
                notes=f"{type(exc).__name__}: {str(exc)[:80]}",
            )
