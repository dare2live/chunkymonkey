from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_stock_list_rows_widget_builds_row_html() -> None:
    script = r"""
require(process.argv[1]);
const widget = globalThis.StockListRowsWidget;
if (!widget || typeof widget.buildStockListRowHtml !== 'function') {
  throw new Error('StockListRowsWidget API missing');
}
const html = widget.buildStockListRowHtml({
  stock_code: '603369',
  stock_name: '今世缘',
  tdx_l1: 'T03',
  tdx_l2: '白酒',
  tdx_l3: '酒类',
  composite_priority_score: 88.6,
  latest_notice_date: '2026-06-01',
  holder_total: 12,
  _sig_v2: {
    action: 'follow',
    institution_name: '测试机构',
    notice_date: '2026-05-29',
    notice_date_source: 'source_notice',
    long: { stats: { ev_pct: 7.2, n: 18 } }
  }
}, 3, {
  esc: (v) => String(v == null ? '' : v),
  fmtDate: (v) => 'DATE:' + v,
  stockCell: (code, name) => `[${code}]${name}`,
  stockCompositeCell: (s) => `SCORE:${s.composite_priority_score}`,
  stockDateSummaryCell: (dateText) => `NOTICE:${dateText}`,
  stockHolderCoverageCell: (s) => `HOLDERS:${s.holder_total}`,
  tdxL1Names: { T03: '日常消费' },
});
if (!html.includes('data-stock-idx="3"')) throw new Error('row idx missing');
if (!html.includes('[603369]今世缘')) throw new Error('stock cell missing');
if (!html.includes('可跟')) throw new Error('signal badge missing');
if (!html.includes('测试机构')) throw new Error('signal institution missing');
if (!html.includes('日常消费')) throw new Error('industry label missing');
if (!html.includes('SCORE:88.6')) throw new Error('composite cell missing');
if (!html.includes('NOTICE:2026-06-01')) throw new Error('notice summary missing');
if (!html.includes('HOLDERS:12')) throw new Error('holder coverage missing');
if (!html.includes('+ 加自选')) throw new Error('watchlist button missing');
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/stock-list-rows.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
