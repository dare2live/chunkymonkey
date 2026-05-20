"""市场感知 (Market Perception) router — Codex 扩展模块入口.

状态: STUB only — 我 (Claude) 先 stake 后端入口, 等 Codex 在此基础上扩展.

Codex 任务范围 (见 docs/market_perception_codex_handoff.md):
- MVP: MarketRegimeEngine 情绪温度计 (risk_on/off + 4-5 daily features)
- 数据源: READ-only from mart_index_daily / fact_stock_kline_daily / fact_lhb_event /
  mart_data_source_watermark / dim_trading_calendar
- 新表: 可 CREATE mart_market_perception_daily (不动现有表)
- Service 路径: backend/services/market_perception/ (新建独立模块)
- API: 此 router 扩展更多 endpoint (current_snapshot / history / breadth / sentiment)
- UI: design/v3-page-market-perception.jsx 同步扩展 (Claude stub 占位)

约束:
- 不 ALTER 现有表 schema
- 不动 panel v4 / ranker / paper_sim / ensemble
- PIT-strict (snapshot date ≤ current trade_date)
- 中文 / 无 emoji
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from services.db import get_conn

logger = logging.getLogger("cm-api.v3-market-perception")
router = APIRouter()


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


@router.get("/snapshot")
async def get_snapshot():
    """市场感知当前快照. STUB — Codex 扩展时改为 SELECT FROM mart_market_perception_daily."""
    payload = {
        "ok": True,
        "stub": True,
        "data": {
            "snapshot_date": None,
            "regime_score": None,
            "breadth_state": None,
            "volatility_state": None,
            "sentiment_phase": None,
            "n_engines_active": 0,
            "note": "占位 — Codex 实施 MarketRegimeEngine 后此 endpoint 返真实数据",
        },
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with get_conn(read_only=True) as conn:
            if _table_exists(conn, "mart_market_perception_daily"):
                row = conn.execute(
                    """
                    SELECT snapshot_date, regime_score, breadth_state,
                           volatility_state, sentiment_phase
                      FROM mart_market_perception_daily
                     ORDER BY snapshot_date DESC LIMIT 1
                    """,
                ).fetchone()
                if row:
                    payload["stub"] = False
                    payload["data"].update(
                        {
                            "snapshot_date": str(row[0]) if row[0] else None,
                            "regime_score": float(row[1]) if row[1] is not None else None,
                            "breadth_state": row[2],
                            "volatility_state": row[3],
                            "sentiment_phase": row[4],
                            "note": "live from mart_market_perception_daily",
                        }
                    )
    except Exception as exc:
        logger.warning("market_perception snapshot fallback to stub: %s", exc)
    return payload


@router.get("/history")
async def get_history(days: int = 90):
    """市场感知历史时序. STUB — Codex 扩展时改为 SELECT FROM mart_market_perception_daily ORDER BY snapshot_date."""
    return {
        "ok": True,
        "stub": True,
        "data": [],
        "days_requested": days,
        "note": "占位 — Codex 扩展返 90 日 regime_score / breadth / volatility 时序",
    }


@router.get("/health")
async def get_health():
    """模块健康检查 — 列出哪些 engine 已实施, 哪些待 Codex 实施."""
    engines = {
        "MarketRegimeEngine": "stub",
        "ThemeLifecycleEngine": "spec_only",
        "ChainDiffusionEngine": "spec_only",
        "FundFlowEngine": "spec_only",
        "LeaderFollowerEngine": "spec_only",
        "CrowdingRiskEngine": "spec_only",
        "StockContextEngine": "spec_only",
    }
    with get_conn(read_only=True) as conn:
        has_mart = _table_exists(conn, "mart_market_perception_daily")
    return {
        "ok": True,
        "engines": engines,
        "mart_table_exists": has_mart,
        "handoff_doc": "docs/market_perception_codex_handoff.md",
    }
