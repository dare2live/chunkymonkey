from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_returns_chart_widget_renders_expected_labels(tmp_path: Path) -> None:
    script = r"""
require(process.argv[1]);
const widget = globalThis.ReturnsChartWidget;
if (!widget || typeof widget.buildReturnsSvg !== 'function') {
  throw new Error('ReturnsChartWidget.buildReturnsSvg missing');
}
const basic = widget.buildReturnsSvg([{ gain_30d: 1.2 }, { gain_30d: -0.5 }], 120, 60);
if (!basic.includes('<svg') || !basic.includes('+1.2%') || !basic.includes('-0.5%')) {
  throw new Error('basic returns svg labels missing');
}
const sampled = widget.buildReturnsSvg(
  Array.from({ length: 61 }, (_, i) => ({ gain_30d: i })),
  120,
  60,
);
if (!sampled.includes('<svg') || !sampled.includes('+60.0%') || !sampled.includes('0.5%')) {
  throw new Error('sampled returns svg labels missing');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/returns-chart.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
