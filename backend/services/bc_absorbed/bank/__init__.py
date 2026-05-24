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

from . import event, multi_tf, pattern, sector, sentiment, technical, volume

ALL_FORMULAS = {
    **technical.TECHNICAL_FORMULAS,
    **pattern.PATTERN_FORMULAS,
    **volume.VOLUME_FORMULAS,
    **multi_tf.MULTI_TF_FORMULAS,
    **event.EVENT_FORMULAS,
    **sector.SECTOR_FORMULAS,
    **sentiment.SENTIMENT_FORMULAS,
}

__all__ = [
    "technical", "pattern", "volume", "multi_tf",
    "event", "sector", "sentiment", "ALL_FORMULAS",
]
