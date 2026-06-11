from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_etf_workbench_widget_exports_and_renders_core_sections() -> None:
    script = r"""
for (const f of process.argv.slice(1)) require(f);  // 依赖序: format-utils 先于 widget (生产由模板 script 序保证, harness 须显式)
const widget = globalThis.ETFWorkbenchWidget;
if (!widget || typeof widget.mountEtfWorkbench !== 'function' || typeof widget.buildWorkbenchHtml !== 'function' || typeof widget.etfNum !== 'function') {
  throw new Error('ETFWorkbenchWidget exports missing');
}
const html = widget.buildWorkbenchHtml({
  snapshot: { snapshot_id: 'snap-1', computed_at: '2026-06-03T00:00:00Z', is_stale: false, etf_count: 12 },
  source_status: {
    universe_count: 12,
    kline_etf_count: 9,
    coverage_2023_count: 7,
    recent_only_count: 3,
    no_kline_count: 1,
    source_breakdown: [{ source: 'tdxhub', count: 9 }],
    universe_source: 'tdxhub',
    universe_source_updated_at: '2026-06-03T00:00:00Z',
    universe_updated_at: '2026-06-03T00:00:00Z',
    history_start: '2021-01-01',
    history_end: '2026-06-03',
    kline_coverage_ratio: 92.5,
    latest_kline_success_at: '2026-06-03T00:00:00Z',
    latest_kline_attempt_at: '2026-06-03T00:00:00Z',
    snapshot_lag_minutes: 5,
    last_error_count: 0,
    connectivity: { holdings_source: true, kline_source: true, industry_source: true, holdings_source_detail: 'akshare', kline_source_detail: 'tdxhub', industry_source_detail: 'tdxhub' },
  },
  overview: {
    temperature_score: 55.1,
    positive_20d_ratio: 60,
    avg_momentum_20d: 3.2,
    avg_volatility_20d: 1.5,
    avg_drawdown_60d: 4.8,
    regime_label: '中性偏强',
    regime_reason: '示例说明',
    strategy_counts: { trend: 4, grid: 3, defensive: 2, avoid: 1 },
  },
  sync_state: { running: false, message: 'done' },
}, {
  esc: v => String(v),
  fmtDateTime: v => String(v),
});
if (html.indexOf('ETF 工作台') < 0 || html.indexOf('机会发现') < 0 || html.indexOf('全量筛选') < 0) {
  throw new Error('Workbench html missing core sections');
}
if (widget.etfNum(12.34, 1) !== '12.3') {
  throw new Error('etfNum mismatch');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/format-utils.js"), str(REPO / "assets/js/widgets/etf-workbench.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
