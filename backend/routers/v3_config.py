"""GET /api/v3/config — 前端展示参数下发 (frontend_config.yaml 唯一真相源).

Platform Runtime Contract: 前端零硬编码阈值。本端点无业务逻辑, 纯透传 yaml +
文件 mtime 作为 as_of (前端可见配置新鲜度)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter

router = APIRouter()

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "frontend_config.yaml"


@router.get("/config")
def get_frontend_config() -> dict:
    cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    cfg["_meta"] = {
        "source": "backend/config/frontend_config.yaml",
        "as_of": datetime.fromtimestamp(_CONFIG_PATH.stat().st_mtime, tz=timezone.utc).isoformat(),
    }
    return cfg
