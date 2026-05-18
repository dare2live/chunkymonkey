"""Tests for MSAF Phase 3 regime_state."""
from __future__ import annotations

import pandas as pd
import pytest

from services.strategies.regime.regime_state import (
    REGIME_WEIGHTS,
    compute_regime_state,
)


def _make_kline(n: int = 100, base: float = 4000.0, slope: float = 1.0) -> pd.DataFrame:
    """Synthesize HS300 K-line with linear trend."""
    dates = pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d").tolist()
    closes = [base + slope * i for i in range(n)]
    return pd.DataFrame({"date": dates, "close": closes})


def test_bull_regime_high_breadth():
    """Bull: trending up + high breadth → bull state, sniper weight 40%."""
    kline = _make_kline(n=100, base=4000.0, slope=5.0)  # strong uptrend
    v = compute_regime_state("2024-06-01", kline, breadth_pct=70.0)
    assert v.state == "bull"
    assert v.above_ma60 is True
    assert v.weights == REGIME_WEIGHTS["bull"]
    assert v.weights["sniper"] == 0.40


def test_neutral_regime_above_ma_low_breadth():
    """Above MA but low breadth → neutral."""
    kline = _make_kline(n=100, base=4000.0, slope=2.0)
    v = compute_regime_state("2024-06-01", kline, breadth_pct=45.0)
    assert v.state == "neutral"


def test_neutral_no_breadth_data():
    """Above MA but no breadth → neutral (conservative)."""
    kline = _make_kline(n=100, base=4000.0, slope=2.0)
    v = compute_regime_state("2024-06-01", kline, breadth_pct=None)
    assert v.state == "neutral"


def test_bear_regime_below_ma_low_breadth():
    """Below MA + low breadth → bear, cash 60%."""
    kline = _make_kline(n=100, base=4000.0, slope=-1.0)  # downtrend
    v = compute_regime_state("2024-06-01", kline, breadth_pct=30.0)
    assert v.state == "bear"
    assert v.above_ma60 is False
    assert v.weights["cash"] == 0.60


def test_crash_regime():
    """60d ret < -15% → crash, all-cash."""
    # 5% drop per day cumulative
    kline = _make_kline(n=100, base=4000.0, slope=-30.0)
    v = compute_regime_state("2024-06-01", kline, breadth_pct=20.0)
    assert v.state == "crash"
    assert v.ret_60d < -0.15
    assert v.weights["cash"] == 1.00


def test_insufficient_data():
    """Less than 60 rows → ValueError."""
    kline = _make_kline(n=30)
    with pytest.raises(ValueError, match="Insufficient HS300 data"):
        compute_regime_state("2024-06-01", kline)


def test_pit_strict_excludes_future():
    """signal_date 之前的数据才用, future data 不影响."""
    kline = _make_kline(n=100, base=4000.0, slope=2.0)
    v1 = compute_regime_state("2024-04-01", kline, breadth_pct=60.0)
    # Inject future row (after 2024-04-01)
    kline2 = pd.concat([kline, pd.DataFrame({
        "date": ["2024-04-05", "2024-04-06"],
        "close": [99999.0, 99999.0],
    })], ignore_index=True)
    v2 = compute_regime_state("2024-04-01", kline2, breadth_pct=60.0)
    assert v1.state == v2.state
    assert v1.hs300_close == v2.hs300_close
    assert v1.hs300_ma60 == v2.hs300_ma60


def test_weights_sum_to_one():
    """All regime weights should sum to ~1.0."""
    for state, w in REGIME_WEIGHTS.items():
        total = sum(w.values())
        assert abs(total - 1.0) < 1e-9, f"{state} weights sum {total} != 1.0"
