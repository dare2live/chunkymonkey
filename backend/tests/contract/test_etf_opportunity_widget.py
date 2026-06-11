from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_etf_opportunity_widget_renders_expected_outputs() -> None:
    script = r"""
for (const f of process.argv.slice(1)) require(f);  // 依赖序: format-utils 先于 widget (生产由模板 script 序保证, harness 须显式)
const widget = globalThis.ETFOpportunityWidget;
if (!widget || typeof widget.etfOverviewTone !== 'function' || typeof widget.etfWatchTags !== 'function' || typeof widget.buildOpportunityHtml !== 'function' || typeof widget.buildOpportunityMiningHtml !== 'function' || typeof widget.mountOpportunity !== 'function') {
  throw new Error('ETFOpportunityWidget exports missing');
}
const tone = widget.etfOverviewTone('panic');
if (!tone.label || tone.label.indexOf('托底') < 0) {
  throw new Error('overview tone mismatch');
}
const tags = widget.etfWatchTags([
  { code: '159919', name: '沪深300ETF', rotation_score: 8.4, setup_state: '收敛待发', strategy_type: '买入持有', grid_step_pct: 1.5 },
], { bg: 'var(--cm-ok-100)', fg: 'var(--cm-ok-500)' }, 'analyze');
if (!tags.includes('data-etf-analyze="159919"') || !tags.includes('沪深300ETF') || !tags.includes('买入持有')) {
  throw new Error('watch tags mismatch');
}
const html = widget.buildOpportunityHtml({
  overview: {
    market_state: 'cooling',
    regime_label: '降温观察期',
    temperature_score: 3.4,
    regime_reason: '测试原因',
    action_hint: '观察',
    macro_scenario: 'scenario',
    macro_note: 'note',
    rotation_rule: 'rule',
    rotation_leaders: [{ code: '159919', name: '沪深300ETF', rotation_score: 8.4, setup_state: '收敛待发', strategy_type: '买入持有', grid_step_pct: 1.5 }],
    rotation_laggards: [{ code: '512000', name: '券商ETF', rotation_score: 2.4, setup_state: '结构松散', strategy_type: '暂不参与', grid_step_pct: 2.5 }],
    positive_20d_ratio: 52.1,
    avg_momentum_20d: 1.2,
    avg_momentum_60d: 2.3,
    avg_volatility_20d: 0.8,
    avg_drawdown_60d: 1.4,
    strategy_counts: { trend: 3, grid: 2, defensive: 1, avoid: 4 },
  }
});
if (!html.includes('ETF 机会发现') || !html.includes('功能入口') || !html.includes('opportunityMiningSection') || !html.includes('关注名单') || !html.includes('回避名单')) {
  throw new Error('opportunity html missing expected labels');
}
const miningHtml = widget.buildOpportunityMiningHtml({
  grid_candidates: [
    { code: '159919', name: '沪深300ETF', best_step_pct: 1.5, backtest_return_pct: 4.2, backtest_excess_pct: 2.1, backtest_max_drawdown_pct: 1.1 },
  ],
  trend_candidates: [
    { code: '512000', name: '券商ETF', action: '观察', relative_strength_4w: 1.2, relative_strength_12w: -0.8, factor_score: 7.8 },
  ],
});
if (!miningHtml.includes('网格交易 Top 5') || !miningHtml.includes('买入持有 Top 5') || !miningHtml.includes('▶ 深度分析')) {
  throw new Error('opportunity mining html missing expected labels');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/format-utils.js"), str(REPO / "assets/js/widgets/etf-opportunity.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
