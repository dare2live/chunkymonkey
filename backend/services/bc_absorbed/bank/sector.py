"""Cross-sectional sector/industry formulas (Phase 2.4 category 6/7).

Each formula consumes per-stock data + cross-sectional rank info to identify
sector leaders / relative strength / industry rotation.

Formulas:
- sector_relative_momentum: stock beats sector mean by N pp over period
- industry_leader_rank: highest formula score per industry over period
- smart_money_flow_rank: top decile inst_flow rank (跟 institution_holdings)
- sector_rotation_entry: prior weak sector turns top quintile this week
- relative_strength_breakout: rs ratio breaks above prior high
- industry_breadth_low_buy: sector low breadth + this stock holding up (resilience)
- sector_volume_concentration: this stock vol > sector vol mean × 2
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def sector_relative_momentum(close: np.ndarray, sector_ret: np.ndarray, *,
                              period: int = 20, outperformance: float = 0.05,
                              **params: Any) -> tuple[np.ndarray, dict]:
    """Stock period return > sector period return + outperformance."""
    stock_ret = (close - np.roll(close, period)) / np.where(np.roll(close, period) > 0, np.roll(close, period), 1)
    entry = stock_ret > (sector_ret + outperformance)
    entry[:period] = False
    return entry.astype(bool), {"name": "sector_relative_momentum", "entry_count": int(entry.sum())}


def industry_leader_rank(stock_score: np.ndarray, industry_rank_pct: np.ndarray, *,
                          top_pct: float = 0.1,
                          **params: Any) -> tuple[np.ndarray, dict]:
    """Stock is top top_pct % within its industry by stock_score."""
    entry = industry_rank_pct >= (1 - top_pct)
    return entry.astype(bool), {"name": "industry_leader_rank", "entry_count": int(entry.sum())}


def smart_money_flow_rank(close: np.ndarray, inst_flow_rank_pct: np.ndarray, *,
                          top_pct: float = 0.2,
                          **params: Any) -> tuple[np.ndarray, dict]:
    """Stock in top 20% institutional flow rank cross-sectional."""
    entry = inst_flow_rank_pct >= (1 - top_pct)
    return entry.astype(bool), {"name": "smart_money_flow_rank", "entry_count": int(entry.sum())}


def sector_rotation_entry(sector_rank_this_week: np.ndarray,
                          sector_rank_prior_week: np.ndarray, *,
                          rotation_pct: float = 0.3,
                          **params: Any) -> tuple[np.ndarray, dict]:
    """Sector was bottom quintile prior week, now top quintile (rotation)."""
    was_weak = sector_rank_prior_week <= rotation_pct
    now_strong = sector_rank_this_week >= (1 - rotation_pct)
    entry = was_weak & now_strong
    return entry.astype(bool), {"name": "sector_rotation_entry", "entry_count": int(entry.sum())}


def relative_strength_breakout(close: np.ndarray, index_close: np.ndarray, *,
                                lookback: int = 20,
                                **params: Any) -> tuple[np.ndarray, dict]:
    """Stock/index ratio breaks above prior lookback high."""
    safe_idx = np.where(index_close > 0, index_close, 1)
    rs = close / safe_idx
    rs_high = pd.Series(rs).rolling(lookback).max().shift(1).values
    entry = rs > rs_high
    entry = np.nan_to_num(entry, nan=False).astype(bool)
    return entry, {"name": "relative_strength_breakout", "entry_count": int(entry.sum())}


def industry_breadth_low_buy(stock_ret: np.ndarray, sector_breadth: np.ndarray, *,
                              breadth_threshold: float = 0.3,
                              **params: Any) -> tuple[np.ndarray, dict]:
    """Sector breadth (% rising) low BUT this stock holds up (positive ret) = resilience."""
    entry = (sector_breadth < breadth_threshold) & (stock_ret > 0)
    return entry.astype(bool), {"name": "industry_breadth_low_buy", "entry_count": int(entry.sum())}


def sector_volume_concentration(volume: np.ndarray, sector_vol_mean: np.ndarray, *,
                                 mult: float = 2.0,
                                 **params: Any) -> tuple[np.ndarray, dict]:
    """Stock vol > sector mean vol × mult (institutional concentration)."""
    entry = volume > sector_vol_mean * mult
    return entry.astype(bool), {"name": "sector_volume_concentration", "entry_count": int(entry.sum())}


SECTOR_FORMULAS = {
    "sector_relative_momentum": sector_relative_momentum,
    "industry_leader_rank": industry_leader_rank,
    "smart_money_flow_rank": smart_money_flow_rank,
    "sector_rotation_entry": sector_rotation_entry,
    "relative_strength_breakout": relative_strength_breakout,
    "industry_breadth_low_buy": industry_breadth_low_buy,
    "sector_volume_concentration": sector_volume_concentration,
}
