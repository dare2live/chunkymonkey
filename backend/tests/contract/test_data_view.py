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
        if (row.health.label !== 'YELLOW' || row.health.tone !== 'warn') throw new Error('health metadata mismatch');
        if (row.fallback.label !== '迁移/兜底可用' || row.fallback.tone !== 'warn') throw new Error('fallback metadata mismatch');
        if (row.freshness !== '12.3h') throw new Error('freshness mismatch: ' + row.freshness);
        if (row.repairLabel !== '运行 step') throw new Error('repair label mismatch: ' + row.repairLabel);
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
