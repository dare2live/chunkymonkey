from __future__ import annotations

import json

import pytest

from conftest import duck_mem
from scripts import run_optuna_synergy_search as subject


pytestmark = pytest.mark.pipeline


def _seed_synergy_inputs(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE mart_temporal_research_panel_quality (
            run_id TEXT,
            built_at TEXT
        );
        INSERT INTO mart_temporal_research_panel_quality VALUES
            ('temporal_unit', '2026-05-06T08:00:00');

        CREATE TABLE mart_feature_temporal_relevance (
            run_id TEXT,
            label_name TEXT,
            feature_name TEXT,
            coverage_pct DOUBLE,
            rank_ic DOUBLE,
            directional_spread DOUBLE,
            stability_score DOUBLE,
            daily_count BIGINT
        );
        INSERT INTO mart_feature_temporal_relevance VALUES
            ('temporal_unit', 'forward_ret_20d', 'strong_a', 100.0, 0.080, 0.030, 0.90, 100),
            ('temporal_unit', 'forward_ret_20d', 'strong_b', 98.0, 0.070, 0.028, 0.88, 100),
            ('temporal_unit', 'forward_ret_20d', 'weak_c', 96.0, 0.010, 0.004, 0.30, 90),
            ('temporal_unit', 'forward_ret_20d', 'low_cov_d', 50.0, 0.200, 0.080, 0.95, 100);

        CREATE TABLE mart_feature_pair_synergy (
            run_id TEXT,
            label_name TEXT,
            feature_a TEXT,
            feature_b TEXT,
            joint_uplift DOUBLE,
            interaction_score DOUBLE,
            joint_obs_count BIGINT,
            feature_corr DOUBLE
        );
        INSERT INTO mart_feature_pair_synergy VALUES
            ('temporal_unit', 'forward_ret_20d', 'strong_a', 'strong_b', 0.040, 0.55, 80, 0.12),
            ('temporal_unit', 'forward_ret_20d', 'strong_a', 'weak_c', 0.004, 0.05, 60, 0.05);

        CREATE TABLE mart_feature_conditional_synergy (
            run_id TEXT,
            label_name TEXT,
            condition_feature TEXT,
            response_feature TEXT,
            incremental_uplift DOUBLE,
            interaction_score DOUBLE,
            conditional_response_obs_count BIGINT,
            feature_corr DOUBLE,
            selected BOOLEAN
        );
        INSERT INTO mart_feature_conditional_synergy VALUES
            ('temporal_unit', 'forward_ret_20d', 'strong_a', 'strong_b', 0.060, 0.75, 80, 0.12, TRUE);

        CREATE TABLE mart_temporal_research_panel (
            run_id TEXT,
            date DATE,
            stock_code TEXT,
            forward_ret_20d DOUBLE,
            strong_a DOUBLE,
            strong_b DOUBLE,
            weak_c DOUBLE
        );
        INSERT INTO mart_temporal_research_panel VALUES
            ('temporal_unit', DATE '2026-01-02', '000001.SZ', 0.030, 0.90, 0.85, 0.10),
            ('temporal_unit', DATE '2026-01-02', '000002.SZ', 0.020, 0.80, 0.75, 0.20),
            ('temporal_unit', DATE '2026-01-02', '000003.SZ', -0.010, 0.20, 0.25, 0.90),
            ('temporal_unit', DATE '2026-01-02', '000004.SZ', -0.020, 0.10, 0.15, 0.80),
            ('temporal_unit', DATE '2026-01-03', '000001.SZ', 0.025, 0.88, 0.83, 0.10),
            ('temporal_unit', DATE '2026-01-03', '000002.SZ', 0.015, 0.78, 0.74, 0.30),
            ('temporal_unit', DATE '2026-01-03', '000003.SZ', -0.015, 0.25, 0.20, 0.85),
            ('temporal_unit', DATE '2026-01-03', '000004.SZ', -0.025, 0.15, 0.10, 0.75),
            ('temporal_unit', DATE '2026-01-04', '000001.SZ', 0.020, 0.92, 0.86, 0.20),
            ('temporal_unit', DATE '2026-01-04', '000002.SZ', 0.010, 0.76, 0.70, 0.25),
            ('temporal_unit', DATE '2026-01-04', '000003.SZ', -0.010, 0.24, 0.22, 0.80),
            ('temporal_unit', DATE '2026-01-04', '000004.SZ', -0.030, 0.12, 0.12, 0.90),
            ('temporal_unit', DATE '2026-01-05', '000001.SZ', 0.018, 0.91, 0.87, 0.15),
            ('temporal_unit', DATE '2026-01-05', '000002.SZ', 0.012, 0.74, 0.72, 0.35),
            ('temporal_unit', DATE '2026-01-05', '000003.SZ', -0.012, 0.22, 0.24, 0.88),
            ('temporal_unit', DATE '2026-01-05', '000004.SZ', -0.028, 0.11, 0.14, 0.82);
        """
    )


def test_optuna_synergy_search_records_trials_summary_and_candidate():
    with duck_mem() as conn:
        _seed_synergy_inputs(conn)

        result = subject.run_optuna_synergy_search(
            conn,
            source_run_id="temporal_unit",
            label_name="forward_ret_20d",
            run_id="optuna_unit",
            trials=5,
            min_features=2,
            max_features=3,
            max_interactions=2,
            min_coverage_pct=80.0,
            seed=20260506,
            storage_url=None,
        )

        trial_count = conn.execute(
            "SELECT COUNT(*) AS n FROM mart_optuna_synergy_trial WHERE run_id = 'optuna_unit'"
        ).fetchone()["n"]
        summary = conn.execute(
            "SELECT * FROM mart_optuna_synergy_study_summary WHERE run_id = 'optuna_unit'"
        ).fetchone()
        candidate = conn.execute(
            "SELECT * FROM mart_synergy_policy_candidate WHERE run_id = 'optuna_unit'"
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'optuna_unit'"
        ).fetchone()

        assert result["selected_count"] >= 2
        assert trial_count == 5
        assert summary["study_total_trials"] == 5
        assert "strong_a" in json.loads(summary["selected_features_json"])
        assert candidate["gate_status"] == "research_only"
        assert json.loads(manifest["perf_summary_json"])["selected_count"] == result["selected_count"]
        assert any(
            row.get("interaction_type") == "conditional"
            for row in json.loads(summary["selected_interactions_json"])
        )


def test_optuna_synergy_search_zero_trials_uses_deterministic_baseline():
    with duck_mem() as conn:
        _seed_synergy_inputs(conn)

        result = subject.run_optuna_synergy_search(
            conn,
            source_run_id="temporal_unit",
            label_name="forward_ret_20d",
            run_id="optuna_baseline",
            trials=0,
            min_features=2,
            max_features=3,
            max_interactions=2,
            min_coverage_pct=80.0,
        )
        row = conn.execute(
            """
            SELECT selected_features_json, selected_interactions_json
              FROM mart_optuna_synergy_study_summary
             WHERE run_id = 'optuna_baseline'
            """
        ).fetchone()

        assert result["study_total_trials"] == 1
        assert json.loads(row["selected_features_json"])[:2] == ["strong_a", "strong_b"]
        assert json.loads(row["selected_interactions_json"])[0] == {
            "interaction_type": "conditional",
            "feature_a": "strong_a",
            "feature_b": "strong_b",
        }


def test_optuna_synergy_search_can_resume_persistent_study(tmp_path):
    storage_url = f"sqlite:///{tmp_path / 'synergy_study.sqlite3'}"
    with duck_mem() as conn:
        _seed_synergy_inputs(conn)
        first = subject.run_optuna_synergy_search(
            conn,
            source_run_id="temporal_unit",
            label_name="forward_ret_20d",
            run_id="optuna_resume_1",
            trials=2,
            min_features=2,
            max_features=3,
            max_interactions=2,
            min_coverage_pct=80.0,
            seed=1,
            storage_url=storage_url,
            study_name="shared_synergy_unit",
        )
        second = subject.run_optuna_synergy_search(
            conn,
            source_run_id="temporal_unit",
            label_name="forward_ret_20d",
            run_id="optuna_resume_2",
            trials=2,
            min_features=2,
            max_features=3,
            max_interactions=2,
            min_coverage_pct=80.0,
            seed=2,
            storage_url=storage_url,
            study_name="shared_synergy_unit",
        )

        assert first["study_total_trials"] == 2
        assert second["study_total_trials"] >= 4
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM mart_optuna_synergy_trial WHERE run_id = 'optuna_resume_2'"
        ).fetchone()["n"] == 2


def test_optuna_synergy_search_risk_aware_records_rerank_metrics():
    with duck_mem() as conn:
        _seed_synergy_inputs(conn)

        result = subject.run_optuna_synergy_search(
            conn,
            source_run_id="temporal_unit",
            label_name="forward_ret_20d",
            run_id="optuna_risk_aware",
            trials=5,
            min_features=2,
            max_features=3,
            max_interactions=2,
            min_coverage_pct=80.0,
            seed=20260507,
            storage_url=None,
            feature_subset_pool_size=3,
            risk_aware=True,
            risk_eval_top_trials=3,
            risk_top_quantile=0.5,
            risk_folds=2,
            risk_transaction_cost_bps=20.0,
        )

        summary = conn.execute(
            "SELECT config_json FROM mart_optuna_synergy_study_summary WHERE run_id = 'optuna_risk_aware'"
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'optuna_risk_aware'"
        ).fetchone()
        trial_metrics = [
            json.loads(row["metrics_json"])
            for row in conn.execute(
                "SELECT metrics_json FROM mart_optuna_synergy_trial WHERE run_id = 'optuna_risk_aware'"
            ).fetchall()
        ]

        config = json.loads(summary["config_json"])
        best_risk = config["best_metrics"]["risk_evaluation"]

        assert result["risk_aware"] is True
        assert result["risk_evaluated_trials"] == 3
        assert config["risk_aware"] is True
        assert config["risk_evaluated_trials"] == 3
        assert config["feature_subset_pool_size"] == 3
        assert best_risk["available"] is True
        assert "worst_max_drawdown" in best_risk
        assert config["risk_feature_rank_cache"]["enabled"] is True
        assert config["risk_feature_rank_cache"]["misses"] >= 1
        assert json.loads(manifest["perf_summary_json"])["risk_aware"] is True
        assert json.loads(manifest["perf_summary_json"])["risk_feature_rank_cache"]["enabled"] is True
        assert sum("risk_evaluation" in row for row in trial_metrics) == 3


def test_optuna_synergy_search_can_use_risk_objective_during_generation():
    with duck_mem() as conn:
        _seed_synergy_inputs(conn)

        result = subject.run_optuna_synergy_search(
            conn,
            source_run_id="temporal_unit",
            label_name="forward_ret_20d",
            run_id="optuna_inline_risk",
            trials=5,
            min_features=2,
            max_features=3,
            max_interactions=2,
            min_coverage_pct=80.0,
            seed=20260507,
            storage_url=None,
            feature_subset_pool_size=3,
            risk_objective_in_search=True,
            risk_rank_feature_pool_size=3,
            risk_top_quantile=0.5,
            risk_folds=2,
            risk_transaction_cost_bps=20.0,
            dedupe_candidates_in_search=True,
            duplicate_candidate_penalty=1.0,
            diversity_penalty_weight=0.1,
        )

        summary = conn.execute(
            "SELECT config_json FROM mart_optuna_synergy_study_summary WHERE run_id = 'optuna_inline_risk'"
        ).fetchone()
        manifest = conn.execute(
            "SELECT perf_summary_json FROM mart_pipeline_run_manifest WHERE run_id = 'optuna_inline_risk'"
        ).fetchone()
        trial_metrics = [
            json.loads(row["metrics_json"])
            for row in conn.execute(
                "SELECT metrics_json FROM mart_optuna_synergy_trial WHERE run_id = 'optuna_inline_risk'"
            ).fetchall()
        ]

        config = json.loads(summary["config_json"])
        perf = json.loads(manifest["perf_summary_json"])

        assert result["risk_objective_in_search"] is True
        assert result["risk_inline_evaluated_trials"] == 5
        assert config["risk_objective_in_search"] is True
        assert config["risk_inline_evaluated_trials"] == 5
        assert config["dedupe_candidates_in_search"] is True
        assert config["risk_feature_rank_cache"]["enabled"] is True
        assert config["risk_feature_rank_cache"]["preferred_feature_count"] == 3
        assert perf["risk_objective_in_search"] is True
        assert perf["risk_inline_evaluated_trials"] == 5
        assert perf["unique_candidate_fingerprint_count"] >= 1
        assert all("risk_evaluation" in row for row in trial_metrics)
        assert all("generation_penalty" in row for row in trial_metrics)


def test_risk_evaluation_reuses_feature_rank_cache_for_candidate_feature_union():
    with duck_mem() as conn:
        _seed_synergy_inputs(conn)
        rank_cache = subject._empty_risk_rank_cache()
        rank_cache["preferred_features"] = ["strong_a", "strong_b", "weak_c"]
        kwargs = {
            "source_run_id": "temporal_unit",
            "label_name": "forward_ret_20d",
            "selected_features": ["strong_a", "strong_b"],
            "selected_interactions": [
                {
                    "interaction_type": "conditional",
                    "feature_a": "strong_a",
                    "feature_b": "strong_b",
                }
            ],
            "top_quantile": 0.5,
            "folds": 2,
            "transaction_cost_bps": 20.0,
            "conditional_threshold": 0.8,
            "proxy_objective": 0.0,
            "proxy_weight": 0.05,
            "rank_ic_weight": 10.0,
            "return_weight": 100.0,
            "drawdown_penalty_weight": 3.0,
            "turnover_penalty_weight": 1.0,
            "rank_cache": rank_cache,
        }
        alternate_kwargs = {
            **kwargs,
            "selected_features": ["strong_a", "weak_c"],
            "selected_interactions": [],
        }

        first = subject._risk_evaluate_selection(conn, **kwargs)
        second = subject._risk_evaluate_selection(conn, **alternate_kwargs)
        stats = subject._risk_rank_cache_stats(rank_cache)

        assert first["available"] is True
        assert first["feature_rank_cache"]["status"] == "miss"
        assert first["rank_feature_count"] == 3
        assert second["feature_rank_cache"]["status"] == "hit"
        assert second["rank_feature_count"] == 3
        assert stats["misses"] == 1
        assert stats["hits"] == 1
        assert stats["entry_count"] == 1
    assert stats["preferred_feature_count"] == 3


def test_candidate_generation_penalty_normalizes_duplicate_signatures():
    features = ["strong_b", "strong_a"]
    interactions = [
        {"interaction_type": "pair", "feature_a": "strong_b", "feature_b": "strong_a"}
    ]
    fingerprint = subject._candidate_fingerprint(features, interactions)
    seen = {fingerprint}

    penalty = subject._generation_penalty(
        selected_features=["strong_a", "strong_b"],
        selected_interactions=[
            {"interaction_type": "pair", "feature_a": "strong_a", "feature_b": "strong_b"}
        ],
        seen_fingerprints=seen,
        prior_candidate_tokens=[
            subject._candidate_tokens(features, interactions),
        ],
        dedupe_candidates_in_search=True,
        duplicate_candidate_penalty=2.0,
        diversity_penalty_weight=0.5,
    )

    assert penalty["duplicate"] is True
    assert penalty["duplicate_penalty"] == pytest.approx(2.0)
    assert penalty["max_similarity_to_prior"] == pytest.approx(1.0)
    assert penalty["total_penalty"] == pytest.approx(2.5)


def test_evaluate_policy_uses_redundancy_clusters_for_feature_diversity():
    relevance = [
        {"feature_name": "signal_a", "coverage_pct": 100, "rank_ic": 0.10, "directional_spread": 0.02, "stability_score": 0.5, "daily_count": 10},
        {"feature_name": "signal_b", "coverage_pct": 100, "rank_ic": 0.09, "directional_spread": 0.02, "stability_score": 0.5, "daily_count": 10},
        {"feature_name": "signal_c", "coverage_pct": 100, "rank_ic": 0.03, "directional_spread": 0.01, "stability_score": 0.4, "daily_count": 10},
    ]
    params = {
        **subject._deterministic_params(),
        "coverage_weight": 0.0,
        "daily_count_weight": 0.0,
    }

    result = subject._evaluate_policy(
        relevance=relevance,
        pairs=[],
        params=params,
        max_features=2,
        max_interactions=0,
        min_coverage_pct=80.0,
        feature_clusters={"signal_a": "cluster_1", "signal_b": "cluster_1", "signal_c": "cluster_2"},
    )

    assert result["selected_features"] == ["signal_a", "signal_c"]
    assert result["metrics"]["cluster_duplicate_count"] == 0
