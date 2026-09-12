"""institution_profile C3 龙虎榜机构席位加权成本 —— grain 契约 r2b F1/F2/F3 判据.

业主口径 (2026-09-11/09-12 批准, 见 scratchpad/fable_grain_contract_r2b.md §1/§3.2):
D1 同股同日同席位买卖金额完全相同的多榜记录按一笔计 (匿名/机构专用同样处理, 可能少算,
已在发布面 fact_top_inst_seat_daily 折叠完成); D2 投资者类别行不计入日频指标 (c3_lhb /
lhb_inst_net); D3 日频指标只计单日榜; D4 (2026-09-12) 日频指标只算项目股票池内证券
(排除可转债/北交所/B股, 前缀白名单读 services.universe)。四条口径合一为
``services.top_inst_seat_publish.daily_metric_filter_sql()``, 两个日频消费方
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
from services.top_inst_seat_publish import daily_metric_filter_sql


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


def _f3_conn():
    """r2b F3 (业主 2026-09-12 裁定, D4 项目股票池): 北交所 ts_code (前缀 92, 不在
    60/00/30/68 白名单内) 即使 D2/D3 都满足 (single_day/seat) 也必须被排除 ——
    唯一候选行被挡掉后 c3_lhb 应为 NULL (不知道≠0, 不拿池外行硬凑权重)。
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
        ('H1', 'H1', '920001', '20240110', '新进', FALSE, 1000.0, NULL,
         '基金', '20240115', 'A', 1, 1, NULL, NULL)
        """
    )
    con.execute(
        "CREATE TABLE mk.v_price_kline_qfq (code VARCHAR, date VARCHAR, close DOUBLE, volume DOUBLE)"
    )
    con.execute(
        """
        INSERT INTO mk.v_price_kline_qfq VALUES
        ('920001', '2024-01-05', 10.0, 100.0),
        ('920001', '2024-01-10', 20.0, 100.0)
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
        ('20240105', '920001.BJ', '机构专用', 100.0, 'single_day', 'anonymous_inst')
        """
    )
    return con


def test_f3_daily_metric_filter_excludes_out_of_pool_ts_code():
    """r2b F3 (业主 2026-09-12 裁定, D4): 北交所 (前缀 92) 行 D2/D3 都满足, 仍必须被
    项目股票池白名单排除 —— c3_lhb 无其它候选行时应为 NULL, 不是拿池外行硬凑出一个数。
    """
    con = _f3_conn()
    try:
        n = ip.build_period_windows(con)
        assert n == 1
        row = con.execute(
            "SELECT c3_lhb FROM period_windows "
            "WHERE stock_code = '920001' AND report_date = '20240110'"
        ).fetchone()
        assert row is not None
        assert row[0] is None
    finally:
        con.close()


def test_f2_daily_metric_filter_sql_wired_into_both_consumers():
    """r2b F2: 两个日频消费方的 SQL 源码里都必须实际**调用** daily_metric_filter_sql()
    (f-string 插值写法 "{daily_metric_filter_sql()}"), 不许各写一份判断字面量
    (发布面单一执行点, CLAUDE.md 规则 5)。

    故意检查插值写法而不是只查标识符子串: 光查 "daily_metric_filter_sql" 这个子串
    会被同一函数里解释这条口径的**注释**文字骗过 (注释里也会提到这个名字) ——
    某一方把调用换成自己手写的 SUBSTR(...)/board_window 判断、只留着旧注释不删,
    子串检查仍然全绿, 但判据已经被绕过了。要求插值写法 "{daily_metric_filter_sql()}"
    这个更窄的子串, 才能确保测到的是"真的在调用", 不是"提到过这个名字"。
    """
    ip_src = inspect.getsource(ip.build_period_windows)
    mp_src = inspect.getsource(mp._market_sql)
    assert "{daily_metric_filter_sql()}" in ip_src, (
        "institution_profile.build_period_windows 必须在 SQL 里插值调用 "
        "daily_metric_filter_sql(), 不能只在注释里提它"
    )
    assert "{daily_metric_filter_sql()}" in mp_src, (
        "market_pulse._market_sql 必须在 SQL 里插值调用 daily_metric_filter_sql(), "
        "不能只在注释里提它"
    )
    # Both modules must import the same object, not a local re-declaration of the string.
    assert ip.daily_metric_filter_sql is daily_metric_filter_sql
    assert mp.daily_metric_filter_sql is daily_metric_filter_sql
