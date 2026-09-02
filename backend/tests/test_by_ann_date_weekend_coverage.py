"""by_ann_date 覆盖周末日历日回归门（历史证据=git log --grep gap_root_cause）。

背景: report_rc/forecast/share_float 三域曾用 batch_mode: by_trade_date, 但它们的 date_param
(report_date/ann_date) 是真实披露日历概念(analyst可以周末发研报/公司可以周末公告), 而
by_trade_date 只在**交易日历**上枚举日期——结构性排除了全部周末数据, 从建域起就是这样
(report_rc 实测 20260328 周六 602 行本地 0 行, 100% 缺失; 更早期证据: forecast/share_float
抽样多个周六/周日均有非零真实数据)。三域已改用既有的 by_ann_date 批模式(为 org_holding/qfii
等"公告日含周末"型域设计, 全日历日枚举), 之前竟从未有单测验证这个批模式真的会展开出周末日期
(该机制本身此前 0 测试覆盖)。

本门锁定: by_ann_date 的批次日期列表必须包含周末日期(不能像 by_trade_date 那样被交易日历过滤掉)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema

# 2026-03-21/22 是周六/周日 (真实证据: report_rc 该周末实测有数据)
START, END = "20260320", "20260322"   # 周五+周六+周日


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
        return [{"ts_code": "TEST.SH", "report_date": params.get("report_date", "?")}]


def _registry(batch_mode: str) -> dict:
    return {
        "defaults": {
            "target_db": "tushare_raw",
            "fetch_timeout_seconds": 120,
            "execution_policy": {"mode": "enabled", "reason": "active"},
        },
        "domains": {
            "report_rc_test": {
                "source": "tushare", "api": "report_rc",
                "target_table": "raw_tushare_report_rc_test",
                "grain": ["ts_code", "report_date"],
                "batch_mode": batch_mode,
                "date_param": "report_date",
                "data_start": START,
            },
        },
    }


@pytest.fixture()
def env(monkeypatch):
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    adapter = _CapturingAdapter()
    monkeypatch.setattr(sr, "_adapter", lambda name: adapter)
    monkeypatch.setattr(sr, "_target_conn", lambda spec: shared)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, conn=None: None)
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec, **_kwargs: sr.DomainEligibility(END, False, "historical_test_window"),
    )
    monkeypatch.setattr(sr, "_RATE_LIMITERS", {})
    yield c, adapter
    c.close()


def test_by_trade_date_excludes_weekend_red(env, monkeypatch):
    """red: 旧 by_trade_date 只在交易日历枚举, 周末(20260321/22)不会出现在任何批次里。"""
    _c, adapter = env
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: ["20260320"])  # 周五唯一交易日
    res = sr.run_domain("report_rc_test", backfill=True, start=START, end=END,
                         registry=_registry("by_trade_date"))
    assert res["ok"] is True
    dates = {c["report_date"] for c in adapter.calls}
    assert dates == {"20260320"}, f"by_trade_date 不该碰到周末, 实际={dates}"
    assert "20260321" not in dates and "20260322" not in dates


def test_by_ann_date_includes_weekend_green(env):
    """green: 新 by_ann_date 按全日历日枚举, 20260321(周六)/20260322(周日) 必须出现在批次里。"""
    _c, adapter = env
    res = sr.run_domain("report_rc_test", backfill=True, start=START, end=END,
                         registry=_registry("by_ann_date"))
    assert res["ok"] is True
    dates = {c["report_date"] for c in adapter.calls}
    assert dates == {"20260320", "20260321", "20260322"}, (
        f"by_ann_date 必须覆盖周末日历日(修复 report_rc/forecast/share_float 结构性缺口的核心机制), "
        f"实际={dates}")


def test_registry_three_domains_use_by_ann_date():
    """真实 registry 声明校验: report_rc/forecast/share_float/holdernumber 已切 by_ann_date。"""
    import yaml
    reg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config"
                           / "sync_registry.yaml").read_text(encoding="utf-8"))
    for dom in ("report_rc", "forecast", "share_float", "stk_holdernumber"):
        assert reg["domains"][dom]["batch_mode"] == "by_ann_date", (
            f"{dom} 应为 by_ann_date(全日历日枚举, 覆盖周末披露), 不应回退 by_trade_date")
    # dividend 的 ex_date 实测周末恒 0 行 (除权机制只落交易日), 不受此 bug 影响, 保持 by_trade_date
    assert reg["domains"]["dividend"]["batch_mode"] == "by_trade_date"
