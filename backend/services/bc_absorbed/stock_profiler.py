"""Layer 0: 股票画像 — 按行为特征给股票分类, 供 Layer 1-3 按 profile 差异化策略.

独立模块, 独立测试, 独立调参. 不依赖 Layer 1/2/3.

画像维度 (全部从 K 线 + SmartMoney 表计算, 不硬编码):
  1. trend_regime: 趋势状态 (uptrend / downtrend / sideways)
  2. volatility_rank: 波动率分位 (high / mid / low)
  3. volume_regime: 量能状态 (expanding / shrinking / flat)
  4. price_position: 价格位置 (near_high / mid_range / near_low)
  5. technical_stage: 技术阶段 (Weinstein 1-4)
  6. board: 板块 (main / cyb / kcb)

用法:
    from stock_profiler import StockProfiler
    profiler = StockProfiler(config)
    profile = profiler.compute(code, close, high, low, volume)
    # profile = {'trend_regime': 'uptrend', 'volatility_rank': 'mid', ...}
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class StockProfile:
    code: str
    trend_regime: str       # uptrend / downtrend / sideways
    volatility_rank: str    # high / mid / low
    volume_regime: str      # expanding / shrinking / flat
    price_position: str     # near_high / mid_range / near_low
    technical_stage: int    # 1=accumulation, 2=markup, 3=distribution, 4=decline
    board: str              # main / cyb / kcb
    raw_metrics: dict       # 原始数值供调参

    def tags(self) -> list[str]:
        return [self.trend_regime, self.volatility_rank, self.volume_regime,
                self.price_position, f"stage{self.technical_stage}", self.board]


DEFAULT_CONFIG: dict[str, Any] = {
    "ma_short": 20,
    "ma_long": 60,
    "ma_trend": 120,
    "vol_lookback": 20,
    "vol_expanding_ratio": 1.3,
    "vol_shrinking_ratio": 0.7,
    "high_lookback": 250,
    "volatility_high_pct": 0.70,
    "volatility_low_pct": 0.30,
    "sideways_slope_threshold": 0.001,
}


def _ma(arr: np.ndarray, w: int) -> np.ndarray:
    out = np.full(len(arr), np.nan, dtype=np.float64)
    if len(arr) >= w:
        kernel = np.ones(w, dtype=np.float64) / w
        out[w - 1:] = np.convolve(arr.astype(np.float64), kernel, mode="valid")
    return out


def _slope(arr: np.ndarray, w: int) -> float:
    if len(arr) < w or w < 2:
        return 0.0
    segment = arr[-w:]
    valid = segment[np.isfinite(segment)]
    if len(valid) < 2:
        return 0.0
    x = np.arange(len(valid), dtype=np.float64)
    mean_x = np.mean(x)
    mean_y = np.mean(valid)
    denom = np.sum((x - mean_x) ** 2)
    if denom == 0:
        return 0.0
    return float(np.sum((x - mean_x) * (valid - mean_y)) / denom / (mean_y if mean_y != 0 else 1))


def compute_trend_regime(close: np.ndarray, cfg: dict) -> tuple[str, dict]:
    ma_s = _ma(close, cfg["ma_short"])
    ma_l = _ma(close, cfg["ma_long"])
    ma_t = _ma(close, cfg["ma_trend"])
    slope_t = _slope(ma_t, 20)
    last_c = float(close[-1]) if len(close) > 0 else 0
    last_ma_s = float(ma_s[-1]) if np.isfinite(ma_s[-1]) else last_c
    last_ma_l = float(ma_l[-1]) if np.isfinite(ma_l[-1]) else last_c
    last_ma_t = float(ma_t[-1]) if np.isfinite(ma_t[-1]) else last_c

    if last_c > last_ma_l and last_ma_s > last_ma_l and slope_t > cfg["sideways_slope_threshold"]:
        regime = "uptrend"
    elif last_c < last_ma_l and last_ma_s < last_ma_l and slope_t < -cfg["sideways_slope_threshold"]:
        regime = "downtrend"
    else:
        regime = "sideways"

    return regime, {"ma_short": last_ma_s, "ma_long": last_ma_l, "ma_trend": last_ma_t, "slope_trend": slope_t}


def compute_volatility_rank(close: np.ndarray, cfg: dict) -> tuple[str, dict]:
    if len(close) < 22:
        return "mid", {"atr_pct": 0, "rank": 0.5}
    returns = np.diff(np.log(close[-cfg["high_lookback"]:]))
    vol_20d = float(np.std(returns[-20:])) * np.sqrt(252)
    vol_full = np.array([float(np.std(returns[i:i + 20])) * np.sqrt(252)
                         for i in range(0, len(returns) - 19)])
    if len(vol_full) == 0:
        return "mid", {"annual_vol": vol_20d, "rank": 0.5}
    rank = float(np.mean(vol_full <= vol_20d))
    if rank >= cfg["volatility_high_pct"]:
        label = "high"
    elif rank <= cfg["volatility_low_pct"]:
        label = "low"
    else:
        label = "mid"
    return label, {"annual_vol": vol_20d, "rank": rank}


def compute_volume_regime(volume: np.ndarray, cfg: dict) -> tuple[str, dict]:
    lb = cfg["vol_lookback"]
    if len(volume) < lb * 2:
        return "flat", {"vol_ratio": 1.0}
    recent = float(np.mean(volume[-lb:]))
    prior = float(np.mean(volume[-lb * 2:-lb]))
    ratio = recent / prior if prior > 0 else 1.0
    if ratio >= cfg["vol_expanding_ratio"]:
        label = "expanding"
    elif ratio <= cfg["vol_shrinking_ratio"]:
        label = "shrinking"
    else:
        label = "flat"
    return label, {"vol_ratio": ratio}


def compute_price_position(close: np.ndarray, high: np.ndarray, low: np.ndarray, cfg: dict) -> tuple[str, dict]:
    lb = cfg["high_lookback"]
    if len(close) < lb:
        lb = len(close)
    if lb < 5:
        return "mid_range", {"position_pct": 0.5}
    hi = float(np.max(high[-lb:]))
    lo = float(np.min(low[-lb:]))
    last = float(close[-1])
    rng = hi - lo
    pct = (last - lo) / rng if rng > 0 else 0.5
    if pct >= 0.8:
        label = "near_high"
    elif pct <= 0.2:
        label = "near_low"
    else:
        label = "mid_range"
    return label, {"position_pct": pct, "high_250": hi, "low_250": lo}


def compute_technical_stage(close: np.ndarray, volume: np.ndarray, cfg: dict) -> tuple[int, dict]:
    ma_l = _ma(close, cfg["ma_long"])
    if not np.isfinite(ma_l[-1]):
        return 1, {"stage_reason": "insufficient data"}
    last_c = float(close[-1])
    last_ma = float(ma_l[-1])
    slope = _slope(ma_l, 20)
    above = last_c > last_ma
    if above and slope > cfg["sideways_slope_threshold"]:
        return 2, {"stage_reason": "price above rising MA"}
    if above and slope <= cfg["sideways_slope_threshold"]:
        return 3, {"stage_reason": "price above flat/falling MA"}
    if not above and slope < -cfg["sideways_slope_threshold"]:
        return 4, {"stage_reason": "price below falling MA"}
    return 1, {"stage_reason": "price below rising/flat MA (accumulation)"}


def compute_board(code: str) -> str:
    if code.startswith("30"):
        return "cyb"
    if code.startswith("68"):
        return "kcb"
    return "main"


class StockProfiler:
    def __init__(self, config: dict[str, Any] | None = None):
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}

    def compute(
        self,
        code: str,
        close: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        volume: np.ndarray,
    ) -> StockProfile:
        trend, trend_m = compute_trend_regime(close, self.cfg)
        vol_rank, vol_m = compute_volatility_rank(close, self.cfg)
        vol_regime, volr_m = compute_volume_regime(volume, self.cfg)
        pos, pos_m = compute_price_position(close, high, low, self.cfg)
        stage, stage_m = compute_technical_stage(close, volume, self.cfg)
        board = compute_board(code)
        return StockProfile(
            code=code,
            trend_regime=trend,
            volatility_rank=vol_rank,
            volume_regime=vol_regime,
            price_position=pos,
            technical_stage=stage,
            board=board,
            raw_metrics={**trend_m, **vol_m, **volr_m, **pos_m, **stage_m},
        )

    def compute_batch(
        self,
        stocks: dict[str, dict],
    ) -> dict[str, StockProfile]:
        return {
            code: self.compute(code, s["close"], s["high"], s["low"], s["volume"])
            for code, s in stocks.items()
            if len(s.get("close", [])) >= 60
        }
