"""动态均线迭代金叉公式 — 用户提供 MQL 完整实现。

核心思想 (开发手册 §4.2.5):
  不直接用 K 线 cross 判断买卖 (噪音大),
  先用配置化迭代轮数过滤假突破/假回落,调整基础参考线 (原始 MQL 是 10 轮,当前仓库默认供给版做了收缩),
  最终 X_36 真上穿调整后的 X_3 才视为可信买点。

MQL 公式逐行翻译:
  X_1: (MA3+MA7+MA13+MA27)/4   四均线平均
  X_2: EMA(CLOSE, 5)            备用基线
  X_3: X_1 优先, 缺失则 X_2     基础参考线
  X_4: (H+L+2O+6C)/10           加权重心 (CLOSE 60%)
  X_5: 阴线/弱势 (4 种之一)     卖出确认
  X_6: 阳线/强势 (4 种之一)     买入确认
  X_7: CROSS(X_4, X_3) AND X_5  假突破
  X_8: CROSS(X_3, X_4) AND X_6  假回落
  X_9~X_36: 2 轮迭代调整 X_3 (默认供给版; 原始 MQL 10 轮过度收缩候选面)
    假突破: 当前参考 × 0.98
    假回落: 当前参考 × 1.02
    否则:    保持 X_4 (重心)
  X_44: CROSS(X_36, X_3)         最终买入信号
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from services.formula_engine.base import (
    FormulaMetadata,
    FormulaSignal,
    cross_up,
    ema,
    register_formula,
    sma,
)

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "formula_dynamic_ma_iterative.yaml"


def _load_yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def _load_config(path: Path | None = None) -> dict[str, float]:
    raw_path = path or CONFIG_PATH
    try:
        raw = _load_yaml(raw_path)
        return {
            "iterations": float(int(raw["iterations"])),
            "multiplier_up": float(raw["multiplier_up"]),
            "multiplier_down": float(raw["multiplier_down"]),
        }
    except KeyError as exc:
        raise ValueError(f"{raw_path.name}: missing dynamic_ma_iterative key {exc.args[0]}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{raw_path.name}: iterations/multipliers must be numeric") from exc


def _x5_bearish(opens, highs, lows, closes) -> np.ndarray:
    """X_5: 阴线/弱势 K (4 种之一)。"""
    n = len(closes)
    if n < 2:
        return np.zeros(n, dtype=bool)
    ref_close = np.concatenate([[closes[0]], closes[:-1]])
    ref_high = np.concatenate([[highs[0]], highs[:-1]])
    chg = np.where(ref_close > 0, closes / ref_close, 1.0)
    return (
        (closes < opens)
        | ((closes < ref_high) & (closes > opens))
        | ((closes >= opens) & ((highs - closes) >= (closes - opens)) & (chg < 1.02))
        | ((closes == opens) & ((highs - closes) >= (closes - lows)) & (chg < 1.05))
    )


def _x6_bullish(opens, highs, lows, closes) -> np.ndarray:
    """X_6: 阳线/强势 K (4 种之一)。"""
    n = len(closes)
    if n < 2:
        return np.zeros(n, dtype=bool)
    ref_close = np.concatenate([[closes[0]], closes[:-1]])
    ref_low = np.concatenate([[lows[0]], lows[:-1]])
    chg = np.where(ref_close > 0, closes / ref_close, 1.0)
    return (
        ((closes > opens) & (chg > 0.94))
        | ((closes > ref_low) & (closes < opens))
        | ((closes <= opens) & ((closes - lows) >= (opens - closes)) & (chg > 0.98))
        | ((closes == opens) & ((closes - lows) >= (highs - closes)) & (chg > 0.95))
    )


@dataclass(frozen=True)
class DynamicMaIterativeCross:
    metadata: FormulaMetadata = FormulaMetadata(
        formula_id="dynamic_ma_iterative_cross",
        name="动态均线迭代金叉",
        tag="DM",
        description="可配置 X36 迭代去噪 + CROSS(X36, X3) 触发 (供给优化版, 原始 MQL 为 10 轮)",
        default_horizon_days=15,
    )

    _config = _load_config()
    iterations: int = int(_config["iterations"])
    multiplier_up: float = _config["multiplier_up"]
    multiplier_down: float = _config["multiplier_down"]

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
        # 最长均线 27 日 + EMA(X_36, 5),给 50 日 warmup 余量
        if n < 50:
            return []

        # X_1: (MA3+MA7+MA13+MA27)/4
        ma3 = sma(closes, 3)
        ma7 = sma(closes, 7)
        ma13 = sma(closes, 13)
        ma27 = sma(closes, 27)
        x1 = (ma3 + ma7 + ma13 + ma27) / 4.0

        # X_2: EMA(CLOSE, 5)
        x2 = ema(closes, 5)

        # X_3: X_1 优先, 缺失则 X_2
        x3 = np.where(np.isnan(x1), x2, x1)

        # X_4: (H+L+2O+6C)/10
        x4 = (highs + lows + 2 * opens + 6 * closes) / 10.0

        # X_5 / X_6: K 线形态
        x5 = _x5_bearish(opens, highs, lows, closes)
        x6 = _x6_bullish(opens, highs, lows, closes)

        # 迭代: current 从 x4 开始, 每轮根据 CROSS(current, x3) 和 K 线性质调整
        current = x4.copy()
        for _ in range(self.iterations):
            cross_up_arr = cross_up(current, x3)
            cross_down_arr = cross_up(x3, current)  # x3 上穿 current = current 下穿 x3
            false_breakout = cross_up_arr & x5         # X_7
            false_pullback = cross_down_arr & x6       # X_8
            next_val = np.where(
                false_breakout, x3 * self.multiplier_down,
                np.where(false_pullback, x3 * self.multiplier_up, current),
            )
            current = next_val

        x36 = current

        # X_44: CROSS(X_36, X_3) — 最终买入信号
        triggers = cross_up(x36, x3)
        # 过滤 NaN (warmup 期)
        valid = ~(np.isnan(x36) | np.isnan(x3))
        triggers = triggers & valid

        signals: list[FormulaSignal] = []
        for i in np.where(triggers)[0]:
            if x3[i] <= 0 or closes[i] <= 0:
                continue
            # strength: X_36 / X_3 偏离比例 + 当日 X_6 强势确认
            deviation = (x36[i] - x3[i]) / x3[i]
            strength = float(min(1.0, max(0.1, deviation * 100)))
            if x6[i]:
                strength = min(1.0, strength + 0.15)

            reason_codes = (
                f"x36_cross_x3:dev={deviation:.4f}",
                f"iterations={self.iterations}",
                "bullish_k" if x6[i] else "neutral_k",
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


register_formula(DynamicMaIterativeCross())
