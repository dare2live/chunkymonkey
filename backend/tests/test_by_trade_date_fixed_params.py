"""by_trade_date + fixed_params 合并回归门 (2026-07-04 根因修复).

背景: run_domain by_trade_date 分支曾只拼 {date_param: d}, 完全丢弃 fixed_params ——
任何声明 fixed_params 的 by_trade_date 域静默失效。此 bug 是在排查"ths_hot 热基子榜"
时发现的 (曾误以为注册独立域 synthetic_fixed_domain + data_type="热基" 能救回热基数据; 后续
逐值实测证实 tushare ths_hot 接口对 data_type 参数已彻底忽略, 该域最终撤销 — 见
sync_registry.yaml ths_hot.dead_groups 注释与 git log --grep r4_completion #1)。
本 bug 修复本身独立成立 (fixed_params 合并是通用架构缺陷), 与"热基"业务判断的
对错无关, 故此处保留合成测试域 (非真实生产域) 验证合并机制。
本门锁定: 批次实际传给 adapter 的 params 必须含 fixed_params 全部键值 + date_param。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema

D = "20260701"

REG = {
    "defaults": {
        "target_db": "tushare_raw",
        "fetch_timeout_seconds": 120,
        "execution_policy": {"mode": "enabled", "reason": "active"},
    },
    "domains": {
        "synthetic_fixed_domain": {
            "source": "tushare", "api": "ths_hot",
            "target_table": "raw_tushare_ths_hot_test",
            "grain": ["trade_date", "data_type", "ts_code"],
            "batch_mode": "by_trade_date",
            "data_start": D,
            "fixed_params": {"data_type": "热基"},
        },
        "no_fixed": {
            "source": "tushare", "api": "daily",
            "target_table": "raw_tushare_daily_test",
            "grain": ["trade_date", "ts_code"],
            "batch_mode": "by_trade_date",
            "data_start": D,
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
    """记录每次 fetch_raw 收到的真实 params, 返回 1 行满足 grain 的数据。"""

    def __init__(self):
        self.calls: list[dict] = []

    def fetch_raw(self, api, **params):
        self.calls.append(dict(params))
        return [{"trade_date": params.get("trade_date", D), "data_type": params.get("data_type", "?"),
                 "ts_code": "TEST.DC"}]


@pytest.fixture()
def env(monkeypatch):
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    adapter = _CapturingAdapter()
    monkeypatch.setattr(sr, "_adapter", lambda name: adapter)
    monkeypatch.setattr(sr, "_target_conn", lambda spec: shared)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: [D])
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, source: None)
    monkeypatch.setattr(sr, "_RATE_LIMITER", None)
    monkeypatch.setattr(sr, "_RATE_LIMITER_INIT", False)
    yield c, adapter
    c.close()


def test_fixed_params_merged_into_by_trade_date_batch(env):
    """核心回归: fixed_params (data_type=热基) 必须出现在实际传给 adapter 的 params 里。"""
    _c, adapter = env
    res = sr.run_domain("synthetic_fixed_domain", registry=REG)
    assert res["ok"] is True
    assert len(adapter.calls) == 1
    call = adapter.calls[0]
    assert call.get("data_type") == "热基", f"fixed_params 未合并, 实际调用参数={call}"
    assert call.get("trade_date") == D


def test_date_param_not_overridden_by_fixed_params(env):
    """date_param 优先级: 批次日期键必须生效, 不被 fixed_params 覆盖 (若 key 冲突)。"""
    _c, adapter = env
    reg = {"defaults": REG["defaults"], "domains": {
        "conflict": {**REG["domains"]["synthetic_fixed_domain"], "fixed_params": {"trade_date": "19000101"}},
    }}
    res = sr.run_domain("conflict", registry=reg)
    assert res["ok"] is True
    assert adapter.calls[0]["trade_date"] == D, "date_param 必须优先于 fixed_params 同名键"


def test_no_fixed_params_domain_unaffected(env):
    """无 fixed_params 声明的域行为不变 (回归防护: 修复不引入意外键)。"""
    _c, adapter = env
    res = sr.run_domain("no_fixed", registry=REG)
    assert res["ok"] is True
    assert adapter.calls[0] == {"trade_date": D}
