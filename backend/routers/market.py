"""市场层只读路由 (P7 退役 miaoxiang 分支后).

历史背景:
  P6.1 (2026-04-27) 把十大流通股东抓取从 datacenter-web 直拉迁到 miaoxiang
  RPT_F10_EH_FREEHOLDERS. P7 (2026-04-28) 整体下架 miaoxiang holders 通道,
  全量改走 tdxhub.holders.HolderFetcher → fact_top10_holder_period (canonical).

  抓取入口:
    backend/scripts/ingest_holders_tdxhub.py  (每日定时, 命令行)

  本模块现在只剩一个只读 /market/status, 供 UI 看库存. 其它历史符号 (REPORT_NAME /
  _fetch_page / _map_api_row / _upsert_batch / _BROWSER_HEADERS / aif10 client)
  随同 market_raw_holdings 表一并退役.
"""

import logging

from fastapi import APIRouter

from services.db import get_conn

logger = logging.getLogger("cm-api")
router = APIRouter()


@router.get("/market/status")
async def market_status():
    """全市场十大流通股东数据概况 (canonical: fact_top10_holder_period).

    过滤: holder_set='free' AND NOT is_secondary_class AND NOT is_exit_row.
    latest_notice_date 用 MAX(fetched_at) 兜底 (新表没有 notice_date 列).
    """
    conn = get_conn()
    try:
        from services.audit import load_quality_audit_snapshot

        audit = load_quality_audit_snapshot(conn)
        if audit and audit.get("layers"):
            raw = audit["layers"].get("raw", {})
            holdings = audit["layers"].get("holdings", {})
            current_rel = audit["layers"].get("current_relationship", {})
            return {
                "ok": True,
                "total_records": raw.get("count", 0),
                "latest_notice_date": raw.get("latest_notice"),
                "total_stocks": raw.get("stocks", 0),
                "matched_stocks": holdings.get("stocks", 0),
                "current_stocks": current_rel.get("stocks", 0),
                "total_periods": raw.get("total_periods", 0),
                "snapshot_meta": audit.get("snapshot_meta"),
            }

        # Fallback (审计快照不可用时直接读 fact_top10_holder_period)
        canonical_where = (
            "holder_set = 'free' AND NOT is_secondary_class AND NOT is_exit_row"
        )
        total = conn.execute(
            f"SELECT COUNT(*) FROM fact_top10_holder_period WHERE {canonical_where}"
        ).fetchone()[0]
        latest = conn.execute(
            f"SELECT MAX(fetched_at) FROM fact_top10_holder_period WHERE {canonical_where}"
        ).fetchone()[0]
        stocks = conn.execute(
            f"SELECT COUNT(DISTINCT stock_code) FROM fact_top10_holder_period WHERE {canonical_where}"
        ).fetchone()[0]
        matched_stocks = conn.execute(
            "SELECT COUNT(DISTINCT stock_code) FROM inst_holdings"
        ).fetchone()[0]
        current_stocks = conn.execute(
            "SELECT COUNT(DISTINCT stock_code) FROM mart_current_relationship"
        ).fetchone()[0]
        periods = conn.execute(
            f"SELECT COUNT(DISTINCT report_date) FROM fact_top10_holder_period WHERE {canonical_where}"
        ).fetchone()[0]
        return {
            "ok": True,
            "total_records": total,
            "latest_notice_date": latest,
            "total_stocks": stocks,
            "matched_stocks": matched_stocks,
            "current_stocks": current_stocks,
            "total_periods": periods,
        }
    finally:
        conn.close()
