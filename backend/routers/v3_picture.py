"""Phase γ — v3 股票画像 + trade plan API。

端点 (供 design/v3-data-live.jsx 第二次 fetch 调用):
  GET /api/v3/picture/{code}            单股完整画像 (最新 snapshot)
  GET /api/v3/picture/batch?codes=...   批量画像 (按 codes 列表)
  GET /api/v3/trade-plan/{code}         单股 trade plan (最新 plan_date)

数据源 (Phase γ D3 落库):
  - mart_stock_picture_daily (5,512 股 × snapshot_date)
  - mart_stock_trade_plan    (Phase γ D4 末由 build_stock_trade_plan.py 落库)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services.db import get_conn

logger = logging.getLogger("cm-api.v3-picture")
router = APIRouter()


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _row_to_picture(row) -> dict[str, Any]:
    """mart_stock_picture_daily 行 → JSON dict (反序列化 JSON 字段)。"""
    def _parse_json(s):
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return {
        "stock_code": row[0],
        "snapshot_date": row[1],
        "latest_close": row[2],
        "chg_pct": row[3],
        "fundamental_stage": row[4],
        "fundamental_stage_days": row[5],
        "technical_stage": row[6],
        "technical_stage_days": row[7],
        "primary_type": row[8],
        "secondary_types": _parse_json(row[9]) or [],
        "valuation_pe": row[10],
        "valuation_pe_pctile": row[11],
        "valuation_upside_pct": row[12],
        "institution_score": row[13],
        "institution_n_insts": row[14],
        "institution_top": _parse_json(row[15]) or [],
        "formulas_hit": _parse_json(row[16]) or [],
        "stock_archetype": row[17],
    }


@router.get("/picture/batch")
async def get_picture_batch(
    codes: str = Query(..., description="逗号分隔的 stock_code 列表, 上限 200"),
    snapshot_date: str | None = Query(None, description="指定 snapshot_date, 默认每股最新"),
):
    """批量画像 (主用例: v3-data-live.jsx 一次拉所有 STOCKS 完整字段)。"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()][:200]
    if not code_list:
        return {"ok": True, "data": [], "total": 0}
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_stock_picture_daily"):
            return {"ok": True, "data": [], "total": 0}
        placeholders = ",".join(["?"] * len(code_list))
        if snapshot_date:
            sql = f"""
                SELECT stock_code, snapshot_date, latest_close, chg_pct,
                       fundamental_stage, fundamental_stage_days,
                       technical_stage, technical_stage_days,
                       primary_type, secondary_types_json,
                       valuation_pe, valuation_pe_pctile, valuation_upside_pct,
                       institution_score, institution_n_insts, institution_top_json,
                       formulas_hit_json, stock_archetype
                  FROM mart_stock_picture_daily
                 WHERE stock_code IN ({placeholders}) AND snapshot_date = ?
            """
            params = code_list + [snapshot_date]
        else:
            # 每股最新一行
            sql = f"""
                SELECT * FROM (
                  SELECT stock_code, snapshot_date, latest_close, chg_pct,
                         fundamental_stage, fundamental_stage_days,
                         technical_stage, technical_stage_days,
                         primary_type, secondary_types_json,
                         valuation_pe, valuation_pe_pctile, valuation_upside_pct,
                         institution_score, institution_n_insts, institution_top_json,
                         formulas_hit_json, stock_archetype,
                         ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY snapshot_date DESC) AS rn
                    FROM mart_stock_picture_daily
                   WHERE stock_code IN ({placeholders})
                )
                WHERE rn = 1
            """
            params = code_list
        rows = conn.execute(sql, params).fetchall()
        # Row 不支持 slice, _row_to_picture 用 int 索引 0..17 即可 (rn 列在 18 被忽略)
        out = [_row_to_picture(r) for r in rows]
        return {"ok": True, "data": out, "total": len(out)}
    finally:
        conn.close()


@router.get("/picture/{stock_code}")
async def get_picture(stock_code: str):
    """单股最新画像 (注: 路径声明顺序必须晚于 /picture/batch, 否则会被截胡)。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_stock_picture_daily"):
            return {"ok": True, "data": None,
                    "message": "mart_stock_picture_daily 未生成, 请先跑 scripts/build_picture_daily.py"}
        row = conn.execute(
            """
            SELECT stock_code, snapshot_date, latest_close, chg_pct,
                   fundamental_stage, fundamental_stage_days,
                   technical_stage, technical_stage_days,
                   primary_type, secondary_types_json,
                   valuation_pe, valuation_pe_pctile, valuation_upside_pct,
                   institution_score, institution_n_insts, institution_top_json,
                   formulas_hit_json, stock_archetype
              FROM mart_stock_picture_daily
             WHERE stock_code = ?
             ORDER BY snapshot_date DESC LIMIT 1
            """,
            [stock_code],
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"stock_code {stock_code} not found")
        return {"ok": True, "data": _row_to_picture(row)}
    finally:
        conn.close()


@router.get("/trade-plan/{stock_code}")
async def get_trade_plan(
    stock_code: str,
    plan_date: str | None = Query(None, description="指定 plan_date, 默认最新"),
    model_id: str = Query("v1", description="模型 ID, 多模型共存时区分"),
):
    """单股 trade plan (按 plan_date + model_id)。"""
    conn = get_conn()
    try:
        if not _table_exists(conn, "mart_stock_trade_plan"):
            return {"ok": True, "data": None,
                    "message": "mart_stock_trade_plan 未生成, 请先跑 scripts/build_stock_trade_plan.py"}
        if plan_date:
            row = conn.execute(
                """
                SELECT stock_code, plan_date, model_id,
                       entry_target_price, entry_aggressive_price, entry_max_price,
                       exit_target_1_price, exit_target_2_price, exit_stop_price,
                       risk_reward_ratio, expected_horizon_days,
                       atr_14, entry_basis, reason_codes_json
                  FROM mart_stock_trade_plan
                 WHERE stock_code = ? AND plan_date = ? AND model_id = ?
                """,
                [stock_code, plan_date, model_id],
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT stock_code, plan_date, model_id,
                       entry_target_price, entry_aggressive_price, entry_max_price,
                       exit_target_1_price, exit_target_2_price, exit_stop_price,
                       risk_reward_ratio, expected_horizon_days,
                       atr_14, entry_basis, reason_codes_json
                  FROM mart_stock_trade_plan
                 WHERE stock_code = ? AND model_id = ?
                 ORDER BY plan_date DESC LIMIT 1
                """,
                [stock_code, model_id],
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"no trade plan for {stock_code}")
        return {
            "ok": True,
            "data": {
                "stock_code": row[0],
                "plan_date": row[1],
                "model_id": row[2],
                "entry_target_price": row[3],
                "entry_aggressive_price": row[4],
                "entry_max_price": row[5],
                "exit_target_1_price": row[6],
                "exit_target_2_price": row[7],
                "exit_stop_price": row[8],
                "risk_reward_ratio": row[9],
                "expected_horizon_days": row[10],
                "atr_14": row[11],
                "entry_basis": row[12],
                "reason_codes": json.loads(row[13]) if row[13] else [],
            },
        }
    finally:
        conn.close()
