"""Decision-assist APIs — Tier3/product consume (Cap A moneyflow).

Separate from /api/v3/pulse so market sensing keeps 「零买卖暗示」.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from services import decision_intersection as di
from services import market_pulse as mp
from services import moneyflow_assist as mfa
from services.data_access import resolver
from services.duck_adapter import connect as duck_connect
from services.universe import classify_exclusion

router = APIRouter()


def get_assist_conn():
    con = duck_connect(resolver.db_path("smartmoney"), read_only=True)
    try:
        yield con
    finally:
        con.close()


def _require_chain(chain: str) -> None:
    cfg = mfa.load_cfg()
    allowed = list(cfg.get("chains") or [mp.CHAIN_DC_INDUSTRY])
    if chain not in allowed:
        raise HTTPException(status_code=400, detail=f"chain must be one of {allowed}")


@router.get("/moneyflow/board")
def moneyflow_board(
    chain: str = Query(default="dc_industry"),
    horizon: int = Query(default=20),
    level: str = Query(default="L1"),
    limit: int = Query(default=20, ge=1, le=500),
    conn=Depends(get_assist_conn),
) -> dict[str, Any]:
    """Sector moneyflow decision-assist board (horizons + behavior map)."""
    _require_chain(chain)
    cfg = mfa.load_cfg()
    hs = [int(h) for h in (cfg.get("horizons") or [])]
    if horizon not in hs:
        raise HTTPException(status_code=400, detail=f"horizon must be one of {hs}")
    if chain == mp.CHAIN_SW and level not in ("L1", "L2", "L3"):
        raise HTTPException(status_code=400, detail="level must be L1|L2|L3 for sw")
    try:
        return mfa.build_sector_board(
            conn, chain=chain, horizon=horizon, level=level, limit=limit, cfg=cfg,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/moneyflow/stock/{code}")
def moneyflow_stock(code: str, conn=Depends(get_assist_conn)) -> dict[str, Any]:
    """Per-stock multi-horizon moneyflow assist (dossier 资金 tab)."""
    digits = "".join(ch for ch in code if ch.isdigit())[:6]
    if len(digits) != 6:
        raise HTTPException(status_code=400, detail="stock code must be 6 digits")
    excl = classify_exclusion(digits)
    if excl is not None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_hs_a", "stock_code": digits, "exclusion": excl},
        )
    return mfa.build_stock_moneyflow(conn, stock_code=digits)


@router.get("/intersection/strongest")
def intersection_strongest(
    horizon: int = Query(default=20),
    limit: int = Query(default=20, ge=1, le=200),
    conn=Depends(get_assist_conn),
) -> dict[str, Any]:
    """Cap 4D 交集最强股 — DC 行业∩概念强势链会员交集决策列表（非原始排名表）。"""
    cfg = di.load_cfg()
    hs = mfa._horizons(mfa.load_cfg())
    if horizon not in hs:
        raise HTTPException(status_code=400, detail=f"horizon must be one of {hs}")
    return di.build_intersection_strongest(conn, horizon=horizon, limit=limit, cfg=cfg)


@router.get("/intersection/stock/{code}")
def intersection_stock(
    code: str,
    horizon: int = Query(default=20),
    conn=Depends(get_assist_conn),
) -> dict[str, Any]:
    """本股是否处于当前交集最强股列表（dossier 交集 tab）。"""
    digits = "".join(ch for ch in code if ch.isdigit())[:6]
    if len(digits) != 6:
        raise HTTPException(status_code=400, detail="stock code must be 6 digits")
    excl = classify_exclusion(digits)
    if excl is not None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_hs_a", "stock_code": digits, "exclusion": excl},
        )
    cfg = di.load_cfg()
    hs = mfa._horizons(mfa.load_cfg())
    if horizon not in hs:
        raise HTTPException(status_code=400, detail=f"horizon must be one of {hs}")
    return di.build_intersection_for_stock(conn, stock_code=digits, horizon=horizon, cfg=cfg)
