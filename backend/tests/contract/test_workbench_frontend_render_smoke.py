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
            policy_gates: [{ run_id: 'synergy_wf_temporal', candidate_run_id: 'optuna_temporal', label_name: 'forward_ret_20d', baseline_horizon_days: 60, candidate_horizon_days: 20, validation_status: 'blocked', promotion_status: 'research_only', avg_rank_ic: 0.07, std_rank_ic: 0.02, avg_top_excess_return: 0.01, worst_top_excess_return: -0.004, avg_cost_adjusted_top_excess_return: 0.008, worst_cost_adjusted_top_excess_return: -0.006, avg_turnover: 0.35, transaction_cost_bps: 10, avg_top_hit_rate: 0.61, worst_max_drawdown: -0.31, blockers: ['excessive_topk_drawdown'] }],
            redundancy_clusters: [{ run_id: 'redundancy_temporal', cluster_id: 'cluster_001', representative_feature: 'signal_a', cluster_size: 2, members: 'signal_a, signal_b', max_abs_corr_in_cluster: 0.98 }],
            conditional_synergies: [{ label_name: 'forward_ret_20d', horizon_days: 20, condition_feature: 'signal_a', response_feature: 'signal_b', incremental_uplift: 0.011, conditional_response_uplift: 0.017, response_uplift: 0.006, interaction_score: 0.24, conditional_response_obs_count: 22, feature_corr: 0.19, selected: true, selection_reason: 'conditional_response_exceeds_unconditional' }],
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

        render('_renderOverview', overview, ['最新完成交易日', 'cron_daily', 'champion_a']);
        render('_renderDataSources', dataSources, ['K线主源', 'tdxhub_quote', '信号快照', '今日信号快照', '数据源水位', 'TDX K线服务器健康', 'TDX F10 Source-Date Audit', 'plan_window_used_as_source_date']);
        render('_renderPipelines', pipelines, ['最近运行', 'cron_daily', '最慢运行']);
        render('_renderFeatures', features, ['Registry', 'Feature Search Space', '漂移缓解候选', 'mitigation_1', 'ret_20d']);
        render('_renderResearch', research, ['研究队列', 'Ranker 性能', 'Rank Matrix Cache', 'rank_matrix_cache_hit', 'ranker_perf', '个股持股周期画像', 'Top 变量影响', 'ma_ratio_250', '股东计划特征家族', 'plan_family_1', 'initial_event', 'shareholder_plan_decrease_count_180d', '时序协同研究', 'signal_a']);
        render('_renderChampion', champion, ['Champion 阻塞上下文', 'deployed', 'champion_a']);
        render('_renderRecommendations', recommendations, ['Primary TopK', 'tdxhub_quote', '平安银行']);
        render('_renderStorage', storage, ['清理计划', '架构清理计划', 'cleanup_1']);

        ['_renderOverview', '_renderDataSources', '_renderPipelines', '_renderFeatures',
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
