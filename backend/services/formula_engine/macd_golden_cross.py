"""MACD 金叉公式 — 区分 0 轴上方 (强势) vs 0 轴下方 (反转初期) 两个 variant。

触发条件:
  DIF 上穿 DEA (任意 DIF 位置, 不再过滤负值)

variants (用 formula_variant 区分):
  macd_golden_cross_above_zero: DIF >= 0 时金叉 → 强势加强信号
  macd_golden_cross_below_zero: DIF < 0  时金叉 → 反转初期信号

state:
  just_crossed:  当日金叉
  compute_state_history() 额外导出 holding / imminent 诊断态, 写入独立 mart
  其中 state history 的持仓窗口可以比 trigger 口径稍宽, 只影响诊断候选供给

参数:
  EMA12 / EMA26 / EMA9 (通达信标准)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from services.formula_engine.base import (
    FormulaMetadata,
    FormulaSignal,
    cross_down,
    cross_up,
    ema,
    register_formula,
)


CROSS_WINDOW = 5         # 多少日内算 "刚" 金叉/死叉
IMMINENT_DAYS = 10       # state history 持仓窗口 (诊断层可略宽于 trigger 口径)
IMMINENT_GAP_RATIO = 0.012  # |gap|/close < 该值算 imminent


@dataclass(frozen=True)
class MacdGoldenCross:
    metadata: FormulaMetadata = FormulaMetadata(
        formula_id="macd_golden_cross",
        name="MACD 金叉",
        tag="MA",
        description="DIF 上穿 DEA, 区分 0 轴上方 (强势) vs 0 轴下方 (反转) 两 variant",
        default_horizon_days=10,
        has_state=True,
    )

    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9

    def _macd_components(self, closes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """计算 MACD 三线 + 金叉/死叉掩码。"""
        ema_fast = ema(closes, self.fast_period)
        ema_slow = ema(closes, self.slow_period)
        dif = ema_fast - ema_slow
        dea = ema(dif, self.signal_period)
        crossed_up = cross_up(dif, dea)
        crossed_down = cross_down(dif, dea)
        return dif, dea, crossed_up, crossed_down

    def _variant_for_dif(self, dif_value: float) -> str:
        return (
            "macd_golden_cross_above_zero"
            if dif_value >= 0
            else "macd_golden_cross_below_zero"
        )

    def _signal_strength(self, *, gap: float, close_now: float, dif_value: float) -> float:
        """把 MACD gap / DIF 压成 0-1 强度。"""
        gap_ratio = gap / close_now
        dif_ratio = dif_value / close_now
        return float(min(1.0, max(0.05, gap_ratio * 50 + dif_ratio * 20)))

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
        if n < self.slow_period + self.signal_period + 1:
            return []

        dif, dea, crossed_up, crossed_down = self._macd_components(closes)

        signals: list[FormulaSignal] = []

        for i in range(n):
            # 触发条件: 当日金叉 (不再过滤 DIF 符号, 用 variant 区分上下轴)
            if not crossed_up[i]:
                continue

            close_now = closes[i]
            if close_now <= 0:
                continue

            gap = dif[i] - dea[i]
            state = "just_crossed"
            variant = self._variant_for_dif(float(dif[i]))
            strength = self._signal_strength(gap=gap, close_now=close_now, dif_value=float(dif[i]))

            reason_codes = (
                f"dif_above_dea:{gap:.4f}",
                f"dif_{'above' if dif[i] >= 0 else 'below'}_zero:{dif[i]:.4f}",
                f"ema{self.fast_period}_x_ema{self.slow_period}",
            )

            signals.append(
                FormulaSignal(
                    stock_code=code,
                    date=str(dates[i]),
                    formula_id=self.metadata.formula_id,
                    formula_variant=variant,
                    strength=strength,
                    state=state,
                    reason_codes=reason_codes,
                )
                )

        return signals

    def compute_state_history(
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
        """导出 MACD state history。

        只输出用于候选供给诊断的 active states:
          - imminent: gap 极小, 接近金叉
          - holding:  已进入金叉后窗口, 仍在 DEA 上方 (诊断层窗口略宽于 trigger)

        cross_up 当日由 compute_signals 负责落 fact_technical_trigger,
        这里不重复写 just_crossed, 以免 stage-opt audit 双算。
        """
        n = len(closes)
        if n < self.slow_period + self.signal_period + 1:
            return []

        dif, dea, crossed_up, crossed_down = self._macd_components(closes)
        state_rows: list[FormulaSignal] = []
        holding_window = max(CROSS_WINDOW, IMMINENT_DAYS)
        last_cross_up = -holding_window - 1

        for i in range(n):
            if crossed_up[i]:
                last_cross_up = i
                continue
            if crossed_down[i]:
                continue

            close_now = float(closes[i])
            if close_now <= 0:
                continue

            gap = float(dif[i] - dea[i])
            gap_ratio = abs(gap) / close_now
            is_above = float(dif[i]) >= 0
            variant = self._variant_for_dif(float(dif[i]))

            state: str | None = None
            if last_cross_up >= 0 and i - last_cross_up <= holding_window and dif[i] > dea[i]:
                state = "holding"
                strength = self._signal_strength(gap=gap, close_now=close_now, dif_value=float(dif[i]))
            elif gap_ratio <= IMMINENT_GAP_RATIO:
                state = "imminent"
                strength = float(min(1.0, max(0.15, 0.75 - gap_ratio * 25)))
            else:
                continue

            reason_codes = (
                f"macd_state:{state}",
                f"dif_{'above' if is_above else 'below'}_zero:{dif[i]:.4f}",
                f"gap_ratio:{gap_ratio:.4f}",
                f"ema{self.fast_period}_x_ema{self.slow_period}",
            )
            state_rows.append(
                FormulaSignal(
                    stock_code=code,
                    date=str(dates[i]),
                    formula_id=self.metadata.formula_id,
                    formula_variant=variant,
                    strength=strength,
                    state=state,
                    reason_codes=reason_codes,
                )
            )

        return state_rows


# 注册到全局 REGISTRY
register_formula(MacdGoldenCross())
