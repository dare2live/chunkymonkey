"""episode 引擎测试 — 合成数据验证 episode 构建/一字板剔/条件化出场/segment/含成本。"""
import numpy as np
import pytest

from services import episode_engine as ee

CFG = {
    "cost": {"commission_pct": 0.00025, "slippage_pct": 0.0008, "transfer_fee_pct": 0.00001,
             "exchange_fee_pct": 0.0000341, "regulatory_fee_pct": 0.00002, "stamp_duty_sell_pct": 0.0005},
    "limit_board": {"detect_tol": 0.985, "by_prefix": {"60": 0.10, "00": 0.10, "30": 0.20, "68": 0.20}},
}


def test_round_trip_cost_asymmetric():
    rt = ee.round_trip_cost(CFG)
    one_side = 0.00025 + 0.0008 + 0.00001 + 0.0000341 + 0.00002
    assert rt == pytest.approx(one_side * 2 + 0.0005)   # 印花仅卖方


def test_limit_pct_prefix():
    assert ee.limit_pct("600519", CFG) == 0.10
    assert ee.limit_pct("300750", CFG) == 0.20
    assert ee.limit_pct("688981", CFG) == 0.20   # 长前缀 68 优先


def _arr(vals):
    return np.array(vals, dtype=float)


def test_basic_episode_buy_to_sell():
    """buy@idx2 → sell@idx5: 入场 open[3], 出场 open[6], hold=3, 含成本。"""
    n = 8
    dates = np.array([f"2020-01-0{i+1}" for i in range(n)])
    opens = _arr([10, 10, 10, 11, 12, 13, 14, 15])
    highs = opens + 0.5
    lows = opens - 0.5
    closes = opens.copy()
    buy = np.zeros(n, dtype=bool); buy[2] = True
    sell = np.zeros(n, dtype=bool); sell[5] = True
    stages = np.array(["1", "1", "1", "2", "2", "2", "2", "2"])
    dif = _arr([-1, -1, -1, 1, 1, 1, 1, 1])   # idx2 dif<0 → below
    eps = ee.build_episodes("600000", dates, opens, highs, lows, closes, buy, sell, stages, dif, cfg=CFG, hold_cap=60)
    assert len(eps) == 1
    e = eps[0]
    assert e.entry_date == "2020-01-04" and e.exit_date == "2020-01-07"   # open[3], open[6]
    assert e.hold_days == 3
    assert e.gross_return == pytest.approx(14 / 11 - 1)
    assert e.net_return == pytest.approx(14 / 11 - 1 - ee.round_trip_cost(CFG))
    assert e.entry_stage == "1" and e.entry_zero_axis == "below" and e.exit_reason == "sell_signal"


def test_one_line_up_entry_skipped():
    """入场日一字涨停 (open==high==low 且涨停) → 买不进, episode 跳过 (buyable-only)。"""
    n = 6
    dates = np.array([f"2020-02-0{i+1}" for i in range(n)])
    # idx2 buy → 入场 idx3 一字涨停 (open=high=low=11=10*1.1)
    opens = _arr([10, 10, 10, 11.0, 12, 13])
    highs = _arr([10.5, 10.5, 10.5, 11.0, 12.5, 13.5])
    lows = _arr([9.5, 9.5, 9.5, 11.0, 11.5, 12.5])
    closes = _arr([10, 10, 10, 11, 12, 13])
    buy = np.zeros(n, dtype=bool); buy[2] = True
    sell = np.zeros(n, dtype=bool); sell[4] = True
    stages = np.array(["1.5"] * n)
    dif = _arr([1, 1, 1, 1, 1, 1])
    eps = ee.build_episodes("600000", dates, opens, highs, lows, closes, buy, sell, stages, dif, cfg=CFG)
    assert len(eps) == 0, "一字涨停买不进, 不应产生 episode"


def test_hold_cap_exit_when_no_sell():
    """无卖出信号 → hold_cap 兜底出场。"""
    n = 10
    dates = np.array([f"2020-03-{i+1:02d}" for i in range(n)])
    opens = _arr([10] * n); opens[1:] = np.arange(10, 10 + (n - 1))
    highs = opens + 0.3; lows = opens - 0.3; closes = opens.copy()
    buy = np.zeros(n, dtype=bool); buy[0] = True
    sell = np.zeros(n, dtype=bool)   # 无卖出
    stages = np.array(["2"] * n)
    dif = _arr([1] * n)
    eps = ee.build_episodes("000001", dates, opens, highs, lows, closes, buy, sell, stages, dif, cfg=CFG, hold_cap=3)
    assert len(eps) == 1
    assert eps[0].exit_reason == "hold_cap" and eps[0].hold_days == 3


def test_months_before():
    assert ee._months_before("2026-06-15", 3) == "2026-03-15"
    assert ee._months_before("2026-01-10", 3) == "2025-10-10"   # 跨年
    assert ee._months_before("2026-06-30", 1) == "2026-05-28"   # 日截断防越界


def test_trailing_windows_recency():
    """近 N 月窗: 嵌套, 越大越含早期; 衰减可见。"""
    eps = [
        ee.Episode("a", "2024-01-02", "2024-01-10", 8, 0.20, 0.19, "1", "below", "sell_signal"),   # 早期大赢
        ee.Episode("b", "2026-05-02", "2026-05-10", 8, -0.05, -0.06, "1", "below", "sell_signal"),  # 近期亏
        ee.Episode("c", "2026-06-02", "2026-06-10", 8, -0.03, -0.04, "1", "below", "sell_signal"),  # 近期亏
    ]
    tw = ee.trailing_windows(eps, months=(3, 36))
    assert tw["3m"]["n_episodes"] == 2 and tw["3m"]["mean_net_return"] < 0   # 近 3 月只剩两笔亏
    assert tw["36m"]["n_episodes"] == 3 and tw["36m"]["mean_net_return"] > 0  # 含 2024 大赢转正


def test_aggregate_by_cell_and_all():
    eps = [
        ee.Episode("a", "2020-01-02", "2020-01-10", 8, 0.10, 0.09, "1", "below", "sell_signal"),
        ee.Episode("b", "2020-02-02", "2020-02-10", 8, -0.05, -0.06, "1", "below", "sell_signal"),
        ee.Episode("c", "2020-03-02", "2020-03-10", 8, 0.20, 0.19, "2", "above", "sell_signal"),
    ]
    agg = ee.aggregate_by_cell(eps)
    assert agg["__ALL__"]["n_episodes"] == 3
    assert agg["1|below"]["n_episodes"] == 2
    assert agg["1|below"]["win_rate"] == pytest.approx(0.5)
    assert agg["2|above"]["n_episodes"] == 1
