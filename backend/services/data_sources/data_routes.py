"""data_routes — 真实业务数据 → 通道 映射 + 迁移目标.

每条记录:
  data_name:     业务数据名 (UI 展示)
  raw_table:     写入的 raw / dim / 直接 mart 表
  current:       当前实际通道
                 - source: tdxhub / aif10 / akshare / datacenter_web (过渡)
                 - protocol: 协议 / endpoint
                 - status: connected (已接) / pending (registry 声明未接) / transitional (datacenter-web 过渡)
  target:        长期迁移目标 (空 = 当前就是终态)
                 - source / protocol / 计划阶段 (P6 等)
  freshness:     T-0 / T-1 / 季 / 静态
  step_id:       updater.py 里对应的 step
  client_module: backend/services/ 里的实际 client 文件
  notes:         备注

设计原则 (来自用户要求):
- 数据源只 3 个: tdxhub (主) / aif10 妙想 (备) / akshare (兜底)
- "datacenter-web 直连" 不是独立 source, 是过渡产物
  → tdxhub 没有 + 妙想能拉的 → 迁妙想 (P6)
  → 妙想也没有的 → 走 akshare ak.X (容忍反爬, 用户接受)
"""
from __future__ import annotations


DATA_ROUTES = [
    # =================================================================
    # tdxhub 通道 (主源, 已稳定)
    # =================================================================
    {
        "data_name": "日 K 线",
        "raw_table": "market.duckdb#price_kline",
        "current": {"source": "tdxhub", "protocol": "TdxHq_API.get_security_bars", "status": "connected"},
        "target": None,
        "freshness": "T-0",
        "step_id": "sync_market_data",
        "client_module": "services/akshare_client.py (内部转 tdxhub)",
        "notes": "tdxhub 主, akshare 3 源 (东财/新浪/腾讯) 兜底",
    },
    {
        "data_name": "月 K 线",
        "raw_table": "market.duckdb#price_kline_monthly",
        "current": {"source": "tdxhub", "protocol": "TdxHq_API.get_security_bars (freq=10)", "status": "connected"},
        "target": None,
        "freshness": "月",
        "step_id": "sync_market_data",
        "client_module": "services/akshare_client.py",
        "notes": "增量, 月底新增 1 行",
    },
    {
        "data_name": "申万行业",
        "raw_table": "dim_stock_tdx_industry",
        "current": {"source": "tdxhub", "protocol": "TdxHq_API.get_block_info (tdxhy.cfg)", "status": "connected"},
        "target": None,
        "freshness": "静态",
        "step_id": "sync_industry",
        "client_module": "services/tdx_industry_client.py",
        "notes": "5607 行 L1=13/L2=56/L3=76, 历史快照入 dim",
    },
    {
        "data_name": "板块/概念",
        "raw_table": "dim_stock_tdx_block",
        "current": {"source": "tdxhub", "protocol": "TdxHq_API.get_block_info (block_zhishu)", "status": "connected"},
        "target": None,
        "freshness": "静态",
        "step_id": "sync_industry",
        "client_module": "services/block_client.py",
        "notes": "概念板块归属",
    },
    {
        "data_name": "财务 gpcw (8 期)",
        "raw_table": "raw_gpcw_financial",
        "current": {"source": "tdxhub", "protocol": "Affair.parse (二进制)", "status": "connected"},
        "target": None,
        "freshness": "季",
        "step_id": "sync_financial",
        "client_module": "services/financial_client.py + tdx_affair_client.py",
        "notes": "tdxhub 独家, gpcw 文件解析 585 字段",
    },
    {
        "data_name": "除权除息",
        "raw_table": "raw_xdxr",
        "current": {"source": "tdxhub", "protocol": "TdxHq_API.get_xdxr_info", "status": "connected"},
        "target": None,
        "freshness": "T-1",
        "step_id": "sync_market_data (复权)",
        "client_module": "services/xdxr_client.py",
        "notes": "送转配权利登记日, K 线复权用",
    },

    # =================================================================
    # aif10 (妙想) — 已能调通的 (registry 声明), 但 chunky-monkey-v2 还没接通到 sync step
    # =================================================================
    {
        "data_name": "十大流通股东 ⭐",
        "raw_table": "fact_top10_holder_period (raw 层: raw_tdx_f10_holder_research)",
        "current": {"source": "tdxhub", "protocol": "tdxhub.holders.HolderFetcher (F10 股东研究 双格式解析)", "status": "connected"},
        "target": None,
        "freshness": "事件 (季报披露驱动)",
        "step_id": "sync_raw",
        "client_module": "scripts/ingest_holders_tdxhub.py",
        "notes": "P7 (2026-04-28): retired miaoxiang RPT_F10_EH_FREEHOLDERS, switched to tdxhub.holders. 99.6% universe coverage, A/H 拆分, 含退出表.",
    },
    {
        "data_name": "股东人数 (深历史)",
        "raw_table": "(待建)",
        "current": {"source": "aif10", "protocol": "RPT_F10_EH_HOLDERNUM", "status": "pending"},
        "target": None,
        "freshness": "季",
        "step_id": "(待加 sync_aif10_holder_count)",
        "client_module": "data_sources/sources/aif10.py",
        "notes": "妙想独家 (tdxhub 没户数), registry 已声明等接通",
    },
    {
        "data_name": "估值分位 PE/PB/PS PEG (历史)",
        "raw_table": "(待建)",
        "current": {"source": "aif10", "protocol": "RPT_STOCKVALUATIONTANTILE", "status": "pending"},
        "target": None,
        "freshness": "T-0",
        "step_id": "(待加 sync_aif10_valuation)",
        "client_module": "data_sources/sources/aif10.py",
        "notes": "妙想独家",
    },
    {
        "data_name": "同行排名 (估值/成长)",
        "raw_table": "(待建)",
        "current": {"source": "aif10", "protocol": "RPT_PCF10_INDUSTRY_CVALUE / RPT_PCF10_INDUSTRY_GROWTH", "status": "pending"},
        "target": None,
        "freshness": "季",
        "step_id": "(待加)",
        "client_module": "data_sources/sources/aif10.py",
        "notes": "妙想独家",
    },
    {
        "data_name": "卖方一致预期",
        "raw_table": "(待建)",
        "current": {"source": "aif10", "protocol": "RPT_HSF10_RES_ORGRATING", "status": "pending"},
        "target": None,
        "freshness": "周",
        "step_id": "(待加)",
        "client_module": "data_sources/sources/aif10.py",
        "notes": "妙想独家",
    },
    {
        "data_name": "财务历史 200 期",
        "raw_table": "(待建)",
        "current": {"source": "aif10", "protocol": "RPT_F10_FINANCE_MAINFINADATA (v0)", "status": "pending"},
        "target": None,
        "freshness": "季",
        "step_id": "(待加)",
        "client_module": "data_sources/sources/aif10.py",
        "notes": "比 tdxhub 8 期更深, 接通后可补充",
    },

    # =================================================================
    # 过渡通道 (datacenter-web 直连) — 用户希望少用, P6 迁妙想
    # =================================================================
    {
        "data_name": "龙虎榜",
        "raw_table": "raw_lhb_daily",
        "current": {"source": "aif10", "protocol": "miaoxiang RPT_DAILYBILLBOARD_DETAILSNEW", "status": "connected"},
        "target": None,
        "freshness": "T-0",
        "step_id": "sync_lhb",
        "client_module": "services/lhb_client.py (P6.2 已迁)",
        "notes": "P6.2 (2026-04-28): reportName 不变, 字段 100% 兼容, 实测 4-20~25 共 385 行",
    },
    {
        "data_name": "QFII 持仓 (季)",
        "raw_table": "raw_qfii_holding_quarterly",
        "current": {"source": "aif10", "protocol": "miaoxiang RPT_DMSK_HOLDERS (个股+机构维度)", "status": "connected"},
        "target": None,
        "freshness": "季",
        "step_id": "sync_qfii",
        "client_module": "services/qfii_client.py (P6.3 已迁)",
        "notes": "P6.3 (2026-04-28): reportName 不变 (RPT_DMSK_HOLDERS), 字段全兼容. 实测 2025Q4 不变 = 34 行",
    },
    {
        "data_name": "融资融券",
        "raw_table": "raw_margin_daily",
        "current": {"source": "akshare", "protocol": "ak.stock_margin_detail_sse / ak.stock_margin_detail_szse", "status": "connected"},
        "target": None,
        "freshness": "T-0",
        "step_id": "sync_margin",
        "client_module": "services/margin_client.py",
        "notes": "走 akshare 库 (沪市+深市两个独立接口), 不走 datacenter-web. 没有迁移需求.",
    },
    {
        "data_name": "机构调研事件",
        "raw_table": "mart_stock_survey_activity",
        "current": {"source": "aif10", "protocol": "miaoxiang RPT_ORG_SURVEYNEW", "status": "connected"},
        "target": None,
        "freshness": "T-0",
        "step_id": "sync_surveys",
        "client_module": "services/institution_survey_client.py (P6.4 已迁)",
        "notes": "P6.4 (2026-04-28): reportName 不变, 实测 4-15+ 共 16546 行",
    },
    {
        "data_name": "市场信号 (融资融券/北向叠加层)",
        "raw_table": "(只读, 给详情页面叠加使用)",
        "current": {"source": "aif10", "protocol": "miaoxiang RPT_DAILYBILLBOARD_DETAILSNEW 等通用 reportName", "status": "connected"},
        "target": None,
        "freshness": "T-0 (10min cache)",
        "step_id": "(详情页 lazy)",
        "client_module": "services/market_signals.py (P6.5 已迁)",
        "notes": "P6.5 (2026-04-28): _eastmoney_rows 把 httpx 直拉 datacenter-web 改为 AIF10Client.get_v1, 保留 cache 逻辑.",
    },
    {
        "data_name": "个股资金流",
        "raw_table": "raw_fund_flow_*",
        "current": {"source": "(已下架)", "protocol": "(2026-04 下架, 反爬高发)", "status": "transitional"},
        "target": {"source": "(评估)", "protocol": "ak.stock_individual_fund_flow_em / 妙想 push2 / deprecate", "phase": "未定"},
        "freshness": "—",
        "step_id": "(模块停用)",
        "client_module": "(fetch_fund_flow_daily.py 已删, 见 git 历史 491072d1)",
        "notes": "项目曾走 datacenter-web 拉历史 + push2his 实时, 反爬卡死. 用户已确认整体下架资金流模块.",
    },

    # =================================================================
    # akshare (真兜底)
    # =================================================================
    {
        "data_name": "交易日历",
        "raw_table": "dim_trading_calendar",
        "current": {"source": "akshare", "protocol": "ak.tool_trade_date_hist_sina", "status": "connected"},
        "target": None,
        "freshness": "静态",
        "step_id": "(启动时初始化)",
        "client_module": "services/security_master.py",
        "notes": "唯一兜底, 无替代源",
    },
    {
        "data_name": "ETF 行情",
        "raw_table": "etf.duckdb",
        "current": {"source": "akshare", "protocol": "ak.fund_etf_spot_ths (同花顺源)", "status": "connected"},
        "target": None,
        "freshness": "T-0",
        "step_id": "(ETF 模块独立)",
        "client_module": "services/etf_engine.py",
        "notes": "唯一兜底, 同花顺源",
    },
]


def get_routes() -> list[dict]:
    """返回数据路由清单."""
    return DATA_ROUTES


def routes_by_status() -> dict[str, list[dict]]:
    """按 current.status 聚合."""
    out: dict[str, list[dict]] = {}
    for r in DATA_ROUTES:
        s = r.get("current", {}).get("status", "unknown")
        out.setdefault(s, []).append(r)
    return out


def stats() -> dict:
    """聚合统计."""
    by_status = {"connected": 0, "transitional": 0, "pending": 0}
    by_source = {}
    has_target = 0
    for r in DATA_ROUTES:
        s = r.get("current", {}).get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
        src = r.get("current", {}).get("source", "?")
        by_source[src] = by_source.get(src, 0) + 1
        if r.get("target"):
            has_target += 1
    return {
        "total": len(DATA_ROUTES),
        "by_status": by_status,
        "by_current_source": by_source,
        "with_migration_target": has_target,
    }
