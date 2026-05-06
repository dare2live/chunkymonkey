from __future__ import annotations

import json

import pytest

from conftest import duck_mem
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


pytestmark = pytest.mark.contract


def test_workbench_overview_returns_stable_read_model():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE dim_trading_calendar (date DATE, is_open BOOLEAN);
            INSERT INTO dim_trading_calendar VALUES
                ('2026-05-04', TRUE),
                ('2026-05-05', FALSE),
                ('2026-05-06', TRUE);

            CREATE TABLE mart_pipeline_run_manifest (
                run_id TEXT,
                pipeline_name TEXT,
                status TEXT,
                started_at TIMESTAMP,
                ended_at TIMESTAMP,
                duration_s DOUBLE,
                gate_result TEXT,
                created_at TIMESTAMP
            );
            INSERT INTO mart_pipeline_run_manifest VALUES
                ('manifest_old', 'old_pipeline', 'success', '2026-05-05 09:00:00', '2026-05-05 09:01:00', 60, NULL, '2026-05-05 09:00:00'),
                ('manifest_new', 'new_pipeline', 'success', '2026-05-06 09:00:00', '2026-05-06 09:01:00', 60, 'pass', '2026-05-06 09:00:00'),
                ('storage_plan', 'plan_storage_retention', 'success', '2026-05-06 08:00:00', '2026-05-06 08:00:01', 1, NULL, '2026-05-06 08:00:00');

            CREATE TABLE mart_research_schedule_plan (
                run_id TEXT,
                task_id TEXT,
                status TEXT,
                built_at TEXT
            );
            INSERT INTO mart_research_schedule_plan VALUES
                ('schedule_a', 'task_1', 'completed', '2026-05-06T01:00:00'),
                ('schedule_a', 'task_2', 'deferred', '2026-05-06T01:00:00');

            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('champion_a', 'champion'),
                ('challenger_b', 'challenger');

            CREATE TABLE mart_feature_drift_root_cause_summary (
                run_id TEXT,
                source_run_id TEXT,
                feature_name TEXT,
                offender_count INTEGER,
                severe_count INTEGER,
                max_psi DOUBLE,
                recommendation TEXT,
                built_at TEXT
            );
            INSERT INTO mart_feature_drift_root_cause_summary VALUES
                ('drift_run', 'model_run', 'ret_60d', 10, 3, 0.77, 'exclude_or_transform_before_next_large_study', '2026-05-06T01:00:00');
            """
        )
        conn.execute(
            """
            CREATE TABLE dim_schema_version (
                table_name TEXT,
                expected_version TEXT,
                actual_version TEXT,
                rebuilt_at TIMESTAMP,
                notes TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO dim_schema_version VALUES ('mart_model_lifecycle', 'v1', 'v0', NULL, NULL)"
        )

        overview = build_workbench_overview(conn, as_of_date="2026-05-06")

        assert overview["latest_trading_day"] == "2026-05-06"
        assert overview["latest_manifest"]["run_id"] == "manifest_new"
        assert overview["latest_manifest"]["gate_result"] == "pass"
        assert overview["schema_drift_count"] >= 1
        assert overview["research_schedule"]["status_counts"] == {"completed": 1, "deferred": 1}
        assert overview["champion"]["counts"]["champion"] == 1
        assert overview["champion"]["champions"] == [{"model_id": "champion_a", "status": "champion"}]
        assert overview["feature_drift"]["top"][0]["feature_name"] == "ret_60d"
        assert overview["storage"]["latest_run_id"] == "storage_plan"
        assert json.dumps(overview, ensure_ascii=False)


def test_workbench_research_returns_schedule_studies_ranker_and_drift():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_research_schedule_plan (
                run_id TEXT,
                task_id TEXT,
                task_type TEXT,
                priority INTEGER,
                status TEXT,
                enabled BOOLEAN,
                evidence_table TEXT,
                evidence_run_id TEXT,
                evidence_found BOOLEAN,
                evidence_status TEXT,
                reason TEXT,
                command_text TEXT,
                built_at TEXT
            );
            INSERT INTO mart_research_schedule_plan VALUES
                ('schedule_latest', 'ranker_perf', 'performance', 10, 'completed', TRUE,
                 'mart_pipeline_run_manifest', 'ranker_perf_smoke', TRUE, 'success',
                 'ranker cache baseline', 'python run_optuna_model_stability_search.py', '2026-05-06T02:00:00'),
                ('schedule_latest', 'gpcw_defer', 'model', 50, 'deferred', FALSE,
                 NULL, NULL, FALSE, 'missing', 'cost gate', NULL, '2026-05-06T02:00:00');

            CREATE TABLE mart_model_stability_search_summary (
                run_id TEXT,
                model_selection_run_id TEXT,
                feature_table TEXT,
                label_name TEXT,
                best_trial_number INTEGER,
                objective_score DOUBLE,
                trials INTEGER,
                study_total_trials INTEGER,
                config_json TEXT,
                built_at TEXT
            );
            INSERT INTO mart_model_stability_search_summary VALUES
                ('stable_ranker', 'selection_a', 'fact_feature_panel', 'label_20d',
                 7, 0.1234, 60, 60,
                 '{"model_family":"lightgbm_ranker","best_status":"pass","best_metrics":{"walkforward_avg_rank_ic":0.071,"walkforward_std_rank_ic":0.018,"walkforward_worst_topk_drawdown":-0.055,"walkforward_worst_feature_drift_psi":0.22}}',
                 '2026-05-06T03:00:00');

            CREATE TABLE mart_pipeline_run_manifest (
                run_id TEXT,
                pipeline_name TEXT,
                started_at TIMESTAMP,
                duration_s DOUBLE,
                perf_summary_json TEXT
            );
            INSERT INTO mart_pipeline_run_manifest VALUES
                ('other_schedule', 'plan_research_schedule', '2026-05-06 05:30:00', 0.10,
                 '{"ranker_policy_deferred":0,"ranker_policy":{"max_runtime_ratio_vs_regression":99.0}}'),
                ('schedule_latest', 'plan_research_schedule', '2026-05-06 04:30:00', 0.12,
                 '{"ranker_policy_deferred":1,"ranker_policy":{"enabled":true,"large_trial_threshold":0,"require_prior_profile":true,"max_runtime_ratio_vs_regression":2.0,"passing_status":"pass","gate_failure_tokens":["drift","drawdown","walkforward","stability","psi"]}}'),
                ('ranker_perf_smoke', 'run_optuna_model_stability_search', '2026-05-06 04:00:00', 1.49,
                 '{"model_family":"lightgbm_ranker","trials":2,"ranker_cache":{"enabled":true,"entries":2,"hits":2,"misses":2,"cached_rows":346249,"max_group_size":6333},"evaluation_cache":{"enabled":true,"entries":{"matrix":3,"feature_drift":3},"hits":{"matrix":6,"feature_drift":3},"misses":{"matrix":3,"feature_drift":3}},"timing":{"train_s":0.22,"drift_s":0.18}}'),
                ('lgbm_false_cache', 'run_optuna_model_stability_search', '2026-05-06 03:30:00', 1.90,
                 '{"model_family":"lightgbm","trials":5,"ranker_cache":{"enabled":false,"entries":0,"hits":0,"misses":0},"timing":{"train_s":0.30}}'),
                ('lgbm_run', 'run_optuna_model_stability_search', '2026-05-05 04:00:00', 2.00,
                 '{"timing":{"train_s":0.4}}');

            CREATE TABLE mart_feature_drift_root_cause_summary (
                run_id TEXT,
                source_run_id TEXT,
                feature_name TEXT,
                offender_count INTEGER,
                severe_count INTEGER,
                max_psi DOUBLE,
                recommendation TEXT,
                built_at TEXT
            );
            INSERT INTO mart_feature_drift_root_cause_summary VALUES
                ('drift_latest', 'stable_ranker', 'ret_60d', 12, 8, 0.81,
                 'exclude_or_transform_before_next_large_study', '2026-05-06T05:00:00');

            CREATE TABLE mart_model_stability_context_summary (
                run_id TEXT,
                source_run_id TEXT,
                label_name TEXT,
                model_family TEXT,
                best_trial_number INTEGER,
                fold_count INTEGER,
                holdout_rank_ic DOUBLE,
                walkforward_avg_rank_ic DOUBLE,
                walkforward_std_rank_ic DOUBLE,
                walkforward_worst_topk_drawdown DOUBLE,
                walkforward_worst_feature_drift_psi DOUBLE,
                negative_rank_ic_folds INTEGER,
                weak_rank_ic_periods INTEGER,
                low_holdout_rank_ic BOOLEAN,
                high_walkforward_std BOOLEAN,
                drift_gate_pass BOOLEAN,
                drawdown_gate_pass BOOLEAN,
                context_diagnosis_counts_json TEXT,
                main_blockers_json TEXT,
                recommendation TEXT,
                built_at TEXT
            );
            INSERT INTO mart_model_stability_context_summary VALUES
                ('context_latest', 'stable_ranker', 'forward_ret_60d', 'lightgbm_ranker',
                 7, 4, 0.0074, 0.0279, 0.0387, -0.017, 0.1478,
                 1, 2, TRUE, TRUE, TRUE, TRUE,
                 '{"broad_rally_rank_inversion":1,"spread_ok_rank_weak":1,"ok":3}',
                 '["market_phase_rank_inversion","low_holdout_rank_ic","high_walkforward_std_rank_ic"]',
                 'regime_split_or_holdout_rank_calibration_before_larger_study',
                 '2026-05-06T06:00:00');

            CREATE TABLE mart_model_stability_context_diagnostic (
                run_id TEXT,
                source_run_id TEXT,
                scope TEXT,
                fold_id INTEGER,
                period_start TEXT,
                period_end TEXT,
                rank_ic DOUBLE,
                spread DOUBLE,
                topk_net_return DOUBLE,
                topk_max_drawdown DOUBLE,
                feature_drift_psi_max DOUBLE,
                label_positive_rate DOUBLE,
                label_mean DOUBLE,
                market_ret_mean DOUBLE,
                dominant_regime TEXT,
                dominant_regime_share DOUBLE,
                diagnosis TEXT,
                built_at TEXT
            );
            INSERT INTO mart_model_stability_context_diagnostic VALUES
                ('context_latest', 'stable_ranker', 'walkforward_fold', 2,
                 '2025-06-06', '2025-07-03', -0.0217, 0.0078, 0.2064,
                 0.0, 0.0879, 0.8164, 0.1743, -0.0160, 'flat', 0.9000,
                 'broad_rally_rank_inversion', '2026-05-06T06:00:00');

            CREATE TABLE mart_stock_horizon_profile (
                run_id TEXT,
                feature_table TEXT,
                feature_set_id TEXT,
                stock_code TEXT,
                label_name TEXT,
                horizon_days INTEGER,
                obs_count INTEGER,
                avg_return DOUBLE,
                median_return DOUBLE,
                win_rate DOUBLE,
                volatility DOUBLE,
                downside_avg DOUBLE,
                compounded_return DOUBLE,
                max_drawdown DOUBLE,
                path_obs_count INTEGER,
                horizon_score DOUBLE,
                rank_in_stock INTEGER,
                is_best BOOLEAN,
                built_at TEXT
            );
            INSERT INTO mart_stock_horizon_profile VALUES
                ('stock_profile_latest', 'fact_feature_panel_candidate', 'feature_set_a',
                 '000001', 'forward_ret_60d', 60, 120, 0.08, 0.06, 0.62, 0.20, -0.03, 0.18, -0.09, 4, 0.124, 1, TRUE, '2026-05-06T07:00:00'),
                ('stock_profile_latest', 'fact_feature_panel_candidate', 'feature_set_a',
                 '000002', 'forward_ret_90d', 90, 120, 0.14, 0.10, 0.68, 0.24, -0.04, 0.31, -0.05, 3, 0.185, 1, TRUE, '2026-05-06T07:00:00'),
                ('stock_profile_old', 'fact_feature_panel_candidate', 'feature_set_a',
                 '000003', 'forward_ret_20d', 20, 80, 0.02, 0.01, 0.51, 0.12, -0.02, 0.05, -0.07, 5, 0.068, 1, TRUE, '2026-05-05T07:00:00');

            CREATE TABLE mart_stock_horizon_feature_effect (
                run_id TEXT,
                stock_code TEXT,
                label_name TEXT,
                horizon_days INTEGER,
                feature_name TEXT,
                obs_count INTEGER,
                corr DOUBLE,
                abs_corr_rank INTEGER,
                effect_direction TEXT,
                built_at TEXT
            );
            INSERT INTO mart_stock_horizon_feature_effect VALUES
                ('stock_profile_latest', '000001', 'forward_ret_60d', 60, 'regime_down', 120, 0.42, 1, 'positive', '2026-05-06T07:00:00'),
                ('stock_profile_latest', '000002', 'forward_ret_90d', 90, 'ret_60d_tdx_l1_rel', 120, -0.38, 1, 'negative', '2026-05-06T07:00:00'),
                ('stock_profile_latest', '000002', 'forward_ret_90d', 90, 'vol_std_20d', 120, -0.31, 2, 'negative', '2026-05-06T07:00:00');
            """
        )

        research = build_workbench_research(conn)

        assert research["research_schedule"]["status_counts"] == {"completed": 1, "deferred": 1}
        assert research["research_schedule"]["tasks"][0]["task_id"] == "ranker_perf"
        assert research["model_stability"][0]["model_family"] == "lightgbm_ranker"
        assert research["model_stability"][0]["walkforward_avg_rank_ic"] == pytest.approx(0.071)
        assert research["ranker_policy"]["ranker_policy_deferred"] == 1
        assert research["ranker_policy"]["policy"]["max_runtime_ratio_vs_regression"] == pytest.approx(2.0)
        assert [row["run_id"] for row in research["ranker_profiles"]] == ["ranker_perf_smoke"]
        assert research["ranker_profiles"][0]["ranker_cache"]["max_group_size"] == 6333
        assert research["ranker_profiles"][0]["duration_per_trial_s"] == pytest.approx(0.745)
        assert research["ranker_profiles"][0]["cache_hit_rate"] == pytest.approx(0.5)
        assert research["ranker_profiles"][0]["eval_cache_hit_rate"] == pytest.approx(9 / 15)
        assert research["ranker_profiles"][0]["matrix_cache_hit_rate"] == pytest.approx(6 / 9)
        assert research["ranker_profiles"][0]["feature_drift_cache_hit_rate"] == pytest.approx(3 / 6)
        assert research["ranker_profiles"][0]["train_time_pct"] == pytest.approx(0.22 / 1.49)
        assert research["ranker_profiles"][0]["runtime_ratio_vs_regression"] == pytest.approx(0.745 / 0.38)
        assert research["stability_context"]["run_id"] == "context_latest"
        assert research["stability_context"]["summaries"][0]["main_blockers"] == [
            "market_phase_rank_inversion",
            "low_holdout_rank_ic",
            "high_walkforward_std_rank_ic",
        ]
        assert research["stability_context"]["summaries"][0]["drift_gate_pass"] is True
        assert research["stability_context"]["summaries"][0]["drawdown_gate_pass"] is True
        assert research["stability_context"]["diagnostics"][0]["diagnosis"] == "broad_rally_rank_inversion"
        assert research["stock_horizon_profile"]["run_id"] == "stock_profile_latest"
        assert research["stock_horizon_profile"]["baseline_label"] == "forward_ret_60d"
        assert research["stock_horizon_profile"]["best_count"] == 2
        assert research["stock_horizon_profile"]["horizon_comparison"][0]["avg_max_drawdown"] == pytest.approx(-0.09)
        assert research["stock_horizon_profile"]["horizon_comparison"][0]["avg_path_obs_count"] == pytest.approx(4.0)
        assert [row["horizon_days"] for row in research["stock_horizon_profile"]["horizon_distribution"]] == [60, 90]
        assert research["stock_horizon_profile"]["horizon_distribution"][0]["is_baseline"] is True
        assert research["stock_horizon_profile"]["top_effects"][0]["feature_name"] == "regime_down"
        assert research["stock_horizon_profile"]["top_effects"][0]["effect_direction"] == "positive"
        assert research["stock_horizon_profile"]["feature_effects_by_horizon"][0]["feature_name"] == "regime_down"
        assert research["stock_horizon_profile"]["feature_effects_by_horizon"][0]["dominant_direction"] == "positive"
        assert research["stock_horizon_profile"]["best_stocks"][0]["stock_code"] == "000002"
        assert research["feature_drift"]["top"][0]["feature_name"] == "ret_60d"
        assert json.dumps(research, ensure_ascii=False)


def test_workbench_champion_returns_gate_evidence_and_primary_topk():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT,
                ic_holdout DOUBLE,
                ic_walkforward_avg DOUBLE,
                ic_walkforward_std DOUBLE,
                drift_score DOUBLE,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('champion_a', 'champion', 0.05, 0.061, 0.012, 0.18, '2026-05-01 00:00:00', '2026-05-06 00:00:00'),
                ('challenger_b', 'challenger', 0.07, 0.068, 0.010, 0.21, '2026-05-02 00:00:00', '2026-05-06 01:00:00');

            CREATE TABLE mart_champion_candidate_evaluation (
                evaluation_run_id TEXT,
                model_id TEXT,
                status TEXT,
                pit_status TEXT,
                pit_violation_rows INTEGER,
                evidence_status TEXT,
                gate_status TEXT,
                failed_steps_json TEXT,
                started_at TIMESTAMP,
                ended_at TIMESTAMP,
                duration_s DOUBLE
            );
            INSERT INTO mart_champion_candidate_evaluation VALUES
                ('eval_1', 'challenger_b', 'failed', 'pass', 0, 'success', 'blocked',
                 '["promotion_gate"]', '2026-05-06 02:00:00', '2026-05-06 02:01:00', 60);

            CREATE TABLE mart_challenger_evidence_bundle (
                evidence_run_id TEXT,
                model_id TEXT,
                status TEXT,
                steps_json TEXT,
                gate_run_id TEXT,
                gate_status TEXT,
                blockers_json TEXT,
                started_at TIMESTAMP,
                ended_at TIMESTAMP,
                duration_s DOUBLE
            );
            INSERT INTO mart_challenger_evidence_bundle VALUES
                ('evidence_1', 'challenger_b', 'success',
                 '[{"step":"pit"},{"step":"gate"}]', 'gate_1', 'blocked',
                 '["drawdown"]', '2026-05-06 02:00:00', '2026-05-06 02:01:00', 60);

            CREATE TABLE mart_tdx_keep_promotion_gate (
                gate_run_id TEXT,
                challenger_model_id TEXT,
                champion_model_id TEXT,
                promotion_status TEXT,
                decision TEXT,
                gate_results_json TEXT,
                blockers_json TEXT,
                rank_ic_challenger DOUBLE,
                rank_ic_champion DOUBLE,
                long_short_challenger DOUBLE,
                long_short_champion DOUBLE,
                max_drawdown_challenger DOUBLE,
                max_drawdown_champion DOUBLE,
                evaluated_at TIMESTAMP
            );
            INSERT INTO mart_tdx_keep_promotion_gate VALUES
                ('gate_1', 'challenger_b', 'champion_a', 'blocked', 'keep_champion',
                 '{"rank_ic":"pass"}', '["drawdown"]',
                 0.07, 0.06, 0.08, 0.06, -0.18, -0.12, '2026-05-06 03:00:00'),
                ('gate_promote', 'champion_a', 'old_champion', 'PASS', 'promote_ready',
                 '{"rank_ic":"pass"}', '[]',
                 0.09, 0.06, 0.08, 0.06, -0.08, -0.12, '2026-05-06 02:00:00'),
                ('gate_self_fail', 'champion_a', 'champion_a', 'FAIL', 'reject',
                 '{"rank_ic":"fail"}', '["rank_ic"]',
                 0.09, 0.09, 0.08, 0.08, -0.08, -0.08, '2026-05-06 04:00:00');

            CREATE TABLE mart_daily_recommendation (
                snapshot_date DATE,
                stock_code TEXT,
                model_id TEXT,
                rank_in_date INTEGER,
                pred_score DOUBLE,
                percentile DOUBLE,
                regime_flag TEXT,
                track_id TEXT,
                run_mode TEXT,
                is_primary BOOLEAN
            );
            INSERT INTO mart_daily_recommendation VALUES
                ('2026-05-06', '000001', 'champion_a', 1, 0.92, 0.99, 'risk_on', 'track_a', 'champion', TRUE),
                ('2026-05-06', '000002', 'champion_a', 2, 0.88, 0.98, 'risk_on', 'track_a', 'champion', TRUE),
                ('2026-05-06', '000099', 'old_champion', 1, 0.85, 0.97, 'risk_on', 'track_old', 'champion', TRUE),
                ('2026-05-06', '000003', 'shadow_x', 1, 0.77, 0.90, 'risk_on', 'track_b', 'shadow', FALSE);

            CREATE TABLE mart_model_stability_context_summary (
                run_id TEXT,
                source_run_id TEXT,
                label_name TEXT,
                model_family TEXT,
                best_trial_number INTEGER,
                fold_count INTEGER,
                holdout_rank_ic DOUBLE,
                walkforward_avg_rank_ic DOUBLE,
                walkforward_std_rank_ic DOUBLE,
                walkforward_worst_topk_drawdown DOUBLE,
                walkforward_worst_feature_drift_psi DOUBLE,
                negative_rank_ic_folds INTEGER,
                weak_rank_ic_periods INTEGER,
                low_holdout_rank_ic BOOLEAN,
                high_walkforward_std BOOLEAN,
                drift_gate_pass BOOLEAN,
                drawdown_gate_pass BOOLEAN,
                context_diagnosis_counts_json TEXT,
                main_blockers_json TEXT,
                recommendation TEXT,
                built_at TEXT
            );
            INSERT INTO mart_model_stability_context_summary VALUES
                ('context_gate', 'source_gate', 'forward_ret_60d', 'lightgbm',
                 4, 4, 0.0074, 0.0279, 0.0387, -0.017, 0.1478,
                 1, 2, TRUE, TRUE, TRUE, TRUE,
                 '{"broad_rally_rank_inversion":1}',
                 '["market_phase_rank_inversion","low_holdout_rank_ic"]',
                 'regime_split_or_holdout_rank_calibration_before_larger_study',
                 '2026-05-06T06:00:00');
            """
        )

        champion = build_workbench_champion(conn)

        assert champion["lifecycle"]["counts"] == {"champion": 1, "challenger": 1}
        assert champion["challengers"][0]["model_id"] == "challenger_b"
        assert champion["candidate_evaluations"][0]["failed_steps"] == ["promotion_gate"]
        assert champion["evidence_bundles"][0]["step_count"] == 2
        assert champion["evidence_bundles"][0]["blocker_count"] == 1
        assert champion["deployment"]["status"] == "deployed"
        assert champion["deployment"]["latest_promotion_gate"]["gate_run_id"] == "gate_promote"
        assert champion["deployment"]["latest_self_check"]["gate_run_id"] == "gate_self_fail"
        assert champion["deployment"]["blockers"] == []
        assert champion["promotion_gates"][0]["gate_run_id"] == "gate_self_fail"
        assert champion["stability_context"]["summaries"][0]["recommendation"] == (
            "regime_split_or_holdout_rank_calibration_before_larger_study"
        )
        assert champion["latest_primary_topk"]["model_id"] == "champion_a"
        assert champion["latest_primary_topk"]["count"] == 2
        assert [row["stock_code"] for row in champion["latest_primary_topk"]["rows"]] == ["000001", "000002"]
        assert json.dumps(champion, ensure_ascii=False)


def test_workbench_data_sources_returns_tdxhub_primary_watermarks_and_feature_lineage():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE dim_trading_calendar (date DATE, is_open BOOLEAN);
            INSERT INTO dim_trading_calendar VALUES ('2026-05-06', TRUE);

            CREATE TABLE mart_data_source_watermark (
                data_domain TEXT,
                source_name TEXT,
                source_tier INTEGER,
                last_success_at TIMESTAMP,
                last_data_date TEXT,
                row_count INTEGER,
                consecutive_failures INTEGER,
                fallback_active BOOLEAN,
                fallback_reason TEXT,
                parser_version TEXT,
                updated_at TIMESTAMP
            );
            INSERT INTO mart_data_source_watermark VALUES
                ('kline_daily', 'tdxhub_quote', 1, '2026-05-06 08:00:00', '2026-04-30', 100, 0, FALSE, NULL, 'tdxhub_qfq_daily', '2026-05-06 08:00:00'),
                ('kline_daily', 'akshare_multi_source', 3, '2026-05-06 08:00:00', '2026-04-30', 20, 0, TRUE, 'fills missing keys', 'akshare_fallback_daily', '2026-05-06 08:00:00'),
                ('northbound_holding', 'akshare_hsgt', 3, NULL, NULL, 0, 1, FALSE, 'no stable primary', 'akshare', '2026-05-06 08:00:00');

            CREATE TABLE mart_feature_panel_validation (
                validation_id TEXT,
                run_mode TEXT,
                status TEXT,
                validated_at TEXT,
                rows INTEGER,
                duplicate_keys INTEGER,
                close_coverage DOUBLE,
                source_lineage_coverage DOUBLE,
                source_fallback_ratio DOUBLE,
                source_distribution_json TEXT,
                blockers_json TEXT
            );
            INSERT INTO mart_feature_panel_validation VALUES
                ('validation_a', 'validate-only', 'passed', '2026-05-06T09:00:00', 120, 0, 0.99, 1.0, 0.12,
                 '[{"source_name":"tdxhub","rows":100},{"source_name":"fallback","rows":20}]',
                 '[]');

            CREATE TABLE dim_data_asset (
                table_name TEXT,
                layer TEXT,
                purpose TEXT,
                writer_module TEXT,
                upstream_source TEXT,
                source_tier INTEGER,
                expected_freshness TEXT,
                sla_hours DOUBLE,
                deprecation_status TEXT
            );
            INSERT INTO dim_data_asset VALUES
                ('price_kline_tdxhub', 'raw', 'kline', 'build_price_kline_tdxhub', 'tdxhub_quote', 1, 'daily', 24, 'active'),
                ('fact_feature_panel', 'fact', 'features', 'build_feature_panel_duck', 'duckdb', 1, 'daily', 24, 'active'),
                ('legacy_shadow_table', 'mart', 'legacy', 'old_writer', 'akshare', 3, 'daily', 24, 'deprecated');

            CREATE TABLE mart_data_health (
                table_name TEXT,
                snapshot_at TEXT,
                row_count INTEGER,
                last_data_date TEXT,
                freshness_hours DOUBLE,
                freshness_ok BOOLEAN,
                severity TEXT,
                issue_summary TEXT,
                source_tier_dist TEXT
            );
            INSERT INTO mart_data_health VALUES
                ('price_kline_tdxhub', '2026-05-06T10:00:00', 100, '2026-04-30', 1.0, TRUE, 'green', NULL, '{"1":100}'),
                ('fact_feature_panel', '2026-05-06T10:00:00', 120, '2026-04-30', 2.0, TRUE, 'yellow', 'fallback rows', '{"1":100,"3":20}'),
                ('legacy_shadow_table', '2026-05-06T10:00:00', 0, NULL, NULL, FALSE, 'red', 'deprecated', '{"3":1}');
            """
        )

        data_sources = build_workbench_data_sources(conn, as_of_date="2026-05-06")

        assert data_sources["calendar_target"] == "2026-05-06"
        assert data_sources["kline"]["primary"]["source_name"] == "tdxhub_quote"
        assert data_sources["kline"]["primary_is_tdxhub"] is True
        assert data_sources["kline"]["fallback_active_count"] == 1
        assert data_sources["latest_feature_validation"]["source_fallback_ratio"] == pytest.approx(0.12)
        assert data_sources["asset_health"]["summary"] == {
            "total": 3,
            "green": 1,
            "yellow": 1,
            "red": 1,
            "unknown": 0,
        }
        assert data_sources["asset_health"]["fallback_active"][0]["table"] == "fact_feature_panel"
        assert [row["upstream_source"] for row in data_sources["source_health"]["sources"]] == [
            "duckdb",
            "tdxhub_quote",
        ]
        assert data_sources["blockers"][0]["kind"] == "source_failures"
        assert json.dumps(data_sources, ensure_ascii=False)


def test_workbench_pipelines_returns_recent_status_slowest_and_blockers():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_pipeline_run_manifest (
                run_id TEXT,
                pipeline_name TEXT,
                status TEXT,
                started_at TIMESTAMP,
                ended_at TIMESTAMP,
                duration_s DOUBLE,
                gate_result TEXT,
                blockers_json TEXT,
                perf_summary_json TEXT,
                model_id TEXT,
                feature_group TEXT,
                label_name TEXT,
                holding_period INTEGER,
                created_at TIMESTAMP
            );
            INSERT INTO mart_pipeline_run_manifest VALUES
                ('run_ok', 'build_features', 'success', '2026-05-06 08:00:00', '2026-05-06 08:01:00', 60, 'pass', '[]', '{"rows":100}', NULL, NULL, NULL, NULL, '2026-05-06 08:00:00'),
                ('run_bad', 'daily_fetch', 'failed', '2026-05-06 09:00:00', '2026-05-06 09:03:00', 180, 'fail', '[{"kind":"network"}]', '{"retry":2}', NULL, NULL, NULL, NULL, '2026-05-06 09:00:00');
            """
        )

        pipelines = build_workbench_pipelines(conn, limit=10)

        assert pipelines["status_counts"] == {"failed": 1, "success": 1}
        assert pipelines["recent"][0]["run_id"] == "run_bad"
        assert pipelines["slowest"][0]["run_id"] == "run_bad"
        assert pipelines["blockers"][0]["blocker_count"] == 1
        assert json.dumps(pipelines, ensure_ascii=False)


def test_workbench_features_returns_registry_validation_search_and_association():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_feature_panel_validation (
                validation_id TEXT,
                run_mode TEXT,
                status TEXT,
                validated_at TEXT,
                rows INTEGER,
                duplicate_keys INTEGER,
                close_coverage DOUBLE,
                source_lineage_coverage DOUBLE,
                source_fallback_ratio DOUBLE,
                source_distribution_json TEXT,
                blockers_json TEXT
            );
            INSERT INTO mart_feature_panel_validation VALUES
                ('validation_a', 'validate-only', 'passed', '2026-05-06T09:00:00', 120, 0, 0.99, 1.0, 0.12, '[]', '[]');

            CREATE TABLE mart_feature_search_space_summary (
                run_id TEXT,
                source_association_run_id TEXT,
                panel_table TEXT,
                label_name TEXT,
                selected_count INTEGER,
                excluded_count INTEGER,
                selected_features_json TEXT,
                group_counts_json TEXT,
                built_at TEXT
            );
            INSERT INTO mart_feature_search_space_summary VALUES
                ('feature_space_a', 'assoc_a', 'fact_feature_panel', 'forward_ret_20d', 2, 1,
                 '["ret_20d","ma_ratio_60"]', '{"price_volume":2}', '2026-05-06T10:00:00');

            CREATE TABLE mart_feature_association_stat (
                run_id TEXT,
                panel_table TEXT,
                label_name TEXT,
                feature_name TEXT,
                feature_group TEXT,
                coverage_pct DOUBLE,
                rank_ic DOUBLE,
                long_short_spread DOUBLE,
                source_fallback_pct DOUBLE,
                built_at TEXT
            );
            INSERT INTO mart_feature_association_stat VALUES
                ('assoc_a', 'fact_feature_panel', 'forward_ret_20d', 'ret_20d', 'price_volume', 99.0, 0.05, 0.03, 0.10, '2026-05-06T10:00:00'),
                ('assoc_a', 'fact_feature_panel', 'forward_ret_20d', 'ma_ratio_60', 'price_volume', 98.0, -0.07, -0.02, 0.11, '2026-05-06T10:00:00');

            CREATE TABLE mart_feature_drift_root_cause_summary (
                run_id TEXT,
                source_run_id TEXT,
                feature_name TEXT,
                offender_count INTEGER,
                severe_count INTEGER,
                max_psi DOUBLE,
                recommendation TEXT,
                built_at TEXT
            );
            INSERT INTO mart_feature_drift_root_cause_summary VALUES
                ('drift_a', 'model_a', 'ret_60d', 4, 2, 0.61, 'exclude_or_transform_before_next_large_study', '2026-05-06T11:00:00');

            CREATE TABLE mart_feature_drift_mitigation_panel_build (
                run_id TEXT,
                output_feature_set_id TEXT,
                model_selection_run_id TEXT,
                base_model_selection_run_id TEXT,
                base_table TEXT,
                root_cause_run_id TEXT,
                transformed_features_json TEXT,
                copied_features_json TEXT,
                original_selected_features_json TEXT,
                selected_features_json TEXT,
                transform_config_json TEXT,
                row_count INTEGER,
                stock_count INTEGER,
                date_count INTEGER,
                min_date TEXT,
                max_date TEXT,
                built_at TEXT
            );
            INSERT INTO mart_feature_drift_mitigation_panel_build VALUES (
                'mitigation_a', 'mitigated_set', 'mitigated_selection',
                'base_selection', 'fact_feature_panel', 'drift_a',
                '{"ret_60d":["ret_60d_xs_rank","ret_60d_xs_winsor"]}',
                '["stable_flow"]', '["ret_60d","stable_flow"]',
                '["stable_flow","ret_60d_xs_rank","ret_60d_xs_winsor"]',
                '{"transform_types":["xs_rank","xs_winsor"]}',
                120, 3, 2, '2026-01-01', '2026-01-02', '2026-05-06T12:00:00'
            );
            """
        )

        features = build_workbench_features(conn)

        assert features["registry"]["model_input_count"] > 0
        assert features["latest_validation"]["status"] == "passed"
        assert features["search_spaces"][0]["run_id"] == "feature_space_a"
        assert features["drift_mitigation_builds"][0]["run_id"] == "mitigation_a"
        assert features["drift_mitigation_builds"][0]["transformed_features"]["ret_60d"] == [
            "ret_60d_xs_rank",
            "ret_60d_xs_winsor",
        ]
        assert features["top_associations"][0]["feature_name"] == "ma_ratio_60"
        assert features["feature_drift"]["top"][0]["feature_name"] == "ret_60d"
        assert json.dumps(features, ensure_ascii=False)


def test_workbench_storage_returns_architecture_cleanup_without_live_plan():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_pipeline_run_manifest (
                run_id TEXT,
                pipeline_name TEXT,
                status TEXT,
                started_at TIMESTAMP
            );
            INSERT INTO mart_pipeline_run_manifest VALUES
                ('storage_plan', 'plan_storage_retention', 'success', '2026-05-06 08:00:00');

            CREATE TABLE mart_architecture_inventory_asset (
                run_id TEXT,
                path TEXT,
                asset_type TEXT,
                module_area TEXT,
                classification TEXT,
                notes TEXT,
                built_at TEXT
            );
            INSERT INTO mart_architecture_inventory_asset VALUES
                ('arch_a', 'backend/services/runtime_patches.py', 'code', 'other', 'compatibility_shim', 'shim', '2026-05-06T01:00:00'),
                ('arch_a', 'old.py', 'code', 'other', 'delete_after_tests', 'unused', '2026-05-06T01:00:00'),
                ('arch_a', 'active.py', 'code', 'api_workbench', 'production', 'active', '2026-05-06T01:00:00');

            CREATE TABLE mart_architecture_cleanup_plan (
                run_id TEXT,
                inventory_run_id TEXT,
                asset_id TEXT,
                asset_type TEXT,
                path TEXT,
                classification TEXT,
                action TEXT,
                status TEXT,
                reason TEXT,
                blockers_json TEXT,
                smoke_status TEXT,
                smoke_error TEXT,
                built_at TEXT
            );
            INSERT INTO mart_architecture_cleanup_plan VALUES
                ('cleanup_a', 'arch_a', 'duckdb:v_l2_profile', 'duckdb_view',
                 'smartmoney.main.v_l2_profile', 'compatibility_shim',
                 'drop_view_in_copied_db_smoke', 'smoke_passed',
                 'view dropped successfully in copied DB smoke', '[]', 'passed', NULL,
                 '2026-05-06T02:00:00');
            """
        )

        storage = build_workbench_storage(conn, include_live_plan=False)

        assert storage["latest_manifest"]["latest_run_id"] == "storage_plan"
        assert storage["retention"]["mode"] == "unavailable"
        assert storage["architecture"]["classification_counts"]["production"] == 1
        assert len(storage["architecture"]["cleanup_candidates"]) == 2
        assert storage["architecture_cleanup"]["run_id"] == "cleanup_a"
        assert storage["architecture_cleanup"]["status_counts"]["smoke_passed"] == 1
        assert storage["architecture_cleanup"]["smoke_counts"]["passed"] == 1
        assert json.dumps(storage, ensure_ascii=False)


def test_workbench_storage_uses_cleanup_manifest_when_plan_has_no_rows():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_pipeline_run_manifest (
                run_id TEXT,
                pipeline_name TEXT,
                status TEXT,
                started_at TIMESTAMP,
                perf_summary_json TEXT
            );
            INSERT INTO mart_pipeline_run_manifest VALUES
                ('cleanup_empty', 'plan_architecture_cleanup', 'success',
                 '2026-05-06 09:00:00',
                 '{"candidate_count":0,"inventory_run_id":"arch_latest","status_counts":{},"action_counts":{},"smoke_counts":{}}');

            CREATE TABLE mart_architecture_inventory_asset (
                run_id TEXT,
                path TEXT,
                asset_type TEXT,
                module_area TEXT,
                classification TEXT,
                notes TEXT,
                built_at TEXT
            );
            INSERT INTO mart_architecture_inventory_asset VALUES
                ('arch_latest', 'active.py', 'code', 'api_workbench', 'production', 'active', '2026-05-06T01:00:00');

            CREATE TABLE mart_architecture_cleanup_plan (
                run_id TEXT,
                inventory_run_id TEXT,
                asset_id TEXT,
                asset_type TEXT,
                path TEXT,
                classification TEXT,
                action TEXT,
                status TEXT,
                reason TEXT,
                blockers_json TEXT,
                smoke_status TEXT,
                smoke_error TEXT,
                built_at TEXT
            );
            """
        )

        storage = build_workbench_storage(conn, include_live_plan=False)

        assert storage["architecture_cleanup"]["run_id"] == "cleanup_empty"
        assert storage["architecture_cleanup"]["inventory_run_id"] == "arch_latest"
        assert storage["architecture_cleanup"]["candidate_count"] == 0
        assert storage["architecture_cleanup"]["candidates"] == []


def test_workbench_recommendations_returns_primary_topk_risk_outcome_and_source_quality():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_daily_recommendation (
                snapshot_date DATE,
                stock_code TEXT,
                model_id TEXT,
                rank_in_date INTEGER,
                pred_score DOUBLE,
                percentile DOUBLE,
                regime_flag TEXT,
                key_features_json TEXT,
                built_at TEXT,
                track_id TEXT,
                is_primary BOOLEAN,
                run_mode TEXT
            );
            CREATE TABLE mart_model_lifecycle (
                model_id TEXT,
                status TEXT
            );
            INSERT INTO mart_model_lifecycle VALUES
                ('champion_a', 'champion'),
                ('old_champion', 'retired');
            INSERT INTO mart_daily_recommendation VALUES
                ('2026-05-06', '000099', 'old_champion', 1, 0.94, 1.00, 'risk_on',
                 '{}', '2026-05-06T09:05:00', 'primary', TRUE, 'champion'),
                ('2026-05-06', '000001', 'champion_a', 1, 0.91, 1.00, 'risk_on',
                 '{"model_top_features":[{"name":"ret_20d"},{"name":"ma_ratio_60"}]}',
                 '2026-05-06T09:00:00', 'primary', TRUE, 'champion'),
                ('2026-05-06', '000002', 'champion_a', 2, 0.89, 0.99, 'risk_on',
                 '{"model_top_features":[{"name":"ret_60d"}]}',
                 '2026-05-06T09:00:00', 'primary', TRUE, 'champion'),
                ('2026-05-06', '000003', 'shadow_a', 1, 0.70, 0.90, 'risk_on',
                 '{}', '2026-05-06T09:00:00', 'shadow', FALSE, 'shadow');

            CREATE TABLE mart_daily_recommendation_risk (
                snapshot_date DATE,
                model_id TEXT,
                track_id TEXT,
                is_primary BOOLEAN,
                top_size INTEGER,
                top1_industry TEXT,
                top1_industry_share DOUBLE,
                top3_industry_share DOUBLE,
                top20_amount_ma20_p25 DOUBLE,
                top20_amount_ma20_median DOUBLE,
                overlap_with_primary DOUBLE,
                built_at TEXT
            );
            INSERT INTO mart_daily_recommendation_risk VALUES
                ('2026-05-06', 'champion_a', 'primary', TRUE, 20, '信息产业',
                 0.30, 0.70, 1000000, 2000000, NULL, '2026-05-06T09:00:00');

            CREATE TABLE mart_prediction_outcome (
                snapshot_date DATE,
                stock_code TEXT,
                model_id TEXT,
                rank_in_date INTEGER,
                pred_score DOUBLE,
                entry_price DOUBLE,
                ret_5d DOUBLE,
                ret_10d DOUBLE,
                ret_30d DOUBLE,
                hit_5d BOOLEAN,
                hit_30d BOOLEAN,
                outcome_known_at TIMESTAMP
            );
            INSERT INTO mart_prediction_outcome VALUES
                ('2026-05-06', '000001', 'champion_a', 1, 0.91, 10, 0.05, 0.08, NULL, TRUE, NULL, '2026-05-13 18:00:00'),
                ('2026-05-06', '000002', 'champion_a', 2, 0.89, 20, -0.01, 0.02, NULL, FALSE, NULL, '2026-05-13 18:00:00');

            CREATE TABLE mart_data_source_watermark (
                data_domain TEXT,
                source_name TEXT,
                source_tier INTEGER,
                last_success_at TIMESTAMP,
                last_data_date TEXT,
                row_count INTEGER,
                consecutive_failures INTEGER,
                fallback_active BOOLEAN,
                fallback_reason TEXT,
                parser_version TEXT,
                updated_at TIMESTAMP
            );
            INSERT INTO mart_data_source_watermark VALUES
                ('kline_daily', 'tdxhub_quote', 1, '2026-05-06 08:00:00', '2026-04-30', 100, 0, FALSE, NULL, 'tdxhub_qfq_daily', '2026-05-06 08:00:00');

            CREATE TABLE mart_feature_panel_validation (
                validation_id TEXT,
                run_mode TEXT,
                status TEXT,
                validated_at TEXT,
                rows INTEGER,
                duplicate_keys INTEGER,
                close_coverage DOUBLE,
                source_lineage_coverage DOUBLE,
                source_fallback_ratio DOUBLE,
                source_distribution_json TEXT,
                blockers_json TEXT
            );
            INSERT INTO mart_feature_panel_validation VALUES
                ('validation_a', 'validate-only', 'passed', '2026-05-06T09:00:00', 120, 0, 0.99, 1.0, 0.12, '[]', '[]');
            """
        )

        recommendations = build_workbench_recommendations(conn)

        assert recommendations["latest_primary"]["model_id"] == "champion_a"
        assert recommendations["latest_primary"]["count"] == 2
        assert [row["stock_code"] for row in recommendations["rows"]] == ["000001", "000002"]
        assert recommendations["rows"][0]["top_features"] == ["ret_20d", "ma_ratio_60"]
        assert recommendations["risk"][0]["top1_industry"] == "信息产业"
        assert recommendations["outcomes"]["count"] == 2
        assert recommendations["outcomes"]["hit_rate_5d"] == pytest.approx(0.5)
        assert recommendations["source_quality"]["kline_primary_is_tdxhub"] is True
        assert recommendations["source_quality"]["source_fallback_ratio"] == pytest.approx(0.12)
        assert json.dumps(recommendations, ensure_ascii=False)
