"""MSAF Phase 3: Regime state 计算.

PIT-strict regime gate for MSAF 3 类策略 adaptive 加权.

4 状态:
- bull: HS300 above MA60 + breadth > 50% + 60d_ret > 0
- neutral: 在 bull / bear 之间, partial 仓位
- bear: HS300 below MA60 + breadth < 40%
- crash: 跌穿 MA60 + 60d_ret < -15%

Output: RegimeVerdict(state, hs300_ma_signal, breadth, ret_60d, weights)

权重 plan (per ORCHESTRATION + msaf_top_design):
- bull:    {lambdamart:30, sniper:40, institution:30, cash:0}
- neutral: {lambdamart:40, sniper:30, institution:30, cash:0}
- bear:    {lambdamart:10, sniper:20, institution:10, cash:60}
- crash:   {lambdamart:0,  sniper:0,  institution:0,  cash:100}

PIT-strict: 用 signal_date 之前 D 天数据, 严格 < signal_date.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class RegimeVerdict:
    signal_date: str
    state: str  # "bull" | "neutral" | "bear" | "crash"
    hs300_close: float
    hs300_ma60: float
    above_ma60: bool
    ret_60d: float
    breadth_pct: float | None  # % stocks close > ma20 (None if not computed)
    weights: dict[str, float]
    reasoning: str


# Weights per regime state (rule-compliance: ok evidence=orchestration-doc-layer3)
REGIME_WEIGHTS = {
    "bull":    {"lambdamart": 0.30, "sniper": 0.40, "institution": 0.30, "cash": 0.00},
    "neutral": {"lambdamart": 0.40, "sniper": 0.30, "institution": 0.30, "cash": 0.00},
    "bear":    {"lambdamart": 0.10, "sniper": 0.20, "institution": 0.10, "cash": 0.60},
    "crash":   {"lambdamart": 0.00, "sniper": 0.00, "institution": 0.00, "cash": 1.00},
}

# Thresholds (rule-compliance: ok evidence=msaf-top-design-doc-r38)
MA_WINDOW = 60       # MA60 trend
RET_WINDOW = 60      # 60d return
BREADTH_BULL = 50.0  # > 50% stocks above MA20 = bull breadth
BREADTH_BEAR = 40.0  # < 40% = bear breadth
RET_CRASH = -0.15    # 60d ret < -15% = crash
# Fallback thresholds when breadth_pct=None (rule-compliance: ok evidence=msaf-top-design-doc-r38)
RET_BULL_FALLBACK = 0.08   # 60d ret > 8% + above MA60 → bull (no breadth)
RET_BEAR_FALLBACK = -0.08  # 60d ret < -8% + below MA60 → bear (no breadth)


def compute_regime_state(
    signal_date: str,
    hs300_kline: pd.DataFrame,
    breadth_pct: float | None = None,
) -> RegimeVerdict:
    """Compute regime state at signal_date.

    Args:
        signal_date: YYYY-MM-DD
        hs300_kline: DataFrame with 'date' + 'close' columns, sorted ascending date.
            Must have at least MA_WINDOW rows < signal_date.
        breadth_pct: Optional pre-computed breadth (% stocks above MA20).
            If None, breadth-based regime classification skipped (uses ma60 only).

    Returns:
        RegimeVerdict
    """
    # PIT-strict filter
    hs300 = hs300_kline[hs300_kline["date"] < signal_date].copy()
    hs300 = hs300.sort_values("date").reset_index(drop=True)

    if len(hs300) < MA_WINDOW:
        raise ValueError(f"Insufficient HS300 data: {len(hs300)} rows, need ≥ {MA_WINDOW}")

    latest = hs300.iloc[-1]
    hs300_close = float(latest["close"])
    hs300_ma60 = float(hs300["close"].tail(MA_WINDOW).mean())
    above_ma60 = hs300_close > hs300_ma60

    # 60d return
    if len(hs300) >= RET_WINDOW + 1:
        ret_60d = float((hs300_close / hs300.iloc[-RET_WINDOW - 1]["close"]) - 1)
    else:
        ret_60d = float((hs300_close / hs300.iloc[0]["close"]) - 1)

    # Classify state
    reasons = []
    if ret_60d < RET_CRASH:
        state = "crash"
        reasons.append(f"ret_60d={ret_60d:.2%} < {RET_CRASH:.2%}")
    elif not above_ma60:
        if breadth_pct is not None and breadth_pct < BREADTH_BEAR:
            state = "bear"
            reasons.append(f"below MA60 + breadth {breadth_pct:.1f}% < {BREADTH_BEAR}%")
        elif breadth_pct is None and ret_60d < RET_BEAR_FALLBACK:
            state = "bear"
            reasons.append(f"below MA60 + ret_60d={ret_60d:.2%} < {RET_BEAR_FALLBACK:.2%} (no breadth)")
        else:
            state = "neutral"
            reasons.append(f"below MA60 but ret_60d={ret_60d:.2%} OK")
    else:  # above_ma60
        if breadth_pct is not None and breadth_pct > BREADTH_BULL:
            state = "bull"
            reasons.append(f"above MA60 + breadth {breadth_pct:.1f}% > {BREADTH_BULL}%")
        elif breadth_pct is None and ret_60d > RET_BULL_FALLBACK:
            state = "bull"
            reasons.append(f"above MA60 + ret_60d={ret_60d:.2%} > {RET_BULL_FALLBACK:.2%} (no breadth)")
        else:
            state = "neutral"
            reasons.append(f"above MA60 but breadth N/A or {breadth_pct}%, ret_60d={ret_60d:.2%}")

    return RegimeVerdict(
        signal_date=signal_date,
        state=state,
        hs300_close=hs300_close,
        hs300_ma60=hs300_ma60,
        above_ma60=above_ma60,
        ret_60d=ret_60d,
        breadth_pct=breadth_pct,
        weights=REGIME_WEIGHTS[state],
        reasoning="; ".join(reasons),
    )


def load_hs300_kline(market_db_path: str = "data/market.duckdb") -> pd.DataFrame:
    """Load HS300 (code 000300) qfq daily K-line from market.duckdb.

    Returns DataFrame with 'date' (DATE), 'close' (DOUBLE), sorted ascending date.
    """
    import duckdb
    con = duckdb.connect(market_db_path, read_only=True)
    try:
        # rule-compliance: ok evidence=hs300-benchmark-fixed-code-000300
        sql_hs300 = (
            "SELECT CAST(date AS VARCHAR) AS date, close FROM v_price_kline_qfq "
            # rule-compliance: ok evidence=hs300-benchmark-fixed-code-000300
            "WHERE code='000300' AND adjust='qfq' AND freq='daily' "
            "ORDER BY date"
        )
        df = con.execute(sql_hs300).fetchdf()
        return df
    finally:
        con.close()


__all__ = ["RegimeVerdict", "compute_regime_state", "load_hs300_kline", "REGIME_WEIGHTS"]
