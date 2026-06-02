from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_model_monitor_widget_exports_and_labels() -> None:
    script = r"""
require(process.argv[1]);
require(process.argv[2]);
const fmt = globalThis.WidgetFormatUtils;
if (!fmt || typeof fmt.formatNumber !== 'function' || typeof fmt.formatPercent !== 'function') {
  throw new Error('WidgetFormatUtils exports missing');
}
if (fmt.formatNumber(1.234, 2) !== '1.23') {
  throw new Error('formatNumber mismatch');
}
if (fmt.formatPercent(0.1234, 2, true) !== '12.34%') {
  throw new Error('formatPercent ratio mismatch');
}
if (fmt.formatPercent(12.34, 1, false) !== '12.3%') {
  throw new Error('formatPercent raw mismatch');
}
const widget = globalThis.ModelMonitorWidget;
if (!widget || typeof widget.loadModelMonitor !== 'function' || typeof widget.renderModelMonitor !== 'function' || typeof widget.renderModelComparison !== 'function' || typeof widget.renderPromotionGate !== 'function' || typeof widget.renderTdxValidation !== 'function' || typeof widget.renderMetricsCards !== 'function' || typeof widget.renderDailyChart !== 'function' || typeof widget.renderRegimeChart !== 'function' || typeof widget.renderFeatureImportance !== 'function' || typeof widget.labelFeature !== 'function' || typeof widget.labelModelId !== 'function' || typeof widget.setFeatureLabels !== 'function') {
  throw new Error('ModelMonitorWidget exports missing');
}
widget.setFeatureLabels({
  features: { ret_1m: '1月收益' },
  models: { lgbm: 'LightGBM' },
  loaded: true,
});
if (widget.labelFeature('ret_1m') !== 'ret_1m（1月收益）') {
  throw new Error('feature label mismatch');
}
if (widget.labelModelId('lgbm_20260101_123456') !== 'LightGBM · 2026-01-01 12:34') {
  throw new Error('model label mismatch');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/format-utils.js"), str(REPO / "assets/js/widgets/model-monitor.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
