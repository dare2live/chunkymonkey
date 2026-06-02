from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_etf_list_widget_renders_expected_outputs() -> None:
    script = r"""
require(process.argv[1]);
const widget = globalThis.ETFListWidget;
if (!widget || typeof widget.collectEtfCategoryCounts !== 'function' || typeof widget.sortEtfCategories !== 'function' || typeof widget.applyEtfListFilters !== 'function' || typeof widget.buildEtfFilterBarHtml !== 'function' || typeof widget.buildEtfListRowHtml !== 'function' || typeof widget.buildEtfListTableHtml !== 'function' || typeof widget.mountEtfList !== 'function') {
  throw new Error('ETFListWidget exports missing');
}
const rows = [
  { code: '159919', name: '沪深300ETF', category: '宽基', strategy_type: '买入持有', relative_strength_4w: 1.2, relative_strength_12w: -2.3, rotation_score: 8.4, rotation_bucket: 'leader', backtest_return_pct: 5.2, buy_hold_return_pct: 2.1, backtest_excess_pct: 3.1, setup_state: '收敛待发', grid_step_pct: 1.5, trend_status: '多头', strategy_reason: 'reason' },
  { code: '512000', name: '券商ETF', category: '金融', strategy_type: '网格交易', relative_strength_4w: -0.6, relative_strength_12w: 0.2, rotation_score: 6.1, rotation_bucket: 'blacklist', backtest_return_pct: 1.2, buy_hold_return_pct: 0.8, backtest_excess_pct: -0.4, setup_state: '结构松散', grid_step_pct: 2.5, trend_status: '空头', strategy_reason: 'grid' },
  { code: '513100', name: '纳指ETF', category: '跨境', strategy_type: '防守停泊', relative_strength_4w: 0.0, relative_strength_12w: 0.4, rotation_score: 7.0, rotation_bucket: '', backtest_return_pct: null, buy_hold_return_pct: null, backtest_excess_pct: null, setup_state: '低波防守', grid_step_pct: null, trend_status: '中性', strategy_reason: '' }
];
const counts = widget.collectEtfCategoryCounts(rows);
if (counts['宽基'] !== 1 || counts['金融'] !== 1 || counts['跨境'] !== 1) {
  throw new Error('category counts mismatch');
}
const sorted = widget.sortEtfCategories(Object.keys(counts));
if (sorted[0] !== '宽基' || !sorted.includes('金融') || !sorted.includes('跨境')) {
  throw new Error('category sort mismatch');
}
const filtered = widget.applyEtfListFilters(rows, { categoryFilter: '金融', strategyFilter: '网格交易' });
if (filtered.length !== 1 || filtered[0].code !== '512000') {
  throw new Error('filtering mismatch');
}
const filterHtml = widget.buildEtfFilterBarHtml({ categoryFilter: 'all', strategyFilter: 'all' }, rows);
if (!filterHtml.includes('全部 (3)') || !filterHtml.includes('策略:全部') || !filterHtml.includes('宽基 (1)')) {
  throw new Error('filter html missing expected labels');
}
const rowHtml = widget.buildEtfListRowHtml(rows[0]);
if (!rowHtml.includes('data-etf-code="159919"') || !rowHtml.includes('xueqiu.com/S/SZ159919') || !rowHtml.includes('买入持有') || !rowHtml.includes('多头')) {
  throw new Error('row html missing expected labels');
}
const tableHtml = widget.buildEtfListTableHtml(rows);
if (!tableHtml.includes('<table class="data-table">') || !tableHtml.includes('data-etf-code="512000"') || !tableHtml.includes('趋势')) {
  throw new Error('table html missing expected labels');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/etf-list.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
