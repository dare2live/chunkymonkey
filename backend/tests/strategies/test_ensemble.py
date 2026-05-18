"""Tests for MSAF Phase 3.2 ensemble."""
from __future__ import annotations

import pandas as pd
import pytest

from services.strategies.ensemble import (
    EnsembleVerdict,
    ensemble_scores,
    normalize_scores,
)
from services.strategies.regime.regime_state import RegimeVerdict, REGIME_WEIGHTS


def _make_regime(state: str = "bull", signal_date: str = "2025-01-15") -> RegimeVerdict:
    return RegimeVerdict(
        signal_date=signal_date,
        state=state,
        hs300_close=4000.0,
        hs300_ma60=3900.0,
        above_ma60=True,
        ret_60d=0.05,
        breadth_pct=60.0,
        weights=REGIME_WEIGHTS[state],
        reasoning="test",
    )


def test_crash_regime_all_cash():
    """Crash → empty top-K, cash 100%."""
    regime = _make_regime("crash")
    lam = pd.Series({"000001": 1.0, "000002": 0.5})
    v = ensemble_scores(signal_date="2025-01-15", regime=regime, lambdamart_scores=lam)
    assert v.cash_pct == 1.0
    assert v.top_k_codes == []


def test_bull_ensemble_all_sources():
    """Bull regime → ensemble combining 3 sources."""
    regime = _make_regime("bull")
    lam = pd.Series({"600519": 1.0, "000001": 0.8, "601318": 0.6, "000333": 0.4, "601166": 0.2})
    sniper = pd.Series({"600519": 7.0, "000001": 6.0, "000858": 5.0})
    inst = pd.Series({"601318": 0.9, "000333": 0.7, "601166": 0.5, "600519": 0.3})
    v = ensemble_scores(
        signal_date="2025-01-15", regime=regime,
        lambdamart_scores=lam, sniper_scores=sniper, institution_scores=inst,
        max_positions=5,
    )
    assert v.regime_state == "bull"
    assert v.cash_pct == 0.0
    assert len(v.top_k_codes) == 5
    # 600519 has highest lambdamart + good sniper + some institution → should top
    assert "600519" in v.top_k_codes


def test_bear_regime_partial_cash():
    """Bear → 60% cash, K reduced."""
    regime = _make_regime("bear")
    lam = pd.Series({f"00000{i}": 1.0 - i*0.1 for i in range(10)})
    v = ensemble_scores(signal_date="2025-01-15", regime=regime, lambdamart_scores=lam, max_positions=5)
    assert v.cash_pct == 0.60
    # 60% cash → 40% stocks → max_positions * 0.4 = 2
    assert len(v.top_k_codes) == 2


def test_neutral_regime_mid_cash():
    """Neutral → no cash, full 5 positions."""
    regime = _make_regime("neutral")
    lam = pd.Series({f"00000{i}": 1.0 - i*0.1 for i in range(10)})
    v = ensemble_scores(signal_date="2025-01-15", regime=regime, lambdamart_scores=lam, max_positions=5)
    assert v.cash_pct == 0.0
    assert len(v.top_k_codes) == 5


def test_no_strategy_sources():
    """No source scores → all cash."""
    regime = _make_regime("bull")
    v = ensemble_scores(signal_date="2025-01-15", regime=regime)
    assert v.cash_pct == 1.0
    assert v.top_k_codes == []


def test_normalize_scores():
    """Normalize [0,1]."""
    s = pd.Series([10.0, 20.0, 30.0])
    n = normalize_scores(s)
    assert n.min() == 0.0
    assert n.max() == 1.0
    assert n.iloc[1] == 0.5


def test_normalize_constant():
    """Constant → all 0."""
    s = pd.Series([5.0, 5.0, 5.0])
    n = normalize_scores(s)
    assert (n == 0.0).all()


def test_ensemble_weight_alignment():
    """Bull weight: lambdamart 30 + sniper 40 + institution 30 → ranks reflect weight balance."""
    regime = _make_regime("bull")
    # Only lambdamart top: 000001
    # Only sniper top: 000002
    # Only institution top: 000003
    lam = pd.Series({"000001": 1.0, "000002": 0.0, "000003": 0.0})
    sniper = pd.Series({"000001": 0.0, "000002": 1.0, "000003": 0.0})
    inst = pd.Series({"000001": 0.0, "000002": 0.0, "000003": 1.0})
    v = ensemble_scores(
        signal_date="2025-01-15", regime=regime,
        lambdamart_scores=lam, sniper_scores=sniper, institution_scores=inst,
        max_positions=3,
    )
    # bull weights: lambdamart 0.30, sniper 0.40, institution 0.30
    # 000001 = 0.30 * 1.0 = 0.30
    # 000002 = 0.40 * 1.0 = 0.40 (top)
    # 000003 = 0.30 * 1.0 = 0.30
    assert v.top_k_codes[0] == "000002"
