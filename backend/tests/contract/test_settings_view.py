from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_settings_view_builds_schema_versions_model_in_one_pass() -> None:
    script = r"""
require(process.argv[1]);
const view = globalThis.SettingsView;
if (!view || typeof view.buildSchemaVersionsModel !== 'function') {
  throw new Error('SettingsView exports missing');
}
const model = view.buildSchemaVersionsModel({
  summary: { total: 3, n_views: 1, drift_count: 1 },
  versions: [
    { table_name: 'fact_a', layer: 'fact', drift: false },
    { table_name: 'mart_b', layer: 'mart', drift: true },
    { table_name: 'dim_c', layer: 'dim_derived', drift: false },
  ],
});
if (model.total !== 3 || model.nViews !== 1 || model.driftCount !== 1) {
  throw new Error('summary mismatch: ' + JSON.stringify(model));
}
if (model.byLayer.fact !== 1 || model.byLayer.mart !== 1 || model.byLayer.dim_derived !== 1) {
  throw new Error('byLayer mismatch: ' + JSON.stringify(model.byLayer));
}
if ('summary' in model) {
  throw new Error('summary should not be exposed on the schema versions model');
}
if (model.driftRows.length !== 1 || model.okRows.length !== 2) {
  throw new Error('row partition mismatch: ' + JSON.stringify({ driftRows: model.driftRows.length, okRows: model.okRows.length }));
}
if (model.driftRows[0].table_name !== 'mart_b') {
  throw new Error('drift row order mismatch: ' + JSON.stringify(model.driftRows));
}
if (model.okRows[0].table_name !== 'fact_a' || model.okRows[1].table_name !== 'dim_c') {
  throw new Error('ok row order mismatch: ' + JSON.stringify(model.okRows));
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/settings-view.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
