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


def test_by_ts_code_batches_suffix_mapping(monkeypatch):
    """沪深后缀映射方向防反 (dc_member 方向反教训) + fixed_params 透传。

    2026-07-02 重构 (用户授权"测试合理性优先"): 原版搭假 market 库穿透 manifest→reference→
    security_master 真实依赖链 = 脆弱集成测试 (universe 源迁移/拆库/缓存三次弄坏它)。
    本测试真正要守的语义 = 后缀映射方向 (000001→.SZ / 600519→.SH 不许反), universe 获取
    是 services.universe 单一计算点的职责 (有自己的 test_universe), 此处 mock 掉。
    """
    import services.universe as universe_mod

    monkeypatch.setattr(universe_mod, "get_active_universe",
                        lambda conn, include_st=False: {"600519", "000001", "300750"})
    class _NoopConn:
        def close(self): pass
    monkeypatch.setattr(sr, "_smartmoney_conn", lambda: _NoopConn())

    batches = sr._by_ts_code_batches({"fixed_params": {"period": "20251231"}})
    ts_codes = [b["ts_code"] for b in batches]
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
    assert reg["defaults"]["auth_expiry_warn_days"] == 14
    # 截断防线: dc 系必须声明 page_limit (catalog 单次上限 5000 实锤)
    assert d["dc_member"]["page_limit"] == 5000
    assert d["dc_index"]["page_limit"] == 5000
    # 截断防线 (2026-07-05 R4 静默截断实锤三例, 宪法第6条同型反例):
    # index_dailybasic 无声明时 16 年全量请求被截最近~3000行(早4年历史丢失);
    # share_float 无声明时全库 390 交易日卡在 5900-6021 (实测真实总量 >=12000, 部分日 >=30916);
    # block_trade 无声明时 20250918 实测 1001 行 (offset=1000 仍返 1 行) 恰好压线 1000 上限。
    assert d["index_dailybasic"]["page_limit"] == 3000
    assert d["share_float"]["page_limit"] == 6000
    assert d["block_trade"]["page_limit"] == 1000
    assert d["trade_cal"]["page_limit"] == 6000
    assert d["trade_cal"]["write_mode"] == "replace_snapshot"
    assert d["stock_basic"]["write_mode"] == "replace_snapshot"
    assert d["trade_cal"]["freshness_date_column"] == "cal_date"
    assert d["trade_cal"]["fixed_params"] == {"exchange": "SSE"}
    assert d["margin_detail"]["batch_completeness"]["required_groups"] == ["SH", "SZ"]
    assert "split_by" not in d["margin_detail"]  # API catalog 无 exchange 参数；传入也被 provider 忽略
    assert d["stk_factor_pro"]["sync_policy"] == "on_demand"
    assert "fixed_params" not in d["stk_factor_pro"]
    # 2026-07-06 全面数据审计实锤第 N 例: stk_limit 全市场(股票+ETF+B股+北交所混合)总量增长
    # 跨过服务端隐式单页上限(实测 limit=6000 仍只回 5800, offset=5800 page2 再回 1877 行),
    # 20260615 起 603/605/688/689/601 前缀连续 3 周静默丢 ~1500 行/天, 现有 continuity gate
    # 因行数比值卡在阈值上沿而无感。
    assert d["stk_limit"]["page_limit"] == 5000
    # 白跑防线: 2019 年截面 ~3700 只, min_rows 必须 <= 3000
    assert d["daily"]["min_rows_per_batch"] <= 3000
    assert d["adj_factor"]["min_rows_per_batch"] <= 3000
    # drain 收敛: moneyflow_ind_dc 2024 时代实测 86 行/日为合法全量 — 2026-07-09 起该保证由
    # 时代分段机制承担(min_rows_before 管 2026 前老时代, min_rows_per_batch 管当前时代 ~1000
    # 行基线的中间态截断检测), 契约改为: 老时代阈值 <= 86 且分段边界已声明
    assert d["moneyflow_ind_dc"]["min_rows_before"] <= 86
    assert d["moneyflow_ind_dc"]["min_rows_since"] == "20260101"
    assert d["moneyflow_ind_dc"]["min_rows_per_batch"] >= 500  # 当前时代必须有真实检测力
    # 范围决策: 未批准历史段不进 drain expected (改回更早值必须走 chain10 决策)
    # 2026-07-05 二次收窄, 对齐 daily(K线真相源)边界 20190102: R4 K线边界孤立数据审计确认
    # top_list/top_inst/cyq_perf/index_dailybasic 的唯一实质消费方都按 trade_date 精确等值
    # JOIN daily 派生视图(mk.v_price_kline_qfq / market_pulse days轴), 边界前数据物删。
    assert d["top_list"]["data_start"] == "20190102"
    assert d["top_inst"]["data_start"] == "20190102"
    assert d["adj_factor"]["data_start"] == "20190102"
    assert d["cyq_perf"]["data_start"] == "20190102"
    assert d["index_dailybasic"]["data_start"] == "20190102"
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


def test_fetch_paged_identical_page_order_insensitive(monkeypatch):
    # 判官修订实证: 网关返回同一全量但行序漂移 — 位置比较失明, 集合签名必须命中
    monkeypatch.setattr(sr.time, "sleep", lambda s: None)
    page_a = _rows(3, "x")
    page_b = list(reversed(page_a))  # 同内容, 反序
    ad = _PagedAdapter([page_a, page_b])
    out = sr._fetch_paged(ad, _paged_spec(), {"trade_date": "20180112"})
    assert out is None  # len==limit 整倍数 + 内容相同 (无视行序) = fail-closed
    assert ad.calls == 2


def test_write_batch_widens_first_batch_inferred_types(monkeypatch, tmp_path):
    # 首批类型推断陷阱回归 (chain9 三案): 首批整数 -> INT32 列, 次批大数/字符串必须自动加宽
    import duckdb as _duck
    from services.duck_adapter import connect as _connect

    conn = _connect(str(tmp_path / "raw.duckdb"))
    spec = {"domain": "demo", "target_table": "raw_demo", "grain": ["k"], "api": "demo"}
    monkeypatch.setattr(sr, "_capture_domain_sample", lambda s, r: None)
    try:
        # 首批: 小整数 -> 可能推断 INTEGER
        conn.execute("CREATE TABLE raw_demo (k VARCHAR, v INTEGER)")  # 模拟已存在的窄类型表
        n1 = sr._write_batch(conn, spec, [{"k": "a", "v": 1}])
        assert n1 == 1
        # 次批: 164.7 亿 (溢 INT32) + 字符串列场景
        n2 = sr._write_batch(conn, spec, [{"k": "b", "v": 16472341619.53}])
        assert n2 == 1
        rows = dict(conn.execute("SELECT k, v FROM raw_demo ORDER BY k").fetchall())
        assert rows["b"] == 16472341619.53  # 值无损
        dt = conn.execute("SELECT data_type FROM information_schema.columns WHERE table_name='raw_demo' AND column_name='v'").fetchone()[0]
        assert dt == "DOUBLE"
    finally:
        conn.close()


def test_write_batch_widens_null_inferred_string_column(monkeypatch, tmp_path):
    # suspend_timing 案: 首批全 NULL -> INT32, 次批字符串 '09:30-10:00' 必须进得去
    from services.duck_adapter import connect as _connect

    conn = _connect(str(tmp_path / "raw2.duckdb"))
    spec = {"domain": "demo", "target_table": "raw_demo2", "grain": ["k"], "api": "demo"}
    monkeypatch.setattr(sr, "_capture_domain_sample", lambda s, r: None)
    try:
        conn.execute("CREATE TABLE raw_demo2 (k VARCHAR, suspend_timing INTEGER)")
        n = sr._write_batch(conn, spec, [{"k": "x", "suspend_timing": "09:30-10:00"}])
        assert n == 1
        v = conn.execute("SELECT suspend_timing FROM raw_demo2").fetchone()[0]
        assert v == "09:30-10:00"
    finally:
        conn.close()
