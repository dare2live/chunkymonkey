"""by_report_period 增量单测 (2026-06-23 修谄媚死: 十大股东/财报日常流按季报期扫增量)。
锁: (1) _latest_expected_report_period 季报截止日逻辑; (2) _by_ts_code_batches 增量跳已最新股;
(3) end_date 动态注入 (2026-07-04 stk_factor_pro 实弹踩出: 只传 start_date 无 end_date 被
API 拒绝 "权限不足", 整域每股必败空转数小时零净进展)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from services.data_sources import sync_runner as sr


@pytest.mark.parametrize("today,expected", [
    ("20260115", "20250930"),  # 1月: 去年Q3 (去年年报4/30才到)
    ("20260429", "20250930"),  # 4/29: 还是去年Q3
    ("20260430", "20260331"),  # 4/30当天: 今年Q1 (截止)
    ("20260501", "20260331"),  # 5月: Q1
    ("20260624", "20260331"),  # 半年报8/31前: Q1
    ("20260831", "20260630"),  # 8/31: 半年
    ("20260901", "20260630"),  # 9月: 半年
    ("20261031", "20260930"),  # 10/31: Q3
    ("20261231", "20260930"),  # 年底: 仍Q3 (年报次年4/30)
])
def test_latest_expected_report_period(today, expected):
    assert sr._latest_expected_report_period(today) == expected


def test_by_ts_code_increment_skips_up_to_date(monkeypatch):
    """increment_mode=by_report_period: 跳过 MAX(end_date)>=目标期的股, 只留缺新期的。"""
    # mock universe = 3 股; 2 已最新(skip), 1 缺新期(留)
    monkeypatch.setattr(sr, "get_active_universe", lambda conn, include_st=False: ["000001", "000002", "600000"],
                        raising=False)
    monkeypatch.setattr("services.universe.get_active_universe",
                        lambda conn, include_st=False: ["000001", "000002", "600000"])
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _DummyConn())
    monkeypatch.setattr(sr, "_latest_expected_report_period", lambda today: "20260331")
    # 000001/600000 已有 20260331; 000002 缺 (最新只到 20251231)
    monkeypatch.setattr(sr, "_stocks_up_to_date", lambda spec, tp, period_col="end_date": {"000001.SZ", "600000.SH"})

    spec = {"domain": "top10_floatholders", "increment_mode": "by_report_period", "target_table": "x"}
    batch = sr._by_ts_code_batches(spec, backfill=False)
    codes = {b["ts_code"] for b in batch}
    assert codes == {"000002.SZ"}  # 只剩缺新期的

    # backfill=True → 全量 (不增量)
    monkeypatch.setattr(sr, "_existing_ts_codes", lambda spec: set())
    full = sr._by_ts_code_batches(spec, backfill=True)
    assert len({b["ts_code"] for b in full}) == 3


def test_by_ts_code_injects_end_date_when_only_start_date_declared(monkeypatch):
    """stk_factor_pro 型域 (fixed_params 只声明 start_date): 必须动态补 end_date, 不能裸传
    start_date 触发 API "权限不足: 请同时提供日期和 ts_code" 拒绝。"""
    monkeypatch.setattr("services.universe.get_active_universe", lambda conn, include_st=False: ["000001"])
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _DummyConn())
    from services import utils as _utils
    monkeypatch.setattr(_utils, "latest_completed_trade_date", lambda conn: "2026-07-04")

    spec = {"domain": "stk_factor_pro", "fixed_params": {"start_date": "20190102"}, "target_table": "x"}
    batch = sr._by_ts_code_batches(spec, backfill=True)
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
