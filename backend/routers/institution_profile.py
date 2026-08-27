"""Tier3 机构披露研究 API；所有输出均为 research evidence。

前端契约 (卡片↔API 一一对应, widget 独立取数):
  GET /api/v3/inst/profiles                 排名列表 (?holder_type=&min_episodes=&order_by=&limit=)
  GET /api/v3/inst/profiles/{holder}        单机构档案 (总体+维度表现+episode 时间线)
  GET /api/v3/inst/signals                  最新披露事件研究流 (?days=&limit=)
数据经 services.institution_profile 读侧 (数据模块成员 owns feature_store 画像表);
该端点不产生 CandidateSignal、StrategyRelease 或买卖建议。

E0: disclosure provider-field reads prefer accepted canonical when shadow MATCH;
``cutover_allowed`` is true only on three-domain MATCH.  Feature-store profiles
use typed enrichment projection (field-level PARTIAL where historical canary
lacks enrichment).  ``disclosure_shadow`` sidecar stays.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services import institution_profile as ip
from services.data_sources.disclosure_boundaries import (
    attest_disclosure_research_surface,
)
from services.data_sources.disclosure_research_read import (
    build_disclosure_read_policy,
)
from services.data_sources.disclosure_shadow_compare import (
    compare_disclosure_research_shadow,
    empty_disclosure_shadow,
)
from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

router = APIRouter()
SURFACE_STATUS = "tier3_research_evidence_only"


def _disclosure_shadow_sidecar() -> dict[str, Any]:
    """Read-only bounded shadow; fail closed.

    holders_top10 live on smartmoney; org_holding on alias org_holding;
    stk_holdertrade on tushare_raw. Route domain_conns accordingly.
    """

    sm = None
    tr = None
    org = None
    try:
        manifest = get_database_manifest()
        sm = duck_connect(str(manifest.path_for("smartmoney")), read_only=True)
    except Exception:  # noqa: BLE001 — missing/locked DB must not invent MATCH
        return empty_disclosure_shadow(reason="smartmoney_not_attached").as_dict()
    try:
        try:
            tr = duck_connect(
                str(manifest.path_for("tushare_raw")), read_only=True
            )
        except Exception:  # noqa: BLE001 — stk domain becomes UNAVAILABLE
            tr = None
        try:
            org_path = manifest.path_for("org_holding")
            org = (
                duck_connect(str(org_path), read_only=True)
                if org_path.is_file()
                else None
            )
        except Exception:  # noqa: BLE001 — org domain becomes UNAVAILABLE
            org = None
        domain_conns: dict[str, Any] = {}
        if tr is not None:
            domain_conns["stk_holdertrade"] = tr
        if org is not None:
            domain_conns["org_holding"] = org
        return compare_disclosure_research_shadow(
            sm,
            max_rows_per_domain=50,
            domain_conns=domain_conns or None,
        ).as_dict()
    except Exception:  # noqa: BLE001 — shadow is observational only
        return empty_disclosure_shadow(reason="disclosure_shadow_probe_failed").as_dict()
    finally:
        for handle in (org, tr, sm):
            if handle is None:
                continue
            try:
                handle.close()
            except Exception:  # noqa: BLE001
                pass


def _research_envelope(**payload):
    shadow = _disclosure_shadow_sidecar()
    read_policy = build_disclosure_read_policy(shadow)
    report = attest_disclosure_research_surface(read_policy)
    return {
        "status": "ok",
        "surface_status": SURFACE_STATUS,
        "disclosure_conformity": report.as_dict(),
        "disclosure_shadow": shadow,
        "disclosure_read_policy": read_policy.as_dict(),
        "cutover_allowed": bool(read_policy.cutover_allowed),
        **payload,
    }


@router.get("/profiles")
def profiles(holder_type: str | None = None,
             min_episodes: int = Query(default=ip.MIN_EPISODES, ge=1, le=1000),
             order_by: str = "median_alpha",
             limit: int = Query(default=50, ge=1, le=500)):
    try:
        return _research_envelope(
            profiles=ip.list_profiles(
                holder_type=holder_type,
                min_episodes=min_episodes,
                order_by=order_by,
                limit=limit,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/profiles/{holder}")
def profile(holder: str):
    out = ip.get_profile(holder)
    if out is None:
        raise HTTPException(status_code=404, detail=f"机构档案不存在: {holder}")
    return _research_envelope(profile=out)


@router.get("/signals")
def signals(days: int = Query(default=30, ge=1, le=365),
            min_holder_episodes: int = Query(default=ip.MIN_EPISODES, ge=1, le=1000),
            limit: int = Query(default=100, ge=1, le=500)):
    return _research_envelope(
        signals=ip.recent_signals(
            days=days, min_holder_episodes=min_holder_episodes, limit=limit
        )
    )
