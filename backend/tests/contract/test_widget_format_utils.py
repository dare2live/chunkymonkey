from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_widget_format_utils_exports_and_formats() -> None:
    script = r"""
const fs = require('fs');
require(process.argv[1]);
const fmt = globalThis.WidgetFormatUtils;
if (!fmt || typeof fmt.formatNumber !== 'function' || typeof fmt.formatPercent !== 'function') {
  throw new Error('WidgetFormatUtils exports missing');
}
if (fmt.formatNumber(1.234, 2) !== '1.23') {
  throw new Error('formatNumber mismatch');
}
if (fmt.formatNumber(null, 2) !== '-') {
  throw new Error('formatNumber empty mismatch');
}
if (fmt.formatPercent(0.1234, 2, true) !== '12.34%') {
  throw new Error('formatPercent ratio mismatch');
}
if (fmt.formatPercent(12.34, 1, false, true) !== '+12.3%') {
  throw new Error('formatPercent signed mismatch');
}
if (fmt.formatPercent(-12.34, 1, false, true) !== '-12.3%') {
  throw new Error('formatPercent negative signed mismatch');
}
require(process.argv[2]);
require(process.argv[3]);
require(process.argv[4]);
require(process.argv[5]);
require(process.argv[6]);
require(process.argv[7]);
require(process.argv[8]);
require(process.argv[9]);
require(process.argv[10]);
require(process.argv[11]);
require(process.argv[12]);
require(process.argv[13]);
const topkSrc = fs.readFileSync(process.argv[10], 'utf8');
if (!topkSrc.includes('WidgetFormatUtils')) {
  throw new Error('TopKStripWidget should use WidgetFormatUtils');
}
const signalParamsSrc = fs.readFileSync(process.argv[11], 'utf8');
if (!signalParamsSrc.includes('WidgetFormatUtils')) {
  throw new Error('SignalParamsWidget should use WidgetFormatUtils');
}
const cohortSrc = fs.readFileSync(process.argv[12], 'utf8');
if (!cohortSrc.includes('WidgetFormatUtils')) {
  throw new Error('CohortCardWidget should use WidgetFormatUtils');
}
const backtestSrc = fs.readFileSync(process.argv[13], 'utf8');
if (!backtestSrc.includes('WidgetFormatUtils')) {
  throw new Error('BacktestPanelWidget should use WidgetFormatUtils');
}
if (!globalThis.ETFSectorRotationWidget || typeof globalThis.ETFSectorRotationWidget.mount !== 'function') {
  throw new Error('ETFSectorRotationWidget exports missing');
}
if (!globalThis.ETFStrategyCompareWidget || typeof globalThis.ETFStrategyCompareWidget.mount !== 'function') {
  throw new Error('ETFStrategyCompareWidget exports missing');
}
if (!globalThis.ETFOpportunityWidget || typeof globalThis.ETFOpportunityWidget.mountOpportunity !== 'function') {
  throw new Error('ETFOpportunityWidget exports missing');
}
if (!globalThis.ETFListWidget || typeof globalThis.ETFListWidget.mountEtfList !== 'function') {
  throw new Error('ETFListWidget exports missing');
}
if (!globalThis.ETFWorkbenchWidget || typeof globalThis.ETFWorkbenchWidget.mountEtfWorkbench !== 'function') {
  throw new Error('ETFWorkbenchWidget exports missing');
}
if (!globalThis.ETFAnalysisWidget || typeof globalThis.ETFAnalysisWidget.mountDeepAnalysis !== 'function') {
  throw new Error('ETFAnalysisWidget exports missing');
}
if (!globalThis.WorkbenchHealthWidget || typeof globalThis.WorkbenchHealthWidget.refreshWorkbenchHealthBar !== 'function') {
  throw new Error('WorkbenchHealthWidget exports missing');
}
if (!globalThis.InstitutionScorecardWidget || typeof globalThis.InstitutionScorecardWidget.mountScorecard !== 'function') {
  throw new Error('InstitutionScorecardWidget exports missing');
}
if (!globalThis.TopKStripWidget || typeof globalThis.TopKStripWidget.mount !== 'function') {
  throw new Error('TopKStripWidget exports missing');
}
if (!globalThis.SignalParamsWidget || typeof globalThis.SignalParamsWidget.mount !== 'function') {
  throw new Error('SignalParamsWidget exports missing');
}
if (!globalThis.CohortCardWidget || typeof globalThis.CohortCardWidget.mount !== 'function' || typeof globalThis.CohortCardWidget.renderCard !== 'function') {
  throw new Error('CohortCardWidget exports missing');
}
if (!globalThis.BacktestPanelWidget || typeof globalThis.BacktestPanelWidget.mount !== 'function') {
  throw new Error('BacktestPanelWidget exports missing');
}
"""

    result = subprocess.run(
        [
            "node",
            "-e",
            script,
            str(REPO / "assets/js/widgets/format-utils.js"),
            str(REPO / "assets/js/widgets/etf-sector-rotation.js"),
            str(REPO / "assets/js/widgets/etf-strategy-compare.js"),
            str(REPO / "assets/js/widgets/etf-opportunity.js"),
            str(REPO / "assets/js/widgets/etf-list.js"),
            str(REPO / "assets/js/widgets/etf-workbench.js"),
            str(REPO / "assets/js/widgets/etf-analysis.js"),
            str(REPO / "assets/js/widgets/workbench-health.js"),
            str(REPO / "assets/js/widgets/institution-scorecard.js"),
            str(REPO / "assets/js/widgets/topk-strip.js"),
            str(REPO / "assets/js/widgets/signal-params.js"),
            str(REPO / "assets/js/widgets/cohort-card.js"),
            str(REPO / "assets/js/widgets/backtest-panel.js"),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
