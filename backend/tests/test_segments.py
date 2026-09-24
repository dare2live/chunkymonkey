"""segments 单测 — 分位分段 SQL 生成 + as-of 查询 (证伪门)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.segments import _case_from_quantile_bands, get_segments, rebuild_all
from conftest import duck_mem

# cut_drop_helper_indexes 最小 rebuild_all 环境 (tr/mkt 两部名同解析生产 ATTACH)。
_SEG_DDL = """
CREATE SCHEMA tr;
CREATE SCHEMA mkt;
CREATE TABLE tr.raw_tushare_daily_basic (
    ts_code TEXT, trade_date TEXT, circ_mv DOUBLE, turnover_rate DOUBLE);
CREATE TABLE mkt.price_kline_qfq_tushare (code TEXT, date TEXT, close DOUBLE);
CREATE TABLE tr.raw_tushare_index_member_all (
    l1_code TEXT, l1_name TEXT, l2_code TEXT, l2_name TEXT, l3_code TEXT, l3_name TEXT,
    ts_code TEXT, name TEXT, in_date TEXT, out_date TEXT, is_new TEXT);
CREATE VIEW tr.v_sw_industry_pit AS
SELECT SPLIT_PART(ts_code, '.', 1) AS stock_code, ts_code, name,
       l1_code, l1_name, l2_code, l2_name, l3_code, l3_name,
       in_date, CAST(out_date AS VARCHAR) AS out_date, is_new
FROM tr.raw_tushare_index_member_all;
"""

_SEG_CFG = {
    "mktcap_segments": {"small": [0.0, 0.5], "large": [0.5, 1.0]},
    "turnover_segments": {"low": [0.0, 0.5], "high": [0.5, 1.0]},
    "vol_regime": {"kline_table": "price_kline_qfq_tushare",
                   "rv_return_window": 2, "rv_pctile_window": 2, "threshold": 0.5},
    "data_start": "20240101",
}


def test_case_bands_boundary_semantics():
    """右开区间, 末段闭合: rank=0.2 落 small (非 micro), rank=1.0 落 large。"""
    sql = _case_from_quantile_bands(
        {"micro": [0.0, 0.2], "small": [0.2, 0.5], "mid": [0.5, 0.8], "large": [0.8, 1.0]}, "r")
    c = duck_mem()
    try:
        for rank, expect in [(0.0, "micro"), (0.19, "micro"), (0.2, "small"),
                             (0.5, "mid"), (0.8, "large"), (1.0, "large")]:
            got = c.execute(f"SELECT {sql} FROM (SELECT ? AS r)", [rank]).fetchone()[0]
            assert got == expect, f"rank={rank}: {got} != {expect}"
    finally:
        c.close()


def test_get_segments_asof_picks_latest_leq(monkeypatch):
    """as-of 语义: 取 <= as_of 的最近交易日标签 (周末查询回退周五)。"""
    c = duck_mem()
    c.executescript("""CREATE TABLE dim_stock_segment_daily (
        stock_code TEXT, trade_date TEXT, mktcap_seg TEXT, turnover_seg TEXT, sw_l1 TEXT,
        circ_mv DOUBLE, turnover_rate DOUBLE)""")
    c.execute("INSERT INTO dim_stock_segment_daily VALUES ('600000','20260626','large','low','银行',1,1)")
    c.execute("INSERT INTO dim_stock_segment_daily VALUES ('600000','20260701','mid','high','银行',1,1)")
    try:
        out = get_segments(["600000"], "20260628", conn=c)  # 周末 → 回退 0626
        assert out["600000"]["mktcap_seg"] == "large"
        out = get_segments(["600000"], "20260702", conn=c)  # → 0701
        assert out["600000"]["mktcap_seg"] == "mid"
    finally:
        c.close()


def test_rebuild_all_no_helper_index():
    """cut_drop_helper_indexes: dim_stock_segment_daily 不带任何 ART 索引 —— 追加后每次
    CHECKPOINT 整棵索引重写, 实测 ~397MB/次日更空洞, 单股查询中位数与有索引持平 (spec_drop_helper_indexes.md)。
    变异: 把 CREATE INDEX 加回 rebuild_all → 本用例红。"""
    c = duck_mem()
    c.executescript(_SEG_DDL)
    c.execute("INSERT INTO tr.raw_tushare_daily_basic VALUES ('600000.SH','20240101',10.0,1.0)")
    try:
        rebuild_all(conn=c, cfg=_SEG_CFG)
        n_idx = c.execute("SELECT count(*) FROM duckdb_indexes() WHERE table_name = 'dim_stock_segment_daily'").fetchone()[0]
        assert n_idx == 0
    finally:
        c.close()
