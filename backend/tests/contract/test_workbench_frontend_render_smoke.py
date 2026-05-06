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
          watermarks: [{ data_domain: 'kline', source_name: 'tdxhub_quote', source_tier: 1, row_count: 10 }],
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
          stability_context: { run_id: 'context_1', summaries: [], diagnostics: [] },
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
        render('_renderDataSources', dataSources, ['K线主源', 'tdxhub_quote', '数据源水位']);
        render('_renderPipelines', pipelines, ['最近运行', 'cron_daily', '最慢运行']);
        render('_renderFeatures', features, ['Registry', 'Feature Search Space', '漂移缓解候选', 'mitigation_1', 'ret_20d']);
        render('_renderResearch', research, ['研究队列', 'Ranker 性能', 'ranker_perf']);
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
