"""MACD 金叉公式 — 区分 0 轴上方 (强势) vs 0 轴下方 (反转初期) 两个 variant。

触发条件:
  DIF 上穿 DEA (任意 DIF 位置, 不再过滤负值)

variants (用 formula_variant 区分):
  macd_golden_cross_above_zero: DIF >= 0 时金叉 → 强势加强信号
  macd_golden_cross_below_zero: DIF < 0  时金叉 → 反转初期信号

state:
  just_crossed:  当日金叉
  (其他态 (holding/imminent/just_dead) 暂留接口, 当前只发 just_crossed)

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
IMMINENT_DAYS = 5        # 即将金叉的窗口
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

        # MACD 三线
        ema_fast = ema(closes, self.fast_period)
        ema_slow = ema(closes, self.slow_period)
        dif = ema_fast - ema_slow
        dea = ema(dif, self.signal_period)

        # 上穿 / 下穿
        crossed_up = cross_up(dif, dea)
        crossed_down = cross_down(dif, dea)

        signals: list[FormulaSignal] = []

        # 跟踪最近一次金叉/死叉位置
        last_up = -CROSS_WINDOW - 1
        last_down = -CROSS_WINDOW - 1

        for i in range(n):
            if crossed_up[i]:
                last_up = i
            if crossed_down[i]:
                last_down = i

            # 触发条件: 当日金叉 (不再过滤 DIF 符号, 用 variant 区分上下轴)
            if not crossed_up[i]:
                continue

            close_now = closes[i]
            if close_now <= 0:
                continue

            gap = dif[i] - dea[i]
            state = "just_crossed"
            # 0 轴判定 → variant
            is_above = dif[i] >= 0
            variant = (
                "macd_golden_cross_above_zero" if is_above
                else "macd_golden_cross_below_zero"
            )

            # strength: 基于 gap 比例 + DIF 绝对值 (下轴 DIF 负 → 减 strength)
            gap_ratio = gap / close_now
            dif_ratio = dif[i] / close_now  # 下轴时为负, 自然降权
            strength = float(min(1.0, max(0.05, gap_ratio * 50 + dif_ratio * 20)))

            reason_codes = (
                f"dif_above_dea:{gap:.4f}",
                f"dif_{'above' if is_above else 'below'}_zero:{dif[i]:.4f}",
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


# 注册到全局 REGISTRY
register_formula(MacdGoldenCross())
