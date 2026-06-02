from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_signal_adapter_aggregates_and_orders_by_stock() -> None:
    script = r"""
const fs = require('fs');
require(process.argv[1]);
const adapter = globalThis.SignalAdapter;
if (!adapter || typeof adapter.eventToView !== 'function') {
  throw new Error('SignalAdapter exports missing');
}
const view = adapter.eventToView({
  event_id: 'e1',
  stock_code: '000001',
  stock_name: '平安银行',
  industry: '银行',
  institution_id: 'inst-1',
  institution_name: '机构1',
  action: 'follow',
  notice_date: '2026-06-03',
  premium_pct: 3.4,
  short: { stats: { ev_pct: 0.8, n: 3, win_rate: 0.4 } },
  long: { stats: { ev_pct: 2.5, n: 5, win_rate: 0.6 } },
});
if (!view || view.stockCode !== '000001' || view.action !== 'follow' || !view.longEV || view.longEV.pct !== 2.5) {
  throw new Error('SignalAdapter eventToView mapping mismatch: ' + JSON.stringify(view));
}
const src = fs.readFileSync(process.argv[1], 'utf8');
if (src.includes('function aggregateByStock(') || src.includes('aggregateByStock,')) {
  throw new Error('SignalAdapter dead aggregateByStock wrapper should be removed');
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/signal-adapter.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
