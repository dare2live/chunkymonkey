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
  rule_breakdown: { checks: [{ key: 'inst_type', raw: '券商' }] },
  action: 'follow',
  notice_date: '2026-06-03',
  premium_pct: 3.4,
  short: { stats: { ev_pct: 0.8, n: 3, win_rate: 0.4 } },
  long: { stats: { ev_pct: 2.5, n: 5, win_rate: 0.6 } },
});
if (!view || view.stockCode !== '000001' || view.action !== 'follow' || !view.longEV || view.longEV.pct !== 2.5 || view.institutionType !== '券商') {
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


def test_signal_adapter_fetch_signals_preserves_sorting_and_grouping() -> None:
    script = r"""
require(process.argv[1]);
const adapter = globalThis.SignalAdapter;
if (!adapter || typeof adapter.fetchSignals !== 'function') {
  throw new Error('SignalAdapter fetchSignals missing');
}
const payload = {
  summary: { total: 4 },
  signals: [
    {
      event_id: 'e1',
      stock_code: '000001',
      stock_name: '平安银行',
      industry: '金融',
      institution_id: 'inst-1',
      institution_name: '机构1',
      action: 'watch',
      notice_date: '2026-06-02',
      premium_pct: 2.0,
      long: { stats: { ev_pct: 1.2, n: 3, win_rate: 0.5 } },
    },
    {
      event_id: 'e2',
      stock_code: '000001',
      stock_name: '平安银行',
      industry: '金融',
      institution_id: 'inst-2',
      institution_name: '机构2',
      action: 'follow',
      notice_date: '2026-06-01',
      premium_pct: 3.0,
      long: { stats: { ev_pct: 1.5, n: 4, win_rate: 0.6 } },
    },
    {
      event_id: 'e3',
      stock_code: '000001',
      stock_name: '平安银行',
      industry: '金融',
      institution_id: 'inst-3',
      institution_name: '机构3',
      action: 'follow',
      notice_date: '2026-06-03',
      premium_pct: 1.5,
      rule_breakdown: { checks: [{ key: 'inst_type', raw: '券商' }] },
      long: { stats: { ev_pct: 2.1, n: 5, win_rate: 0.7 } },
    },
    {
      event_id: 'e4',
      stock_code: '000002',
      stock_name: '万科A',
      industry: '地产',
      institution_id: 'inst-4',
      institution_name: '机构4',
      action: 'skip',
      notice_date: '2026-06-01',
      premium_pct: 0.5,
      long: { stats: { ev_pct: 0.4, n: 2, win_rate: 0.4 } },
    },
  ],
};
let fetchCalls = 0;
let capturedUrl = '';
global.fetch = async (url) => {
  fetchCalls += 1;
  capturedUrl = String(url);
  return {
    ok: true,
    status: 200,
    json: async () => payload,
  };
};
(async () => {
  const result = await adapter.fetchSignals(30);
  if (fetchCalls !== 1) throw new Error(`expected one fetch call, got ${fetchCalls}`);
  if (!capturedUrl.includes('/api/signals/today?freshness_days=30&limit=2000')) {
    throw new Error(`unexpected fetch url: ${capturedUrl}`);
  }
  if (!result || !result.summary || result.summary.total !== 4) {
    throw new Error('summary mismatch: ' + JSON.stringify(result && result.summary));
  }
  if (!Array.isArray(result.byStock) || result.byStock.length !== 2) {
    throw new Error('byStock length mismatch: ' + JSON.stringify(result.byStock));
  }
  const first = result.byStock[0];
  if (first.stockCode !== '000001' || first.bestAction !== 'follow') {
    throw new Error('first stock order mismatch: ' + JSON.stringify(first));
  }
  if (!first.topEvent || first.topEvent.noticeDate !== '2026-06-03' || first.topEvent.institutionType !== '券商') {
    throw new Error('top event mismatch: ' + JSON.stringify(first.topEvent));
  }
  const eventOrder = first.events.map(ev => `${ev.action}:${ev.noticeDate}`).join('|');
  if (eventOrder !== 'follow:2026-06-03|follow:2026-06-01|watch:2026-06-02') {
    throw new Error(`events order mismatch: ${eventOrder}`);
  }
  const timelineOrder = first.timelineEvents.map(ev => `${ev.action}:${ev.noticeDate}`).join('|');
  if (timelineOrder !== 'follow:2026-06-03|watch:2026-06-02|follow:2026-06-01') {
    throw new Error(`timeline order mismatch: ${timelineOrder}`);
  }
  const second = result.byStock[1];
  if (second.stockCode !== '000002' || second.bestAction !== 'skip') {
    throw new Error('second stock order mismatch: ' + JSON.stringify(second));
  }
})().catch(err => {
  console.error(err);
  process.exit(1);
});
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/signal-adapter.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
