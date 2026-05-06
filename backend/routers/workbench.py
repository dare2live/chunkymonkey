"""Stable workbench read-model endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from services.db import get_conn
from services.workbench_read import (
    build_workbench_champion,
    build_workbench_data_sources,
    build_workbench_features,
    build_workbench_overview,
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
def workbench_storage():
    conn = get_conn()
    try:
        return build_workbench_storage(conn)
    finally:
        conn.close()


@router.get("/recommendations")
def workbench_recommendations():
    conn = get_conn()
    try:
        return build_workbench_recommendations(conn)
    finally:
        conn.close()
