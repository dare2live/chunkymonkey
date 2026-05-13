"""海龟突破法 — 20 日 / 55 日双 variant.

经典 Stan Weinstein 系趋势跟踪策略:
  - close 突破前 N 日 (不含当日) close 最高 + 量能放大确认
  - 触发后 ATR 止损 / 三档加仓位由 stock_turtle_engine 提供 (本公式只产信号)

ATR 不在本公式计算 (避免与 stock_turtle_engine 重复, 信号日的 ATR 由后续 trade plan 引擎查表)。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from services.formula_engine.base import (
    FormulaMetadata,
    FormulaSignal,
    register_formula,
    sma,
)


VOLUME_MULTIPLE = 1.3   # 量比 > 1.3 算放量确认 (海龟原版要求量增)


def _rolling_max_excluding_today(values: np.ndarray, window: int) -> np.ndarray:
    """对每个 i 返回 max(values[i-window : i]) (不含当日)。前 window 行为 nan。"""
    n = len(values)
    out = np.full(n, np.nan)
    if n <= window:
        return out
    for i in range(window, n):
        out[i] = float(values[i - window:i].max())
    return out


@dataclass(frozen=True)
class TurtleBreakout20:
    metadata: FormulaMetadata = FormulaMetadata(
        formula_id="turtle_breakout_20",
        name="海龟突破 20 日",
        tag="T2",
        description="close 突破前 20 日 close 最高 + 量能放大",
        default_horizon_days=20,
        has_variant=True,
    )
    window: int = 20

    def compute_signals(
        self,
        code: str,
        dates: np.ndarray,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        amounts: np.ndarray,
    ) -> list[FormulaSignal]:
        n = len(closes)
        if n <= self.window + 1:
            return []

        prev_max = _rolling_max_excluding_today(closes, self.window)
        breakout = closes > prev_max  # 当日 close 突破前 N 日最高

        # 量能: volume > MA20(volume) × VOLUME_MULTIPLE
        vol_ma20 = sma(volumes, 20)
        vol_confirm = (volumes > VOLUME_MULTIPLE * vol_ma20) & ~np.isnan(vol_ma20)

        triggers = breakout & vol_confirm

        signals: list[FormulaSignal] = []
        for i in np.where(triggers)[0]:
            if np.isnan(prev_max[i]) or prev_max[i] <= 0:
                continue
            # strength: 突破幅度比例 + 量比 (capped 0-1)
            breakout_pct = (closes[i] - prev_max[i]) / prev_max[i]
            vol_ratio = volumes[i] / max(vol_ma20[i], 1.0)
            strength = float(min(1.0, max(0.05, breakout_pct * 10 + (vol_ratio - 1.0) * 0.3)))

            reason_codes = (
                f"close_above_{self.window}d_high:{breakout_pct:.3%}",
                f"volume_above_ma20:{vol_ratio:.2f}x",
            )
            signals.append(
                FormulaSignal(
                    stock_code=code,
                    date=str(dates[i]),
                    formula_id=self.metadata.formula_id,
                    formula_variant=self.metadata.formula_id,
                    strength=strength,
                    state=None,
                    reason_codes=reason_codes,
                )
            )
        return signals


@dataclass(frozen=True)
class TurtleBreakout55:
    metadata: FormulaMetadata = FormulaMetadata(
        formula_id="turtle_breakout_55",
        name="海龟突破 55 日",
        tag="T5",
        description="close 突破前 55 日 close 最高 + 量能放大",
        default_horizon_days=30,
        has_variant=True,
    )
    window: int = 55

    def compute_signals(
        self,
        code: str,
        dates: np.ndarray,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        volumes: np.ndarray,
        amounts: np.ndarray,
    ) -> list[FormulaSignal]:
        # 复用 TurtleBreakout20 的逻辑,只换 window
        n = len(closes)
        if n <= self.window + 1:
            return []
        prev_max = _rolling_max_excluding_today(closes, self.window)
        breakout = closes > prev_max
        vol_ma20 = sma(volumes, 20)
        vol_confirm = (volumes > VOLUME_MULTIPLE * vol_ma20) & ~np.isnan(vol_ma20)
        triggers = breakout & vol_confirm
        signals: list[FormulaSignal] = []
        for i in np.where(triggers)[0]:
            if np.isnan(prev_max[i]) or prev_max[i] <= 0:
                continue
            breakout_pct = (closes[i] - prev_max[i]) / prev_max[i]
            vol_ratio = volumes[i] / max(vol_ma20[i], 1.0)
            strength = float(min(1.0, max(0.05, breakout_pct * 8 + (vol_ratio - 1.0) * 0.4)))
            reason_codes = (
                f"close_above_{self.window}d_high:{breakout_pct:.3%}",
                f"volume_above_ma20:{vol_ratio:.2f}x",
            )
            signals.append(
                FormulaSignal(
                    stock_code=code,
                    date=str(dates[i]),
                    formula_id=self.metadata.formula_id,
                    formula_variant=self.metadata.formula_id,
                    strength=strength,
                    state=None,
                    reason_codes=reason_codes,
                )
            )
        return signals


register_formula(TurtleBreakout20())
register_formula(TurtleBreakout55())
