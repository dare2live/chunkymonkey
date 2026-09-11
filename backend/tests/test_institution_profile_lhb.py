"""institution_profile C3 龙虎榜机构席位加权成本 —— grain 契约 r2b F1/F2 判据.

业主口径 (2026-09-11 批准, 见 scratchpad/fable_grain_contract_r2b.md §1/§3.2):
D1 同股同日同席位买卖金额完全相同的多榜记录按一笔计 (匿名/机构专用同样处理, 可能少算,
已在发布面 fact_top_inst_seat_daily 折叠完成); D2 投资者类别行不计入日频指标 (c3_lhb /
lhb_inst_net); D3 日频指标只计单日榜。三条口径合一为
``services.top_inst_seat_publish.DAILY_METRIC_FILTER_SQL``, 两个日频消费方
(market_pulse.lhb_inst / institution_profile.build_period_windows 的 c3_lhb) 都必须
import 它, 不许各写一份判断字面量 (发布面单一执行点, CLAUDE.md 规则 5)。

只读, 内存 DuckDB, 不连 data/ 下任何库。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services import institution_profile as ip
from services import market_pulse as mp
from services.top_inst_seat_publish import DAILY_METRIC_FILTER_SQL


def _f1_conn():
    """r2b §3.2 F1 fixture, 字面照抄.

    sm.canonical_top10_float_holders_period 一条披露行 (report_date 20240110,
    prev_period 缺 → 回看窗 (report_date-92d, report_date]); mk.v_price_kline_qfq
    两天收盘价 (D=2024-01-05 close 10, D2=2024-01-10 close 20); sm 上的
    fact_top_inst_seat_daily 三行: 机构专用 D single_day |net|100 (唯一该计入的行),
    机构专用 D2 multi_day |net|1e6 (D3 应排除), 机构投资者 D single_day
    investor_category |net|1e6 (D2 应排除, 故意放单日榜以隔离 D2 单独生效)。
    """
    con = duck_mem()
    con.execute("ATTACH ':memory:' AS sm")
    con.execute("ATTACH ':memory:' AS mk")
    con.execute(
        """
        CREATE TABLE sm.canonical_top10_float_holders_period (
            holder_name_norm VARCHAR, holder_name VARCHAR, stock_code VARCHAR,
            report_date VARCHAR, change_status VARCHAR, is_exit_row BOOLEAN,
            shares_approx DOUBLE, hold_change_num DOUBLE, holder_type VARCHAR,
            notice_date VARCHAR, share_class VARCHAR, holder_rank INTEGER,
            row_seq INTEGER, holder_code VARCHAR, is_holder_org BOOLEAN
        )
        """
    )
    con.execute(
        """
        INSERT INTO sm.canonical_top10_float_holders_period VALUES
        ('H1', 'H1', '600000', '20240110', '新进', FALSE, 1000.0, NULL,
         '基金', '20240115', 'A', 1, 1, NULL, NULL)
        """
    )
    con.execute(
        "CREATE TABLE mk.v_price_kline_qfq (code VARCHAR, date VARCHAR, close DOUBLE, volume DOUBLE)"
    )
    con.execute(
        """
        INSERT INTO mk.v_price_kline_qfq VALUES
        ('600000', '2024-01-05', 10.0, 100.0),
        ('600000', '2024-01-10', 20.0, 100.0)
        """
    )
    con.execute(
        """
        CREATE TABLE sm.fact_top_inst_seat_daily (
            trade_date VARCHAR, ts_code VARCHAR, exalter VARCHAR, net_buy DOUBLE,
            board_window VARCHAR, seat_kind VARCHAR
        )
        """
    )
    con.execute(
        """
        INSERT INTO sm.fact_top_inst_seat_daily VALUES
        ('20240105', '600000.SH', '机构专用', 100.0, 'single_day', 'anonymous_inst'),
        ('20240110', '600000.SH', '机构专用', 1000000.0, 'multi_day', 'anonymous_inst'),
        ('20240105', '600000.SH', '机构投资者', 1000000.0, 'single_day', 'investor_category')
        """
    )
    return con


def test_f1_build_period_windows_applies_daily_metric_filter():
    """r2b F1: c3_lhb 只计单日榜且非投资者类别 → 只剩机构专用 D 那一行 (net=100, close=10)
    → c3_lhb == 10.0。不排除 D3 (多日榜) 会被 1e6 的 D2 行拉向 ~20；不排除 D2 (类别行) 会
    被 1e6 的机构投资者行拉偏 (同日同价不改变本例数值, 但会让「谁在贡献权重」变得不可解释——
    真正的证伪门是变异测试: institution_profile 只 LIKE 不加谓词 → 断言 == 10.0 应当失败)。
    """
    con = _f1_conn()
    try:
        n = ip.build_period_windows(con)
        assert n == 1
        row = con.execute(
            "SELECT c3_lhb FROM period_windows "
            "WHERE stock_code = '600000' AND report_date = '20240110'"
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(10.0)
    finally:
        con.close()


def test_f2_daily_metric_filter_sql_wired_into_both_consumers():
    """r2b F2: 两个日频消费方的 SQL 源码里都必须字面含 DAILY_METRIC_FILTER_SQL 这个
    标识符 (import 引用, 不是复制字面量) —— 判据只在发布模块判一次 (CLAUDE.md 规则 5)。
    """
    ip_src = inspect.getsource(ip.build_period_windows)
    mp_src = inspect.getsource(mp._market_sql)
    assert "DAILY_METRIC_FILTER_SQL" in ip_src
    assert "DAILY_METRIC_FILTER_SQL" in mp_src
    # Both modules must import the same object, not a local re-declaration of the string.
    assert ip.DAILY_METRIC_FILTER_SQL is DAILY_METRIC_FILTER_SQL
    assert mp.DAILY_METRIC_FILTER_SQL is DAILY_METRIC_FILTER_SQL
