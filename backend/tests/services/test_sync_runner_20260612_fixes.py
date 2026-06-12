"""2026-06-12 chain7 善后三修复的防回退测试.

1. by_ts_code 批清单: smartmoney 连接必须 ATTACH market (chain7 step3 fina_mainbz
   0 行回填的确定性 bug — get_conn 不挂 market, Binder Error)。
2. 日历 clamp 显式告警: data_start 早于日历首日不许静默 (top_list 2005-2022 反例)。
3. registry 手术契约: page_limit/min_rows/data_start 的 2026-06-12 决策值钉死,
   改动必须过 git diff (防口头放宽)。
"""
from __future__ import annotations

import logging

import duckdb
import pytest

from services.data_sources import sync_runner as sr


def test_by_ts_code_batches_attaches_market(tmp_path, monkeypatch):
    market = tmp_path / "market.duckdb"
    conn = duckdb.connect(str(market))
    conn.execute("CREATE TABLE price_kline_tdxhub (code VARCHAR, date DATE, freq VARCHAR)")
    for code in ("600519", "000001", "300750", "830001"):
        conn.execute(
            "INSERT INTO price_kline_tdxhub VALUES (?, current_date - 1, 'daily')", [code]
        )
    conn.close()

    class FakeManifest:
        def path_for(self, alias):
            assert alias == "market"
            return market

    import services.database_manifest as dm

    monkeypatch.setattr(dm, "get_database_manifest", lambda: FakeManifest())
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: duckdb.connect(":memory:"))

    batches = sr._by_ts_code_batches({"fixed_params": {"period": "20251231"}})
    ts_codes = [b["ts_code"] for b in batches]
    # 北交所 830001 不在 universe; 沪深映射方向防反
    assert ts_codes == ["000001.SZ", "300750.SZ", "600519.SH"]
    assert all(b["period"] == "20251231" for b in batches)


def test_warn_if_clamped_emits_on_clamp(caplog):
    with caplog.at_level(logging.WARNING):
        sr._warn_if_clamped("top_list", "20050104", ["20230103", "20230104"])
    assert any("被日历 clamp" in r.message and "top_list" in r.message for r in caplog.records)


def test_warn_if_clamped_silent_when_aligned(caplog):
    with caplog.at_level(logging.WARNING):
        sr._warn_if_clamped("top_list", "20230103", ["20230103", "20230104"])
    assert not caplog.records


def test_warn_if_clamped_empty_window(caplog):
    with caplog.at_level(logging.WARNING):
        sr._warn_if_clamped("demo", "20990101", [])
    assert any("零交易日" in r.message for r in caplog.records)


def test_registry_surgery_contract_20260612():
    reg = sr.load_registry()
    d = reg["domains"]
    # 截断防线: dc 系必须声明 page_limit (catalog 单次上限 5000 实锤)
    assert d["dc_member"]["page_limit"] == 5000
    assert d["dc_index"]["page_limit"] == 5000
    # 白跑防线: 2019 年截面 ~3700 只, min_rows 必须 <= 3000
    assert d["daily"]["min_rows_per_batch"] <= 3000
    assert d["adj_factor"]["min_rows_per_batch"] <= 3000
    # drain 收敛: moneyflow_ind_dc 实测 86 行/日为合法全量
    assert d["moneyflow_ind_dc"]["min_rows_per_batch"] <= 86
    # 范围决策: 未批准历史段不进 drain expected (改回更早值必须走 chain10 决策)
    assert d["top_list"]["data_start"] == "20180102"
    assert d["top_inst"]["data_start"] == "20180102"
    assert d["adj_factor"]["data_start"] == "20190102"
    assert d["cyq_perf"]["data_start"] == "20230103"
    assert d["limit_list_d"]["data_start"] == "20230103"


class _PagedAdapter:
    """模拟 vendor 网关的三种分页病理."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    def fetch_raw(self, api_name, **params):
        idx = min(self.calls, len(self.pages) - 1)
        self.calls += 1
        return self.pages[idx]


def _paged_spec():
    return {"domain": "demo", "api": "demo", "page_limit": 3,
            "retry": {"max_attempts": 1, "backoff_seconds": [0]}}


def _rows(n, tag):
    return [{"ts_code": f"{tag}{i}", "v": i} for i in range(n)]


def test_fetch_paged_gateway_ignores_limit_returns_full(monkeypatch):
    # 首页 5 行 > limit 3 = 网关无视 limit 给全量 → 单页收齐, 只烧 1 发
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    ad = _PagedAdapter([_rows(5, "a")])
    out = sr._fetch_paged(ad, _paged_spec(), {"trade_date": "20180112"})
    assert len(out) == 5 and ad.calls == 1


def test_fetch_paged_identical_page_nonmultiple_takes_first(monkeypatch):
    # offset 失效 + 行数恰为 limit 整倍数 → 无法区分全量与截断, 必须 fail-closed
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    page3 = _rows(3, "c")
    ad = _PagedAdapter([page3, page3])
    out = sr._fetch_paged(ad, _paged_spec(), {"trade_date": "20180112"})
    assert out is None  # 整倍数 + offset 失效 = 疑截断, 拒收 (dc_member 整 5000 pin 反例)
    assert ad.calls == 2  # 不再烧到 50 页


def test_fetch_paged_real_pagination_still_works(monkeypatch):
    # 真分页: 两页不同, 末页短 → 正常拼接
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    ad = _PagedAdapter([_rows(3, "p1"), _rows(2, "p2")])
    out = sr._fetch_paged(ad, _paged_spec(), {"trade_date": "20180112"})
    assert len(out) == 5 and ad.calls == 2
