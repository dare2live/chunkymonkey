#!/usr/bin/env python3
"""Build the TDX-first data need coverage and source priority tables."""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402

logger = logging.getLogger("tdx_data_need_coverage")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

ROOT = Path(__file__).resolve().parents[3]
CHUNKY = ROOT / "chunky-monkey-v2"
TDXHUB = ROOT / "tdxhub"
MIAOXIANG = ROOT / "miaoxiang"

INPUT_PATHS = [
    CHUNKY / "backend/services/data_sources/clients_registry.py",
    CHUNKY / "backend/services/data_sources/data_routes.py",
    CHUNKY / "backend/services/data_lineage/registry.py",
    CHUNKY / "backend/services/model_feature_schema.py",
    CHUNKY / "backend/scripts/build_candidate_feature_panel.py",
    TDXHUB / "docs/capability-map.md",
    MIAOXIANG / "aif10_scraper/registry.py",
]


DDL = """
CREATE TABLE IF NOT EXISTS mart_tdx_data_need_coverage (
    need_id TEXT PRIMARY KEY,
    need_name TEXT NOT NULL,
    consumer TEXT,
    current_source TEXT,
    tdxhub_capability TEXT,
    tdx_coverage_level TEXT,
    preferred_source TEXT NOT NULL,
    fallback_source TEXT,
    action TEXT NOT NULL,
    notes TEXT,
    built_at TEXT
);

CREATE TABLE IF NOT EXISTS dim_data_source_priority (
    data_domain TEXT PRIMARY KEY,
    preferred_source TEXT NOT NULL,
    fallback_1 TEXT,
    fallback_2 TEXT,
    reason TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS mart_data_source_reassignment_proposal (
    table_name TEXT PRIMARY KEY,
    current_source TEXT,
    proposed_primary_source TEXT NOT NULL,
    fallback_source TEXT,
    migration_required BOOLEAN DEFAULT FALSE,
    risk TEXT,
    reason TEXT,
    built_at TEXT
);
"""


NEEDS = [
    ("need_001", "A 股代码、市场列表", "security_master", "akshare/tdx mixed", "tdxhub.stocks", "full", "tdxhub_quote", "akshare", "keep_tdx_primary", "TDX code universe is sufficient for A-share routing."),
    ("need_002", "日 K 与收益率标签", "feature_panel", "akshare:stock_zh_a_hist", "tdxhub.bars/k", "full", "tdxhub_quote", "akshare", "downgrade_akshare_to_fallback", "Price labels should prefer TDX bars."),
    ("need_003", "指数 K 与市场基准", "feature_panel", "akshare/index", "tdxhub.index_bars", "full", "tdxhub_quote", "akshare", "prefer_tdx", "Index bars are a TDX stable source."),
    ("need_004", "除权除息复权", "price_adjustment", "akshare xdxr/fhps", "tdxhub.xdxr", "full", "tdxhub_quote", "miaoxiang", "prefer_tdx", "TDX xdxr is the primary adjustment feed."),
    ("need_005", "行业与板块映射", "dimension", "tdxhub/akshare mixed", "tdxhub.block", "partial", "tdxhub_quote", "miaoxiang", "keep_tdx_primary", "TDX block covers mapping; miaoxiang can add descriptive taxonomy."),
    ("need_006", "财务三表原始字段", "fundamental", "raw_gpcw_detail", "tdxhub.gpcw", "full", "tdxhub_gpcw", "akshare", "keep_tdx_primary", "gpcw wide/detail rows cover quarterly statements."),
    ("need_007", "财务质量因子", "model_feature", "raw_gpcw_detail", "tdxhub.gpcw", "full", "tdxhub_gpcw", "akshare", "auto_feature_validate", "OCF/profit, receivables, inventory and debt ratios are derivable."),
    ("need_008", "盈利成长因子", "model_feature", "raw_gpcw_detail", "tdxhub.gpcw", "full", "tdxhub_gpcw", "akshare", "auto_feature_validate", "Revenue/profit/EPS/ROE trends should be auto-derived."),
    ("need_009", "股东人数与筹码集中", "model_feature", "TDX F10 + gpcw", "tdxhub.gpcw + TDX F10", "full", "tdxhub_gpcw", "tdxhub_f10", "cross_validate", "gpcw aggregate and F10 history should cross-check."),
    ("need_010", "十大股东聚合", "holder_fact", "gpcw/F10", "tdxhub.gpcw", "partial", "tdxhub_gpcw", "tdxhub_f10", "prefer_aggregate_tdx", "Aggregate shares are available; names still need F10/miaoxiang."),
    ("need_011", "十大股东进入退出事件", "holder_event", "TDX F10/miaoxiang", "TDX F10 raw/extra", "partial", "tdxhub_f10", "miaoxiang", "keep_f10_primary_with_fallback", "TDX parser should remain first for event extraction."),
    ("need_012", "机构持仓聚合", "institution_feature", "raw_gpcw_detail", "tdxhub.gpcw", "full", "tdxhub_gpcw", "miaoxiang", "keep_tdx_primary", "Fund/QFII/social-security/broker/insurance aggregates exist."),
    ("need_013", "机构持仓明细到机构名", "institution_detail", "miaoxiang", "tdxhub.gpcw aggregate only", "none", "miaoxiang", "tdxhub_gpcw", "keep_miaoxiang_detail", "gpcw cannot replace institution-name detail."),
    ("need_014", "基金持股明细", "fund_holding", "TDX F10 + miaoxiang", "TDX F10 section 7", "partial", "tdxhub_f10", "miaoxiang", "keep_f10_with_fallback", "F10 section 7 provides details; miaoxiang remains fallback."),
    ("need_015", "业绩预告", "forecast_feature", "raw_gpcw_detail", "tdxhub.gpcw", "full", "tdxhub_gpcw", "miaoxiang", "deep_dive_priority", "Forecast fields are the strongest current family."),
    ("need_016", "业绩快报", "express_feature", "raw_gpcw_detail", "tdxhub.gpcw", "full", "tdxhub_gpcw", "miaoxiang", "deep_dive_priority", "Express fields and announce dates are in gpcw."),
    ("need_017", "主营构成", "business_segment", "miaoxiang", "not in gpcw", "none", "miaoxiang", "akshare", "keep_miaoxiang", "Product/region/industry revenue split is not supplied by TDX gpcw."),
    ("need_018", "估值分位", "valuation", "miaoxiang/aif10", "tdx quote can compute raw valuation only", "none", "miaoxiang", "akshare", "keep_miaoxiang", "Historical percentile definitions remain miaoxiang-owned."),
    ("need_019", "龙虎榜", "event_detail", "eastmoney/miaoxiang", "not in current TDX path", "none", "miaoxiang", "akshare", "keep_external", "TDX path is not the primary LHB detail source."),
    ("need_020", "融资融券", "margin", "akshare", "not in current TDX path", "none", "miaoxiang", "akshare", "evaluate_miaoxiang_or_keep_akshare", "No stable TDX feed in this project."),
    ("need_021", "机构调研", "survey", "miaoxiang", "not in gpcw", "none", "miaoxiang", "akshare", "keep_miaoxiang", "Research survey details remain miaoxiang-owned."),
    ("need_022", "机构预测/评级", "analyst", "miaoxiang/aif10", "company forecast only", "none", "miaoxiang", "akshare", "keep_miaoxiang", "Company forecast is not sell-side consensus."),
    ("need_023", "分红", "capital_event", "akshare/miaoxiang", "tdxhub.xdxr", "partial", "tdxhub_quote", "miaoxiang", "split_primary_and_detail", "Adjustment facts from TDX; detailed plans from miaoxiang."),
    ("need_024", "增发/解禁/回购", "capital_event", "akshare/miaoxiang", "limited TDX", "partial", "miaoxiang", "akshare", "keep_detail_source", "Complex event details are not fully covered by TDX."),
    ("need_025", "高管、实控人、公司概况", "company_profile", "miaoxiang/F10 raw", "TDX F10 raw", "partial", "miaoxiang", "tdxhub_f10", "parse_f10_or_keep_miaoxiang", "Use F10 if stable, otherwise keep miaoxiang."),
    ("need_026", "ETF 行情与成分验证", "etf", "akshare/tdx mixed", "tdxhub.quotes", "partial", "tdxhub_quote", "akshare", "prefer_tdx_price_fallback_components", "TDX can price; constituent detail may need fallback."),
]


PRIORITIES = [
    ("quotes_and_labels", "tdxhub_quote", "akshare", None, "行情、指数、复权优先 TDX，akshare 只兜底。"),
    ("gpcw_financials", "tdxhub_gpcw", "akshare", None, "财务三表、质量、成长、预告快报从 gpcw 主供。"),
    ("holder_aggregate", "tdxhub_gpcw", "tdxhub_f10", "miaoxiang", "聚合口径优先 gpcw，明细事件由 F10/miaoxiang 补。"),
    ("holder_detail", "tdxhub_f10", "miaoxiang", None, "F10 raw/extra 能解析时优先 TDX，失败时 miaoxiang 补。"),
    ("institution_aggregate", "tdxhub_gpcw", "miaoxiang", None, "机构类型聚合由 gpcw 主供。"),
    ("institution_detail", "miaoxiang", "tdxhub_gpcw", None, "机构名明细不是 gpcw 强项。"),
    ("valuation_and_consensus", "miaoxiang", "akshare", None, "估值分位和卖方一致预期不由 TDX/gpcw 主供。"),
    ("survey_and_text", "miaoxiang", "akshare", None, "机构调研、文本、主营构成保留 miaoxiang。"),
    ("capital_events", "tdxhub_quote", "miaoxiang", "akshare", "除权除息用 TDX；复杂资本事件细节由 miaoxiang/akshare 补。"),
]


REASSIGNMENTS = [
    ("fact_feature_panel", "akshare price + derived", "tdxhub_quote", "akshare", True, "medium", "价格和 forward labels 可从 TDX bars 主供。"),
    ("raw_gpcw_detail", "tdxhub.gpcw", "tdxhub_gpcw", "akshare", False, "low", "已经是 TDX gpcw 主链路。"),
    ("raw_tdx_gpcw_wide", "tdxhub.gpcw", "tdxhub_gpcw", None, False, "low", "保留全字段 JSON 作为语义和自动特征源。"),
    ("fact_tdx_gpcw_auto_feature_quarterly", "new derived", "tdxhub_gpcw", None, False, "low", "由 gpcw semantic 自动派生。"),
    ("fact_holder_count_period", "TDX F10 extra", "tdxhub_f10", "tdxhub_gpcw", False, "low", "股东人数历史优先 F10，gpcw 互校。"),
    ("fact_top10_holder_period", "TDX F10/miaoxiang", "tdxhub_f10", "miaoxiang", False, "medium", "明细股东事件仍需 F10 parser。"),
    ("fact_fund_holding_tdx_f10", "TDX F10 section 7", "tdxhub_f10", "miaoxiang", False, "medium", "基金持股明细由 F10 section 7 主供。"),
    ("fact_common_major_holder_stock", "TDX F10 extra", "tdxhub_f10", "miaoxiang", False, "medium", "同大股东关系为 F10 extra 深挖产物。"),
    ("raw_aif10_forecast_consensus", "miaoxiang/aif10", "miaoxiang", "akshare", False, "low", "卖方一致预期不能被公司业绩预告替代。"),
    ("raw_institution_surveys", "miaoxiang", "miaoxiang", "akshare", False, "low", "机构调研 TDX/gpcw 不覆盖。"),
    ("raw_lhb_daily", "akshare/eastmoney", "miaoxiang", "akshare", True, "medium", "龙虎榜明细仍需外部源。"),
    ("raw_margin_daily", "akshare", "miaoxiang", "akshare", True, "medium", "融资融券暂无 TDX 主供链路。"),
    ("raw_capital_dividend_detail", "akshare", "tdxhub_quote", "miaoxiang", True, "medium", "除权除息用 TDX，方案细节保留 fallback。"),
    ("raw_capital_repurchase", "akshare", "miaoxiang", "akshare", False, "low", "回购明细 TDX/gpcw 不覆盖。"),
]


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)


def _read_input_inventory() -> list[dict[str, Any]]:
    inventory = []
    for path in INPUT_PATHS:
        text = path.read_text(encoding="utf-8", errors="ignore")
        inventory.append(
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": len(text.encode("utf-8")),
                "lines": text.count("\n") + 1,
            }
        )
    return inventory


def audit_tdx_data_need_coverage(conn: Any) -> dict[str, Any]:
    ensure_tables(conn)
    input_inventory = _read_input_inventory()
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_tdx_data_need_coverage
        (need_id, need_name, consumer, current_source, tdxhub_capability,
         tdx_coverage_level, preferred_source, fallback_source, action, notes, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(*row, built_at) for row in NEEDS],
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO dim_data_source_priority
        (data_domain, preferred_source, fallback_1, fallback_2, reason, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(*row, built_at) for row in PRIORITIES],
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_data_source_reassignment_proposal
        (table_name, current_source, proposed_primary_source, fallback_source,
         migration_required, risk, reason, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(*row, built_at) for row in REASSIGNMENTS],
    )
    conn.commit()
    return {
        "coverage_rows": len(NEEDS),
        "priority_rows": len(PRIORITIES),
        "reassignment_rows": len(REASSIGNMENTS),
        "input_files_read": input_inventory,
        "built_at": built_at,
    }


def main() -> int:
    conn = get_conn()
    try:
        result = audit_tdx_data_need_coverage(conn)
        logger.info("tdx data need coverage: %s", result)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
