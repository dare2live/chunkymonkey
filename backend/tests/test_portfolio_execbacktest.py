"""execution-aware 引擎证伪门 (2026-06-15 P1) — 逐场景手算核对 R2 摩擦。

red->green: 删任一摩擦 (T+1 open/一字板剔篮/非对称成本/停牌冻结/容量) 本测试必转红。
owner=docs/strategy_validation_contract.md C-R2。
"""
from __future__ import annotations

import pytest

from services.portfolio_execbacktest import (
    ExecConfig, run_execution_backtest, _sizing_weights, _metrics,
    _is_one_line_up, _is_one_line_down,
)


def _zero_cost_cfg() -> ExecConfig:
    return ExecConfig(commission_pct=0.0, stamp_duty_sell_pct=0.0, transfer_fee_pct=0.0,
                      exchange_fee_pct=0.0, regulatory_fee_pct=0.0, slippage_pct=0.0,
                      large_order_surcharge_pct=0.0, limit_by_prefix={"60": 0.10, "30": 0.20},
                      one_line_buy_block=True, one_line_sell_block=True, detect_tol=0.985,
                      capital_cny=1_000_000.0, adv_window=20, participation_threshold=0.10)


def _real_cfg() -> ExecConfig:
    return ExecConfig.load()


# ---- 1. T+1 open 入场 + 零成本路径 (手算) ----
def test_t1_open_entry_zero_cost_path():
    cal = ["d0", "d1", "d2"]
    # A: d0 close=9; d1 open=10 (入场价); d2 close=12. 大量防容量溢价。
    bars = {"600000": {"d0": (9, 9, 9, 9, 2_000_000), "d1": (10, 11, 9, 11, 2_000_000),
                       "d2": (12, 12, 12, 12, 2_000_000)}}
    res = run_execution_backtest([("d0", [("600000", 1.0)])], bars, cal,
                                 config=_zero_cost_cfg(), top_k=1, gross_exposure=1.0)
    # 入场 open=10, d2 close=12 -> 12/10-1 = 20%; 零成本 final_nav≈1.2
    assert res["final_nav"] == pytest.approx(1.2, abs=1e-9)
    # 无未来函数: 决策日 d0 不在 nav (只 d1,d2 持有)
    assert [d for d, _ in res["nav"]] == ["d1", "d2"]


# ---- 2. 一字涨停 T+1 买不进 -> 剔篮 (N8/N12) ----
def test_one_line_up_blocks_entry():
    cal = ["d0", "d1", "d2"]
    # A d1 一字涨停: open=high=low=10, prev_close(d0)=9 -> 11.1% >= 10%*0.985 -> 买不进
    bars = {"600000": {"d0": (9, 9, 9, 9, 1000), "d1": (10, 10, 10, 10, 1000), "d2": (11, 11, 11, 11, 1000)}}
    res = run_execution_backtest([("d0", [("600000", 1.0)])], bars, cal,
                                 config=_zero_cost_cfg(), top_k=1)
    assert res["final_nav"] == pytest.approx(1.0, abs=1e-9)   # 没买成, 全现金持平


def test_one_line_up_detector_board_aware():
    cfg = _zero_cost_cfg()
    # 主板 60: 9->10 = 11.1% >10%*0.985 一字涨停
    assert _is_one_line_up((10, 10, 10, 10, 1), 9.0, cfg, "600000") is True
    # 创业板 30: 同涨幅 11.1% < 20%*0.985 -> 非一字涨停 (板块自适应)
    assert _is_one_line_up((10, 10, 10, 10, 1), 9.0, cfg, "300001") is False
    # open!=high -> 非一字
    assert _is_one_line_up((10, 11, 9, 10, 1), 9.0, cfg, "600000") is False


def test_one_line_down_detector():
    cfg = _zero_cost_cfg()
    assert _is_one_line_down((9, 9, 9, 9, 1), 10.0, cfg, "600000") is True   # -10% 一字跌停
    assert _is_one_line_down((9.5, 10, 9, 9.5, 1), 10.0, cfg, "600000") is False


# ---- 3. 非对称成本: 卖方加印花, 买方不加 (N13) ----
def test_asymmetric_cost_stamp_on_sell_only():
    cfg = _real_cfg()
    assert cfg.sell_cost_pct() - cfg.buy_cost_pct() == pytest.approx(cfg.stamp_duty_sell_pct, abs=1e-12)
    # 容量超阈值 -> 两侧都加大单溢价
    assert cfg.buy_cost_pct(over_capacity=True) - cfg.buy_cost_pct(False) == pytest.approx(
        cfg.large_order_surcharge_pct, abs=1e-12)


def test_single_buy_cost_applied_once():
    cal = ["d0", "d1", "d2"]
    cfg = _real_cfg()
    # 价格全 10 不变, 大量防容量溢价 -> final = 1 - 一次买入成本
    bars = {"600000": {"d0": (10, 10, 10, 10, 5_000_000), "d1": (10, 10, 10, 10, 5_000_000),
                       "d2": (10, 10, 10, 10, 5_000_000)}}
    res = run_execution_backtest([("d0", [("600000", 1.0)])], bars, cal, config=cfg, top_k=1)
    assert res["final_nav"] == pytest.approx(1.0 - cfg.buy_cost_pct(False), abs=1e-6)


# ---- 4. 停牌冻结: 缺价不剔篮不归零 (N11) ----
def test_suspension_freezes_position_not_dropped():
    cal = ["d0", "d1", "d2", "d3"]
    # 买入 d1 open=10; d2 停牌(无 bar) -> 冻结在 d1 close=10; d3 复牌 close=10
    bars = {"600000": {"d0": (10, 10, 10, 10, 5_000_000), "d1": (10, 10, 10, 10, 5_000_000),
                       "d3": (10, 10, 10, 10, 5_000_000)}}
    res = run_execution_backtest([("d0", [("600000", 1.0)])], bars, cal, config=_zero_cost_cfg(), top_k=1)
    navs = dict(res["nav"])
    # d2 停牌: 仓位冻结在 last_price=10, nav 不归零 (若被错误剔篮则 nav 掉到现金 0)
    assert navs["d2"] == pytest.approx(1.0, abs=1e-9)
    assert navs["d3"] == pytest.approx(1.0, abs=1e-9)


# ---- 5. 容量: 小 ADV -> 参与度超阈值 -> 大单溢价 + 警告 (N10) ----
def test_capacity_warning_on_thin_adv():
    cal = ["d0", "d1", "d2"]
    # ADV 小 (volume=100, close=10 -> adv=1000); capital 1e6 * weight 1 = 1e6 order -> participation=1000 >> 0.1
    bars = {"600000": {"d0": (10, 10, 10, 10, 100), "d1": (10, 10, 10, 10, 100), "d2": (10, 10, 10, 10, 100)}}
    res = run_execution_backtest([("d0", [("600000", 1.0)])], bars, cal, config=_real_cfg(), top_k=1)
    assert res["capacity_warn_rate"] == pytest.approx(1.0, abs=1e-9)
    assert res["max_participation"] > 0.10


# ---- 6. sizing policy ----
def test_sizing_equal_empty_slots_are_cash():
    cfg = _zero_cost_cfg()
    # top_k=4 但只 2 只 -> 每只 gross/top_k=0.25, 共 0.5 投资, 0.5 现金
    w = _sizing_weights([("600000", 1.0), ("600001", 0.5)], "equal", 1.0, 4, {}, "d1", {})
    assert w["600000"] == pytest.approx(0.25)
    assert sum(w.values()) == pytest.approx(0.5)


def test_sizing_rank_higher_signal_more_weight():
    cfg = _zero_cost_cfg()
    sel = [("600000", 0.9), ("600001", 0.1)]  # 已按 signal 降序
    w = _sizing_weights(sel, "rank", 1.0, 2, {}, "d1", {})
    assert w["600000"] > w["600001"]           # 高 signal 高权重


# ---- 7. 联合 metrics: 段级胜率 + 盈亏比 + 正期望 (C-WinReturn) ----
def test_metrics_payoff_and_expectancy():
    # 段收益: 2 胜 (+0.10,+0.10) 1 负 (-0.05) -> win_rate=2/3, payoff=0.10/0.05=2, expectancy=2/3*2-1/3=1.0
    m = _metrics(["2023-01-31", "2023-02-28", "2023-03-31"], [1.10, 1.05, 1.15], [0.10, -0.05, 0.10])
    assert m["win_rate"] == pytest.approx(2 / 3)
    assert m["payoff_ratio"] == pytest.approx(2.0)
    assert m["expectancy"] == pytest.approx(1.0)


# ---- 8. config 从 yaml 加载 (真相源) ----
def test_exec_config_loads_from_yaml():
    cfg = ExecConfig.load()
    assert cfg.stamp_duty_sell_pct > 0          # 卖方印花存在
    assert cfg.limit_by_prefix["60"] == pytest.approx(0.10)
    assert cfg.limit_by_prefix["30"] == pytest.approx(0.20)
    assert cfg.capital_cny == pytest.approx(1_000_000.0)
