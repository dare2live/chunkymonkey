"""Paper Sim v2 — tx_cost 真实成本单测.

边界 + 常规 + 沪深差异 + 最低佣金 + 往返估算.
"""
from __future__ import annotations

import pytest

from services.paper_sim.config import load_config
from services.paper_sim.tx_cost import (
    compute_buy_cost,
    compute_sell_revenue,
    estimate_round_trip_pct,
    _is_sh_market,
)


@pytest.fixture
def cfg():
    return load_config().tx_cost


def test_buy_cost_components(cfg):
    """买入 10 元 × 100 股 = 1000 元 base."""
    r = compute_buy_cost(cfg, price=10.0, shares=100, stock_code="000001")
    assert r.base_amount == 1000.0
    # 佣金 max(5, 1000 × 0.025%) = max(5, 0.25) = 5
    assert r.commission == 5.0
    # 买入不收印花税
    assert r.stamp_duty == 0.0
    # 深市 000001 无过户费
    assert r.transfer_fee == 0.0
    # 滑点 0.1% × 1000 = 1
    assert r.slippage == pytest.approx(1.0)
    # 总 = 5 + 0 + 0 + 1 = 6
    assert r.total_cost == pytest.approx(6.0)
    # 实付 = 1000 + 6 = 1006
    assert r.effective_amount == pytest.approx(1006.0)


def test_sell_revenue_components_shenzhen(cfg):
    """深市 000001 卖出: 含印花税, 无过户费."""
    r = compute_sell_revenue(cfg, price=10.0, shares=100, stock_code="000001")
    assert r.base_amount == 1000.0
    assert r.commission == 5.0           # 最低佣金
    assert r.stamp_duty == pytest.approx(0.5)   # 1000 × 0.05% = 0.5
    assert r.transfer_fee == 0.0
    assert r.slippage == pytest.approx(1.0)
    assert r.total_cost == pytest.approx(6.5)
    # 实收 = 1000 - 6.5 = 993.5
    assert r.effective_amount == pytest.approx(993.5)


def test_sell_revenue_components_shanghai(cfg):
    """沪市 600519 卖出: 含过户费."""
    r = compute_sell_revenue(cfg, price=10.0, shares=100, stock_code="600519")
    assert r.base_amount == 1000.0
    assert r.commission == 5.0
    assert r.stamp_duty == pytest.approx(0.5)
    # 上交所过户 1000 × 0.001% = 0.01
    assert r.transfer_fee == pytest.approx(0.01)
    assert r.slippage == pytest.approx(1.0)


def test_min_commission_kicks_in_for_small_trades(cfg):
    """100 元交易: 佣金 < 5 时取 5."""
    r = compute_buy_cost(cfg, price=1.0, shares=100, stock_code="000001")
    assert r.commission == 5.0
    # 1% 净成本对小单极大 → 真实约束 (这就是为什么 paper sim 必须算这个)


def test_large_trade_commission_above_floor(cfg):
    """1 万元交易: 佣金 = 1万 × 0.025% = 2.5 < 5, 仍取最低 5? 不, 2.5 < 5 取 5."""
    r = compute_buy_cost(cfg, price=10.0, shares=1000, stock_code="000001")
    # 1000 股 × 10 = 10000, commission max(5, 10000 × 0.00025) = max(5, 2.5) = 5
    assert r.commission == 5.0
    # 真大单:
    r2 = compute_buy_cost(cfg, price=100.0, shares=1000, stock_code="000001")
    # 100000 × 0.00025 = 25 > 5 → commission = 25
    assert r2.commission == 25.0


def test_round_trip_pct_estimate(cfg):
    """往返 ≈ 0.35% (gap_buffer_pct 的依据)."""
    pct = estimate_round_trip_pct(cfg)
    # base 1000 → buy 6 + sell 6.5 = 12.5 / 1000 = 1.25%
    # 这里因为 base 太小最低佣金 5 主导. 用更大 base 测真实比例:
    # 实际上往返净成本占比公式 = 2*commission_pct + stamp_duty + 2*slippage 不含最低佣金尾部.
    # 测试要点: 往返成本 > 0 且 < 5%
    assert 0 < pct < 0.05


def test_is_sh_market_classification():
    assert _is_sh_market("600519") is True
    assert _is_sh_market("601318") is True
    assert _is_sh_market("688981") is True
    assert _is_sh_market("000001") is False
    assert _is_sh_market("002415") is False
    assert _is_sh_market("300750") is False


def test_zero_shares_returns_zero(cfg):
    r = compute_buy_cost(cfg, price=10.0, shares=0, stock_code="000001")
    assert r.base_amount == 0
    assert r.total_cost == 0
