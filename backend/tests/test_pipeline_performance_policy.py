from __future__ import annotations

from services.pipeline_performance_policy import load_pipeline_performance_policy
from services.pricing_policy import load_pricing_label_policy


def test_pipeline_performance_policy_is_loaded_outside_pricing_hash():
    pricing_policy = load_pricing_label_policy()
    performance_policy = load_pipeline_performance_policy()

    assert pricing_policy.policy_hash() == "a4a1ea9e4efa38e9"
    assert performance_policy.policy_id == "pipeline_performance_policy_v1"
    assert performance_policy.progress_heartbeat_required_after_s == 30
    assert performance_policy.pipeline_duration_budgets_s["benchmark_tdx_kline_fetch"] == 120
    assert performance_policy.pipeline_duration_budgets_s["build_feature_rank_matrix_duck"] == 120
    assert performance_policy.pipeline_duration_budgets_s["run_optuna_feature_space"] == 120
    assert performance_policy.pipeline_duration_budgets_s["rerank_optuna_synergy_mtm"] == 900
    assert performance_policy.pipeline_duration_budgets_s["sweep_synergy_mtm_strategy"] == 900
    assert performance_policy.pipeline_duration_budgets_s["build_industry_pit"] == 120
