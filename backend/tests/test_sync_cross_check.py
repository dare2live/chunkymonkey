"""allow_empty 交叉参照门单测 (R1 件3, 2026-07-03).

锁: allow_empty 域 0 行批 + 交叉域 (cross_check_domain) 同日行数 > 阈值 → suspicious_empty
入 failure_queue + 计 failed_batches (不当合法空); 交叉域同空/表缺 → 合法空照旧放行。
背景: top_inst 16 缺日源端全有数据被 allow_empty 吞 (audit data_foundation_audit_20260703),
drain 对 allow_empty 域 drain_inapplicable → 永不自愈, 本门是唯一在线检测点。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema

D = "20260610"   # top_inst 实缺日之一 (audit 实测同日 top_list 有 75-126 行)

REG = {
    "defaults": {"target_db": "tushare_raw"},
    "domains": {
        "top_inst": {
            "source": "tushare", "api": "top_inst",
            "target_table": "raw_tushare_top_inst",
            "grain": ["trade_date", "ts_code", "exalter", "side"],
            "batch_mode": "by_trade_date",
            "data_start": D,
            "allow_empty_batch": True,
            "cross_check_domain": "top_list",
        },
        "top_list": {
            "source": "tushare", "api": "top_list",
            "target_table": "raw_tushare_top_list",
            "grain": ["trade_date", "ts_code", "reason"],
            "batch_mode": "by_trade_date",
            "data_start": D,
        },
    },
}


class _NoClose:
    """共享内存库连接包装: sync_runner 各处 finally close 不真关, 测试可继续断言。"""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        pass


class _EmptyAdapter:
    def fetch_raw(self, api, **params):
        return []


@pytest.fixture()
def env(monkeypatch):
    c = duck_mem()
    c.execute("CREATE TABLE raw_tushare_top_list (trade_date TEXT, ts_code TEXT, reason TEXT)")
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    monkeypatch.setattr(sr, "_adapter", lambda name: _EmptyAdapter())
    monkeypatch.setattr(sr, "_target_conn", lambda spec: shared)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: [D])
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, source: None)
    monkeypatch.setattr(sr, "_RATE_LIMITER", None)
    monkeypatch.setattr(sr, "_RATE_LIMITER_INIT", False)
    yield c
    c.close()


def _fill_cross(c, n: int) -> None:
    c.executemany("INSERT INTO raw_tushare_top_list VALUES (?, ?, 'r')",
                  [(D, f"{600000 + i}.SH") for i in range(n)])


def test_suspicious_empty_enters_failure_queue(env):
    """0 行 + 交叉域 100 行 (> 默认阈值 50) → failed_batches=1 + suspicious_empty 入队。"""
    _fill_cross(env, 100)
    res = sr.run_domain("top_inst", registry=REG)
    assert res["ok"] is False and res["failed_batches"] == 1
    q = env.execute(
        "SELECT data_domain, status, last_error FROM mart_data_source_failure_queue "
        "WHERE error_type = 'suspicious_empty'").fetchall()
    assert len(q) == 1
    assert q[0][0] == "sync:top_inst" and q[0][1] == "open"
    payload = json.loads(q[0][2])
    assert payload["cross_check_domain"] == "top_list"
    assert payload["cross_rows"] == 100 and payload["params"]["trade_date"] == D


def test_legal_empty_when_cross_also_empty(env):
    """交叉域同日也空 = 真·市场无数据 → 合法空照旧成功, 无 suspicious_empty。"""
    res = sr.run_domain("top_inst", registry=REG)
    assert res["ok"] is True and res["failed_batches"] == 0
    n = env.execute("SELECT COUNT(*) FROM mart_data_source_failure_queue "
                    "WHERE error_type = 'suspicious_empty'").fetchone()[0]
    assert n == 0


def test_threshold_is_strictly_greater(env):
    """交叉域恰 50 行 (= 默认阈值) → 不算可疑 (门语义: 严格大于)。"""
    _fill_cross(env, 50)
    res = sr.run_domain("top_inst", registry=REG)
    assert res["ok"] is True and res["failed_batches"] == 0


def test_cross_table_missing_is_legal_empty(env):
    """交叉表不存在 (交叉域从未拉过) → 无法判定, 合法空放行 (不 raise 不误伤)。"""
    env.execute("DROP TABLE raw_tushare_top_list")
    res = sr.run_domain("top_inst", registry=REG)
    assert res["ok"] is True and res["failed_batches"] == 0


def test_known_empty_tombstone_bypasses_gate(env):
    """known_empty_days 墓碑日 (实测核证源端真空) 不进可疑判定 — 否则真空日永久失败循环
    (与 drain 的墓碑语义一致; 新增墓碑前必实测源端确认空, 不可拿它掩盖真失败)。"""
    reg = {"defaults": dict(REG["defaults"]),
           "domains": {**{k: dict(v) for k, v in REG["domains"].items()}}}
    reg["domains"]["top_inst"]["known_empty_days"] = [D]
    _fill_cross(env, 100)
    res = sr.run_domain("top_inst", registry=reg)
    assert res["ok"] is True and res["failed_batches"] == 0


def test_domain_without_cross_check_unaffected(env):
    """未声明 cross_check_domain 的 allow_empty 域行为不变 (0 行 = 合法空)。"""
    reg = {"defaults": dict(REG["defaults"]), "domains": {
        "suspend_d": {"source": "tushare", "api": "suspend_d",
                      "target_table": "raw_tushare_suspend_d",
                      "grain": ["ts_code", "trade_date", "suspend_type"],
                      "batch_mode": "by_trade_date", "data_start": D,
                      "allow_empty_batch": True}}}
    _fill_cross(env, 100)   # 交叉表有数据也无关 — 该域没声明交叉
    res = sr.run_domain("suspend_d", registry=reg)
    assert res["ok"] is True and res["failed_batches"] == 0


def test_real_registry_declares_cross_checks():
    """生产 registry 接线契约: top_inst→top_list / block_trade→daily, 且交叉域真实注册。"""
    reg = sr.load_registry()
    assert reg["domains"]["top_inst"].get("cross_check_domain") == "top_list"
    assert reg["domains"]["block_trade"].get("cross_check_domain") == "daily"
    for cross in ("top_list", "daily"):
        assert cross in reg["domains"], f"交叉域 {cross} 必须已注册"
        assert not reg["domains"][cross].get("allow_empty_batch"), \
            "交叉参照域自身必须非 allow_empty (否则两域同吞时门失明)"


def test_universe_filter_all_dropped_code_shape_fingerprint():
    """假阳性根治 (2026-07-03 share_float 两撞): 被丢行像股票代码=合法长尾 (任意行数);
    不像 (日期样)=filter 列配错才 raise。"""
    import pandas as pd
    from services.data_sources import sync_runner as sr

    spec = {"domain": "t_test_dom", "universe_filter": True, "grain": ["ts_code", "ann_date"], "target_table": "t_test"}
    bj_batch = pd.DataFrame({"ts_code": [f"83518{i}.BJ" for i in range(8)], "ann_date": ["20240101"] * 8})
    misconfig = pd.DataFrame({"ts_code": ["20240101", "20240102"], "ann_date": ["20240101"] * 2})

    class _FakeConn:
        def execute(self, *a, **k):
            raise AssertionError("空批不应触 SQL")
        def register(self, *a, **k): pass
        def unregister(self, *a, **k): pass

    assert sr._write_batch(_FakeConn(), spec, bj_batch.to_dict("records")) == 0  # 8 行全 BJ 合法
    import pytest as _pt
    with _pt.raises(ValueError, match="不像股票代码"):
        sr._write_batch(_FakeConn(), spec, misconfig.to_dict("records"))
