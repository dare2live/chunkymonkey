from __future__ import annotations

from pathlib import Path
import subprocess
import textwrap

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_data_view_build_audit_results_model_is_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.CMDataView;
        if (!view || typeof view.buildAuditResultsModel !== 'function') {
          throw new Error('CMDataView.buildAuditResultsModel missing');
        }

        const model = view.buildAuditResultsModel({
          details: [
            { table: 't1', issues: [{ level: 'error', msg: 'boom' }] },
            { table: 't2', issues: [] },
            null,
            { table: 't3' },
          ],
          n_error: '2',
          n_warn: 1,
          n_ok: 3,
          n_tables: 4,
          run_at: '2026-06-03T01:00:00',
        });

        if (model.issues.length !== 1) throw new Error('unexpected issue count: ' + model.issues.length);
        if (model.okRows.length !== 2) throw new Error('unexpected ok count: ' + model.okRows.length);
        if (model.okRows[0].table !== 't2' || model.okRows[1].table !== 't3') throw new Error('ok row order changed');
        if (model.nError !== 2 || model.nWarn !== 1 || model.nOk !== 3 || model.nTables !== 4) {
          throw new Error('summary counters not normalized');
        }
        if (model.runAt !== '2026-06-03T01:00:00') throw new Error('runAt mismatch: ' + model.runAt);
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/data-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_data_view_build_routes_table_model_is_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.CMDataView;
        if (!view || typeof view.buildRoutesTableModel !== 'function') {
          throw new Error('CMDataView.buildRoutesTableModel missing');
        }

        const model = view.buildRoutesTableModel(
          [
            null,
            {
              data_name: 'data_a',
              raw_table: 'fact_a',
              step_id: 'sync_a',
              notes: 'alpha route',
              current: { source: 'tdxhub', protocol: 'duckdb', status: 'connected' },
              target: { source: 'miaoxiang', phase: 'fallback' },
            },
            {
              data_name: 'data_b',
              raw_table: 'fact_b',
              notes: 'beta route',
              current: { source: 'akshare', protocol: 'http', status: 'pending' },
            },
          ],
          'alpha',
          [
            { table_name: 'fact_a', severity: 'yellow', issue_summary: 'stale', freshness_hours: 12.3 },
            { table_name: 'fact_b', severity: 'red', issue_summary: 'missing', freshness_hours: 8.1 },
          ]
        );

        if (!model || !Array.isArray(model.list)) throw new Error('model list missing');
        if (model.list.length !== 1) throw new Error('filtered route count mismatch: ' + model.list.length);
        const row = model.list[0];
        if (row.route.data_name !== 'data_a') throw new Error('wrong filtered route');
        if (row.cur.source !== 'tdxhub' || row.tgt.source !== 'miaoxiang') throw new Error('route endpoints mismatch');
        if (row.health.label !== 'YELLOW' || row.health.tone !== 'warn') throw new Error('health metadata mismatch');
        if (row.fallback.label !== '迁移/兜底可用' || row.fallback.tone !== 'warn') throw new Error('fallback metadata mismatch');
        if (row.freshness !== '12.3h') throw new Error('freshness mismatch: ' + row.freshness);
        if (row.repairLabel !== '运行 step') throw new Error('repair label mismatch: ' + row.repairLabel);

        const byProtocol = view.buildRoutesTableModel(
          [
            {
              data_name: 'data_a',
              raw_table: 'fact_a',
              step_id: 'sync_a',
              notes: 'alpha route',
              current: { source: 'tdxhub', protocol: 'duckdb', status: 'connected' },
            },
          ],
          'duckdb',
          []
        );
        if (byProtocol.list.length !== 1 || byProtocol.list[0].route.raw_table !== 'fact_a') {
          throw new Error('protocol filter mismatch: ' + JSON.stringify(byProtocol.list));
        }

        const byRawTable = view.buildRoutesTableModel(
          [
            {
              data_name: 'data_a',
              raw_table: 'fact_a',
              step_id: 'sync_a',
              notes: 'alpha route',
              current: { source: 'tdxhub', protocol: 'duckdb', status: 'connected' },
            },
          ],
          'fact_a',
          []
        );
        if (byRawTable.list.length !== 1 || byRawTable.list[0].route.step_id !== 'sync_a') {
          throw new Error('raw_table filter mismatch: ' + JSON.stringify(byRawTable.list));
        }
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/data-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_data_view_build_source_cards_model_is_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.CMDataView;
        if (!view || typeof view.buildSourceCardsModel !== 'function') {
          throw new Error('CMDataView.buildSourceCardsModel missing');
        }

        const model = view.buildSourceCardsModel([
          {
            name: 'tdxhub',
            display_name: 'TDXHub',
            priority: 1,
            repo_url: 'https://example.com/tdxhub',
            capabilities: [
              null,
              { name: 'kline_daily', freshness: 'daily', description: 'K线' },
              { name: 'financial_gpcw_8q', freshness: 'quarterly', description: '财务' },
            ],
            telemetry: { call_count: 2, fail_count: 1, avg_latency_ms: 123.4 },
            health: { state: 'ok', notes: 'healthy' },
          },
          {
            name: 'akshare',
            priority: 9,
            capabilities: [],
            telemetry: {},
            health: { state: 'down' },
          },
        ]);

        if (!Array.isArray(model) || model.length !== 2) throw new Error('source model length mismatch');
        const first = model[0];
        if (first.name !== 'tdxhub' || first.displayName !== 'TDXHub') throw new Error('display fields mismatch');
        if (first.tone !== 'ok' || first.color !== '#0a0' || first.stateLabel !== 'OK') throw new Error('healthy source meta mismatch');
        if (first.teleLine !== '2 次调用 / 1 失败 · 123.4ms 均延') throw new Error('teleLine mismatch: ' + first.teleLine);
        if (first.capabilityCount !== 2) throw new Error('capability count mismatch: ' + first.capabilityCount);
        if (!first.hasRepoLink) throw new Error('repo link flag missing');
        if (!first.detailRowsHtml.includes('kline_daily') || !first.detailRowsHtml.includes('financial_gpcw_8q')) {
          throw new Error('detail rows html mismatch: ' + first.detailRowsHtml);
        }
        const second = model[1];
        if (second.tone !== 'bad' || second.color !== '#d33' || second.stateLabel !== 'DOWN') throw new Error('down source meta mismatch');
        if (second.teleLine !== '尚未通过 registry 调用') throw new Error('fallback teleLine mismatch');
        if (second.capabilityCount !== 0) throw new Error('capability count mismatch');
        if (!second.detailRowsHtml.includes('暂无')) throw new Error('empty detail rows fallback mismatch: ' + second.detailRowsHtml);
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/data-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_data_view_build_cockpit_models_are_stable():
    script = textwrap.dedent(
        r"""
        const fs = require('fs');
        const vm = require('vm');
        const file = process.argv[1];
        global.window = global;
        global.document = { getElementById: () => null };
        vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });

        const view = globalThis.CMDataView;
        for (const name of [
          'buildHealthHeatmapModel',
          'buildSourcePriorityModel',
          'buildFallbackPanelModel',
          'buildDriftQueueModel',
          'buildCapabilityTableModel',
        ]) {
          if (!view || typeof view[name] !== 'function') {
            throw new Error('CMDataView.' + name + ' missing');
          }
        }

        const heatmap = view.buildHealthHeatmapModel({
          summary: { green: 2, yellow: 1, red: 0, unknown: 3 },
          by_layer: {
            fact: { green: 1, yellow: 0, red: 0, unknown: 1 },
            mart: { green: 1, yellow: 1, red: 0, unknown: 2 },
          },
        });
        if (heatmap.total !== 6) throw new Error('heatmap total mismatch: ' + heatmap.total);
        if (heatmap.layers.length !== 2 || heatmap.layers[0].layer !== 'fact' || heatmap.layers[1].layer !== 'mart') {
          throw new Error('heatmap layers mismatch: ' + JSON.stringify(heatmap.layers));
        }
        if (heatmap.barSegments[0].label !== 'green 2') throw new Error('heatmap bar mismatch: ' + heatmap.barSegments[0].label);

        const sourcePriority = view.buildSourcePriorityModel({
          sources: [
            { upstream_source: 'aif10', green_count: 1, yellow_count: 2, red_count: 3, asset_count: 7 },
          ],
        });
        if (sourcePriority.rows.length !== 1 || sourcePriority.rows[0].source !== 'aif10' || sourcePriority.rows[0].total !== 7) {
          throw new Error('source priority model mismatch: ' + JSON.stringify(sourcePriority.rows));
        }

        const fallback = view.buildFallbackPanelModel(
          {
            fallback_active: [
              null,
              {
                data_name: 'fund_flow',
                current: { source: 'akshare' },
                target: { source: 'aif10' },
              },
            ],
            source_tier_distribution: { '1': 3, '2': 5 },
          },
          {},
          [
            null,
            { current: { source: 'tdxhub', status: 'connected' }, target: null },
            { current: { source: 'akshare', status: 'pending' }, target: { source: 'aif10' } },
          ]
        );
        if (fallback.activeCount !== 1) throw new Error('fallback active count mismatch: ' + fallback.activeCount);
        if (fallback.tierEntries.length !== 2 || fallback.tierMax !== 5) {
          throw new Error('fallback tier mismatch: ' + JSON.stringify({ entries: fallback.tierEntries, tierMax: fallback.tierMax }));
        }
        if (fallback.transitionRows.length !== 1 || fallback.transitionRows[0].dataName !== 'fund_flow' || fallback.transitionRows[0].target !== 'aif10') {
          throw new Error('fallback transition mismatch: ' + JSON.stringify(fallback.transitionRows));
        }

        const drift = view.buildDriftQueueModel({
          summary: { drift_count: 1 },
          versions: [
            null,
            { table_name: 't1', drift: true },
            { table_name: 't2', drift: false },
          ],
        });
        if (drift.driftCount !== 1 || drift.driftRows.length !== 1 || drift.driftRows[0].table_name !== 't1') {
          throw new Error('drift model mismatch: ' + JSON.stringify(drift));
        }

        const caps = view.buildCapabilityTableModel([
          null,
          {
            capability: 'individual_fund_flow',
            freshness: 'daily',
            primary_source: 'akshare',
            fallback_chain: ['aif10'],
            description: '资金流',
          },
          {
            capability: 'lhb_daily',
            freshness: 'daily',
            primary_source: 'aif10',
            fallback_chain: ['akshare'],
            description: '龙虎榜',
          },
        ], 'fund_flow');
        if (caps.list.length !== 1 || caps.list[0].capability !== 'individual_fund_flow') {
          throw new Error('capability filter mismatch: ' + JSON.stringify(caps.list));
        }
        if (caps.list[0].fallbackChainText !== 'aif10') {
          throw new Error('capability fallback chain mismatch: ' + caps.list[0].fallbackChainText);
        }

        const link = view.buildLinkOverviewModel(
          {
            snapshot_at: '2026-06-03 04:00',
            summary: { green: 2, yellow: 1, red: 0, unknown: 0 },
            by_layer: { fact: { green: 1, yellow: 0, red: 0, unknown: 0 } },
          },
          {
            manual: [
              { decision: 'keep' },
              { decision: 'watch' },
              { decision: 'drop' },
              null,
              { decision: 'ignore' },
            ],
            pit: { tdx_f10_gpcw_v1: { violation_rows: 0 } },
          },
          {
            sources: [
              { upstream_source: 'tdxhub', green_count: 3, asset_count: 5 },
              { upstream_source: 'akshare', green_count: 1, asset_count: 2 },
            ],
          }
        );
        if (link.snapshotAt !== '2026-06-03 04:00') throw new Error('link snapshot mismatch: ' + link.snapshotAt);
        if (link.keep !== 1 || link.watch !== 1 || link.drop !== 1 || link.pit !== 0) {
          throw new Error('link counters mismatch: ' + JSON.stringify(link));
        }
        if (link.sourceLabel !== 'tdxhub:3/5 · akshare:1/2') throw new Error('link source label mismatch: ' + link.sourceLabel);
        if (link.nodes[1][2] !== 'warn' || link.nodes[2][2] !== 'ok' || link.nodes[3][2] !== 'warn') {
          throw new Error('link nodes mismatch: ' + JSON.stringify(link.nodes));
        }
        """
    ).strip()

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/data-view.js")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
