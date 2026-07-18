"""by_trade_date 显式 --start/--end 手工回填不丢首日 (2026-07-06 根因修复).

背景: run_domain by_trade_date 分支"增量模式跳过 watermark 当天"的判据曾写成
`days[0] == (start or _last_watermark_date(...) or "")` —— 调用方显式传 --start 时
这个判据必然为真 (days[0] 本就是由 start_d=start 算出的), 导致任何手工
`--start X --end Y` 范围回填都静默丢第一天。此 bug 是在响应全面数据审计
(analysis/comprehensive_data_module_audit_20260706.md) 修复 stk_limit page_limit
截断问题时实测踩出的: --start 20260615 --end 20260703 回填后, 20260615 当天
仍缺 1493 行, 复查发现是这个独立的、此前从未被发现的 sync_runner 逻辑 bug。

本门锁定两个场景:
(1) 显式给 start 的手工范围回填 —— 首日必须出现在实际 batch 里 (不能被当成"watermark
    当天"跳过)。
(2) 不给 start、纯粹靠 watermark 续拉的常规增量 —— 仍要跳过 watermark 当天 (回归防护:
    修复不能反向破坏原有省重拉语义)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema

REG = {
    "defaults": {"target_db": "tushare_raw"},
    "domains": {
        "synthetic_trade_date_domain": {
            "source": "tushare", "api": "stk_limit",
            "target_table": "raw_tushare_synthetic_trade_date_test",
            "grain": ["trade_date", "ts_code"],
            "batch_mode": "by_trade_date",
            "data_start": "20260101",
        },
    },
}


class _NoClose:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        pass


class _CapturingAdapter:
    def __init__(self):
        self.calls: list[dict] = []

    def fetch_raw(self, api, **params):
        self.calls.append(dict(params))
        return [{"trade_date": params["trade_date"], "ts_code": "000001.SZ"}]


@pytest.fixture()
def env(monkeypatch):
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    adapter = _CapturingAdapter()
    monkeypatch.setattr(sr, "_adapter", lambda name: adapter)
    monkeypatch.setattr(sr, "_target_conn", lambda spec: shared)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: ["20260615", "20260616", "20260617"])
    monkeypatch.setattr(sr, "_RATE_LIMITER", None)
    monkeypatch.setattr(sr, "_RATE_LIMITER_INIT", False)
    yield c, adapter
    c.close()


def test_explicit_start_range_backfill_keeps_first_day(env, monkeypatch):
    """手工 --start 20260615 --end 20260617 (未加 --backfill): 首日不能被静默丢弃。"""
    _c, adapter = env
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, source: "20260614")
    res = sr.run_domain("synthetic_trade_date_domain", registry=REG,
                         start="20260615", end="20260617")
    assert res["ok"] is True
    dates = sorted(c["trade_date"] for c in adapter.calls)
    assert dates == ["20260615", "20260616", "20260617"], (
        f"显式 --start 范围回填丢了首日, 实际抓取日期={dates}")


def test_routine_incremental_still_skips_watermark_day(env, monkeypatch):
    """不传 --start, 纯 watermark 续拉: watermark 当天(已写过)仍应被跳过 (回归防护)。"""
    _c, adapter = env
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, source: "20260615")
    res = sr.run_domain("synthetic_trade_date_domain", registry=REG, end="20260617")
    assert res["ok"] is True
    dates = sorted(c["trade_date"] for c in adapter.calls)
    assert dates == ["20260616", "20260617"], (
        f"常规增量续拉未跳过 watermark 当天, 实际抓取日期={dates}")
