from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_workbench_health_widget_exports_and_normalizes_sources() -> None:
    script = r"""
for (const f of process.argv.slice(1)) require(f);  // 依赖序: format-utils 先于 widget (生产由模板 script 序保证, harness 须显式)
const widget = globalThis.WorkbenchHealthWidget;
if (!widget || typeof widget.refreshWorkbenchHealthBar !== 'function' || typeof widget.refreshNetwork !== 'function' || typeof widget.normalizeSourceName !== 'function' || typeof widget.setSourcePill !== 'function') {
  throw new Error('WorkbenchHealthWidget exports missing');
}
if (widget.normalizeSourceName('tdxhub_218.6.170.47:7709', 'fallback') !== 'tdxhub') {
  throw new Error('tdxhub normalization mismatch');
}
if (widget.normalizeSourceName('HTTP 500', 'akshare') !== 'akshare') {
  throw new Error('HTTP normalization mismatch');
}
if (widget.normalizeSourceName('', 'akshare') !== 'akshare') {
  throw new Error('fallback normalization mismatch');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/widgets/format-utils.js"), str(REPO / "assets/js/widgets/workbench-health.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
