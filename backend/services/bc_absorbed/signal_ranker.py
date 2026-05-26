"""Layer 2: 共振评分器 — 多公式信号合并评分, 独立模块, 独立调参.

不依赖 Layer 3 (股票池). 输入 Layer 1 各公式信号, 输出每只股票每天的 composite_score.

评分维度:
  1. resonance: 共振度 — 同窗口内多少公式同时发信号 (越多越强)
  2. formula_quality: 公式历史胜率 (可选, 需外部传入)
  3. vol_price_confirm: 量价确认 — 信号日放量 + 涨幅

用法:
    from signal_ranker import SignalRanker
    ranker = SignalRanker(config)
    scored = ranker.score(signals, profiles=None)
    # scored = [{'code': '300616', 'date_idx': 230, 'score': 0.85, 'formulas': ['gs_raw_buy', 'obv'], ...}]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class ScoredSignal:
    code: str
    date_idx: int
    score: float
    resonance_count: int
    formulas: list[str]
    detail: dict[str, Any] = field(default_factory=dict)


DEFAULT_CONFIG: dict[str, Any] = {
    "resonance_window": 1,
    "w_resonance": 0.40,
    "w_quality": 0.30,
    "w_vol_price": 0.30,
    "min_resonance": 1,
    "default_formula_quality": 0.5,
}


class SignalRanker:
    def __init__(self, config: dict[str, Any] | None = None):
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}

    def score(
        self,
        formula_signals: dict[str, np.ndarray],
        *,
        close: np.ndarray | None = None,
        volume: np.ndarray | None = None,
        volume_ma20: np.ndarray | None = None,
        formula_quality: dict[str, float] | None = None,
    ) -> list[ScoredSignal]:
        """Score signals from multiple formulas for ONE stock.

        Args:
            formula_signals: {formula_id: entry_bool_array}
            close: close prices (for vol_price scoring)
            volume: volume array
            volume_ma20: precomputed MA20 of volume
            formula_quality: {formula_id: historical_win_rate} (optional)
        """
        fq = formula_quality or {}
        window = self.cfg["resonance_window"]
        default_q = self.cfg["default_formula_quality"]
        n = max((len(v) for v in formula_signals.values()), default=0)
        if n == 0:
            return []

        all_entry_indices: dict[int, list[str]] = {}
        for fid, entry_arr in formula_signals.items():
            for idx in np.where(entry_arr)[0]:
                idx = int(idx)
                for offset in range(-window, window + 1):
                    target = idx + offset
                    if 0 <= target < n:
                        all_entry_indices.setdefault(target, [])
                        if fid not in all_entry_indices[target]:
                            all_entry_indices[target].append(fid)

        results: list[ScoredSignal] = []
        for date_idx, formulas in sorted(all_entry_indices.items()):
            resonance = len(formulas)
            if resonance < self.cfg["min_resonance"]:
                continue

            s_resonance = min(resonance / 5.0, 1.0)
            s_quality = float(np.mean([fq.get(f, default_q) for f in formulas]))

            s_vol_price = 0.5
            if close is not None and volume is not None and date_idx > 0 and date_idx < len(close):
                ret = (close[date_idx] - close[date_idx - 1]) / close[date_idx - 1] if close[date_idx - 1] > 0 else 0
                vol_r = 1.0
                if volume_ma20 is not None and date_idx < len(volume_ma20) and volume_ma20[date_idx] > 0:
                    vol_r = volume[date_idx] / volume_ma20[date_idx]
                s_vol_price = min((max(ret, 0) * 10 + min(vol_r / 3.0, 1.0)) / 2, 1.0)

            w = self.cfg
            composite = (w["w_resonance"] * s_resonance +
                         w["w_quality"] * s_quality +
                         w["w_vol_price"] * s_vol_price)

            results.append(ScoredSignal(
                code="",
                date_idx=date_idx,
                score=round(composite, 4),
                resonance_count=resonance,
                formulas=formulas,
                detail={"s_resonance": round(s_resonance, 3),
                        "s_quality": round(s_quality, 3),
                        "s_vol_price": round(s_vol_price, 3)},
            ))

        results.sort(key=lambda s: -s.score)
        return results

    def score_multi_stock(
        self,
        all_signals: dict[str, dict[str, np.ndarray]],
        stocks_data: dict[str, dict] | None = None,
        formula_quality: dict[str, float] | None = None,
    ) -> list[ScoredSignal]:
        """Score signals across multiple stocks. Returns sorted by score desc."""
        results: list[ScoredSignal] = []
        for code, formula_signals in all_signals.items():
            stock = (stocks_data or {}).get(code, {})
            close = stock.get("close")
            volume = stock.get("volume")
            vma20 = None
            if volume is not None and len(volume) >= 20:
                kernel = np.ones(20, dtype=np.float64) / 20
                vma20 = np.full(len(volume), np.nan)
                vma20[19:] = np.convolve(volume.astype(np.float64), kernel, "valid")
            scored = self.score(
                formula_signals,
                close=close, volume=volume, volume_ma20=vma20,
                formula_quality=formula_quality,
            )
            for s in scored:
                s.code = code
            results.extend(scored)
        results.sort(key=lambda s: -s.score)
        return results
