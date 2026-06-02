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
