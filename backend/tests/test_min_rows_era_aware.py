"""min_rows_since/min_rows_before 时代分段阈值机制门（历史证据=git log --grep gap_root_cause 全审计HIGH#4）。

背景: margin_detail 的 min_rows_per_batch 从 800 提到 2000(锚定当前基线)后, drain_domain 用
同一个静态值当全历史"完整日"门槛 → 594 个 2019-2021 真实完整日(941-1999行, 两融标的池 2021 年
才扩过 2000)被永久判成幻影缺口反复重拉不收敛。registry 可声明 min_rows_since(该日期起用
min_rows)/min_rows_before(之前时代的地板, 缺省 1), drain 和 run_domain 批次判定都按批次日期
选用对应阈值。本门红绿锁定: (1) drain 对边界前低行数日不再判缺口, 边界后仍正常判; (2)
run_domain 历史回拉批不误报 below_min_rows; (3) 不声明 min_rows_since 时行为完全不变。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import duck_mem
from services.data_sources import sync_runner as sr
from services.source_watermarks import ensure_source_watermark_schema

OLD_DAY, NEW_DAY = "20190104", "20260707"   # 边界(20220104)前后各一天


def _registry(with_era: bool) -> dict:
    entry = {
        "source": "tushare", "api": "margin_detail",
        "target_table": "raw_tushare_md_era_test",
        "grain": ["ts_code", "trade_date"],
        "batch_mode": "by_trade_date",
        "data_start": OLD_DAY,
        "min_rows_per_batch": 2000,
    }
    if with_era:
        entry["min_rows_since"] = "20220104"
        entry["min_rows_before"] = 800
    return {
        "defaults": {
            "target_db": "tushare_raw",
            "fetch_timeout_seconds": 120,
            "execution_policy": {"mode": "enabled", "reason": "active"},
        },
        "domains": {"md_era_test": entry},
    }


class _NoClose:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        pass


def _seed(conn):
    """老时代日 941 行(2019 真实完整量级) + 新时代日 3400 行。"""
    conn.execute("CREATE TABLE raw_tushare_md_era_test (ts_code TEXT, trade_date TEXT)")
    rows = [(f"6{i:05d}.SH", OLD_DAY) for i in range(941)]
    rows += [(f"0{i:05d}.SZ", NEW_DAY) for i in range(3400)]
    conn.executemany("INSERT INTO raw_tushare_md_era_test VALUES (?, ?)", rows)


def test_drain_era_aware_old_complete_day_not_phantom_gap(monkeypatch):
    """green: 声明 min_rows_since 后, 2019 年 941 行完整日不再被 2000 阈值判成缺口。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    _seed(c)
    r = sr.drain_domain("md_era_test", registry=_registry(with_era=True), conn=_NoClose(c),
                         adapter=None, expected_trading_days=[OLD_DAY, NEW_DAY], record=False)
    assert r["status"] == "clean", f"941行的2019完整日不该是缺口: {r}"
    c.close()


def test_drain_without_era_field_old_day_is_phantom_gap(monkeypatch):
    """red 对照: 不声明 min_rows_since(旧行为), 941 行老日被 2000 阈值判成缺口 — 证明
    机制真实生效而非测试恒过(幻影缺口 bug 的最小复现)。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    _seed(c)

    calls = []

    class _A:
        def fetch_raw(self, api, **params):
            calls.append(dict(params))
            return [{"ts_code": f"6{i:05d}.SH", "trade_date": params["trade_date"]}
                    for i in range(941)]   # vendor 重拉也只有 941 行(真实完整量)

    r = sr.drain_domain("md_era_test", registry=_registry(with_era=False), conn=_NoClose(c),
                         adapter=_A(), expected_trading_days=[OLD_DAY, NEW_DAY], record=False)
    assert calls, "旧行为下老日应被判缺口并重拉"
    assert r["status"] == "partial", f"重拉后仍 <2000, 旧行为下永久 partial(幻影缺口): {r}"
    c.close()


def test_drain_era_aware_new_day_truncation_still_caught(monkeypatch):
    """边界后的日仍受 2000 阈值保护: 新时代日只有 1652 行(截断)必须仍被判缺口重拉。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    c.execute("CREATE TABLE raw_tushare_md_era_test (ts_code TEXT, trade_date TEXT)")
    rows = [(f"6{i:05d}.SH", OLD_DAY) for i in range(941)]
    rows += [(f"0{i:05d}.SZ", NEW_DAY) for i in range(1652)]   # 20260703 真实截断值
    c.executemany("INSERT INTO raw_tushare_md_era_test VALUES (?, ?)", rows)

    calls = []

    class _A:
        def fetch_raw(self, api, **params):
            calls.append(dict(params))
            return [{"ts_code": f"0{i:05d}.SZ", "trade_date": params["trade_date"]}
                    for i in range(3472)]   # vendor 真实全量

    r = sr.drain_domain("md_era_test", registry=_registry(with_era=True), conn=_NoClose(c),
                         adapter=_A(), expected_trading_days=[OLD_DAY, NEW_DAY], record=False)
    assert len(calls) == 1 and calls[0]["trade_date"] == NEW_DAY, \
        f"只有新时代截断日该被重拉, 实际: {calls}"
    assert r["status"] == "drained", f"补齐 3472 行后应收敛: {r}"
    c.close()


def test_run_domain_era_aware_no_false_below_min_rows(monkeypatch):
    """run_domain 历史回拉批(2019, 941行)不误报 below_min_rows; 新时代腰斩批(1652行)仍报。"""
    c = duck_mem()
    ensure_source_watermark_schema(c)
    shared = _NoClose(c)

    class _A:
        def fetch_raw(self, api, **params):
            d = params["trade_date"]
            n = 941 if d == OLD_DAY else 1652
            return [{"ts_code": f"6{i:05d}.SH", "trade_date": d} for i in range(n)]

    monkeypatch.setattr(sr, "_adapter", lambda name: _A())
    monkeypatch.setattr(sr, "_target_conn", lambda spec: shared)
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: shared)
    monkeypatch.setattr(sr, "trading_days", lambda start, end=None: [OLD_DAY, NEW_DAY])
    monkeypatch.setattr(sr, "_last_watermark_date", lambda domain, conn=None: None)
    monkeypatch.setattr(sr, "_RATE_LIMITERS", {})

    res = sr.run_domain("md_era_test", backfill=True, registry=_registry(with_era=True))
    assert res["failed_batches"] == 1, \
        f"只有新时代腰斩批该报 below_min_rows(老时代941行是完整), 实际: {res}"
    c.close()
