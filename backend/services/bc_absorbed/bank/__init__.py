"""Formula bank for bc_absorbed (Phase 2.4).

Per goal.md MASTER_SYNTHESIS Phase 2.4: 7 categories × ~7 formulas ≈ 50 total.

Categories:
- technical: indicator-based (RSI, MACD, Bollinger, KDJ, etc) — IMPLEMENTED
- pattern: price patterns (cup-handle, double-bottom, triangle) — TODO
- volume: institutional footprint (OBV, MFI, VWAP) — TODO
- multi_tf: weekly+daily synergy — TODO
- event: earnings/insider/HSGT/LHB — TODO
- sector: cross-sectional rank — TODO
- sentiment: theme/leader-follower (Perception) — TODO

Each formula returns:
  entry: numpy.ndarray[bool] — entry signal per bar
  meta: dict — formula-specific metadata

Phase 2.4 ETA: 1 week, one category per day.
"""

from . import technical

__all__ = ["technical"]
