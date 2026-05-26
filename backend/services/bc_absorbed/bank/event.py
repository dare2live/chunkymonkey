"""Event-driven formulas (Phase 2.4 category 5/7).

Reference panel features (fact_capital_flow / fact_lhb_event / etc).
Each formula takes precomputed event signal arrays + price + returns entry signals.

Formulas:
- earnings_surprise_drift: post-earnings 5-day positive drift continuation
- insider_buy_cluster: 3+ insider buys in 30d window
- block_trade_anomaly: dzjy discount > 5% (block buy at discount = institutional accumulation)
- hsgt_net_buy_streak: 北上 net buy 5+ consecutive days
- lhb_institutional_appearance: 龙虎榜 institutional seats > 3
- dividend_ex_dividend_bounce: ex-dividend day -1d to +5d typical bounce
- index_inclusion_news: pending index inclusion typical run-up
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def earnings_surprise_drift(close: np.ndarray, earnings_surprise: np.ndarray, *,
                              drift_window: int = 5, surprise_threshold: float = 0.05,
                              **params: Any) -> tuple[np.ndarray, dict]:
    """Post-earnings drift: positive surprise + drift_window day continuation.

    earnings_surprise: array same length as close, value = surprise pct (0 if no earnings).
    """
    entry = np.zeros(len(close), dtype=bool)
    for i in range(drift_window, len(close)):
        prior_surprise = earnings_surprise[i - drift_window:i + 1]
        if (prior_surprise > surprise_threshold).any():
            # Verify drift continues (close > drift_window-ago close)
            if close[i] > close[i - drift_window]:
                entry[i] = True
    return entry, {"name": "earnings_surprise_drift", "entry_count": int(entry.sum())}


def insider_buy_cluster(close: np.ndarray, insider_buy_count: np.ndarray, *,
                        cluster_window: int = 30, min_buys: int = 3,
                        **params: Any) -> tuple[np.ndarray, dict]:
    """3+ insider buys in 30d window."""
    rolling_sum = pd.Series(insider_buy_count).rolling(cluster_window).sum().values
    entry = rolling_sum >= min_buys
    return entry.astype(bool), {"name": "insider_buy_cluster", "entry_count": int(entry.sum())}


def block_trade_anomaly(close: np.ndarray, block_discount: np.ndarray, *,
                         discount_threshold: float = 0.05,
                         **params: Any) -> tuple[np.ndarray, dict]:
    """Block trade (dzjy) at discount > threshold (institutional accumulation signal)."""
    entry = block_discount > discount_threshold
    return entry.astype(bool), {"name": "block_trade_anomaly", "entry_count": int(entry.sum())}


def hsgt_net_buy_streak(close: np.ndarray, hsgt_net: np.ndarray, *,
                         streak_days: int = 5,
                         **params: Any) -> tuple[np.ndarray, dict]:
    """北上 net buy streak_days consecutive positive days."""
    entry = np.zeros(len(close), dtype=bool)
    for i in range(streak_days, len(close)):
        if (hsgt_net[i - streak_days + 1:i + 1] > 0).all():
            entry[i] = True
    return entry, {"name": "hsgt_net_buy_streak", "entry_count": int(entry.sum())}


def lhb_institutional_appearance(close: np.ndarray, lhb_inst_seats: np.ndarray, *,
                                   min_seats: int = 3,
                                   **params: Any) -> tuple[np.ndarray, dict]:
    """LHB institutional buying seats >= min_seats."""
    entry = lhb_inst_seats >= min_seats
    return entry.astype(bool), {"name": "lhb_institutional_appearance", "entry_count": int(entry.sum())}


def dividend_ex_dividend_bounce(close: np.ndarray, ex_dividend_flag: np.ndarray, *,
                                  pre_window: int = 1, post_window: int = 5,
                                  **params: Any) -> tuple[np.ndarray, dict]:
    """Ex-dividend day: signal at ex-date close, buy T+1 open (no future data)."""
    entry = np.zeros(len(close), dtype=bool)
    ex_idx = np.where(ex_dividend_flag > 0)[0]
    for idx in ex_idx:
        if idx + 1 < len(close):
            entry[idx + 1] = True
    return entry, {"name": "dividend_ex_dividend_bounce", "entry_count": int(entry.sum())}


def index_inclusion_news(close: np.ndarray, index_inclusion_flag: np.ndarray, *,
                          run_up_window: int = 5,
                          **params: Any) -> tuple[np.ndarray, dict]:
    """Index inclusion announcement: run-up window N days."""
    entry = np.zeros(len(close), dtype=bool)
    for i in range(len(close)):
        if index_inclusion_flag[i] > 0:
            for j in range(min(run_up_window, len(close) - i - 1)):
                entry[i + 1 + j] = True
    return entry, {"name": "index_inclusion_news", "entry_count": int(entry.sum())}


EVENT_FORMULAS = {
    "earnings_surprise_drift": earnings_surprise_drift,
    "insider_buy_cluster": insider_buy_cluster,
    "block_trade_anomaly": block_trade_anomaly,
    "hsgt_net_buy_streak": hsgt_net_buy_streak,
    "lhb_institutional_appearance": lhb_institutional_appearance,
    "dividend_ex_dividend_bounce": dividend_ex_dividend_bounce,
    "index_inclusion_news": index_inclusion_news,
}
