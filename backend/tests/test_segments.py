"""segments 单测 — 分位分段 SQL 生成 + as-of 查询 (证伪门)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.segments import _case_from_quantile_bands, get_segments
from conftest import duck_mem


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
