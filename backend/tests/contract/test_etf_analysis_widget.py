from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_etf_analysis_widget_renders_expected_outputs() -> None:
    script = r"""
for (const f of process.argv.slice(1)) require(f);  // 依赖序: format-utils 先于 widget (生产由模板 script 序保证, harness 须显式)
const widget = globalThis.ETFAnalysisWidget;
if (!widget || typeof widget.buildEtfTradeTimelineHtml !== 'function' || typeof widget.buildNavCurveSvg !== 'function' || typeof widget.buildDeepAnalysisHtml !== 'function') {
  throw new Error('ETFAnalysisWidget exports missing');
}
const timeline = widget.buildEtfTradeTimelineHtml(
  [
    { date: '2026-05-01', close: 10 },
    { date: '2026-05-02', close: 11 },
    { date: '2026-05-03', close: 12 },
  ],
  [
    { date: '2026-05-01', side: 'buy', seq: 1, price: 10, units: 100, notional: 1000, fee: 1, realized_pnl: 5, realized_pnl_pct: 0.5, note: 'first buy' },
    { date: '2026-05-03', side: 'sell', seq: 2, price: 12, units: 100, notional: 1200, fee: 2, realized_pnl: 20, realized_pnl_pct: 2.0, note: 'take profit' },
  ],
  1.5
);
if (!timeline.includes('日线买卖点时间轴') || !timeline.includes('红买绿卖') || !timeline.includes('买入') || !timeline.includes('卖出')) {
  throw new Error('timeline markup missing expected labels');
}
const svg = widget.buildNavCurveSvg(
  [
    { date: '2026-05-01', nav: 1.00 },
    { date: '2026-05-02', nav: 1.04 },
    { date: '2026-05-03', nav: 1.08 },
  ],
  [
    { date: '2026-05-01', nav: 1.00 },
    { date: '2026-05-02', nav: 1.01 },
    { date: '2026-05-03', nav: 1.02 },
  ],
  1.5
);
if (!svg.includes('<svg') || !svg.includes('网格 1.5%') || !svg.includes('买入持有')) {
  throw new Error('curve svg missing expected labels');
}
const html = widget.buildDeepAnalysisHtml('159919', {
  info: { name: 'ETF 测试产品', tradeability_status: 'ok', strategy_type: '趋势', setup_state: '已就绪' },
  verdict: { rating: '推荐', lines: ['测试结论 A', '测试结论 B'] },
  recommended_strategy: '买入持有',
  optimizer_summary: { candidate_step_count: 3, valid_step_count: 2, rejected_step_count: 1, grid_available: true, model_rules: ['rule-a'] },
  best_step: {
    step_pct: 1.5,
    return_pct: 4.2,
    annual_return_pct: 10.1,
    max_drawdown_pct: 1.2,
    sharpe: 1.7,
    calmar: 2.3,
    win_rate: 60,
    trade_count: 8,
    days: 30,
    curve: [{ date: '2026-05-01', nav: 1.00 }, { date: '2026-05-02', nav: 1.04 }, { date: '2026-05-03', nav: 1.08 }],
    trades: [{ date: '2026-05-01', side: 'buy', seq: 1, price: 10, units: 100, notional: 1000, fee: 1, realized_pnl: 5, realized_pnl_pct: 0.5, note: 'first buy' }],
  },
  buy_hold: {
    step_pct: 1.5,
    return_pct: 2.0,
    annual_return_pct: 6.0,
    max_drawdown_pct: 2.2,
    sharpe: 1.1,
    calmar: 1.3,
    days: 30,
    curve: [{ date: '2026-05-01', nav: 1.00 }, { date: '2026-05-02', nav: 1.01 }, { date: '2026-05-03', nav: 1.02 }],
  },
  all_steps: [{ step_pct: 1.5, hard_gate_passed: true, hard_gate_reason: 'ok', return_pct: 4.2, annual_return_pct: 10.1, max_drawdown_pct: 1.2, sharpe: 1.7, calmar: 2.3, win_rate: 60, buy_count: 1, sell_count: 0 }],
  multi_period: [{ window: '2026-05', days: 30, best: { return_pct: 4.2, max_drawdown_pct: 1.2, step_pct: 1.5 }, buy_hold: { return_pct: 2.0, max_drawdown_pct: 2.2 } }],
  daily_prices: [{ date: '2026-05-01', close: 10 }, { date: '2026-05-02', close: 11 }, { date: '2026-05-03', close: 12 }],
});
if (!html.includes('深度量化分析') || !html.includes('核心指标对比') || !html.includes('实盘账本检验') || !html.includes('量化基金经理结论') || !html.includes('日线买卖点时间轴')) {
  throw new Error('deep analysis html missing expected sections');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/format-utils.js"), str(REPO / "assets/js/widgets/etf-analysis.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
