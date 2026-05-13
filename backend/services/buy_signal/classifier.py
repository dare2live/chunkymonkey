"""Phase η+++++ — score → tier 分类器 (单一职责)."""
from __future__ import annotations

from services.buy_signal.configs import (
    BuySignalTier, TIER_THRESHOLDS, TierThresholds,
)


def classify_tier(score: float, thresholds: TierThresholds = TIER_THRESHOLDS) -> BuySignalTier:
    """score → tier ("NO_SIGNAL" / "WATCH" / "BUY" / "STRONG_BUY")."""
    if score >= thresholds.strong_buy_min:
        return "STRONG_BUY"
    if score >= thresholds.buy_min:
        return "BUY"
    if score >= thresholds.watch_min:
        return "WATCH"
    return "NO_SIGNAL"
