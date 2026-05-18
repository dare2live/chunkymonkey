"""MSAF Phase 3: Regime adaptive 加权 module."""

from services.strategies.regime.regime_state import (
    REGIME_WEIGHTS,
    RegimeVerdict,
    compute_regime_state,
    load_hs300_kline,
)

__all__ = ["REGIME_WEIGHTS", "RegimeVerdict", "compute_regime_state", "load_hs300_kline"]
