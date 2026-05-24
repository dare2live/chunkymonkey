"""Sentiment / Theme formulas (Phase 2.4 category 7/7, final).

Consume Perception P3/P5/P6/P7 mart outputs as features.
Theme momentum / leader-follower diffusion / crowding indicator / stock context.

Formulas:
- theme_emerging_membership: stock joins newly-active theme
- leader_follower_diffusion_buy: leader stock up, follower not yet (entry follower)
- theme_crowding_avoidance: avoid stocks in heavy-crowded theme
- stock_context_high_quality: P7 context_score top quintile
- theme_lifecycle_active: theme in 'active accumulation' lifecycle stage
- diffusion_score_rising: P5 diffusion score 5d trend up
- under_reaction_event: P4 under_reaction_score > threshold (event-driven alpha)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def theme_emerging_membership(theme_score: np.ndarray, theme_member_since: np.ndarray, *,
                                emerging_window: int = 5,
                                **params: Any) -> tuple[np.ndarray, dict]:
    """Stock recently joined theme (member_since < emerging_window) + theme_score > 0."""
    entry = (theme_member_since <= emerging_window) & (theme_score > 0)
    return entry.astype(bool), {"name": "theme_emerging_membership", "entry_count": int(entry.sum())}


def leader_follower_diffusion_buy(diffusion_score: np.ndarray, is_leader: np.ndarray, *,
                                    diffusion_threshold: float = 0.5,
                                    **params: Any) -> tuple[np.ndarray, dict]:
    """Follower (is_leader=False) in theme with high diffusion score = buy entry."""
    entry = (~is_leader.astype(bool)) & (diffusion_score > diffusion_threshold)
    return entry, {"name": "leader_follower_diffusion_buy", "entry_count": int(entry.sum())}


def theme_crowding_avoidance(crowding_score: np.ndarray, *,
                               crowding_threshold: float = 0.7,
                               **params: Any) -> tuple[np.ndarray, dict]:
    """Negative signal: avoid stocks in crowding > threshold themes."""
    # Returns INVERTED: stocks NOT in crowded theme = entry candidate
    entry = crowding_score < crowding_threshold
    return entry.astype(bool), {"name": "theme_crowding_avoidance", "entry_count": int(entry.sum())}


def stock_context_high_quality(context_score: np.ndarray, *,
                                top_quintile: float = 0.6,
                                **params: Any) -> tuple[np.ndarray, dict]:
    """P7 context_score above top_quintile threshold = high quality context."""
    entry = context_score >= top_quintile
    return entry.astype(bool), {"name": "stock_context_high_quality", "entry_count": int(entry.sum())}


def theme_lifecycle_active(lifecycle_stage: np.ndarray,
                            **params: Any) -> tuple[np.ndarray, dict]:
    """Theme in 'active' lifecycle stage (P3 ThemeBoundary classification)."""
    # lifecycle_stage encoded numerically: 1=emerging, 2=active, 3=mature, 4=decline
    entry = lifecycle_stage == 2
    return entry.astype(bool), {"name": "theme_lifecycle_active", "entry_count": int(entry.sum())}


def diffusion_score_rising(diffusion_score: np.ndarray, *, period: int = 5,
                            **params: Any) -> tuple[np.ndarray, dict]:
    """Diffusion score rising 5d trend (P5 LeaderFollower momentum)."""
    slope = diffusion_score - np.roll(diffusion_score, period)
    entry = slope > 0
    entry[:period] = False
    return entry.astype(bool), {"name": "diffusion_score_rising", "entry_count": int(entry.sum())}


def under_reaction_event(under_reaction_score: np.ndarray, *, threshold: float = 0.3,
                          **params: Any) -> tuple[np.ndarray, dict]:
    """P4 UnderReaction score > threshold = market hasn't fully priced event."""
    entry = under_reaction_score > threshold
    return entry.astype(bool), {"name": "under_reaction_event", "entry_count": int(entry.sum())}


SENTIMENT_FORMULAS = {
    "theme_emerging_membership": theme_emerging_membership,
    "leader_follower_diffusion_buy": leader_follower_diffusion_buy,
    "theme_crowding_avoidance": theme_crowding_avoidance,
    "stock_context_high_quality": stock_context_high_quality,
    "theme_lifecycle_active": theme_lifecycle_active,
    "diffusion_score_rising": diffusion_score_rising,
    "under_reaction_event": under_reaction_event,
}
