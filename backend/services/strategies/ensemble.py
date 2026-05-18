"""MSAF Phase 3.2: 3 类策略 ensemble + regime-adaptive 加权.

输入:
- LambdaMART v6 ranking (mart_p0b_lambdamart_v6_predictions, top-K candidates)
- Sniper confluence verdict (per stock, score 0-7)
- 机构跟随 composite score (LHB + 主力 + 调研 + 北向)

调度:
1. regime_state → 拿 weights (bull/neutral/bear/crash)
2. 3 类 strategy 各产 candidate scores
3. ensemble score = w1*lambdamart + w2*sniper + w3*institution
4. 按 ensemble score top-K (依 max_positions)
5. cash portion: regime.cash * NAV (空仓 buffer)

PIT-strict: 每类 strategy 自己保证 (regime_state + Codex Phase 2.x).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from services.strategies.regime.regime_state import RegimeVerdict


@dataclass(frozen=True)
class EnsembleVerdict:
    signal_date: str
    regime_state: str
    weights: dict[str, float]
    top_k_codes: list[str]
    top_k_scores: list[float]
    cash_pct: float
    detail: dict[str, Any]


def normalize_scores(scores: pd.Series) -> pd.Series:
    """Min-max normalize to [0, 1]. NaN → 0."""
    s = scores.fillna(0.0)
    lo, hi = s.min(), s.max()
    if hi - lo < 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - lo) / (hi - lo)


def ensemble_scores(
    *,
    signal_date: str,
    regime: RegimeVerdict,
    lambdamart_scores: pd.Series | None = None,
    sniper_scores: pd.Series | None = None,
    institution_scores: pd.Series | None = None,
    max_positions: int = 5,
) -> EnsembleVerdict:
    """Compute MSAF ensemble verdict.

    Args:
        signal_date: YYYY-MM-DD
        regime: RegimeVerdict from regime_state.compute_regime_state()
        lambdamart_scores: pd.Series indexed by stock_code, ml_score float
        sniper_scores: pd.Series indexed by stock_code, confluence 0-7
        institution_scores: pd.Series indexed by stock_code, composite float
        max_positions: top-K from ensemble (default 5)

    Returns:
        EnsembleVerdict with top_k_codes + cash_pct
    """
    weights = regime.weights
    cash_pct = weights.get("cash", 0.0)

    # Crash regime → all cash
    if cash_pct >= 1.0 or regime.state == "crash":
        return EnsembleVerdict(
            signal_date=signal_date,
            regime_state=regime.state,
            weights=weights,
            top_k_codes=[],
            top_k_scores=[],
            cash_pct=1.0,
            detail={"reason": "crash regime / all cash"},
        )

    # Collect union of all candidate stocks
    sources: dict[str, pd.Series] = {}
    if lambdamart_scores is not None and len(lambdamart_scores) > 0:
        sources["lambdamart"] = normalize_scores(lambdamart_scores)
    if sniper_scores is not None and len(sniper_scores) > 0:
        sources["sniper"] = normalize_scores(sniper_scores)
    if institution_scores is not None and len(institution_scores) > 0:
        sources["institution"] = normalize_scores(institution_scores)

    if not sources:
        return EnsembleVerdict(
            signal_date=signal_date,
            regime_state=regime.state,
            weights=weights,
            top_k_codes=[],
            top_k_scores=[],
            cash_pct=1.0,
            detail={"reason": "no strategy scores available"},
        )

    # Union of all stock codes
    all_codes = set()
    for s in sources.values():
        all_codes.update(s.index)

    # Compute weighted ensemble score per stock
    ensemble = pd.Series(0.0, index=sorted(all_codes), name="ensemble_score")
    for name, scores in sources.items():
        w = weights.get(name, 0.0)
        if w <= 0:
            continue
        aligned = scores.reindex(ensemble.index).fillna(0.0)
        ensemble += w * aligned

    # Top-K selection. cash_pct 不全 = stocks_pct, max_positions 缩放.
    stocks_pct = 1.0 - cash_pct
    effective_k = max(1, int(round(max_positions * stocks_pct / 1.0)))

    top_k = ensemble.nlargest(effective_k)
    return EnsembleVerdict(
        signal_date=signal_date,
        regime_state=regime.state,
        weights=weights,
        top_k_codes=top_k.index.tolist(),
        top_k_scores=top_k.values.tolist(),
        cash_pct=cash_pct,
        detail={
            "sources_used": list(sources.keys()),
            "candidates_total": len(all_codes),
            "effective_k": effective_k,
        },
    )


__all__ = ["EnsembleVerdict", "ensemble_scores", "normalize_scores"]
