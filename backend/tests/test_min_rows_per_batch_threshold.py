"""min_rows_per_batch 静态地板值校准回归（历史证据=analysis/gap_root_cause_20260708.md）。

背景: margin_detail 的 min_rows_per_batch 曾设 800(锚定 2019 建域首日历史最低点), 但 2026 年真实
基线已涨到 ~3400+, 800 这个地板值对"腰斩但仍非零"的截断(实测 20260703: 3472→1652)完全测不出——
1652 > 800, 照样判合法批不进 failure_queue。本门锁定: 同一批次行数, 用旧地板值(800)判定为合法,
用新地板值(2000)判定为 below_min_rows 并在写前拒绝整批——红绿对照证明阈值
提高是有效的, 不是摆设。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema

D = "20260703"
TRUNCATED_ROW_COUNT = 1652   # 实测 2026-07-08 发现的真实截断行数


def _registry(min_rows: int) -> dict:
    return {
        "defaults": {
            "target_db": "tushare_raw",
            "fetch_timeout_seconds": 120,
            "execution_policy": {"mode": "enabled", "reason": "active"},
        },
        "domains": {
            "margin_detail_test": {
                "source": "tushare", "api": "margin_detail",
                "target_table": "raw_tushare_margin_detail_test",
                "grain": ["ts_code", "trade_date"],
                "batch_mode": "by_trade_date",
                "data_start": D,
                "min_rows_per_batch": min_rows,
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


class _TruncatedAdapter:
    """模拟 20260703 那次真实截断: 只返回 1652 行(满足 grain 唯一)。"""

    def fetch_raw(self, api, **params):
        return [{"trade_date": params.get("trade_date", D), "ts_code": f"60{i:04d}.SH"}
                for i in range(TRUNCATED_ROW_COUNT)]


@pytest.fixture()
def env(monkeypatch):
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)
    adapter = _TruncatedAdapter()
    monkeypatch.setattr(sr, "_adapter", lambda name: adapter)
    monkeypatch.setattr(sr, "_target_conn", lambda spec: shared)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: [D])
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, source: None)
    monkeypatch.setattr(sr, "_RATE_LIMITER", None)
    monkeypatch.setattr(sr, "_RATE_LIMITER_INIT", False)
    yield c
    c.close()


def test_old_800_threshold_misses_the_real_truncation(env):
    """red: 旧地板值 800 对 1652 行截断视而不见, ok=True 不进 failure_queue。"""
    res = sr.run_domain("margin_detail_test", registry=_registry(min_rows=800))
    assert res["ok"] is True
    assert res["failed_batches"] == 0
    assert res["rows"] == TRUNCATED_ROW_COUNT


def test_new_2000_threshold_catches_the_same_truncation(env):
    """green: 新地板值 2000 同一批次(1652行)判 below_min_rows, ok=False 进 failure_queue,
    且在 DELETE/INSERT 前 fail closed，不能让残缺批覆盖旧数据。"""
    res = sr.run_domain("margin_detail_test", registry=_registry(min_rows=2000))
    assert res["ok"] is False
    assert res["failed_batches"] == 1
    assert res["rows"] == 0
    exists = env.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name='raw_tushare_margin_detail_test'"
    ).fetchone()[0]
    assert exists == 0


def test_registry_margin_detail_threshold_is_2000():
    """真实 registry 声明校验: margin_detail 确实已从 800 提到 2000, 防回潮。"""
    from pathlib import Path as _P

    import yaml
    reg = yaml.safe_load((_P(__file__).resolve().parents[2] / "backend" / "config"
                           / "sync_registry.yaml").read_text(encoding="utf-8"))
    assert reg["domains"]["margin_detail"]["min_rows_per_batch"] == 2000
