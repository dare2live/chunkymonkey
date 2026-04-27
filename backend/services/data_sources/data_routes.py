"""data_routes — 真实业务数据 → 通道 映射.

这是从 chunky-monkey-v2 现有代码反推出来的"实际跑什么", 不是 registry 的声明.
更新此文件: 当 backend 加新 sync step / 改通道时同步更新.

每条:
  data_name: 业务数据名 (UI 展示)
  raw_table: 写入的 raw / dim / 直接 mart 表
  source: 数据源名 (跟 registry source.name 对齐)
  protocol: 实际协议 / endpoint
  freshness: T-0 / T-1 / 季 / 静态 / 事件
  step_id: updater.py 里对应的 step
  client_module: backend/services/ 里的实际 client 文件
  notes: 字段口径 / 反爬 / 速度等备注
  status: connected / pending (registry 声明但还没接通)
"""
from __future__ import annotations

DATA_ROUTES = [
    # ---------- tdxhub (通达信) ----------
    {
        "data_name": "日 K 线",
        "raw_table": "market.duckdb#price_kline",
        "source": "tdxhub",
        "protocol": "TdxHq_API.get_security_bars",
        "freshness": "T-0",
        "step_id": "sync_market_data",
        "client_module": "services/akshare_client.py",
        "notes": "tdxhub 主, akshare 兜底 (东财→新浪→腾讯)",
        "status": "connected",
    },
    {
        "data_name": "月 K 线",
        "raw_table": "market.duckdb#price_kline_monthly",
        "source": "tdxhub",
        "protocol": "TdxHq_API.get_security_bars (freq=10)",
        "freshness": "月",
        "step_id": "sync_market_data",
        "client_module": "services/akshare_client.py",
        "notes": "增量, 月底新增 1 行",
        "status": "connected",
    },
    {
        "data_name": "申万行业",
        "raw_table": "dim_stock_tdx_industry",
        "source": "tdxhub",
        "protocol": "TdxHq_API.get_block_info (tdxhy.cfg)",
        "freshness": "静态",
        "step_id": "sync_industry",
        "client_module": "services/tdx_industry_client.py",
        "notes": "5607 行 L1=13/L2=56/L3=76, 历史快照入 dim",
        "status": "connected",
    },
    {
        "data_name": "板块/概念",
        "raw_table": "dim_stock_tdx_block",
        "source": "tdxhub",
        "protocol": "TdxHq_API.get_block_info (block_zhishu)",
        "freshness": "静态",
        "step_id": "sync_industry",
        "client_module": "services/block_client.py",
        "notes": "概念板块归属",
        "status": "connected",
    },
    {
        "data_name": "财务 gpcw (8 期)",
        "raw_table": "raw_gpcw_financial",
        "source": "tdxhub",
        "protocol": "Affair.parse (二进制)",
        "freshness": "季",
        "step_id": "sync_financial",
        "client_module": "services/financial_client.py + services/tdx_affair_client.py",
        "notes": "tdxhub 独家, gpcw 文件解析 585 字段",
        "status": "connected",
    },
    {
        "data_name": "除权除息",
        "raw_table": "raw_xdxr",
        "source": "tdxhub",
        "protocol": "TdxHq_API.get_xdxr_info",
        "freshness": "T-1",
        "step_id": "sync_market_data (复权)",
        "client_module": "services/xdxr_client.py",
        "notes": "送转配权利登记日, 用于 K 线复权",
        "status": "connected",
    },

    # ---------- em_datacenter (东财 datacenter-web) ----------
    {
        "data_name": "十大流通股东 ⭐",
        "raw_table": "market_raw_holdings",
        "source": "em_datacenter",
        "protocol": "datacenter-web RPT_F10_EH_FREEHOLDERS",
        "freshness": "事件 (季报披露驱动)",
        "step_id": "sync_raw",
        "client_module": "routers/market.py",
        "notes": "项目核心数据, 增量 filter UPDATE_DATE>=下一日",
        "status": "connected",
    },
    {
        "data_name": "龙虎榜",
        "raw_table": "raw_lhb_daily",
        "source": "em_datacenter",
        "protocol": "datacenter-web RPT_DAILYBILLBOARD_DETAILSNEW",
        "freshness": "T-0",
        "step_id": "sync_lhb",
        "client_module": "services/lhb_client.py",
        "notes": "增量按 trade_date",
        "status": "connected",
    },
    {
        "data_name": "QFII 季度持仓",
        "raw_table": "raw_qfii_holding_quarterly",
        "source": "em_datacenter",
        "protocol": "datacenter-web RPT_DMSK_HOLDERS",
        "freshness": "季",
        "step_id": "sync_qfii",
        "client_module": "services/qfii_client.py",
        "notes": "替代 akshare.stock_qfii_*",
        "status": "connected",
    },
    {
        "data_name": "融资融券",
        "raw_table": "raw_margin_daily",
        "source": "em_datacenter",
        "protocol": "datacenter-web RPT_MARGIN_*",
        "freshness": "T-0",
        "step_id": "sync_margin",
        "client_module": "services/margin_client.py",
        "notes": "日明细",
        "status": "connected",
    },
    {
        "data_name": "机构调研事件",
        "raw_table": "mart_stock_survey_activity",
        "source": "em_datacenter",
        "protocol": "datacenter-web RPT_RESEARCH_*",
        "freshness": "T-0",
        "step_id": "sync_surveys",
        "client_module": "services/institution_survey_client.py",
        "notes": "替代 akshare.stock_jgdy_tj_em",
        "status": "connected",
    },

    # ---------- aif10 (妙想 F10) — 全部 pending, P6 接通 ----------
    {
        "data_name": "股东人数 (深历史)",
        "raw_table": "(待建)",
        "source": "aif10",
        "protocol": "RPT_F10_EH_HOLDERNUM",
        "freshness": "季",
        "step_id": "(待加 sync_aif10_holder_count)",
        "client_module": "services/data_sources/sources/aif10.py",
        "notes": "妙想独家, registry 已声明, 实际未调用",
        "status": "pending",
    },
    {
        "data_name": "估值分位 (PE/PB/PS PEG 历史)",
        "raw_table": "(待建)",
        "source": "aif10",
        "protocol": "RPT_STOCKVALUATIONTANTILE",
        "freshness": "T-0",
        "step_id": "(待加 sync_aif10_valuation)",
        "client_module": "services/data_sources/sources/aif10.py",
        "notes": "妙想独家",
        "status": "pending",
    },
    {
        "data_name": "同行排名 (估值/成长)",
        "raw_table": "(待建)",
        "source": "aif10",
        "protocol": "RPT_PCF10_INDUSTRY_CVALUE / RPT_PCF10_INDUSTRY_GROWTH",
        "freshness": "季",
        "step_id": "(待加)",
        "client_module": "services/data_sources/sources/aif10.py",
        "notes": "妙想独家",
        "status": "pending",
    },
    {
        "data_name": "卖方一致预期",
        "raw_table": "(待建)",
        "source": "aif10",
        "protocol": "RPT_HSF10_RES_ORGRATING",
        "freshness": "周",
        "step_id": "(待加)",
        "client_module": "services/data_sources/sources/aif10.py",
        "notes": "妙想独家",
        "status": "pending",
    },
    {
        "data_name": "财务历史 200 期",
        "raw_table": "(待建)",
        "source": "aif10",
        "protocol": "RPT_F10_FINANCE_MAINFINADATA (v0)",
        "freshness": "季",
        "step_id": "(待加)",
        "client_module": "services/data_sources/sources/aif10.py",
        "notes": "比 tdxhub 8 期更深, 接通后可替代/补充",
        "status": "pending",
    },

    # ---------- akshare (兜底) ----------
    {
        "data_name": "交易日历",
        "raw_table": "dim_trading_calendar",
        "source": "akshare",
        "protocol": "ak.tool_trade_date_hist_sina",
        "freshness": "静态",
        "step_id": "(启动时初始化)",
        "client_module": "services/security_master.py",
        "notes": "唯一兜底, 无替代源",
        "status": "connected",
    },
    {
        "data_name": "ETF 行情",
        "raw_table": "etf.duckdb",
        "source": "akshare",
        "protocol": "ak.fund_etf_spot_ths (同花顺源)",
        "freshness": "T-0",
        "step_id": "(ETF 模块独立)",
        "client_module": "services/etf_engine.py",
        "notes": "唯一兜底, 同花顺源",
        "status": "connected",
    },
]


def get_routes() -> list[dict]:
    """返回数据路由清单."""
    return DATA_ROUTES


def routes_by_source() -> dict[str, list[dict]]:
    """按 source 聚合."""
    out: dict[str, list[dict]] = {}
    for r in DATA_ROUTES:
        out.setdefault(r["source"], []).append(r)
    return out


def stats() -> dict:
    """聚合统计."""
    by_source = routes_by_source()
    by_status = {"connected": 0, "pending": 0}
    for r in DATA_ROUTES:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {
        "total": len(DATA_ROUTES),
        "by_source_count": {k: len(v) for k, v in by_source.items()},
        "by_status": by_status,
    }
