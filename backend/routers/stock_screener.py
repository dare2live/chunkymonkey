"""Cap 5B 形态/阶段选股面 API — Tier3/product consume (read-only filter surface).

Consumes ``fact_stock_form_daily`` — same Tier1 brick as the stock dossier
「形态·阶段」 tab. No scoring/ranking model, no Optuna, no StrategyRelease.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from services import stock_screener as ss
from services.data_access import resolver
from services.duck_adapter import connect as duck_connect

router = APIRouter()


def get_screener_conn():
    con = duck_connect(resolver.db_path("smartmoney"), read_only=True)
    try:
        yield con
    finally:
        con.close()


@router.get("/options")
def screener_options(conn=Depends(get_screener_conn)) -> dict[str, Any]:
    """Live 形态/阶段 filter menu (facet counts at current as-of; never hardcoded)."""
    return ss.build_options(conn)


@router.get("/form_stage")
def screener_form_stage(
    form_name: list[str] | None = Query(default=None),
    axis_pos: str | None = Query(default=None),
    axis_trend: str | None = Query(default=None),
    axis_purity: str | None = Query(default=None),
    axis_vol: str | None = Query(default=None),
    is_breakout_event: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    conn=Depends(get_screener_conn),
) -> dict[str, Any]:
    """形态/阶段选股面 — Tier1 bricks as strategy surface (plan §3.6; no Optuna/Release)."""
    cfg = ss.load_cfg()
    axis_choices = {
        "axis_pos": set((cfg.get("axis_pos_zh") or {}).keys()),
        "axis_trend": set((cfg.get("axis_trend_zh") or {}).keys()),
        "axis_purity": set((cfg.get("axis_purity_zh") or {}).keys()),
        "axis_vol": set((cfg.get("axis_vol_zh") or {}).keys()),
    }
    for name, val in (
        ("axis_pos", axis_pos),
        ("axis_trend", axis_trend),
        ("axis_purity", axis_purity),
        ("axis_vol", axis_vol),
    ):
        if val is not None and val not in axis_choices[name]:
            raise HTTPException(
                status_code=400,
                detail=f"{name} must be one of {sorted(axis_choices[name])}",
            )
    return ss.build_form_stage_screen(
        conn,
        form_names=form_name,
        axis_pos=axis_pos,
        axis_trend=axis_trend,
        axis_purity=axis_purity,
        axis_vol=axis_vol,
        is_breakout_event=is_breakout_event,
        limit=limit,
        cfg=cfg,
    )
