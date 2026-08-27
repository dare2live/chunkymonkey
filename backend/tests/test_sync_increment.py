"""by_report_period 增量单测.

锁: (1) 采集钟 = 已结束报告期, 不是法定截止;
(2) 法定截止钟仍可查 (completeness, 不驱动 increment);
(3) _by_ts_code_batches 增量跳已最新股;
(4) end_date 动态注入.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from services.data_sources import sync_runner as sr


@pytest.mark.parametrize("today,expected", [
    ("20260115", "20251231"),  # 年报期已结束, 不等 4/30
    ("20260429", "20260331"),  # Q1 已结束, 不等 4/30
    ("20260430", "20260331"),
    ("20260624", "20260331"),  # H1 尚未结束
    ("20260630", "20260630"),
    ("20260827", "20260630"),  # 披露季中跟源, 不等 8/31
    ("20260831", "20260630"),
    ("20261031", "20260930"),
    ("20261231", "20261231"),
])
def test_latest_ended_report_period(today, expected):
    assert sr._latest_ended_report_period(today) == expected


@pytest.mark.parametrize("today,expected", [
    ("20260115", "20250930"),  # 1月: 去年年报截止未到
    ("20260429", "20250930"),
    ("20260430", "20260331"),
    ("20260624", "20260331"),
    ("20260831", "20260630"),
    ("20260901", "20260630"),
    ("20261031", "20260930"),
    ("20261231", "20260930"),
])
def test_latest_statutory_complete_report_period(today, expected):
    assert sr._latest_expected_report_period(today) == expected


def test_ended_ahead_of_statutory_during_h1_filing_window():
    assert sr._latest_ended_report_period("20260827") == "20260630"
    assert sr._latest_expected_report_period("20260827") == "20260331"


def test_by_ts_code_increment_skips_up_to_date(monkeypatch):
    """increment_mode=by_report_period: 跳过 MAX(end_date)>=已结束期的股, 只留缺新期的。"""
    monkeypatch.setattr(sr, "get_active_universe", lambda conn, include_st=False: ["000001", "000002", "600000"],
                        raising=False)
    monkeypatch.setattr("services.universe.get_active_universe",
                        lambda conn, include_st=False: ["000001", "000002", "600000"])
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _DummyConn())
    monkeypatch.setattr(sr, "_latest_ended_report_period", lambda today: "20260630")
    monkeypatch.setattr(sr, "_stocks_up_to_date", lambda spec, tp, period_col="end_date": {"000001.SZ", "600000.SH"})

    spec = {"domain": "top10_floatholders", "increment_mode": "by_report_period", "target_table": "x"}
    batch = sr._by_ts_code_batches(spec, backfill=False)
    codes = {b["ts_code"] for b in batch}
    assert codes == {"000002.SZ"}

    monkeypatch.setattr(sr, "_existing_ts_codes", lambda spec: set())
    full = sr._by_ts_code_batches(spec, backfill=True)
    assert len({b["ts_code"] for b in full}) == 3


def test_by_ts_code_consumes_planned_end_when_only_start_date_declared(monkeypatch):
    """planner 提供的 eligible end 必须注入逐股请求，helper 不得另算第二个 frontier。"""
    monkeypatch.setattr("services.universe.get_active_universe", lambda conn, include_st=False: ["000001"])
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _DummyConn())
    spec = {"domain": "stk_factor_pro", "fixed_params": {"start_date": "20190102"}, "target_table": "x"}
    batch = sr._by_ts_code_batches(spec, backfill=True, end="20260704")
    assert batch, "batch 不应为空"
    for b in batch:
        assert b.get("start_date") == "20190102"
        assert b.get("end_date") == "20260704", f"end_date 未动态注入: {b}"


def test_by_ts_code_end_date_not_overridden_when_declared(monkeypatch):
    """已显式声明 end_date 的域 (如 balancesheet fixed_params 锁窗口) 不应被动态注入覆盖。"""
    monkeypatch.setattr("services.universe.get_active_universe", lambda conn, include_st=False: ["000001"])
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _DummyConn())

    spec = {"domain": "balancesheet", "fixed_params": {"start_date": "20200101", "end_date": "20260612"},
            "target_table": "x"}
    batch = sr._by_ts_code_batches(spec, backfill=True)
    assert batch
    for b in batch:
        assert b.get("end_date") == "20260612"


class _DummyConn:
    def close(self):
        pass
