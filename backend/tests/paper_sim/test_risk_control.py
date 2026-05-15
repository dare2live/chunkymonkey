"""Paper Sim — risk_control 单测 (v3 实验 2026-05-15).

portfolio_dd hard stop 防 black swan + alpha 失效.
"""
from __future__ import annotations

from services.paper_sim.risk_control import (
    compute_portfolio_dd,
    should_hard_stop,
    is_buy_frozen,
    compute_freeze_until,
)


# ============ compute_portfolio_dd ============


def test_compute_dd_normal():
    """1M peak → 0.85M today → -15% dd."""
    dd = compute_portfolio_dd(today_nav=850_000, peak_nav=1_000_000)
    assert abs(dd - (-0.15)) < 1e-9


def test_compute_dd_at_peak():
    """today == peak → 0% dd."""
    assert compute_portfolio_dd(1_000_000, 1_000_000) == 0.0


def test_compute_dd_above_peak():
    """1.1M today vs 1M peak → +10% (positive, no dd)."""
    dd = compute_portfolio_dd(1_100_000, 1_000_000)
    assert abs(dd - 0.1) < 1e-9


def test_compute_dd_zero_peak():
    """edge case: 0 peak → 0 dd (avoid div by 0)."""
    assert compute_portfolio_dd(100, 0) == 0.0


# ============ should_hard_stop ============


def test_hard_stop_triggered():
    """dd -0.21 below -0.20 threshold → trigger."""
    assert should_hard_stop(current_dd=-0.21, max_dd_hard_stop_pct=-0.20)


def test_hard_stop_exact_threshold():
    """dd -0.20 == threshold → trigger (<=)."""
    assert should_hard_stop(-0.20, -0.20)


def test_hard_stop_not_triggered_above():
    """dd -0.10 above threshold → no trigger."""
    assert not should_hard_stop(-0.10, -0.20)


def test_hard_stop_not_triggered_positive():
    """dd +0.05 (profit) → no trigger."""
    assert not should_hard_stop(0.05, -0.20)


# ============ freeze period ============


def test_freeze_until_calc():
    """today + freeze_days 自然日."""
    assert compute_freeze_until("2025-07-01", 30) == "2025-07-31"


def test_buy_frozen_within_period():
    """today=2025-07-15 < freeze_until=2025-07-31 → frozen."""
    assert is_buy_frozen("2025-07-15", "2025-07-31")


def test_buy_frozen_at_boundary():
    """today=2025-07-31 == freeze_until → 不 frozen (恰好结束)."""
    assert not is_buy_frozen("2025-07-31", "2025-07-31")


def test_buy_frozen_no_freeze():
    """freeze_until=None → 不 frozen."""
    assert not is_buy_frozen("2025-07-15", None)


def test_buy_frozen_past_period():
    """today=2025-08-15 > freeze_until=2025-07-31 → 不 frozen (已过)."""
    assert not is_buy_frozen("2025-08-15", "2025-07-31")
