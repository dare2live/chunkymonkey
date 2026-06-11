"""sync_runner 单测 — registry 驱动同步器的契约 (宪法 v2 第 6/7/9 条)."""
from __future__ import annotations

import pytest

from services.data_sources import sync_runner as sr


def _registry(**domain_overrides):
    base = {
        "version": 1,
        "defaults": {
            "retry": {"max_attempts": 3, "backoff_seconds": [0, 0, 0]},
            "zero_row_policy": "fail",
            "target_db": "tushare_raw",
        },
        "domains": {
            "demo": {
                "source": "tushare",
                "api": "demo_api",
                "target_table": "raw_tushare_demo",
                "grain": ["ts_code", "trade_date"],
                "batch_mode": "by_trade_date",
                "pit_anchor": "trade_date; t-1",
                "data_start": "20260601",
                "freshness_sla_trading_days": 1,
                "min_rows_per_batch": 2,
                **domain_overrides,
            }
        },
    }
    return base


class FakeAdapter:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def fetch_raw(self, api_name, **params):
        self.calls += 1
        item = self.payloads.pop(0) if self.payloads else []
        if isinstance(item, Exception):
            raise item
        return item


def test_unregistered_domain_rejected():
    """宪法 v2 第 7/9 条: 未注册域 = 不存在."""
    with pytest.raises(KeyError, match="未注册的数据域"):
        sr._domain_spec(sr.load_registry(), "definitely_not_registered")


def test_write_batch_merge_idempotent():
    """grain MERGE: 同批重跑不翻倍 (幂等回填契约)."""
    from services.duck_adapter import connect

    conn = connect(":memory:")
    spec = sr._domain_spec(_registry(), "demo")
    rows = [
        {"ts_code": "000001.SZ", "trade_date": "20260610", "net": 1.0},
        {"ts_code": "000002.SZ", "trade_date": "20260610", "net": 2.0},
    ]
    n1 = sr._write_batch(conn, spec, rows)
    n2 = sr._write_batch(conn, spec, rows)  # 重跑
    total = conn.execute("SELECT COUNT(*) FROM raw_tushare_demo").fetchone()[0]
    assert (n1, n2, total) == (2, 2, 2), "重跑同批必须 MERGE 不翻倍"
    # built_at 必须存在 (PIT 守门列)
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='raw_tushare_demo'"
    ).fetchall()}
    assert "built_at" in cols
    conn.close()


def test_write_batch_schema_evolution():
    """api 新增列 → raw 表自动加列 (镜像语义), 不炸不丢."""
    from services.duck_adapter import connect

    conn = connect(":memory:")
    spec = sr._domain_spec(_registry(), "demo")
    sr._write_batch(conn, spec, [{"ts_code": "a", "trade_date": "20260609", "net": 1.0}])
    sr._write_batch(conn, spec, [{"ts_code": "a", "trade_date": "20260610", "net": 1.0, "new_col": "x"}])
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='raw_tushare_demo'"
    ).fetchall()}
    assert "new_col" in cols
    conn.close()


def test_write_batch_missing_grain_raises():
    from services.duck_adapter import connect

    conn = connect(":memory:")
    spec = sr._domain_spec(_registry(), "demo")
    with pytest.raises(ValueError, match="缺 grain 列"):
        sr._write_batch(conn, spec, [{"ts_code": "a", "net": 1.0}])  # 无 trade_date
    conn.close()


def test_zero_rows_retried_then_failed():
    """宪法 v2 第 6 条: 0 行 = 失败重试, 终败返回 None (入 failure_queue 由调用方)."""
    spec = sr._domain_spec(_registry(), "demo")
    fake = FakeAdapter([[], [], []])  # 三次全空
    out = sr._fetch_with_retry(fake, spec, {"trade_date": "20260610"})
    assert out is None and fake.calls == 3


def test_zero_rows_allowed_when_flagged():
    """allow_empty_batch 条目 (suspend_d 无停牌日) 0 行合法且不重试三次."""
    spec = sr._domain_spec(_registry(allow_empty_batch=True), "demo")
    fake = FakeAdapter([[]])
    out = sr._fetch_with_retry(fake, spec, {"trade_date": "20260610"})
    assert out == [] and fake.calls == 1


def test_retry_recovers_from_intermittent_empty():
    """间歇空响应 (vendor gateway 实测模式): 第二次重试拿到数据."""
    spec = sr._domain_spec(_registry(), "demo")
    good = [{"ts_code": "a", "trade_date": "20260610", "net": 1.0},
            {"ts_code": "b", "trade_date": "20260610", "net": 2.0}]
    fake = FakeAdapter([[], good])
    out = sr._fetch_with_retry(fake, spec, {"trade_date": "20260610"})
    assert out == good and fake.calls == 2
