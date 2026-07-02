"""institution_profile router — 机构档案 API (Phase A, 2026-07-02)

前端契约 (卡片↔API 一一对应, widget 独立取数):
  GET /api/v3/inst/profiles                 排名列表 (?holder_type=&min_episodes=&order_by=&limit=)
  GET /api/v3/inst/profiles/{holder}        单机构档案 (总体+维度表现+episode 时间线)
  GET /api/v3/inst/signals                  最新建仓信号流 (跟随入口; ?days=&limit=)
数据经 services.institution_profile 读侧 (数据模块成员 owns feature_store 画像表);
用户跟随动作 → POST /api/v3/paper/positions (strategy_tag=inst_follow, W2 实盘模拟)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services import institution_profile as ip

router = APIRouter()


@router.get("/profiles")
def profiles(holder_type: str | None = None,
             min_episodes: int = Query(default=ip.MIN_EPISODES, ge=1, le=1000),
             order_by: str = "median_alpha",
             limit: int = Query(default=50, ge=1, le=500)):
    try:
        return {"status": "ok", "profiles": ip.list_profiles(
            holder_type=holder_type, min_episodes=min_episodes, order_by=order_by, limit=limit)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/profiles/{holder}")
def profile(holder: str):
    out = ip.get_profile(holder)
    if out is None:
        raise HTTPException(status_code=404, detail=f"机构档案不存在: {holder}")
    return {"status": "ok", "profile": out}


@router.get("/signals")
def signals(days: int = Query(default=30, ge=1, le=365),
            min_holder_episodes: int = Query(default=ip.MIN_EPISODES, ge=1, le=1000),
            limit: int = Query(default=100, ge=1, le=500)):
    return {"status": "ok", "signals": ip.recent_signals(
        days=days, min_holder_episodes=min_holder_episodes, limit=limit)}
