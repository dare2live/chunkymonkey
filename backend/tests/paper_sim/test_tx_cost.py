"""Paper Sim — tx_cost 完整 A 股成本结构单测 (Codex C-B 2026-05-15).

边界 + 常规 + 6 项成本结构 (佣金/印花/过户/规费/证管/滑点) + 大单 surcharge + 往返估算.
2023+ 沪深统一过户费, 不再分 _is_sh_market.
"""
from __future__ import annotations

import pytest

from services.paper_sim.config import load_config
from services.paper_sim.tx_cost import (
    compute_buy_cost,
    compute_sell_revenue,
    estimate_round_trip_pct,
)


@pytest.fixture
def cfg():
    return load_config().tx_cost


def test_buy_cost_components(cfg):
    """买 10 元 × 100 股 = 1000 元 base, 验全 6 项."""
    r = compute_buy_cost(cfg, price=10.0, shares=100)
    assert r.base_amount == 1000.0
    # 佣金 max(5, 1000 × 0.025%) = 5
    assert r.commission == 5.0
    # 买入无印花税
    assert r.stamp_duty == 0.0
    # 过户费 1000 × 0.001% = 0.01
    assert r.transfer_fee == pytest.approx(0.01)
    # 交易所规费 1000 × 0.00341% = 0.0341
    assert r.exchange_fee == pytest.approx(0.0341)
    # 证管费 1000 × 0.002% = 0.02
    assert r.regulatory_fee == pytest.approx(0.02)
    # 基础滑点 1000 × 8 bps = 0.8
    assert r.slippage_base == pytest.approx(0.8)
    # 大单 surcharge: ADV20 默认 None → 不触发
    assert r.large_order_surcharge == 0.0
    # 总 = 5 + 0 + 0.01 + 0.0341 + 0.02 + 0.8 + 0 = 5.8641
    assert r.total_cost == pytest.approx(5.8641)
    assert r.effective_amount == pytest.approx(1005.8641)


def test_sell_revenue_components(cfg):
    """卖 10 元 × 100 股: 含印花税单边."""
    r = compute_sell_revenue(cfg, price=10.0, shares=100)
    assert r.base_amount == 1000.0
    assert r.commission == 5.0
    # 印花税 1000 × 0.05% = 0.5
    assert r.stamp_duty == pytest.approx(0.5)
    assert r.transfer_fee == pytest.approx(0.01)
    assert r.exchange_fee == pytest.approx(0.0341)
    assert r.regulatory_fee == pytest.approx(0.02)
    assert r.slippage_base == pytest.approx(0.8)
    # 总 = 5 + 0.5 + 0.01 + 0.0341 + 0.02 + 0.8 + 0 = 6.3641
    assert r.total_cost == pytest.approx(6.3641)
    assert r.effective_amount == pytest.approx(993.6359)


def test_min_commission_kicks_in_for_small_trades(cfg):
    """100 元交易: 佣金 < 5 时取 5."""
    r = compute_buy_cost(cfg, price=1.0, shares=100)
    assert r.commission == 5.0


def test_large_commission_above_floor(cfg):
    """10 万元: 佣金 = 10 万 × 0.025% = 25 > 5."""
    r2 = compute_buy_cost(cfg, price=100.0, shares=1000)
    assert r2.commission == 25.0


def test_large_order_surcharge_triggers_above_threshold(cfg):
    """order > 3% × ADV20 → +15 bps slippage surcharge."""
    # base = 10万元, ADV20 = 100万 → 10万/100万 = 10% > 3% 阈值 → 触发
    r = compute_buy_cost(cfg, price=100.0, shares=1000, adv20=1_000_000.0)
    assert r.large_order_surcharge == pytest.approx(100_000.0 * 0.0015)  # 150


def test_large_order_surcharge_skipped_below_threshold(cfg):
    """order < 3% × ADV20 → 无 surcharge."""
    # base = 1 万元, ADV20 = 100 万 → 1万/100万 = 1% < 3% → 不触发
    r = compute_buy_cost(cfg, price=10.0, shares=1000, adv20=1_000_000.0)
    assert r.large_order_surcharge == 0.0


def test_large_order_surcharge_skipped_when_adv20_missing(cfg):
    """adv20=None (数据缺失) → 不触发 surcharge (保守不加 penalty)."""
    r = compute_buy_cost(cfg, price=100.0, shares=1000, adv20=None)
    assert r.large_order_surcharge == 0.0


def test_round_trip_pct_estimate(cfg):
    """往返成本占比 > 0 且 < 5%, 不含大单 surcharge."""
    pct = estimate_round_trip_pct(cfg)
    assert 0 < pct < 0.05


def test_zero_shares_returns_zero(cfg):
    r = compute_buy_cost(cfg, price=10.0, shares=0)
    assert r.base_amount == 0
    assert r.total_cost == 0
