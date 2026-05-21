"""Research read-model helpers for the Workbench surface."""
from __future__ import annotations

from typing import Any

from services.workbench_champion_read import _model_stability_context
from services.workbench_industry_pit_read import build_industry_pit_readiness
from services.workbench_model_stability_studies_read import build_model_stability_studies
from services.workbench_rank_matrix_read import build_rank_matrix_cache_view
from services.workbench_ranker_runtime_read import build_ranker_runtime_view
from services.workbench_research_meta_read import build_research_feature_drift, build_research_read_model_meta
from services.workbench_research_schedule_read import build_research_schedule_view
from services.workbench_shareholder_plan_read import (
    build_shareholder_plan_family_eval_view,
    build_shareholder_plan_family_walkforward_view,
    build_shareholder_plan_initial_feature_panel_view,
)
from services.workbench_stock_horizon_read import build_workbench_stock_horizon_profile
from services.workbench_temporal_synergy_read import build_temporal_synergy_research

def build_workbench_research(conn: Any, *, task_limit: int = 20, study_limit: int = 12) -> dict[str, Any]:
    research_schedule = build_research_schedule_view(conn, task_limit=task_limit)
    schedule_run_id = research_schedule["run_id"]
    runtime_view = build_ranker_runtime_view(conn, schedule_run_id=schedule_run_id)

    return {
        "read_model": build_research_read_model_meta(conn),
        "research_schedule": research_schedule,
        "ranker_policy": runtime_view["ranker_policy"],
        "model_stability": build_model_stability_studies(conn, study_limit=study_limit),
        "ranker_profiles": runtime_view["ranker_profiles"],
        "rank_matrix_cache": build_rank_matrix_cache_view(conn),
        "stability_context": _model_stability_context(conn),
        "stock_horizon_profile": build_workbench_stock_horizon_profile(conn),
        "shareholder_plan_initial_feature_panel": build_shareholder_plan_initial_feature_panel_view(conn),
        "shareholder_plan_family_eval": build_shareholder_plan_family_eval_view(conn),
        "shareholder_plan_family_walkforward": build_shareholder_plan_family_walkforward_view(conn),
        "temporal_synergy": build_temporal_synergy_research(conn),
        "industry_pit": build_industry_pit_readiness(conn),
        "feature_drift": build_research_feature_drift(conn, limit=12),
    }
