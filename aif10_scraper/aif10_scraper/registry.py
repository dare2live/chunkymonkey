"""16 模块 reportName 注册表 (核心).

每个 ReportSpec 描述:
- name: reportName (东财内部表名)
- module: 一级模块 (16 个之一)
- subname: 子栏目名
- key: 主键字段 (用于增量入库)
- date_field: 时间字段 (REPORT_DATE / NOTICE_DATE / TRADE_DATE / END_DATE)
- frequency: 更新频率 (real-time / daily / weekly / quarterly / event / monthly / static / once)
- batch_friendly: 不带 SECUCODE 过滤是否可拉全市场
- api: v0 / v1 (大部分 v1)
- v0_sty: v0 接口必传 sty 参数

完整字段表见 docs/eastmoney-aif10-spec.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Module = Literal[
    "trading", "shareholder", "business", "themes", "news",
    "events", "profile", "peer", "forecast", "research",
    "financial", "dividend", "share_capital", "executives",
    "capital_ops", "related",
]

Frequency = Literal[
    "realtime", "daily", "weekly", "quarterly",
    "event", "monthly", "static", "once",
]

Api = Literal["v1", "v0"]


@dataclass
class ReportSpec:
    name: str                          # reportName 或 v0 type
    module: Module
    subname: str
    key: tuple[str, ...]              # 主键字段
    date_field: str | None = None     # 时间字段, 用于增量
    frequency: Frequency = "quarterly"
    batch_friendly: bool = True       # 是否支持不带 SECUCODE 全市场拉
    api: Api = "v1"
    v0_sty: str | None = None         # v0 接口必传 sty
    sort_columns: str = ""
    sort_types: str = "-1"
    notes: str = ""


# ---------------------------------------------------------------------------
# 16 模块注册表
# ---------------------------------------------------------------------------

REPORTS: list[ReportSpec] = [
    # ===== 1. 操盘必读 (Trading Essentials) - 8 reports =====
    ReportSpec(
        name="RPT_PCF10_FINANCEMAINFINADATA",
        module="trading", subname="最新指标 (财务主指标)",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
        sort_columns="REPORT_DATE",
        notes="EPS / ROE / 毛利率 / 营收同比 / 归母净利同比 等数十项, 8 期历史",
    ),
    ReportSpec(
        name="RPT_DMSK_NEWINDICATOR",
        module="trading", subname="最新指标 (扩展)",
        key=("SECUCODE",),
        frequency="daily",
        notes="实时市值/涨跌等",
    ),
    ReportSpec(
        name="RPT_STOCKVALUATIONTANTILE",
        module="trading", subname="估值分位",
        key=("SECUCODE", "STATISTICS_CYCLE", "INDEX_TYPE"),
        frequency="daily",
        notes="PE/PB/PS/PEG 在 1Y/3Y/5Y/10Y 的 30/50/70 分位",
    ),
    ReportSpec(
        name="RPT_BILLBOARD_DAILYDETAILS",
        module="trading", subname="龙虎榜单 (操盘版)",
        key=("SECUCODE", "TRADE_DATE"),
        date_field="TRADE_DATE",
        frequency="event",
        sort_columns="TRADE_DATE",
        notes="个股龙虎榜每日明细",
    ),
    ReportSpec(
        name="RPT_OPERATEDEPT_TRADE",
        module="trading", subname="龙虎榜营业部 (买/卖)",
        key=("SECUCODE", "TRADE_DATE", "OPERATEDEPT_NAME", "TRADE_DIRECTION"),
        date_field="TRADE_DATE",
        frequency="event",
        notes="TRADE_DIRECTION=0 买 / 1 卖",
    ),
    ReportSpec(
        name="RPT_DATA_BLOCKTRADE",
        module="trading", subname="大宗交易",
        key=("SECUCODE", "TRADE_DATE"),
        date_field="TRADE_DATE",
        frequency="event",
        sort_columns="TRADE_DATE",
    ),
    ReportSpec(
        name="RPT_MARGIN_STATISTICS_STOCKS",
        module="trading", subname="融资融券",
        key=("SECUCODE", "TRADE_DATE"),
        date_field="TRADE_DATE",
        frequency="daily",
        sort_columns="TRADE_DATE",
        notes="融资余额 / 融资买入 / 融券余量 / 融券卖出",
    ),
    ReportSpec(
        name="RPT_STOCK_MARGINTRENDEXPLAIN",
        module="trading", subname="融资融券趋势解读",
        key=("SECUCODE", "TRADE_DATE"),
        date_field="TRADE_DATE",
        frequency="daily",
    ),

    # ===== 2. 股东研究 (Shareholder) - 11 reports =====
    ReportSpec(
        name="RPT_F10_EH_HOLDERNUM",
        module="shareholder", subname="股东人数趋势",
        key=("SECUCODE", "END_DATE"),
        date_field="END_DATE",
        frequency="quarterly",
        sort_columns="END_DATE",
        notes="户数 / 集中度 / 人均流通股 / TOP10 占比",
    ),
    ReportSpec(
        name="RPT_F10_EH_RELATION",
        module="shareholder", subname="实控人",
        key=("SECUCODE",),
        frequency="static",
    ),
    ReportSpec(
        name="RPT_F10_EH_HOLDERSDATE",
        module="shareholder", subname="十大股东 报告期列表",
        key=("SECUCODE", "END_DATE"),
        date_field="END_DATE",
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_F10_EH_FREEHOLDERSDATE",
        module="shareholder", subname="十大流通股东 报告期列表",
        key=("SECUCODE", "END_DATE"),
        date_field="END_DATE",
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_F10_EH_HOLDERS",
        module="shareholder", subname="十大股东",
        key=("SECUCODE", "END_DATE", "HOLDER_RANK"),
        date_field="END_DATE",
        frequency="quarterly",
        sort_columns="END_DATE,HOLDER_RANK",
        sort_types="-1,1",
    ),
    ReportSpec(
        name="RPT_F10_EH_FREEHOLDERS",
        module="shareholder", subname="十大流通股东 ⭐",
        key=("SECUCODE", "END_DATE", "HOLDER_RANK"),
        date_field="END_DATE",
        frequency="quarterly",
        sort_columns="END_DATE,HOLDER_RANK",
        sort_types="-1,1",
        notes="项目主用 (流通股口径 alpha)",
    ),
    ReportSpec(
        name="RPT_F10_SHAREHOLDER_CHANGE",
        module="shareholder", subname="十大股东持股变动 (季度差分)",
        key=("SECUCODE", "END_DATE", "HOLDER_RANK"),
        date_field="END_DATE",
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_F10_FREE_TOTALHOLDNUM",
        module="shareholder", subname="流通股东合计",
        key=("SECUCODE", "END_DATE"),
        date_field="END_DATE",
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_F10_MAIN_ORGHOLDDETAILS",
        module="shareholder", subname="机构持仓概览 (ORG_TYPE 分桶)",
        key=("SECUCODE", "END_DATE", "ORG_TYPE"),
        date_field="END_DATE",
        frequency="quarterly",
        notes="ORG_TYPE: 01 基金 / 02 QFII / 03 社保 / 04 券商 / 05 保险 / 06 信托",
    ),
    ReportSpec(
        name="RPT_MAIN_ORGHOLDDETAIL",
        module="shareholder", subname="机构持仓明细 (按机构)",
        key=("SECUCODE", "END_DATE", "ORG_CODE"),
        date_field="END_DATE",
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_MUTUAL_STOCK_HOLDRANKN_NEW",
        module="shareholder", subname="沪深港通持股",
        key=("SECUCODE", "TRADE_DATE"),
        date_field="TRADE_DATE",
        frequency="daily",
    ),

    # ===== 3. 经营分析 (Business) - 3 reports =====
    ReportSpec(
        name="RPT_HSF9_BASIC_ORGINFO",
        module="business", subname="主营范围 (BUSINESS_SCOPE)",
        key=("SECUCODE",),
        frequency="static",
    ),
    ReportSpec(
        name="RPT_F10_FN_MAINOP",
        module="business", subname="主营构成 (行业/产品/地区)",
        key=("SECUCODE", "REPORT_DATE", "MAINOP_TYPE", "ITEM_NAME"),
        date_field="REPORT_DATE",
        frequency="quarterly",
        notes="MAINOP_TYPE: 1=行业 / 2=产品 / 3=地区",
    ),
    ReportSpec(
        name="RPT_F10_OP_BUSINESSANALYSIS",
        module="business", subname="经营评述 (NLP, 全文)",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
    ),

    # ===== 4. 核心题材 (Themes) - 4 reports =====
    ReportSpec(
        name="RPT_F10_CORETHEME_BOARDTYPE",
        module="themes", subname="概念题材",
        key=("SECUCODE", "BOARD_CODE"),
        frequency="static",
    ),
    ReportSpec(
        name="RPT_F10_CORETHEME_CONTENT",
        module="themes", subname="题材亮点 / 详情",
        key=("SECUCODE", "KEY_CLASSIF_CODE"),
        frequency="static",
    ),
    ReportSpec(
        name="RTP_F10_POPULAR_LEADING",
        module="themes", subname="人气龙头",
        key=("SECUCODE",),
        frequency="daily",
    ),

    # ===== 5. 资讯公告 (News) - 4 接口, 不在 datacenter 而在独立子域 =====
    # 这些走 emdcnewsapp / np-anotice-pc / np-cnotice-pc / np-creport-pc, 单独处理
    # 见 reports/news.py

    # ===== 6. 公司大事 (Events) - 8 reports =====
    ReportSpec(
        name="RPT_F10_REMIND_RELATIONSHIP",
        module="events", subname="大事提醒 (字典)",
        key=("SECUCODE", "EVENT_TYPE"),
        frequency="event",
    ),
    ReportSpec(
        name="RPTA_APP_LIFTFUTURE",
        module="events", subname="限售解禁",
        key=("SECUCODE", "LIFT_DATE"),
        date_field="LIFT_DATE",
        frequency="event",
        notes="替代 ak.stock_restricted_release_detail_em",
    ),
    ReportSpec(
        name="RPTA_APP_ACCUMDETAILS",
        module="events", subname="限售解禁 (持有人维度)",
        key=("SECUCODE", "LIFT_DATE", "HOLDER_NAME"),
        date_field="LIFT_DATE",
        frequency="event",
    ),
    ReportSpec(
        name="RPT_EXECUTIVE_HOLD_DETAILS",
        module="events", subname="高管持股变动",
        key=("SECUCODE", "CHANGE_DATE", "CHANGE_PERSON"),
        date_field="CHANGE_DATE",
        frequency="event",
        notes="替代 ak.stock_ggcg_em",
    ),
    ReportSpec(
        name="RTP_F10_ADVANCE_DETAIL_NEW",
        module="events", subname="同类事件扩展",
        key=("SECUCODE", "NOTICE_DATE", "EVENT_TYPE"),
        date_field="NOTICE_DATE",
        frequency="event",
    ),

    # ===== 7. 公司概况 (Profile) - 5 reports =====
    ReportSpec(
        name="RPT_F10_BASIC_ORGINFO",
        module="profile", subname="基本资料",
        key=("SECUCODE",),
        frequency="static",
    ),
    ReportSpec(
        name="RPT_PCF10_ORG_ISSUEINFO",
        module="profile", subname="发行信息",
        key=("SECUCODE",),
        frequency="once",
    ),
    ReportSpec(
        name="RPT_ORG_COURSECHANGE",
        module="profile", subname="发展历程",
        key=("SECUCODE", "CHANGE_DATE"),
        date_field="CHANGE_DATE",
        frequency="event",
    ),
    ReportSpec(
        name="RPT_ORG_RECAPITALIZE",
        module="profile", subname="资本运作 (重大重组)",
        key=("SECUCODE", "NOTICE_DATE"),
        date_field="NOTICE_DATE",
        frequency="event",
    ),
    ReportSpec(
        name="RPT_F10_PUBLIC_OP_HOLDINGORG",
        module="profile", subname="参股控股",
        key=("SECUCODE", "ORG_NAME"),
        frequency="static",
    ),

    # ===== 8. 同行比较 (Peer) - 6 reports =====
    ReportSpec(
        name="RPT_F10_RELATE_GN",
        module="peer", subname="行业归属",
        key=("SECUCODE", "BOARD_CODE"),
        frequency="static",
    ),
    ReportSpec(
        name="RPT_PCF10_INDUSTRY_GROWTH",
        module="peer", subname="成长性同行比较",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
        notes="EPS/营收 多年复合增长率 + 行业平均/中值/排名",
    ),
    ReportSpec(
        name="RPT_PCF10_INDUSTRY_CVALUE",
        module="peer", subname="估值同行比较",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
        notes="PEG/PE/PS 多年度 + 行业平均/中值/排名",
    ),
    ReportSpec(
        name="RPT_PCF10_INDUSTRY_DBFX",
        module="peer", subname="杜邦分析比较",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_PCF10_MARKETPER",
        module="peer", subname="市场表现",
        key=("SECUCODE", "TRADE_DATE"),
        date_field="TRADE_DATE",
        frequency="daily",
    ),
    ReportSpec(
        name="RPT_PCF10_INDUSTRY_MARKET",
        module="peer", subname="公司规模 (同行排序)",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
    ),

    # ===== 9. 盈利预测 (Forecast) - 5 reports =====
    ReportSpec(
        name="RPT_HSF10_RES_ORGRATING",
        module="forecast", subname="评级统计",
        key=("SECUCODE", "STATISTICS_DATE"),
        date_field="STATISTICS_DATE",
        frequency="weekly",
        notes="综合评级 + 各档家数",
    ),
    ReportSpec(
        name="RPT_HSF10_RES_ORGPREDICT",
        module="forecast", subname="机构预测 (按机构)",
        key=("SECUCODE", "ORG_CODE", "FORECAST_YEAR"),
        frequency="weekly",
    ),
    ReportSpec(
        name="RPT_HSF10_RESPREDICT_STATISTICS",
        module="forecast", subname="预测均值 (按年度)",
        key=("SECUCODE", "FORECAST_YEAR"),
        frequency="weekly",
        notes="EPS / EPS增速 / PE 多年度均值",
    ),
    ReportSpec(
        name="RPT_HSF10_RESPREDICT_COUNTSTATISTICS",
        module="forecast", subname="预测统计扩展",
        key=("SECUCODE", "FORECAST_YEAR", "INDICATOR"),
        frequency="weekly",
    ),
    ReportSpec(
        name="RPT_HSF10_RES_PREDICTDETAIL",
        module="forecast", subname="预测明细 (机构-分析师-发布日)",
        key=("SECUCODE", "ORG_CODE", "ANALYST_NAME", "RESEARCHER_DATE"),
        date_field="RESEARCHER_DATE",
        frequency="weekly",
    ),

    # ===== 10. 研究报告 (Research) - 走 np-creport-pc, 单独处理 =====

    # ===== 11. 财务分析 (Financial) - 12 reports =====
    ReportSpec(
        name="RPT_F10_PUBLIC_COMPANYTPYE",
        module="financial", subname="⭐ 公司类型 (财报模板)",
        key=("SECUCODE",),
        frequency="static",
        notes="入库前必查, 决定字段集 (一般/银行/保险/券商)",
    ),
    ReportSpec(
        name="RPT_F10_FINANCE_MAINFINADATA",
        module="financial", subname="主要指标 (按报告期)",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
        api="v0",
        v0_sty="APP_F10_MAINFINADATA",
        notes="v0 接口, 历史 200 期",
    ),
    ReportSpec(
        name="RPT_F10_QTR_MAINFINADATA",
        module="financial", subname="主要指标 (按单季)",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_F10_FINANCE_DUPONT",
        module="financial", subname="杜邦分析",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_F10_FINANCE_GBALANCE",
        module="financial", subname="资产负债表",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
        api="v0",
        v0_sty="F10_FINANCE_GBALANCE",
    ),
    ReportSpec(
        name="RPT_F10_FINANCE_GINCOME",
        module="financial", subname="利润表 (累计)",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
        api="v0",
        v0_sty="APP_F10_GINCOME",
    ),
    ReportSpec(
        name="RPT_F10_FINANCE_GINCOMEQC",
        module="financial", subname="利润表 (单季)",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
        api="v0",
        v0_sty="PC_F10_GINCOMEQC",
    ),
    ReportSpec(
        name="RPT_F10_FINANCE_GCASHFLOW",
        module="financial", subname="现金流量表 (累计)",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
        api="v0",
        v0_sty="APP_F10_GCASHFLOW",
    ),
    ReportSpec(
        name="RPT_F10_FINANCE_GCASHFLOWQC",
        module="financial", subname="现金流量表 (单季)",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
        api="v0",
        v0_sty="PC_F10_GCASHFLOWQC",
    ),
    ReportSpec(
        name="RPT_F10_FINANCE_GRATIO",
        module="financial", subname="百分比/同比报表",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_PCF10_ORIG_REPORT",
        module="financial", subname="原始财报披露",
        key=("SECUCODE", "REPORT_DATE"),
        date_field="REPORT_DATE",
        frequency="event",
    ),

    # ===== 12. 分红融资 (Dividend) - 7 reports =====
    ReportSpec(
        name="RPT_F10_DIVIDENDNEW_PROFILE",
        module="dividend", subname="分红融资概览",
        key=("SECUCODE",),
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_F10_DIVIDENDNEW_LITY",
        module="dividend", subname="分红提示 (派现概率)",
        key=("SECUCODE",),
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_PCF10_DIVIDENDNEW_RANK",
        module="dividend", subname="分红排名",
        key=("SECUCODE", "DATA_TYPE"),
        frequency="quarterly",
    ),
    ReportSpec(
        name="RPT_F10_DIVIDEND_MAIN",
        module="dividend", subname="分红明细",
        key=("SECUCODE", "NOTICE_DATE"),
        date_field="NOTICE_DATE",
        frequency="event",
        notes="替代 ak.stock_history_dividend_detail",
    ),
    ReportSpec(
        name="RPT_F10_DIVIDEND_COMPRE",
        module="dividend", subname="分红汇总 (按年)",
        key=("SECUCODE", "STATISTICS_YEAR"),
        frequency="yearly",
    ),
    ReportSpec(
        name="RPT_F10_DIVIDEND_3YEAR",
        module="dividend", subname="近 3 年分红汇总",
        key=("SECUCODE",),
        frequency="yearly",
    ),
    ReportSpec(
        name="RPT_F10_DIVIDEND_SEO",
        module="dividend", subname="融资明细 (增发/配股)",
        key=("SECUCODE", "NOTICE_DATE"),
        date_field="NOTICE_DATE",
        frequency="event",
    ),
    ReportSpec(
        name="RPT_F10_DIVIDEND_CURVE",
        module="dividend", subname="除权除息曲线",
        key=("SECUCODE", "TRADE_DATE"),
        date_field="TRADE_DATE",
        frequency="daily",
    ),
    ReportSpec(
        name="RPT_F10_DIVIDEND_EFFECT",
        module="dividend", subname="分红影响",
        key=("SECUCODE",),
        frequency="daily",
    ),

    # ===== 13. 股本结构 (Share Capital) - 3 reports =====
    ReportSpec(
        name="RPT_F10_EH_EQUITY",
        module="share_capital", subname="股本结构 / 历年变动",
        key=("SECUCODE", "END_DATE"),
        date_field="END_DATE",
        frequency="event",
        notes="pageSize=1 取最新; 全量取历史变动",
    ),

    # ===== 14. 公司高管 (Executives) - 2 reports =====
    ReportSpec(
        name="RPT_F10_ORGINFO_MANAINTRO",
        module="executives", subname="高管列表 / 简介",
        key=("SECUCODE", "PERSON_NAME"),
        frequency="static",
    ),
    ReportSpec(
        name="RPT_F10_TRADE_EXCHANGEHOLD",
        module="executives", subname="高管及相关人员持股变动",
        key=("SECUCODE", "CHANGE_DATE", "CHANGE_PERSON"),
        date_field="CHANGE_DATE",
        frequency="event",
        notes="兼容字段 RPT_EXECUTIVE_HOLD_DETAILS",
    ),

    # ===== 15. 资本运作 (Capital Ops) - 2 reports =====
    ReportSpec(
        name="RPT_F10_CAPITAL_RAISE",
        module="capital_ops", subname="募集资金来源",
        key=("SECUCODE", "NOTICE_DATE"),
        date_field="NOTICE_DATE",
        frequency="event",
    ),
    ReportSpec(
        name="RPT_F10_CAPITAL_ITEM",
        module="capital_ops", subname="项目进度",
        key=("SECUCODE", "PROJECT_NAME"),
        frequency="event",
    ),

    # ===== 16. 关联个股 (Related) - 5 reports =====
    ReportSpec(
        name="RPT_F10_RELATE_RANK",
        module="related", subname="同行业/概念/地域排名",
        key=("SECUCODE", "BOARD_CODE"),
        frequency="daily",
        notes="BOARD_TYPE_NEW=2 行业 / 3 概念 / 4 地域",
    ),
]


# 索引: name → ReportSpec
REPORT_BY_NAME: dict[str, ReportSpec] = {r.name: r for r in REPORTS}


def get_report(name: str) -> ReportSpec:
    """按 reportName 拿 spec, 找不到抛 KeyError."""
    if name not in REPORT_BY_NAME:
        raise KeyError(f"未注册的 reportName: {name}")
    return REPORT_BY_NAME[name]


def reports_by_module(module: str) -> list[ReportSpec]:
    """按模块名取所有 reports."""
    return [r for r in REPORTS if r.module == module]


def reports_by_frequency(freq: str) -> list[ReportSpec]:
    """按更新频率取所有 reports."""
    return [r for r in REPORTS if r.frequency == freq]


def stats() -> dict:
    """注册表统计."""
    from collections import Counter
    return {
        "total": len(REPORTS),
        "by_module": dict(Counter(r.module for r in REPORTS)),
        "by_frequency": dict(Counter(r.frequency for r in REPORTS)),
        "by_api": dict(Counter(r.api for r in REPORTS)),
        "batch_friendly": sum(1 for r in REPORTS if r.batch_friendly),
    }
