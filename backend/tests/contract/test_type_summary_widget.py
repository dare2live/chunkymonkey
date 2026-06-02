from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_type_summary_widget_collects_enabled_counts_and_labels() -> None:
    script = r"""
require(process.argv[1]);
const widget = globalThis.TypeSummaryWidget;
if (!widget || typeof widget.collectEnabledTypeSummary !== 'function') {
  throw new Error('TypeSummaryWidget.collectEnabledTypeSummary missing');
}
const rows = [
  { enabled: true, blacklisted: false, type: '券商' },
  { enabled: true, blacklisted: false, type: '券商' },
  { enabled: true, blacklisted: false, type: '基金' },
  { enabled: false, blacklisted: false, type: '基金' },
  { enabled: true, blacklisted: true, type: '保险' },
  { enabled: true, blacklisted: false, type: '保险' },
  { enabled: true, blacklisted: false, type: '信托' },
];
const summary = widget.collectEnabledTypeSummary(rows, 3);
if (summary.active !== 5 || summary.total !== 7) {
  throw new Error('count summary mismatch');
}
if (summary.orderedTypes.join(',') !== '券商,保险,基金,信托') {
  throw new Error('ordered types mismatch');
}
if (!summary.label.includes('券商2') || !summary.label.includes('+1类')) {
  throw new Error('summary label mismatch');
}
if (!summary.title.includes('券商 2') || !summary.title.includes('信托 1')) {
  throw new Error('summary title mismatch');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/type-summary.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
