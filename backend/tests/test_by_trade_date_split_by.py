"""by_trade_date + split_by 根因修复门（历史证据=git log --grep gap_root_cause）。

背景: margin 域裸调 pro.margin(trade_date=d) 在 2026 年偶发(~0.5%交易日)漏返 BSE/SZSE
(vendor 网关对"无过滤条件汇总查询"的补全遗漏怪癖, 非分页截断非披露滞后)——显式加
exchange_id=SSE/SZSE/BSE 逐个查询可 100% 拿全(含历史已发生的漏返日期实测验证)。
split_by: {param, values} 在一个日期级逻辑批内部执行 len(values) 个 API 调用；只有全部
分片成功并满足 group contract 后才允许一次原子写。本门锁定调用展开、日期级原子性和
无 split_by 声明域的原有行为。
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
        "retry": {"max_attempts": 1, "backoff_seconds": [0]},
    },
    "domains": {
        "synthetic_split_domain": {
            "source": "tushare", "api": "margin",
            "target_table": "raw_tushare_margin_test",
            "grain": ["trade_date", "exchange_id"],
            "batch_mode": "by_trade_date",
            "data_start": D,
            "split_by": {"param": "exchange_id", "values": ["SSE", "SZSE", "BSE"]},
            "min_rows_per_batch": 3,
            "batch_completeness": {
                "group_from": {"column": "exchange_id", "transform": "identity"},
                "required_groups": ["SSE", "SZSE", "BSE"],
            },
        },
        "no_split": {
            "source": "tushare", "api": "daily",
            "target_table": "raw_tushare_daily_test2",
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
    """记录每次 fetch_raw 收到的真实 params, 每次返回 1 行满足 grain 的数据。"""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail_exchange: str | None = None

    def fetch_raw(self, api, **params):
        self.calls.append(dict(params))
        if self.fail_exchange is not None and params.get("exchange_id") == self.fail_exchange:
            return []
        return [{"trade_date": params.get("trade_date", D),
                  "exchange_id": params.get("exchange_id", "?"),
                  "ts_code": params.get("ts_code", "TEST.DC")}]


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
    monkeypatch.setattr(sr.time, "sleep", lambda _seconds: None)
    yield c, adapter
    c.close()


def test_split_by_expands_one_day_into_n_calls(env):
    """核心回归: 1 个交易日 + 3 个 split values = 3 次独立 adapter 调用, 各带正确的值。"""
    _c, adapter = env
    res = sr.run_domain("synthetic_split_domain", registry=REG)
    assert res["ok"] is True
    assert res["batches"] == 1, "三个 provider 分片仍属于一个日期级逻辑批"
    assert len(adapter.calls) == 3, f"应展开成 3 次调用, 实际 {len(adapter.calls)}: {adapter.calls}"
    exchange_ids = {c["exchange_id"] for c in adapter.calls}
    assert exchange_ids == {"SSE", "SZSE", "BSE"}
    for call in adapter.calls:
        assert call["trade_date"] == D


def test_split_by_rows_written_per_grain_no_dedup_collision(env):
    """3 次调用各写 1 行, grain=[trade_date,exchange_id] 互不冲突, 本地应有 3 行。"""
    c, _adapter = env
    res = sr.run_domain("synthetic_split_domain", registry=REG)
    assert res["rows"] == 3
    n = c.execute("SELECT COUNT(*) FROM raw_tushare_margin_test").fetchone()[0]
    assert n == 3
    codes = {r[0] for r in c.execute("SELECT exchange_id FROM raw_tushare_margin_test").fetchall()}
    assert codes == {"SSE", "SZSE", "BSE"}


def test_split_by_failure_keeps_existing_logical_day_untouched(env):
    """任一交易所分片失败时，已返回分片不得先写，旧完整日必须原样保留。"""
    c, adapter = env
    c.execute(
        "CREATE TABLE raw_tushare_margin_test "
        "(trade_date VARCHAR, exchange_id VARCHAR, ts_code VARCHAR, built_at VARCHAR)"
    )
    c.executemany(
        "INSERT INTO raw_tushare_margin_test VALUES (?, ?, ?, 'old')",
        [(D, exchange, "OLD.DC") for exchange in ("SSE", "SZSE", "BSE")],
    )
    adapter.fail_exchange = "SZSE"

    res = sr.run_domain("synthetic_split_domain", registry=REG)

    assert res["ok"] is False and res["failed_batches"] == 1
    rows = c.execute(
        "SELECT exchange_id, ts_code, built_at FROM raw_tushare_margin_test ORDER BY 1"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("BSE", "OLD.DC", "old"),
        ("SSE", "OLD.DC", "old"),
        ("SZSE", "OLD.DC", "old"),
    ]


def test_no_split_by_domain_unaffected(env):
    """无 split_by 声明的域行为不变 (回归防护: 修复不引入意外行为)。"""
    _c, adapter = env
    res = sr.run_domain("no_split", registry=REG)
    assert res["ok"] is True
    assert len(adapter.calls) == 1
    assert adapter.calls[0] == {"trade_date": D}


@pytest.mark.parametrize(
    "trade_date,expected",
    [
        ("20211115", ["SSE", "SZSE"]),
        ("20230210", ["SSE", "SZSE"]),
        ("20230213", ["SSE", "SZSE", "BSE"]),
    ],
)
def test_split_by_respects_exact_margin_business_start_boundary(
    monkeypatch, trade_date, expected
):
    """北交所两融边界必须精确：启动前不请求，启动日起成为必需分片。"""
    calls = []

    class Adapter:
        def fetch_raw(self, _api, **params):
            calls.append(dict(params))
            return [{"trade_date": params["trade_date"], "exchange_id": params["exchange_id"]}]

    spec = {
        "domain": "margin_like",
        "api": "margin",
        "date_param": "trade_date",
        "split_by": {
            "param": "exchange_id",
            "values": ["SSE", "SZSE", "BSE"],
        },
        "batch_completeness": {"required_groups_since": {"BSE": "20230213"}},
        "retry": {"max_attempts": 1, "backoff_seconds": [0]},
    }
    rows = sr._fetch_logical_batch(Adapter(), spec, {"trade_date": trade_date})
    assert [row["exchange_id"] for row in rows] == expected
    assert [call["exchange_id"] for call in calls] == expected


def test_production_margin_business_start_has_one_config_owner():
    import yaml

    registry = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "backend/config/sync_registry.yaml").read_text()
    )
    margin = registry["domains"]["margin"]
    assert margin["batch_completeness"]["required_groups_since"] == {
        "BSE": "20230213"
    }
    assert "values_since" not in margin["split_by"]
    assert "min_rows_since" not in margin and margin["min_rows_per_batch"] == 2
