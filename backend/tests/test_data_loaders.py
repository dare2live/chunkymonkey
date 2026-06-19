"""services.data_loaders 加载器单测 (2026-06-19, A0 地基止血 #1).

用 :memory: DuckDB fixture 验转换逻辑 (不依赖真库, 绕开 tushare_raw 写锁):
  - load_kline: code×date 升序聚合 + limit_stocks
  - load_moneyflow: net/total_flow 计算 + ts_code->code6 + 日期 ISO 化
  - load_quality_reports: ann_date YYYYMMDD->ISO (PIT 锚) + 升序 + metric 注入门
  - in_active_universe: config 驱动板块过滤 (排北交所)
"""
from __future__ import annotations

import pathlib
import sys

import duckdb
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from services.data_loaders import (  # noqa: E402
    in_active_universe,
    load_kline,
    load_moneyflow,
    load_quality_reports,
)


def _kline_conn():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE price_kline_qfq_tushare "
              "(code VARCHAR, date VARCHAR, close DOUBLE, high DOUBLE, low DOUBLE, "
              "open DOUBLE, volume DOUBLE, amount DOUBLE)")
    # 故意乱序插入, 验证 ORDER BY code,date
    c.executemany("INSERT INTO price_kline_qfq_tushare VALUES (?,?,?,?,?,?,?,?)", [
        ("000001", "2020-01-03", 11.0, 11.5, 10.8, 10.9, 200.0, 2200.0),
        ("000001", "2020-01-02", 10.0, 10.5, 9.8, 9.9, 100.0, 1000.0),
        ("600000", "2020-01-02", 20.0, 20.5, 19.8, 19.9, 300.0, 6000.0),
    ])
    return c


def test_load_kline_aggregates_sorted():
    by_code = load_kline("2020-01-01", conn=_kline_conn())
    assert set(by_code) == {"000001", "600000"}
    d = by_code["000001"]
    assert d["date"] == ["2020-01-02", "2020-01-03"]      # 升序
    assert d["close"] == [10.0, 11.0]
    assert d["open"] == [9.9, 10.9] and d["volume"] == [100.0, 200.0]  # execution-aware 列


def test_load_kline_limit_stocks():
    by_code = load_kline("2020-01-01", limit_stocks=1, conn=_kline_conn())
    assert list(by_code) == ["000001"]   # ORDER BY code LIMIT 1


def test_load_moneyflow_net_and_total_flow():
    c = duckdb.connect(":memory:")
    cols = ("ts_code VARCHAR, trade_date VARCHAR, net_mf_amount DOUBLE, "
            "buy_sm_amount DOUBLE, buy_md_amount DOUBLE, buy_lg_amount DOUBLE, buy_elg_amount DOUBLE, "
            "sell_sm_amount DOUBLE, sell_md_amount DOUBLE, sell_lg_amount DOUBLE, sell_elg_amount DOUBLE")
    c.execute(f"CREATE TABLE raw_tushare_moneyflow ({cols})")
    c.execute("INSERT INTO raw_tushare_moneyflow VALUES "
              "('000001.SZ','20200102', 50.0, 10,10,10,10, 5,5,5,5)")   # total_flow=60
    out = load_moneyflow("2020-01-01", conn=c)
    assert "000001" in out                               # ts_code -> code6
    net, flow = out["000001"]["2020-01-02"]              # trade_date -> ISO
    assert net == 50.0 and flow == 60.0                  # net_mf_amount / 全单买卖额和


def test_load_moneyflow_skips_null_net():
    c = duckdb.connect(":memory:")
    cols = ("ts_code VARCHAR, trade_date VARCHAR, net_mf_amount DOUBLE, "
            "buy_sm_amount DOUBLE, buy_md_amount DOUBLE, buy_lg_amount DOUBLE, buy_elg_amount DOUBLE, "
            "sell_sm_amount DOUBLE, sell_md_amount DOUBLE, sell_lg_amount DOUBLE, sell_elg_amount DOUBLE")
    c.execute(f"CREATE TABLE raw_tushare_moneyflow ({cols})")
    c.execute("INSERT INTO raw_tushare_moneyflow VALUES ('000001.SZ','20200102', NULL, 1,1,1,1, 1,1,1,1)")
    assert load_moneyflow("2020-01-01", conn=c) == {}    # net IS NULL 被 WHERE 滤掉


def test_load_quality_reports_anndate_iso_and_order():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE raw_tushare_fina_indicator (ts_code VARCHAR, ann_date VARCHAR, end_date VARCHAR, roe_dt DOUBLE)")
    c.executemany("INSERT INTO raw_tushare_fina_indicator VALUES (?,?,?,?)", [
        ("600000.SH", "20240828", "20240630", 0.18),     # 后披露
        ("600000.SH", "20240430", "20240331", 0.15),     # 先披露
    ])
    out = load_quality_reports(conn=c)
    reps = out["600000"]
    assert reps[0] == ("2024-04-30", "20240331", 0.15)   # ann_date 升序 + ISO 化; end_date 保留 YYYYMMDD
    assert reps[1] == ("2024-08-28", "20240630", 0.18)


def test_load_quality_reports_rejects_bad_metric():
    with pytest.raises(ValueError):
        load_quality_reports(metric="roe; DROP TABLE x")  # 防 SQL 注入门


def test_in_active_universe_config_driven():
    assert in_active_universe("600000")   # 沪主板
    assert in_active_universe("000001")   # 深主板
    assert in_active_universe("300750")   # 创业板
    assert in_active_universe("688981")   # 科创板
    assert not in_active_universe("830799")  # 北交所 (排除)
    assert not in_active_universe("")
