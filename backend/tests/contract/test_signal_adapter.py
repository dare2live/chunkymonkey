from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_signal_adapter_aggregates_and_orders_by_stock() -> None:
    script = r"""
require(process.argv[1]);
const adapter = globalThis.SignalAdapter;
if (!adapter || typeof adapter.aggregateByStock !== 'function' || typeof adapter.eventToView !== 'function') {
  throw new Error('SignalAdapter exports missing');
}
const rows = [
  {
    event_id: 'e1',
    stock_code: '000001',
    stock_name: '平安银行',
    industry: '银行',
    institution_id: 'inst-1',
    action: 'watch',
    notice_date: '2026-06-02',
    notice_date_source: 'source_notice',
    premium_pct: 1.2,
    long: { stats: { ev_pct: 2.5 } },
    short: { stats: { ev_pct: 0.8 } },
  },
  {
    event_id: 'e2',
    stock_code: '000001',
    stock_name: '平安银行',
    industry: '银行',
    institution_id: 'inst-2',
    action: 'follow',
    notice_date: '2026-06-03',
    notice_date_source: 'page_update_date',
    premium_pct: 3.4,
    long: { stats: { ev_pct: 1.1 } },
    short: { stats: { ev_pct: 1.6 } },
  },
  {
    event_id: 'e3',
    stock_code: '000002',
    stock_name: '万科A',
    industry: '地产',
    institution_id: 'inst-3',
    action: 'skip',
    notice_date: '2026-06-01',
    notice_date_source: 'fetched_at_observed',
  },
];
const grouped = adapter.aggregateByStock(rows);
if (grouped.length !== 2) {
  throw new Error('group count mismatch: ' + grouped.length);
}
if (grouped[0].stockCode !== '000001' || grouped[0].bestAction !== 'follow') {
  throw new Error('primary group mismatch: ' + JSON.stringify(grouped[0]));
}
if (grouped[0].events[0].action !== 'follow' || grouped[0].events[1].action !== 'watch') {
  throw new Error('group ordering mismatch: ' + grouped[0].events.map((event) => event.action).join(','));
}
if (grouped[0].instCount !== 2 || grouped[0].eventCount !== 2) {
  throw new Error('group counts mismatch: ' + JSON.stringify({ instCount: grouped[0].instCount, eventCount: grouped[0].eventCount }));
}
if (Math.abs(grouped[0].premiumAvg - 2.3) > 1e-9) {
  throw new Error('premium avg mismatch: ' + grouped[0].premiumAvg);
}
if (grouped[0].longEVBest !== 2.5 || grouped[0].shortEVBest !== 1.6) {
  throw new Error('ev summary mismatch: ' + JSON.stringify({ long: grouped[0].longEVBest, short: grouped[0].shortEVBest }));
}
if (grouped[0].latestNotice !== '2026-06-03') {
  throw new Error('latest notice mismatch: ' + grouped[0].latestNotice);
}
if (grouped[0].noticeSourceCounts.source_notice !== 1 || grouped[0].noticeSourceCounts.page_update_date !== 1) {
  throw new Error('notice source counts mismatch: ' + JSON.stringify(grouped[0].noticeSourceCounts));
}
if (grouped[1].stockCode !== '000002' || grouped[1].bestAction !== 'skip') {
  throw new Error('secondary group mismatch: ' + JSON.stringify(grouped[1]));
}
"""

    result = subprocess.run(
        ["node", "-e", script, str(REPO / "assets/js/signal-adapter.js")],
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
