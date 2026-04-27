"""策略预设 (P4): 把信号阈值 / Cohort / 回测窗口 / 选股条件打成包.

后端表 dim_strategy_preset:
  name        VARCHAR PRIMARY KEY
  payload     JSON  (任意结构, 由前端定义)
  is_default  BOOLEAN
  created_at  TIMESTAMP
  updated_at  TIMESTAMP
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.db import get_conn


logger = logging.getLogger("cm-api.strategy_preset")
router = APIRouter()


def _ensure_table():
    """幂等建表."""
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dim_strategy_preset (
                name VARCHAR PRIMARY KEY,
                payload JSON,
                is_default BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 默认 3 个预设 (如果表为空)
        cnt = conn.execute("SELECT COUNT(*) FROM dim_strategy_preset").fetchone()[0]
        if cnt == 0:
            seed_presets = [
                ("稳健型", {
                    "signals": {"follow_threshold": 0.7, "watch_threshold": 0.5, "skip_threshold": 0.3, "cooldown_days": 5},
                    "backtest": {"lookback_days": 252, "rebalance": "weekly"},
                }),
                ("激进型", {
                    "signals": {"follow_threshold": 0.6, "watch_threshold": 0.4, "skip_threshold": 0.2, "cooldown_days": 3},
                    "backtest": {"lookback_days": 60, "rebalance": "daily"},
                }),
                ("试验型", {
                    "signals": {"follow_threshold": 0.55, "watch_threshold": 0.4, "skip_threshold": 0.25, "cooldown_days": 1},
                    "backtest": {"lookback_days": 90, "rebalance": "daily"},
                }),
            ]
            for name, payload in seed_presets:
                conn.execute(
                    "INSERT INTO dim_strategy_preset(name, payload, is_default) VALUES(?, ?, ?)",
                    [name, json.dumps(payload, ensure_ascii=False), name == "稳健型"],
                )
            logger.info("[strategy_preset] seed 3 个默认预设")
    finally:
        conn.close()


# 启动时建表
try:
    _ensure_table()
except Exception as exc:
    logger.warning(f"[strategy_preset] 建表失败 (非致命): {exc}")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("/preset/list")
def list_presets():
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT name, payload, is_default,
                   CAST(created_at AS VARCHAR) AS created_at,
                   CAST(updated_at AS VARCHAR) AS updated_at
            FROM dim_strategy_preset
            ORDER BY is_default DESC, name
        """).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r[1]) if r[1] else {}
            except Exception:
                payload = {}
            out.append({
                "name": r[0],
                "payload": payload,
                "is_default": bool(r[2]),
                "created_at": r[3],
                "updated_at": r[4],
            })
        return {"presets": out, "total": len(out)}
    finally:
        conn.close()


@router.get("/preset/get")
def get_preset(name: str = Query(...)):
    conn = get_conn()
    try:
        r = conn.execute(
            "SELECT name, payload, is_default FROM dim_strategy_preset WHERE name = ?",
            [name],
        ).fetchone()
        if not r:
            raise HTTPException(404, f"预设不存在: {name}")
        try:
            payload = json.loads(r[1]) if r[1] else {}
        except Exception:
            payload = {}
        return {
            "name": r[0],
            "payload": payload,
            "is_default": bool(r[2]),
        }
    finally:
        conn.close()


class SavePresetIn(BaseModel):
    name: str
    payload: dict[str, Any]
    is_default: bool = False


@router.post("/preset/save")
def save_preset(body: SavePresetIn):
    if not body.name or not body.name.strip():
        raise HTTPException(400, "name 不能为空")
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT 1 FROM dim_strategy_preset WHERE name = ?", [body.name]
        ).fetchone()
        payload_json = json.dumps(body.payload, ensure_ascii=False)
        if existing:
            conn.execute("""
                UPDATE dim_strategy_preset
                SET payload = ?, is_default = ?, updated_at = CURRENT_TIMESTAMP
                WHERE name = ?
            """, [payload_json, body.is_default, body.name])
            return {"ok": True, "action": "updated", "name": body.name}
        else:
            conn.execute("""
                INSERT INTO dim_strategy_preset(name, payload, is_default)
                VALUES(?, ?, ?)
            """, [body.name, payload_json, body.is_default])
            return {"ok": True, "action": "created", "name": body.name}
    finally:
        conn.close()


class DeletePresetIn(BaseModel):
    name: str


@router.post("/preset/delete")
def delete_preset(body: DeletePresetIn):
    conn = get_conn()
    try:
        r = conn.execute(
            "DELETE FROM dim_strategy_preset WHERE name = ?", [body.name]
        )
        return {"ok": True, "name": body.name}
    finally:
        conn.close()
