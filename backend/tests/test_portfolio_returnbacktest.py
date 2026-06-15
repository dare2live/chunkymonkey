"""return-based 回测引擎证伪门 — 手算已知场景逐字核对 (2026-06-15 干净重建验收)。

旧引擎退役重建, 新引擎每步可核; 本测固化"已知输入->已知输出", 改坏一处必红 (red->green 实证)。
"""
import pytest

from services.portfolio_returnbacktest import run_return_backtest

CAL = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]


def test_known_scenario_hand_computed():
    """2 股单调仓, T+1 入场, 含 10bps 成本 — 手算 final_nav=1.0989。"""
    price = {
        "A": {"2024-01-01": 10, "2024-01-02": 10, "2024-01-03": 11, "2024-01-04": 11, "2024-01-05": 12},
        "B": {"2024-01-01": 20, "2024-01-02": 20, "2024-01-03": 22, "2024-01-04": 22, "2024-01-05": 20},
    }
    r = run_return_backtest([("2024-01-01", ["A", "B"])], price, CAL, cost_bps=10.0)
    # 入场 T+1=01-02; 买入换手 1.0 -> cost=10bps -> nav0=0.999
    assert r["nav"][0][0] == "2024-01-02"
    assert r["nav"][0][1] == pytest.approx(0.999, abs=1e-6)
    # 末日 01-05: mean(12/10, 20/20)=1.1 -> 0.999*1.1
    assert r["final_nav"] == pytest.approx(0.999 * 1.1, abs=1e-6)
    assert len(r["nav"]) == 4   # 01-02..01-05 (入场到末日)
    assert r["cost_drag"] == pytest.approx(0.001, abs=1e-6)
    assert r["avg_turnover"] == pytest.approx(1.0, abs=1e-6)


def test_t_plus_1_no_lookahead():
    """决策日当天价格绝不作入场价 (T+1 防当日成交未来函数)。"""
    # 决策日 01-01 给 A 极端低价 (1.0); 若引擎误用决策日价入场, final_nav 会暴涨。
    price = {"A": {"2024-01-01": 1.0, "2024-01-02": 10, "2024-01-03": 10, "2024-01-04": 10, "2024-01-05": 10}}
    r = run_return_backtest([("2024-01-01", ["A"])], price, CAL, cost_bps=0.0)
    # 入场=01-02 价 10, 之后恒 10 -> nav 平 (1.0); 若误用 01-01 价 1.0 入场 -> nav=10x
    assert r["final_nav"] == pytest.approx(1.0, abs=1e-6)


def test_missing_price_dropped_not_crash():
    """某股某日缺价 -> 该日剔出等权篮, 不崩、不假装成交。"""
    price = {
        "A": {"2024-01-01": 10, "2024-01-02": 10, "2024-01-03": 12, "2024-01-04": 12, "2024-01-05": 12},
        "B": {"2024-01-01": 20, "2024-01-02": 20, "2024-01-05": 20},  # 03/04 缺价
    }
    r = run_return_backtest([("2024-01-01", ["A", "B"])], price, CAL, cost_bps=0.0)
    # 03 日 B 缺价 -> 只用 A (12/10=1.2) -> nav=1.2 (非崩)
    nav_by_date = dict(r["nav"])
    assert nav_by_date["2024-01-03"] == pytest.approx(1.2, abs=1e-6)


def test_empty_basket_flat():
    """空篮 (无有效价) -> 持平不崩。"""
    r = run_return_backtest([("2024-01-01", ["Z"])], {"Z": {}}, CAL, cost_bps=10.0)
    assert r["nav"] == [] or r["final_nav"] == pytest.approx(r["nav"][0][1])
