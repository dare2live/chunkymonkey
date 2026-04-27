"""
全市场数据路由

负责下载十大流通股东数据到 market_raw_holdings。

P6.1 迁移 (2026-04-27): 数据通道从 datacenter-web 直拉改走 miaoxiang
  (aif10_scraper.AIF10Client → datacenter.eastmoney.com/securities/api/data/v1/get).
  reportName 不变 (RPT_F10_EH_FREEHOLDERS), 字段兼容. 字段差异:
  - 妙想没 FREE_HOLDNUM 字段 → _map_api_row 的 _first() fallback 自动用 HOLD_NUM
  - 其他关键字段 (SECUCODE/HOLDER_NAME/HOLDER_RANK/END_DATE/UPDATE_DATE/HOLD_RATIO/
    HOLDER_MARKET_CAP/HOLDER_NEWTYPE/HOLD_NUM_CHANGE) 全一致.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter

from services.db import get_conn
from services.utils import safe_float as _safe_float

logger = logging.getLogger("cm-api")
router = APIRouter()

# 数据源: 走 miaoxiang/aif10-scraper. 共享一个 AIF10Client 复用 Session.
REPORT_NAME = "RPT_F10_EH_FREEHOLDERS"
PAGE_SIZE = 500

# 兼容老代码 import: 已退役, 仅留命名占位
EASTMONEY_ENDPOINT = "(deprecated, P6.1 迁移到 aif10_scraper)"
_BROWSER_HEADERS = {}  # 兼容 backend/routers/updater.py 旧 import

_aif10_client = None
def _get_aif10_client():
    global _aif10_client
    if _aif10_client is None:
        from aif10_scraper import AIF10Client
        _aif10_client = AIF10Client(retry=2, timeout=20.0)
    return _aif10_client


def _compact_date(val) -> str:
    if not val:
        return ""
    s = str(val).strip()[:10]
    return s.replace("-", "").replace("/", "")


def _safe_int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _first(row, *keys):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


def _map_api_row(row: dict) -> dict:
    """将东财 API 返回的一行映射为 market_raw_holdings 字段"""
    secucode = str(row.get("SECUCODE", "") or "")
    code = secucode.split(".")[0] if "." in secucode else str(row.get("SECURITY_CODE", ""))

    return {
        "holder_name": str(row.get("HOLDER_NAME", "") or "").strip(),
        "stock_code": code,
        "stock_name": str(row.get("SECURITY_NAME_ABBR", "") or "").strip(),
        "report_date": _compact_date(_first(row, "END_DATE", "REPORT_DATE")),
        "notice_date": _compact_date(_first(row, "UPDATE_DATE", "NOTICE_DATE")),
        "holder_rank": _safe_int(_first(row, "HOLDER_RANK", "HOLDER_RANKN")),
        "hold_amount": _safe_float(_first(row, "FREE_HOLDNUM", "HOLD_NUM")),
        "hold_market_cap": _safe_float(_first(row, "HOLDER_MARKET_CAP", "HOLD_MARKET_CAP")),
        "hold_ratio": _safe_float(_first(row, "HOLD_RATIO", "FREE_RATIO", "FREEHOLDRATIO")),
        "holder_type": str(row.get("HOLDER_NEWTYPE", "") or row.get("HOLDER_TYPE", "") or ""),
        "hold_change": str(row.get("HOLDER_STATEE", "") or row.get("HOLDSTATE", "") or ""),
        "hold_change_num": _safe_float(_first(row, "HOLD_NUM_CHANGE", "HOLD_CHANGE", "HOLD_CHANGE_NUM")),
        "raw_json": json.dumps(row, ensure_ascii=False, default=str),
    }


async def _fetch_page(client, filter_str: str, page: int) -> dict:
    """走 miaoxiang/aif10-scraper 拉一页 RPT_F10_EH_FREEHOLDERS.

    保持原签名 (client/filter_str/page) 让 backend/routers/updater.py 不用改.
    返回结构兼容老 datacenter-web shape: {"success": True, "result": {pages, count, data}}.

    第一参数 client 已退役 (httpx.AsyncClient), 这里忽略, 走全局 AIF10Client.
    """
    aif = _get_aif10_client()
    # AIF10Client.get_v1 是 sync, 用 to_thread 让 updater.py 仍能 await
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: aif.get_v1(
            REPORT_NAME,
            page=page,
            page_size=PAGE_SIZE,
            sort_columns="UPDATE_DATE,SECURITY_CODE,HOLDER_RANK",
            sort_types="-1,1,1",
            filter_expr=filter_str if filter_str else None,
        ),
    )
    # AIF10Client 返回 {"pages":..., "data":..., "count":...}
    # 包装成 datacenter-web 兼容 shape, 上游 updater.py 不动.
    return {
        "success": True,
        "result": {
            "pages": result.get("pages", 0),
            "count": result.get("count", 0),
            "data": result.get("data", []),
        },
    }


def _upsert_batch(conn, rows: list) -> int:
    """批量写入 market_raw_holdings，返回成功写入/更新行数.

    DuckDB 早期迁移过来的 market_raw_holdings 可能没有真实 UNIQUE 约束，
    此时 ON CONFLICT 会逐行 BinderException。这里显式按业务键删除再插入，
    避免“分页完成但 0 行落库”的静默失败。
    """
    now = datetime.now().isoformat()
    written = 0
    errors = 0
    for r in rows:
        try:
            rank_val = r["holder_rank"]
            if rank_val is not None:
                conn.execute("""
                    DELETE FROM market_raw_holdings
                    WHERE holder_name = ? AND stock_code = ? AND report_date = ? AND holder_rank IS NULL
                """, (r["holder_name"], r["stock_code"], r["report_date"]))
                conn.execute("""
                    DELETE FROM market_raw_holdings
                    WHERE holder_name = ? AND stock_code = ? AND report_date = ? AND holder_rank = ?
                """, (r["holder_name"], r["stock_code"], r["report_date"], rank_val))
            else:
                conn.execute("""
                    DELETE FROM market_raw_holdings
                    WHERE holder_name = ? AND stock_code = ? AND report_date = ? AND holder_rank IS NULL
                """, (r["holder_name"], r["stock_code"], r["report_date"]))

            conn.execute("""
                INSERT INTO market_raw_holdings
                    (holder_name, stock_code, stock_name, report_date, notice_date,
                     holder_rank, hold_amount, hold_market_cap, hold_ratio,
                     holder_type, hold_change, hold_change_num, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["holder_name"], r["stock_code"], r["stock_name"],
                r["report_date"], r["notice_date"],
                rank_val, r["hold_amount"], r["hold_market_cap"], r["hold_ratio"],
                r["holder_type"], r["hold_change"], r["hold_change_num"],
                r["raw_json"], now
            ))
            written += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                logger.warning(
                    "[upsert] 写入失败 %s/%s/%s/%s: %s",
                    r.get("stock_code"), r.get("report_date"),
                    r.get("holder_rank"), r.get("holder_name"), e,
                )
            else:
                logger.debug("[upsert] skip: %s", e)
    if errors:
        logger.warning("[upsert] 本批次 %d/%d 行写入失败", errors, len(rows))
    return written


@router.get("/market/status")
async def market_status():
    """全市场数据概况"""
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

        total = conn.execute("SELECT COUNT(*) FROM market_raw_holdings").fetchone()[0]
        latest = conn.execute("SELECT MAX(notice_date) FROM market_raw_holdings").fetchone()[0]
        stocks = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM market_raw_holdings").fetchone()[0]
        matched_stocks = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM inst_holdings").fetchone()[0]
        current_stocks = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM mart_current_relationship").fetchone()[0]
        periods = conn.execute("SELECT COUNT(DISTINCT report_date) FROM market_raw_holdings").fetchone()[0]
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
