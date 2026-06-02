from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_workbench_frontend_render_smoke_for_all_tabs():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        const elements = {
          'wb-tab-root': { innerHTML: '', querySelectorAll: () => [] },
          'wb-overview-root': { innerHTML: '', querySelectorAll: () => [] },
        };
        global.window = global;
        global.document = {
          getElementById: (id) => elements[id] || null,
        };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        function assertContains(html, needle, renderName) {
          if (!html.includes(needle)) {
            throw new Error(renderName + ' missing expected text: ' + needle + '\n' + html.slice(0, 800));
          }
        }

        function render(name, data, needles) {
          elements['wb-tab-root'].innerHTML = '';
          window.WorkbenchView[name](data);
          const html = elements['wb-tab-root'].innerHTML;
          if (html.length < 80) throw new Error(name + ' rendered too little HTML');
          if (html.includes('undefined')) throw new Error(name + ' rendered undefined');
          needles.forEach((needle) => assertContains(html, needle, name));
        }

        const overview = {
          latest_trading_day: '2026-05-06',
          latest_manifest: { pipeline_name: 'cron_daily', run_id: 'cron_1', status: 'success', duration_s: 3.2 },
          champion: { counts: { champion: 1 }, champions: [{ model_id: 'champion_a' }] },
          research_schedule: { run_id: 'schedule_1', status_counts: { completed: 1 } },
          storage: { latest_run_id: 'cleanup_1', latest_status: 'success' },
          feature_drift: { run_id: 'drift_1', top: [] },
          blockers: [],
        };
        const dataSources = {
          calendar_target: '2026-05-06',
          kline: {
            primary: { source_name: 'tdxhub_quote', source_tier: 1, row_count: 10, last_data_date: '2026-05-06' },
            primary_is_tdxhub: true,
            fallback_active_count: 0,
          },
          latest_feature_validation: {
            validation_id: 'feature_val_1',
            status: 'passed',
            source_fallback_ratio: 0.01,
            source_distribution: [{ source_name: 'tdxhub_quote', row_count: 10 }],
          },
          tdx_server_health: {
            summary: { healthy_count: 1, timeout_server_count: 1, total_successes: 3, total_failures: 1, total_timeouts: 1, capabilities: ['kline_daily_raw'] },
            updated_at: '2026-05-07T05:02:00+00:00',
            servers: [{ server_host: '218.6.170.47', server_port: 7709, capability: 'kline_daily_raw', success_count: 3, failure_count: 0, timeout_count: 0, health_score: 34.1, avg_success_elapsed_s: 0.42, last_attempt_elapsed_s: 0.39, last_success_at: '2026-05-07T05:00:00+00:00', source_run_id: 'tdx_probe_good' }],
          },
          f10_source_date_audit: {
            run_id: 'f10_source_v3',
            built_at: '2026-05-07T10:00:00',
            summary: { raw_row_count: 100, audit_rows: 2, source_notice_candidate_occurrences: 95, source_notice_candidate_future_occurrences: 0, future_occurrence_count: 3 },
            rows: [
              { section_id: '2', section_name: '股东增减持计划', pattern_name: 'latest_announce_date', date_role: 'source_notice_date', source_notice_candidate: true, occurrence_count: 95, future_occurrence_count: 0, min_date: '2026-01-01', max_date: '2026-05-01', stock_count: 80, raw_row_count: 100 },
              { section_id: '2', section_name: '股东增减持计划', pattern_name: 'change_end_date', date_role: 'plan_end_date', source_notice_candidate: false, occurrence_count: 20, future_occurrence_count: 3, min_date: '2026-01-01', max_date: '2026-12-31', stock_count: 80, raw_row_count: 100 },
            ],
          },
          tdx_f10_source_dq: {
            gate_run_id: 'dq_f10_source',
            gate_status: 'pass',
            blocker_count: 0,
            warning_count: 0,
            ended_at: '2026-05-07T10:00:13',
            summary: { detail_count: 1 },
            details: [{ table_name: 'fact_shareholder_plan_tdx_f10', column_name: 'source_available_date', check_name: 'plan_window_used_as_source_date', status: 'pass', severity: 'blocker', row_count: 12062, violation_count: 0 }],
          },
          watermarks: [{ data_domain: 'kline', source_name: 'tdxhub_quote', source_tier: 1, row_count: 10 }],
          today_signal_cache: { status: 'hit', signal_count: 9261, freshness_days: 90, source_max_notice_date: '2026-05-05', current_source_max_notice_date: '2026-05-05', built_at: '2026-05-07T08:00:00', stale: false, requires_refresh: false, step: { status: 'completed', records: 9261, finished_at: '2026-05-07T08:00:01' } },
          blockers: [],
        };
        const pipelines = {
          status_counts: { success: 1 },
          recent: [{ run_id: 'cron_1', pipeline_name: 'cron_daily', status: 'success', duration_s: 3.2 }],
          slowest: [{ run_id: 'ranker_1', pipeline_name: 'ranker', status: 'success', duration_s: 11.6 }],
          blockers: [],
        };
        const features = {
          registry: { feature_count: 3, model_input_count: 2, label_count: 1, group_counts: { price_volume: 2 } },
          latest_validation: { validation_id: 'feature_val_1', status: 'passed', rows: 100, source_lineage_coverage: 1, source_fallback_ratio: 0 },
          search_spaces: [{ run_id: 'space_1', panel_table: 'fact_feature_panel', label_name: 'forward_ret_20d', selected_count: 2, excluded_count: 1, group_counts: { price_volume: 2 } }],
          drift_mitigation_builds: [{ run_id: 'mitigation_1', output_feature_set_id: 'mitigated_set', model_selection_run_id: 'mitigated_selection', root_cause_run_id: 'drift_1', row_count: 100, date_count: 2, min_date: '2026-01-01', max_date: '2026-01-02', transformed_features: { ret_60d: ['ret_60d_xs_rank', 'ret_60d_xs_winsor'] } }],
          top_associations: [{ feature_name: 'ret_20d', feature_group: 'price_volume', rank_ic: 0.04, coverage_pct: 0.99 }],
          feature_drift: { top: [] },
        };
        const research = {
          research_schedule: { run_id: 'schedule_1', status_counts: { completed: 1 }, tasks: [{ task_id: 'ranker_perf', status: 'completed', priority: 1 }] },
          model_stability: [{ run_id: 'stable_1', model_family: 'lightgbm_ranker', best_status: 'pass', objective_score: 0.1, trials: 2 }],
          ranker_policy: { run_id: 'schedule_1', ranker_policy_deferred: 0, policy: { max_runtime_ratio_vs_regression: 2, large_trial_threshold: 8 } },
          ranker_profiles: [{ run_id: 'ranker_perf', duration_s: 11.6, duration_per_trial_s: 5.8, cache_hit_rate: 0.5, train_time_pct: 0.2, runtime_ratio_vs_regression: 1.4, ranker_cache: { hits: 3, misses: 3, cached_rows: 100, max_group_size: 12 }, timing: { train_s: 2.3 } }],
          rank_matrix_cache: {
            summary: { entry_count: 1, total_rows: 100, total_hits: 1, latest_used_at: '2026-05-07T06:39:29Z' },
            latest_benchmarks: [{ run_id: 'rank_matrix_cache_hit', label_name: 'follow_net_return_60d', feature_count: 12, label_count: 1, total_rows: 100, rank_matrix_rows: 100, matrix_duration_s: 0.9, rank_matrix_build_s: 0.2, proxy_association_s: 0.4, gate_status: 'pass', max_abs_rank_ic_delta: 0.00008, rank_matrix_cache: { status: 'hit', table_name: 'mart_feature_rank_matrix_cache_abc' }, built_at: '2026-05-07T06:39:30Z' }],
            cache_entries: [{ table_name: 'mart_feature_rank_matrix_cache_abc', panel_table: 'fact_feature_panel', row_count: 100, rank_column_count: 13, hit_count: 1, build_duration_s: 5.0, last_used_at: '2026-05-07T06:39:29Z' }],
          },
          stability_context: { run_id: 'context_1', summaries: [], diagnostics: [] },
          stock_horizon_profile: {
            run_id: 'stock_horizon_1',
            baseline_label: 'follow_net_return_60d',
            profile_count: 20,
            best_count: 4,
            selection_count: 4,
            effect_count: 12,
            horizon_comparison: [{ label_name: 'follow_net_return_60d', horizon_days: 60, stock_count: 4, avg_return: 0.02, avg_compounded_return: 0.12, median_compounded_return: 0.10, avg_max_drawdown: -0.18, median_max_drawdown: -0.16, avg_win_rate: 0.56, avg_volatility: 0.20, avg_path_obs_count: 6, avg_horizon_score: 0.12, is_baseline: true }],
            horizon_distribution: [{ label_name: 'follow_net_return_90d', horizon_days: 90, stock_count: 2, avg_return: 0.03, avg_compounded_return: 0.18, median_compounded_return: 0.17, avg_max_drawdown: -0.12, median_max_drawdown: -0.11, avg_win_rate: 0.60, avg_volatility: 0.21, avg_horizon_score: 0.16, is_baseline: false }],
            selected_horizon_distribution: [{ selected_label: 'follow_net_return_90d', selected_horizon_days: 90, gate_status: 'selected', stock_count: 2, avg_confidence: 0.72, avg_score_advantage: 0.04, avg_return_advantage: 0.01, is_baseline: false }],
            horizon_selection: [{ stock_code: '000001', baseline_label: 'follow_net_return_60d', selected_label: 'follow_net_return_90d', selected_horizon_days: 90, selected_horizon_confidence: 0.72, score_advantage: 0.04, avg_return_advantage: 0.01, selected_max_drawdown: -0.12, baseline_max_drawdown: -0.18, gate_status: 'selected', top_feature_effects: [{ feature_name: 'ma_ratio_250', corr: -0.41, effect_direction: 'negative' }] }],
            top_effects: [{ feature_name: 'ma_ratio_250', effect_direction: 'negative', stock_count: 2, avg_abs_corr: 0.4, avg_corr: -0.4, min_horizon_days: 60, max_horizon_days: 90 }],
            feature_effects_by_horizon: [{ label_name: 'follow_net_return_90d', horizon_days: 90, feature_name: 'ma_ratio_250', dominant_direction: 'negative', stock_count: 2, avg_abs_corr: 0.4, avg_corr: -0.4, positive_share: 0.1, avg_obs_count: 80 }],
            best_stocks: [{ stock_code: '000001', label_name: 'follow_net_return_90d', horizon_days: 90, horizon_score: 0.16, avg_return: 0.03, compounded_return: 0.18, max_drawdown: -0.12, win_rate: 0.6, volatility: 0.21, path_obs_count: 6, is_baseline: false }],
          },
          shareholder_plan_family_eval: {
            run_id: 'plan_family_1',
            summary: { panel_rows: 1000, row_count: 65, source_family_count: 2, feature_count: 7, label_count: 5, built_at: '2026-05-07T10:47:57' },
            family_summary: [{ source_family: 'initial_event', label_name: 'follow_net_return_90d', feature_count: 6, panel_rows: 1000, avg_nondefault_pct: 2.3, max_abs_rank_ic: 0.012, max_abs_spread: 0.095, positive_spread_share: 1 }],
            paired_advantages: [{ feature_name: 'shareholder_plan_decrease_count_180d', label_name: 'follow_net_return_90d', latest_rank_ic: 0.006, initial_rank_ic: 0.012, latest_spread: 0.06, initial_spread: 0.09, abs_spread_advantage: 0.03, latest_nondefault_pct: 8, initial_nondefault_pct: 10 }],
            top_effects: [{ source_family: 'initial_event', source_table: 'mart_shareholder_plan_initial_event', feature_name: 'shareholder_plan_decrease_count_180d', feature_purpose: 'initial_notice_capital_attention_candidate', label_name: 'follow_net_return_90d', window_days: 180, valid_rows: 980, nondefault_pct: 10, event_rows: 8163, distinct_event_stocks: 2500, ic: 0.04, rank_ic: 0.012, daily_rank_ic_count: 120, positive_rank_ic_share: 0.62, label_mean_when_active: 0.15, label_mean_when_inactive: 0.06, active_inactive_label_spread: 0.09, built_at: '2026-05-07T10:47:57' }],
          },
          shareholder_plan_initial_feature_panel: {
            run_id: 'sp_initial_panel_1',
            quality: { run_id: 'sp_initial_panel_1', feature_set_id: 'sp_initial_set', panel_rows: 3561243, stock_count: 5143, date_count: 715, min_date: '2023-01-03', max_date: '2025-12-15', initial_event_rows: 8163, matched_event_rows: 6224, active_rows: 257653, active_pct: 7.23, dropped_incomplete_label_rows: 466580, dropped_incomplete_context_rows: 25152, calendar_mismatch_rows: 0, labels: ['follow_net_return_60d', 'follow_net_return_90d'], context_features: ['ret_20d_rank'], initial_features: ['sp_initial_decrease_count_180d'], stage_timings: { total_s: 13.741 } },
          },
          shareholder_plan_family_walkforward: {
            run_id: 'plan_wf_1',
            summary: { row_count: 2, source_family_count: 2, feature_count: 1, label_count: 1, fold_count: 4, max_valid_fold_count: 2, built_at: '2026-05-07T11:11:01', gate_status_counts: { blocked: 2 } },
            gate_summary: [{ source_family: 'initial_event', label_name: 'follow_net_return_60d', gate_status: 'blocked', feature_count: 1, max_valid_fold_count: 2, max_signal_rank_ic: 0.012, max_long_short_spread: 0.19, worst_drawdown: -0.07, avg_active_pct: 0.012 }],
            paired_rows: [{ feature_name: 'shareholder_plan_decrease_count_180d', label_name: 'follow_net_return_60d', latest_gate_status: 'blocked', initial_gate_status: 'blocked', latest_signal_rank_ic: 0.005, initial_signal_rank_ic: 0.012, latest_long_short_spread: 0.05, initial_long_short_spread: 0.19, long_short_advantage: 0.14, latest_valid_fold_count: 1, initial_valid_fold_count: 2 }],
            top_rows: [{ source_family: 'initial_event', source_table: 'mart_shareholder_plan_initial_event', feature_name: 'shareholder_plan_decrease_count_180d', feature_purpose: 'initial_notice_capital_attention_candidate', label_name: 'follow_net_return_60d', window_days: 180, fold_count: 4, valid_fold_count: 2, gate_status: 'blocked', avg_signal_adjusted_holdout_rank_ic: 0.012, avg_holdout_long_short_spread: 0.19, positive_long_short_fold_share: 1, worst_holdout_long_short_max_drawdown: -0.07, avg_holdout_active_pct: 0.012, min_holdout_active_rows: 29, blockers: ['insufficient_valid_walkforward_folds'], cautions: ['sparse_activation_requires_auxiliary_or_context_use'], built_at: '2026-05-07T11:11:01' }],
          },
          temporal_synergy: {
            run_id: 'temporal_1',
            quality: { run_id: 'temporal_1', panel_rows: 100, stock_count: 20, feature_count: 3, label_count: 2, dropped_future_source_rows: 1, min_signal_date: '2026-01-01', max_signal_date: '2026-01-20', source_date_filter_applied: true, source_available_date_column: 'source_available_date' },
            label_summary: [{ label_name: 'forward_ret_20d', feature_count: 3, avg_coverage_pct: 99, max_abs_rank_ic: 0.08, max_directional_spread: 0.012 }],
            top_relevance: [{ label_name: 'forward_ret_20d', horizon_days: 20, feature_name: 'signal_a', rank_ic: 0.08, directional_spread: 0.012, long_short_spread: 0.012, stability_score: 0.07, coverage_pct: 99, daily_count: 20 }],
            top_synergies: [{ label_name: 'forward_ret_20d', horizon_days: 20, feature_a: 'signal_a', feature_b: 'signal_b', joint_uplift: 0.012, interaction_score: 0.42, joint_obs_count: 30, feature_corr: 0.22, joint_active_label_mean: 0.061, best_standalone_label_mean: 0.049 }],
            selected_interactions: [{ label_name: 'forward_ret_20d', horizon_days: 20, feature_a: 'signal_a', feature_b: 'signal_b', selected: true, selection_reason: 'joint_effect_exceeds_standalone', joint_uplift: 0.012, interaction_score: 0.42, joint_obs_count: 30 }],
            optuna_studies: [{ run_id: 'optuna_temporal', label_name: 'forward_ret_20d', best_trial_number: 3, objective_score: 1.25, trials: 8, study_total_trials: 8, selected_features: ['signal_a', 'signal_b'], selected_interactions: [{ feature_a: 'signal_a', feature_b: 'signal_b' }], best_metrics: { feature_component: 0.8, interaction_component: 0.45 } }],
            policy_candidates: [{ run_id: 'optuna_temporal', label_name: 'forward_ret_20d', gate_status: 'research_only', objective_score: 1.25, selected_count: 2, selected_interaction_count: 1, built_at: '2026-05-06T08:10:00' }],
            policy_gates: [{ run_id: 'synergy_wf_temporal', candidate_run_id: 'optuna_temporal', label_name: 'forward_ret_20d', baseline_horizon_days: 60, candidate_horizon_days: 20, validation_status: 'blocked', promotion_status: 'research_only', production_eligible: false, gate_mode: 'strict_fold', avg_rank_ic: 0.07, std_rank_ic: 0.02, avg_top_excess_return: 0.01, worst_top_excess_return: -0.004, avg_cost_adjusted_top_excess_return: 0.008, worst_cost_adjusted_top_excess_return: -0.006, avg_turnover: 0.35, transaction_cost_bps: 10, avg_top_hit_rate: 0.61, worst_max_drawdown: -0.31, blockers: ['excessive_topk_drawdown'] }],
            policy_mtm_gates: [{ run_id: 'mtm_temporal', candidate_run_id: 'optuna_temporal', label_name: 'forward_ret_20d', baseline_horizon_days: 60, candidate_horizon_days: 20, validation_status: 'blocked', promotion_status: 'research_only', production_eligible: false, position_count: 22860, date_count: 775, total_return: 0.52, annualized_return: 0.15, max_drawdown: -0.39, sharpe: 0.65, avg_active_positions: 1799.4, position_hit_rate: 0.47, non_tdxhub_kline_count: 0, missing_path_price_count: 0, forward_filled_path_price_count: 1521, blockers: ['excessive_mark_to_market_drawdown'] }],
            redundancy_clusters: [{ run_id: 'redundancy_temporal', cluster_id: 'cluster_001', representative_feature: 'signal_a', cluster_size: 2, members: 'signal_a, signal_b', max_abs_corr_in_cluster: 0.98 }],
            conditional_synergies: [{ label_name: 'forward_ret_20d', horizon_days: 20, condition_feature: 'signal_a', response_feature: 'signal_b', incremental_uplift: 0.011, conditional_response_uplift: 0.017, response_uplift: 0.006, interaction_score: 0.24, conditional_response_obs_count: 22, feature_corr: 0.19, selected: true, selection_reason: 'conditional_response_exceeds_unconditional' }],
          },
          industry_pit: {
            run_id: 'industry_pit_1',
            signal_table: 'mart_shareholder_plan_initial_feature_panel',
            min_signal_date: '2023-01-03',
            max_signal_date: '2025-12-15',
            signal_row_count: 3561243,
            signal_stock_count: 5143,
            signal_date_count: 715,
            pit_row_count: 28039,
            pit_stock_count: 5609,
            history_snapshot_count: 5,
            history_min_snapshot_date: '2026-04-25',
            history_max_snapshot_date: '2026-05-05',
            observed_pit_signal_rows: 0,
            fallback_signal_rows: 3561243,
            fallback_ratio: 1,
            missing_pit_rows: 0,
            pit_eligible: false,
            blockers: ['industry_current_label_fallback_in_signal_window'],
          },
          feature_drift: { top: [] },
        };
        const champion = {
          lifecycle: { counts: { champion: 1 }, champions: [{ model_id: 'champion_a', status: 'champion' }] },
          deployment: { status: 'deployed', blockers: [], latest_promotion_gate: { gate_run_id: 'gate_1' } },
          stability_context: { run_id: 'context_1', summaries: [], diagnostics: [] },
          challengers: [],
          latest_primary_topk: { snapshot_date: '2026-05-06', model_id: 'champion_a', count: 1, rows: [{ stock_code: '000001', stock_name: '平安银行', score: 0.8, rank_no: 1 }] },
          candidate_evaluations: [],
          promotion_gates: [],
          evidence_bundles: [],
        };
        const recommendations = {
          latest_primary: { snapshot_date: '2026-05-06', model_id: 'champion_a', count: 1 },
          source_quality: { kline_primary: 'tdxhub_quote', kline_primary_is_tdxhub: true, source_fallback_ratio: 0, feature_validation_id: 'feature_val_1' },
          outcomes: { count: 1, hit_rate_5d: 0.6, avg_ret_5d: 0.02 },
          risk: [{ kind: 'industry', status: 'ok', count: 1, reason: 'balanced' }],
          rows: [{ stock_code: '000001', stock_name: '平安银行', rank_no: 1, score: 0.8 }],
        };
        const storage = {
          latest_manifest: { latest_run_id: 'cleanup_1', latest_status: 'success' },
          retention: { candidate_count: 0, mode: 'dry-run', protected_model_count: 1, candidates: [] },
          architecture: { run_id: 'arch_1', classification_counts: { production: 10 }, cleanup_candidates: [] },
          architecture_cleanup: { run_id: 'cleanup_1', status_counts: { blocked: 1 }, candidates: [] },
        };
        const delivery = {
          ready_for_delivery: false,
          verdict: 'NOT_READY',
          avg_pct: 92.83,
          live_go_no_go: {
            pct: 80,
            ship_baseline_passed: true,
            perfect_ladder_ready: false,
            msaf_n_obs: 22,
            msaf_sharpe: 0.81,
            msaf_max_dd: -0.2428,
          },
          live_gate: {
            model_id: 'live_model',
            promote_action: 'block',
            pbo: { passes: true, reason: 'PBO=0.145' },
            dsr: { passes: true, reason: 'DSR p_conf=0.9825' },
            conservative: { passes: true, reason: 'pass' },
            is_oos: { passes: false, reason: 'proxy fail' },
          },
          challenger: {
            model_id: 'phase5_model',
            decision: { decision: 'hold_reject', production_status: 'candidate_hold_reject' },
            gate: {
              n_obs_20d: 34,
              n_obs_5d: 135,
              pbo: { passes: false, reason: 'PBO=0.626' },
            },
          },
          sources: {
            available: { institution: true },
            wired: { institution: false },
            institution_evaluation: { production_decision: 'hold_reject' },
          },
          blockers: [
            { scope: 'milestone', text: 'n_obs 22 < 30 for 85%' },
            { scope: 'challenger', text: 'pbo: PBO=0.626' },
          ],
          criteria: [{ criterion: '实盘 GO/NO-GO', pct: 80, verdict: 'PASS', msaf_n_obs: 22 }],
        };

        render('_renderOverview', overview, ['最新完成交易日', 'cron_daily', 'champion_a']);
        render('_renderDataSources', dataSources, ['K线主源', 'tdxhub_quote', '信号快照', '今日信号快照', '数据源水位', 'TDX K线服务器健康', 'TDX F10 Source-Date Audit', 'plan_window_used_as_source_date']);
        render('_renderPipelines', pipelines, ['最近运行', 'cron_daily', '最慢运行']);
        render('_renderFeatures', features, ['Registry', 'Feature Search Space', '漂移缓解候选', 'mitigation_1', 'ret_20d']);
        render('_renderDelivery', delivery, ['GO/NO-GO Delivery Board', 'NOT_READY', 'Rejected Challenger', 'PBO=0.626', 'Remaining Gaps']);
        render('_renderResearch', research, ['研究队列', 'Ranker 性能', 'Rank Matrix Cache', 'rank_matrix_cache_hit', 'ranker_perf', '个股持股周期画像', 'Top 变量影响', 'ma_ratio_250', '股东计划特征家族', 'plan_family_1', 'initial_event', 'shareholder_plan_decrease_count_180d', '行业 PIT 就绪度', 'industry_pit_1', '时序协同研究', 'signal_a']);
        render('_renderChampion', champion, ['Champion 阻塞上下文', 'deployed', 'champion_a']);
        render('_renderRecommendations', recommendations, ['Primary TopK', 'tdxhub_quote', '平安银行']);
        render('_renderStorage', storage, ['清理计划', '架构清理计划', 'cleanup_1']);

        ['_renderOverview', '_renderDataSources', '_renderPipelines', '_renderFeatures', '_renderDelivery',
         '_renderResearch', '_renderChampion', '_renderRecommendations', '_renderStorage'].forEach((name) => {
          elements['wb-tab-root'].innerHTML = '';
          window.WorkbenchView[name]({});
          const html = elements['wb-tab-root'].innerHTML;
          if (html.length < 40) throw new Error(name + ' empty-state rendered too little HTML');
          if (html.includes('undefined')) throw new Error(name + ' empty-state rendered undefined');
        });
        """
    )
    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/workbench-view.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_workbench_read_model_meta_is_pure_and_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.WorkbenchView;
        if (!view || typeof view.buildReadModelMeta !== 'function') {
          throw new Error('WorkbenchView.buildReadModelMeta missing');
        }

        const model = view.buildReadModelMeta({
          read_model: {
            endpoint: '/api/workbench/overview',
            source_mode: 'materialized_snapshot',
            recompute_on_read: true,
            latest_materialized_at: '2026-06-03 02:00:00',
            materialized_tables: [
              { available: true },
              { available: false },
              null,
            ],
          },
        });

        if (!model) throw new Error('expected model');
        if (model.endpoint !== '/api/workbench/overview') throw new Error('endpoint mismatch');
        if (model.sourceMode !== 'materialized_snapshot') throw new Error('sourceMode mismatch');
        if (model.recomputeLabel !== 'read recompute' || model.recomputeTone !== 'warn') throw new Error('recompute metadata mismatch');
        if (model.availableCount !== 1 || model.totalCount !== 3) throw new Error('materialized table counts mismatch');
        if (model.latestMaterializedAt !== '2026-06-03 02:00:00') throw new Error('latest materialized mismatch');
        if (view.buildReadModelMeta({ read_model: {} }) !== null) throw new Error('expected null for missing source mode');
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/workbench-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_workbench_delivery_model_is_pure_and_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.WorkbenchView;
        if (!view || typeof view.buildDeliveryModel !== 'function') {
          throw new Error('WorkbenchView.buildDeliveryModel missing');
        }

        const model = view.buildDeliveryModel({
          ready_for_delivery: true,
          verdict: 'READY',
          avg_pct: 88.4,
          live_go_no_go: { pct: 80, ship_baseline_passed: true, msaf_n_obs: 22, msaf_sharpe: 0.81, msaf_max_dd: -0.2428 },
          live_gate: {
            model_id: 'live_model',
            promote_action: 'block',
            pbo: { passes: true, reason: 'PBO=0.145' },
            dsr: { passes: true, reason: 'DSR p_conf=0.9825' },
            conservative: { passes: true, reason: 'pass' },
            is_oos: { passes: false, reason: 'proxy fail' },
          },
          challenger: {
            model_id: 'phase5_model',
            decision: { decision: 'hold_reject', production_status: 'candidate_hold_reject' },
            gate: { n_obs_20d: 34, n_obs_5d: 135, pbo: { passes: false, reason: 'PBO=0.626' } },
          },
          sources: {
            available: { institution: true },
            wired: { institution: false },
            institution_evaluation: { production_decision: 'hold_reject' },
          },
          blockers: [{ scope: 'milestone', text: 'n_obs 22 < 30 for 85%' }],
          criteria: [{ criterion: '实盘 GO/NO-GO', pct: 80, verdict: 'PASS' }],
        });

        if (!model || model.verdict !== 'READY' || model.readyForDelivery !== true) throw new Error('top-level model mismatch');
        if (model.avgPct !== 88.4) throw new Error('avgPct mismatch');
        if (model.liveGoNoGo.msaf_sharpe !== 0.81 || model.liveGate.promote_action !== 'block') throw new Error('live gate mismatch');
        if (model.challengerDecision.decision !== 'hold_reject' || model.challengerGate.n_obs_20d !== 34) throw new Error('challenger mismatch');
        if (model.sourceAvailable.institution !== true || model.sourceWired.institution !== false) throw new Error('source wiring mismatch');
        if (model.blockers.length !== 1 || model.criteria.length !== 1) throw new Error('list normalization mismatch');
        if (view.buildDeliveryModel({}) .readyForDelivery !== false) throw new Error('default normalization mismatch');
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/workbench-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_workbench_champion_model_is_pure_and_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.WorkbenchView;
        if (!view || typeof view.buildChampionModel !== 'function') {
          throw new Error('WorkbenchView.buildChampionModel missing');
        }

        const model = view.buildChampionModel({
          lifecycle: { champions: [{ model_id: 'champion_a' }], counts: { ready: 3, blocked: 1 } },
          challengers: [{ model_id: 'challenger_a' }, { model_id: 'challenger_b' }],
          candidate_evaluations: [{ evaluation_run_id: 'eval_1' }],
          latest_primary_topk: { count: 8, snapshot_date: '2026-06-03', model_id: 'champion_a', rows: [{ model_id: 'champion_a' }] },
          stability_context: { run_id: 'stability_1' },
          deployment: { status: 'deployed', latest_promotion_gate: { gate_run_id: 'gate_1' }, blockers: [] },
        });

        if (!model || model.firstChampion !== 'champion_a') throw new Error('firstChampion mismatch');
        if (model.challengerCount !== 2 || model.evaluationCount !== 1) throw new Error('count mismatch');
        if (!model.lifecycle || !model.champions.length || model.topk.count !== 8) throw new Error('model passthrough mismatch');
        if (model.stabilityContext.run_id !== 'stability_1' || model.deployment.status !== 'deployed') throw new Error('context mismatch');
        if (view.buildChampionModel({}).firstChampion !== '-') throw new Error('default normalization mismatch');
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/workbench-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_workbench_data_sources_model_is_pure_and_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.WorkbenchView;
        if (!view || typeof view.buildDataSourcesModel !== 'function') {
          throw new Error('WorkbenchView.buildDataSourcesModel missing');
        }

        const model = view.buildDataSourcesModel({
          calendar_target: '2026-06-03',
          blockers: ['akshare down'],
          watermarks: [{ source_name: 'tdxhub', watermark_date: '2026-06-03' }],
          watermark_count: 1,
          kline: {
            primary_is_tdxhub: true,
            fallback_active_count: 2,
            primary: { source_name: 'tdxhub', source_tier: 1, row_count: 9, last_data_date: '2026-06-03' },
          },
          latest_feature_validation: {
            validation_id: 'v1',
            status: 'pass',
            validated_at: '2026-06-03 02:00:00',
            source_fallback_ratio: 0.125,
            source_distribution: [{ source_name: 'tdxhub', row_count: 10 }],
          },
          processing_monitor: {
            total_rejected_rows: 3,
            run_count: 5,
            recent_runs: [{ tool_name: 'dq', run_id: 'run1' }],
            reason_counts: [{ reason: 'bad row', count: 3 }],
          },
          today_signal_cache: { status: 'fresh', signal_count: 12, requires_refresh: false, stale: false },
          asset_health: {
            summary: { total: 4 },
            items: [{ table_name: 'fact_x', severity: 'warning', frontend_visibility: 'governance_visible' }],
            governance_counts: {
              quality_gate_level: { blocking: 1, warning: 2 },
              coverage_policy: {},
              null_policy: {},
              model_eligibility: {},
            },
          },
          tdx_server_health: { updated_at: '2026-06-03', summary: { healthy_count: 7, timeout_server_count: 1 } },
          tdx_f10_capabilities: [{ capability: 'x' }],
          f10_source_date_audit: { run_id: 'audit_1' },
          tdx_f10_source_dq: { run_id: 'dq_1' },
        });

        if (!model || model.signalCacheTone !== 'fresh') throw new Error('signalCacheTone mismatch');
        if (!model.kline.primary_is_tdxhub || model.primary.source_name !== 'tdxhub') throw new Error('kline normalization mismatch');
        if (model.qualityCounts.blocking !== 1 || model.tdxHealthSummary.timeout_server_count !== 1) throw new Error('counts mismatch');
        if (model.blockers.length !== 1 || model.watermarks.length !== 1 || model.tdxF10Capabilities.length !== 1) throw new Error('list normalization mismatch');
        if (view.buildDataSourcesModel({}).signalCacheTone !== 'unknown') throw new Error('default normalization mismatch');
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/workbench-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_workbench_features_model_is_pure_and_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.WorkbenchView;
        if (!view || typeof view.buildFeaturesModel !== 'function') {
          throw new Error('WorkbenchView.buildFeaturesModel missing');
        }

        const model = view.buildFeaturesModel({
          registry: { feature_count: 3, model_input_count: 2, label_count: 1, group_counts: { alpha: 2 } },
          latest_validation: { validation_id: 'v1', status: 'pass', rows: 7, source_lineage_coverage: 0.8, source_fallback_ratio: 0.25, validated_at: '2026-06-03' },
          availability_contract: { source: 'registry', rows: [{ feature_name: 'ret_20d' }], role_counts: { model: 1 } },
          feature_catalog: { summary: { total_features: 9, allowed_features: 7, critical_features: 1 } },
          search_spaces: [{ feature_name: 'ret_20d' }],
          pit_coverage: [{ feature_name: 'ret_20d' }],
          drift_mitigation_builds: [{ run_id: 'mit_1' }],
          top_associations: [{ feature_name: 'ret_20d' }],
          feature_drift: { run_id: 'drift_1' },
        });

        if (!model || model.registry.feature_count !== 3 || model.validation.rows !== 7) throw new Error('base model mismatch');
        if (model.availability.rows.length !== 1 || model.catalogSummary.total_features !== 9) throw new Error('nested normalization mismatch');
        if (model.searchSpaces.length !== 1 || model.pitCoverage.length !== 1 || model.driftMitigationBuilds.length !== 1) throw new Error('list normalization mismatch');
        if (model.featureDrift.run_id !== 'drift_1' || model.availability.source !== 'registry') throw new Error('passthrough mismatch');
        if (view.buildFeaturesModel({}).availability.rows.length !== 0) throw new Error('default normalization mismatch');
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/workbench-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_workbench_temporal_synergy_model_is_pure_and_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.WorkbenchView;
        if (!view || typeof view.buildTemporalSynergyModel !== 'function') {
          throw new Error('WorkbenchView.buildTemporalSynergyModel missing');
        }

        const model = view.buildTemporalSynergyModel({
          quality: { run_id: 'temp_1', panel_rows: 12, stock_count: 3, feature_count: 4, label_count: 2, min_signal_date: '2026-01-01', max_signal_date: '2026-06-03' },
          label_summary: [{ label_name: 'ret_20d' }],
          top_relevance: [{ feature_name: 'x' }],
          top_synergies: [{ feature_name: 'y' }],
          selected_interactions: [{ feature_name: 'z' }],
          optuna_studies: [{ run_id: 'opt_1' }],
          policy_candidates: [{ run_id: 'p_1' }],
          policy_gates: [{ gate_run_id: 'g_1' }],
          policy_mtm_gates: [{ gate_run_id: 'mg_1' }],
          policy_mtm_strategy_sweeps: [{ sweep_id: 's_1' }],
          redundancy_clusters: [{ cluster_id: 'c_1' }],
          conditional_synergies: [{ condition_name: 'cond_1' }],
        });

        if (!model || model.quality.run_id !== 'temp_1') throw new Error('quality mismatch');
        if (model.labels.length !== 1 || model.relevance.length !== 1 || model.synergies.length !== 1) throw new Error('list normalization mismatch');
        if (model.optuna.length !== 1 || model.policies.length !== 1 || model.gates.length !== 1) throw new Error('secondary list normalization mismatch');
        if (model.mtmGates.length !== 1 || model.strategySweeps.length !== 1 || model.clusters.length !== 1 || model.conditional.length !== 1) throw new Error('tertiary list normalization mismatch');
        if (view.buildTemporalSynergyModel({}).isEmpty !== true) throw new Error('default normalization mismatch');
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/workbench-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_workbench_rank_matrix_cache_model_is_pure_and_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.WorkbenchView;
        if (!view || typeof view.buildRankMatrixCacheModel !== 'function') {
          throw new Error('WorkbenchView.buildRankMatrixCacheModel missing');
        }

        const model = view.buildRankMatrixCacheModel({
          summary: { entry_count: 2, total_rows: 12, total_hits: 7, latest_used_at: '2026-06-03' },
          latest_benchmarks: [{ run_id: 'rank_1', rank_matrix_cache: { status: 'ready', table_name: 'fact_rank_matrix' } }],
          cache_entries: [{ table_name: 'rank_cache_1', panel_table: 'panel_1', feature_set_id: 'fs_1' }],
        });

        if (!model || model.summary.entry_count !== 2) throw new Error('summary mismatch');
        if (model.latestBenchmarks.length !== 1 || model.cacheEntries.length !== 1) throw new Error('list normalization mismatch');
        if (model.isEmpty !== false) throw new Error('empty flag mismatch');
        if (view.buildRankMatrixCacheModel({}).isEmpty !== true) throw new Error('default normalization mismatch');
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/workbench-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_workbench_overview_model_is_pure_and_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.WorkbenchView;
        if (!view || typeof view.buildOverviewModel !== 'function') {
          throw new Error('WorkbenchView.buildOverviewModel missing');
        }

        const model = view.buildOverviewModel({
          latest_trading_day: '2026-06-03',
          latest_manifest: { pipeline_name: 'cron_daily', run_id: 'run_1', status: 'done', duration_s: 32, gate_result: 'pass' },
          schema_drift_count: 2,
          champion: { counts: { champion: 1 }, champions: [{ model_id: 'champion_a' }] },
          storage: { latest_run_id: 'cleanup_1', started_at: '2026-06-03 01:00:00', latest_status: 'done' },
          research_schedule: { run_id: 'research_1', status_counts: { ready: 2 } },
          blockers: [{ scope: 'schema', text: 'drift' }],
          feature_drift: { run_id: 'drift_1' },
        });

        if (!model || model.latestTradingDay !== '2026-06-03') throw new Error('latestTradingDay mismatch');
        if (model.schemaDriftCount !== 2 || model.championId !== 'champion_a') throw new Error('summary mismatch');
        if (model.blockers.length !== 1 || model.research.run_id !== 'research_1') throw new Error('nested normalization mismatch');
        if (model.storage.latest_run_id !== 'cleanup_1' || model.featureDrift.run_id !== 'drift_1') throw new Error('passthrough mismatch');
        if (view.buildOverviewModel({}).championId !== '-') throw new Error('default normalization mismatch');
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/workbench-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_workbench_recommendations_model_is_pure_and_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.WorkbenchView;
        if (!view || typeof view.buildRecommendationsModel !== 'function') {
          throw new Error('WorkbenchView.buildRecommendationsModel missing');
        }

        const model = view.buildRecommendationsModel({
          latest_primary: { count: 8, snapshot_date: '2026-06-03', model_id: 'topk_1' },
          source_quality: { kline_primary: 'tdxhub', kline_primary_is_tdxhub: true, source_fallback_ratio: 0.2, feature_validation_id: 'v1', source_lineage_coverage: 0.7, feature_validation_status: 'pass' },
          outcomes: { count: 3, latest_outcome_known_at: '2026-06-03', hit_rate_5d: 0.55, avg_ret_5d: 0.12 },
          risk: [{ track_id: 'risk_1', is_primary: true }],
          rows: [{ stock_code: '600519', rank_in_date: 1 }],
        });

        if (!model || model.latestPrimary.count !== 8 || model.sourceQuality.kline_primary !== 'tdxhub') throw new Error('top-level mismatch');
        if (model.outcomes.count !== 3 || model.rows.length !== 1 || model.risk.length !== 1) throw new Error('list normalization mismatch');
        if (model.isEmpty !== false) throw new Error('empty flag mismatch');
        if (view.buildRecommendationsModel({}).isEmpty !== true) throw new Error('default normalization mismatch');
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/workbench-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
