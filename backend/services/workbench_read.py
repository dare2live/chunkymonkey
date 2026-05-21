"""Workbench read models for the frontend operations surface."""
from __future__ import annotations

from typing import Any

from services.storage_retention import load_storage_retention_policy, plan_storage_cleanup
from services.workbench_champion_read import build_workbench_champion
from services.workbench_data_source_read import build_workbench_data_sources
from services.workbench_feature_read import build_workbench_features
from services.workbench_overview_read import build_workbench_overview
from services.workbench_paper_sim_read import build_workbench_paper_sim_kpi_timeseries
from services.workbench_pipeline_read import build_workbench_pipelines
from services.workbench_recommendation_read import build_workbench_recommendations as _build_workbench_recommendations
from services.workbench_research_read import build_workbench_research
from services.workbench_storage_read import (
    build_workbench_storage as _build_workbench_storage,
)

def build_workbench_storage(conn: Any, *, include_live_plan: bool = True) -> dict[str, Any]:
    return _build_workbench_storage(
        conn,
        include_live_plan=include_live_plan,
        load_policy=load_storage_retention_policy,
        plan_cleanup=plan_storage_cleanup,
    )


def build_workbench_recommendations(conn: Any, *, limit: int = 50) -> dict[str, Any]:
    return _build_workbench_recommendations(
        conn,
        limit=limit,
        data_sources_builder=lambda seen_conn: build_workbench_data_sources(seen_conn, limit=20),
    )
