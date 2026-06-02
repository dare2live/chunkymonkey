from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_stock_summary_widget_merges_counts_and_top_lists() -> None:
    script = r"""
require(process.argv[1]);
const widget = globalThis.StockSummaryWidget;
if (!widget || typeof widget.mergeStockSummary !== 'function') {
  throw new Error('StockSummaryWidget.mergeStockSummary missing');
}
const rows = [
  { priority_pool: 'A池', enabled: true, blacklisted: false, type: '券商', setup_tag: 'x', setup_priority: 1, setup_industry_name: '银行', _dual_confirm: true },
  { priority_pool: 'B池', enabled: true, blacklisted: false, type: '基金', setup_tag: 'y', setup_priority: 2, setup_industry_name: '券商' },
  { priority_pool: 'C池', enabled: true, blacklisted: false, type: '券商', setup_industry_name: '券商' },
  { priority_pool: 'A池', enabled: false, blacklisted: false, type: '保险', setup_industry_name: '保险' },
];
const summary = widget.mergeStockSummary(rows, {
  followTotal: 9,
  pools: { A池: 99 },
  topSources: [{ name: '后端', count: 3 }],
}, {
  stockGateInfo: (s) => ({ key: s.priority_pool === 'C池' ? 'watch' : 'follow' }),
  stockSourceName: (s) => s.type || '',
});
if (summary.total !== 4 || summary.abTotal !== 3 || summary.followTotal !== 9) {
  throw new Error('summary counts mismatch');
}
if (summary.pools['A池'] !== 99 || summary.pools['B池'] !== 1) {
  throw new Error('pool merge mismatch');
}
if (!summary.topIndustries.length || summary.topSignals[0].name !== 'A1') {
  throw new Error('top list mismatch');
}
if (!summary.topSources.length || summary.topSources[0].name !== '后端') {
  throw new Error('top source override mismatch');
}
if (summary.dualConfirm !== 1) {
  throw new Error('dual confirm mismatch');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/stock-summary.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
