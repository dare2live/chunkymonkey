"""P0a cost-after label 单测.

正常 / 边界 / 不可成交 mask 路径都覆盖.
"""
from __future__ import annotations

from services.labels.cost_after import (
    compute_forward_cost_after_returns,
    compute_round_trip_cost_pct,
)
from services.paper_sim.config import TxCostConfig


# 跟 paper_sim_config.yaml::tx_cost 一致的实测值
_TX = TxCostConfig(
    commission_pct=0.00025,
    commission_min_cny=5,
    stamp_duty_sell_pct=0.0005,
    transfer_fee_sh_pct=0.00001,
    slippage_pct=0.001,
)


def test_round_trip_cost_around_0_3pct():
    """实测: paper_sim_config tx_cost 单次往返 ≈ 0.3%.

    evidence: 2*0.00025 + 2*0.001 + 0.0005 + 2*0.00001 = 0.00302.
    """
    rt = compute_round_trip_cost_pct(_TX)
    assert 0.0029 < rt < 0.0031


def test_normal_path_5d_10d_20d():
    """入场 100 → 退出 105/110/115, 减 round-trip 0.3% → 净 +4.7% / 9.7% / 14.7%."""
    rt = compute_round_trip_cost_pct(_TX)
    out = compute_forward_cost_after_returns(
        entry_vwap=100.0,
        exit_vwap_5d=105.0,
        exit_vwap_10d=110.0,
        exit_vwap_20d=115.0,
        tx=_TX,
    )
    assert abs(out.fwd_cost_after_5d - (0.05 - rt)) < 1e-9
    assert abs(out.fwd_cost_after_10d - (0.10 - rt)) < 1e-9
    assert abs(out.fwd_cost_after_20d - (0.15 - rt)) < 1e-9
    assert out.round_trip_cost_pct == rt


def test_entry_unable_all_none():
    """T+1 entry 停牌/涨跌停 → 所有 horizon label=None."""
    out = compute_forward_cost_after_returns(
        entry_vwap=100.0,
        exit_vwap_5d=105.0,
        exit_vwap_10d=110.0,
        exit_vwap_20d=115.0,
        tx=_TX,
        entry_unable=True,
    )
    assert out.fwd_cost_after_5d is None
    assert out.fwd_cost_after_10d is None
    assert out.fwd_cost_after_20d is None


def test_only_5d_exit_unable():
    """T+1 + 5 日 exit 停牌, 但 10/20 日能成交 → 仅 5d=None."""
    out = compute_forward_cost_after_returns(
        entry_vwap=100.0,
        exit_vwap_5d=None,
        exit_vwap_10d=110.0,
        exit_vwap_20d=115.0,
        tx=_TX,
        exit_5d_unable=True,
    )
    assert out.fwd_cost_after_5d is None
    assert out.fwd_cost_after_10d is not None
    assert out.fwd_cost_after_20d is not None


def test_zero_or_negative_price_returns_none():
    """0 / 负价格 (异常数据) → None, 不 raise."""
    out = compute_forward_cost_after_returns(
        entry_vwap=0.0,
        exit_vwap_5d=100.0,
        exit_vwap_10d=100.0,
        exit_vwap_20d=100.0,
        tx=_TX,
    )
    assert out.fwd_cost_after_5d is None
    assert out.fwd_cost_after_10d is None
    assert out.fwd_cost_after_20d is None

    out2 = compute_forward_cost_after_returns(
        entry_vwap=100.0,
        exit_vwap_5d=-10.0,
        exit_vwap_10d=0.0,
        exit_vwap_20d=110.0,
        tx=_TX,
    )
    assert out2.fwd_cost_after_5d is None
    assert out2.fwd_cost_after_10d is None
    assert out2.fwd_cost_after_20d is not None


def test_loss_path():
    """亏损路径: 100 → 95 (-5%), 减 0.3% → -5.3%."""
    rt = compute_round_trip_cost_pct(_TX)
    out = compute_forward_cost_after_returns(
        entry_vwap=100.0,
        exit_vwap_5d=95.0,
        exit_vwap_10d=92.0,
        exit_vwap_20d=90.0,
        tx=_TX,
    )
    assert abs(out.fwd_cost_after_5d - (-0.05 - rt)) < 1e-9
    assert abs(out.fwd_cost_after_10d - (-0.08 - rt)) < 1e-9
    assert abs(out.fwd_cost_after_20d - (-0.10 - rt)) < 1e-9


def test_round_trip_invariant_across_horizons():
    """同入场不同 horizon, round_trip 一致 (常量, 不复利)."""
    out = compute_forward_cost_after_returns(
        entry_vwap=100.0,
        exit_vwap_5d=100.0,
        exit_vwap_10d=200.0,
        exit_vwap_20d=50.0,
        tx=_TX,
    )
    rt = compute_round_trip_cost_pct(_TX)
    assert abs(out.fwd_cost_after_5d - (0.0 - rt)) < 1e-9
    assert abs(out.fwd_cost_after_10d - (1.0 - rt)) < 1e-9
    assert abs(out.fwd_cost_after_20d - (-0.5 - rt)) < 1e-9
