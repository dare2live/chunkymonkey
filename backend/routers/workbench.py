"""Stable workbench read-model endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from services.db import get_conn
from services.workbench_delivery_read import build_workbench_delivery_readiness
from services.workbench_read import (
    build_workbench_champion,
    build_workbench_data_sources,
    build_workbench_features,
    build_workbench_overview,
    build_workbench_paper_sim_kpi_timeseries,
    build_workbench_pipelines,
    build_workbench_recommendations,
    build_workbench_research,
    build_workbench_storage,
)


router = APIRouter()


@router.get("/overview")
def workbench_overview():
    conn = get_conn()
    try:
        return build_workbench_overview(conn)
    finally:
        conn.close()


@router.get("/research")
def workbench_research():
    conn = get_conn()
    try:
        return build_workbench_research(conn)
    finally:
        conn.close()


@router.get("/champion")
def workbench_champion():
    conn = get_conn()
    try:
        return build_workbench_champion(conn)
    finally:
        conn.close()


@router.get("/data-sources")
def workbench_data_sources():
    conn = get_conn()
    try:
        return build_workbench_data_sources(conn)
    finally:
        conn.close()


@router.get("/pipelines")
def workbench_pipelines():
    conn = get_conn()
    try:
        return build_workbench_pipelines(conn)
    finally:
        conn.close()


@router.get("/features")
def workbench_features():
    conn = get_conn()
    try:
        return build_workbench_features(conn)
    finally:
        conn.close()


@router.get("/storage")
def workbench_storage(include_live_plan: bool = False):
    conn = get_conn()
    try:
        return build_workbench_storage(conn, include_live_plan=include_live_plan)
    finally:
        conn.close()


@router.get("/recommendations")
def workbench_recommendations():
    conn = get_conn()
    try:
        return build_workbench_recommendations(conn)
    finally:
        conn.close()


@router.get("/paper-sim/kpi-timeseries")
def workbench_paper_sim_kpi_timeseries(limit: int = 50, variant: str | None = None):
    conn = get_conn()
    try:
        return build_workbench_paper_sim_kpi_timeseries(conn, limit=limit, variant=variant)
    finally:
        conn.close()


@router.get("/delivery-readiness")
def workbench_delivery_readiness():
    return build_workbench_delivery_readiness()
