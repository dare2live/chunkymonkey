from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_stock_list_controls_widget_sorts_and_builds_filters() -> None:
    script = r"""
require(process.argv[1]);
const widget = globalThis.StockListControlsWidget;
if (!widget || typeof widget.sortStockRows !== 'function' || typeof widget.buildStockFilterBar !== 'function') {
  throw new Error('StockListControlsWidget APIs missing');
}
const rows = [
  { stock_code: '000002', stock_name: '万科A', latest_notice_date: '2026-06-01', composite_priority_score: 82, discovery_score: 11, tdx_l1: 'T10' },
  { stock_code: '000001', stock_name: '平安银行', latest_notice_date: '2026-06-02', composite_priority_score: 79, discovery_score: 15, tdx_l1: 'T10' },
  { stock_code: '600000', stock_name: '浦发银行', latest_notice_date: '2026-05-31', composite_priority_score: 91, discovery_score: 9, tdx_l1: 'T11' },
];
const composite = widget.sortStockRows(rows, 'composite').map((row) => row.stock_code);
const notice = widget.sortStockRows(rows, 'notice').map((row) => row.stock_code);
if (composite[0] !== '600000' || composite[1] !== '000002' || composite[2] !== '000001') {
  throw new Error('composite sort mismatch: ' + composite.join(','));
}
if (notice[0] !== '000001' || notice[1] !== '000002' || notice[2] !== '600000') {
  throw new Error('notice sort mismatch: ' + notice.join(','));
}
const html = widget.buildStockFilterBar({
  stocks: rows,
  activeGate: 'follow',
  activeIndustry: 'T10',
  activeSortMode: 'notice',
  tdxL1Names: { T10: '金融', T11: '建筑地产' },
});
if (!html.includes('data-filter-group="gate"') || !html.includes('data-filter-group="industry"') || !html.includes('data-filter-group="sort"')) {
  throw new Error('filter groups missing');
}
if (!html.includes('active') || !html.includes('金融 2') || !html.includes('建筑地产 1')) {
  throw new Error('filter html mismatch');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/stock-list-controls.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
