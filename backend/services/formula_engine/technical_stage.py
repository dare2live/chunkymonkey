"""Stan Weinstein 4-stage 技术形态分类 — 纯规则版 v1。

判定规则 (开发手册 §4.7):
  Stage 1 底部基础:  价格在 60 周低位 ±15% + 30/50 周线走平 + 量能枯竭
  Stage 1.5 突破中:  突破 30 周线 + 量比 > 1.5 + 持续 1-10 日
  Stage 2 上升趋势:  MA10 > MA30 > MA50 (周线) + 价 > MA30 + 回撤 < 15%
  Stage 3 顶部分布:  价创新高但量背离 OR MA10 死叉 MA30 OR 距 MA30 偏离过大
  Stage 4 下跌趋势:  MA10 < MA30 < MA50 + 价 < MA30
  unknown: 数据不足
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from services.formula_engine.base import sma


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "technical_stage.yaml"
DEFAULT_RULES: dict[str, float | int] = {
    "ma_fast_days": 50,
    "ma_mid_days": 150,
    "ma_slow_days": 250,
    "range_lookback": 300,
    "breakout_recent_days": 10,
    "drawdown_max_stage2": 0.15,
    "drawdown_lookback_days": 60,
    "stage1_pos_max": 0.30,
    "stage1_slope_max_abs": 0.02,
    "stage1_vol_ratio_max": 0.8,
    "stage15_vol_ratio_min": 1.5,
    "stage15_recent_below_min_count": 2,
    "volume_ma_days": 20,
    "slope_lookback_days": 20,
    "stage3_price_above_ma_mid_multiple": 1.15,
    "stage3_slope_min": 0.0,
    "stage4_slope_max": -0.02,
}


def _load_yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return loaded


def _load_rules(path: Path | None = None) -> dict[str, float | int]:
    raw_path = path or CONFIG_PATH
    try:
        raw = _load_yaml(raw_path)
    except FileNotFoundError:
        return DEFAULT_RULES.copy()

    try:
        return {
            "ma_fast_days": int(raw.get("ma_fast_days", DEFAULT_RULES["ma_fast_days"])),
            "ma_mid_days": int(raw.get("ma_mid_days", DEFAULT_RULES["ma_mid_days"])),
            "ma_slow_days": int(raw.get("ma_slow_days", DEFAULT_RULES["ma_slow_days"])),
            "range_lookback": int(raw.get("range_lookback", DEFAULT_RULES["range_lookback"])),
            "breakout_recent_days": int(raw.get("breakout_recent_days", DEFAULT_RULES["breakout_recent_days"])),
            "drawdown_max_stage2": float(raw.get("drawdown_max_stage2", DEFAULT_RULES["drawdown_max_stage2"])),
            "drawdown_lookback_days": int(raw.get("drawdown_lookback_days", DEFAULT_RULES["drawdown_lookback_days"])),
            "stage1_pos_max": float(raw.get("stage1_pos_max", DEFAULT_RULES["stage1_pos_max"])),
            "stage1_slope_max_abs": float(raw.get("stage1_slope_max_abs", DEFAULT_RULES["stage1_slope_max_abs"])),
            "stage1_vol_ratio_max": float(raw.get("stage1_vol_ratio_max", DEFAULT_RULES["stage1_vol_ratio_max"])),
            "stage15_vol_ratio_min": float(raw.get("stage15_vol_ratio_min", DEFAULT_RULES["stage15_vol_ratio_min"])),
            "stage15_recent_below_min_count": int(raw.get("stage15_recent_below_min_count", DEFAULT_RULES["stage15_recent_below_min_count"])),
            "volume_ma_days": int(raw.get("volume_ma_days", DEFAULT_RULES["volume_ma_days"])),
            "slope_lookback_days": int(raw.get("slope_lookback_days", DEFAULT_RULES["slope_lookback_days"])),
            "stage3_price_above_ma_mid_multiple": float(raw.get("stage3_price_above_ma_mid_multiple", DEFAULT_RULES["stage3_price_above_ma_mid_multiple"])),
            "stage3_slope_min": float(raw.get("stage3_slope_min", DEFAULT_RULES["stage3_slope_min"])),
            "stage4_slope_max": float(raw.get("stage4_slope_max", DEFAULT_RULES["stage4_slope_max"])),
        }
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{raw_path.name}: technical_stage rules must be numeric mappings") from exc


TECHNICAL_STAGE_RULES = _load_rules()
MA_FAST_DAYS = int(TECHNICAL_STAGE_RULES["ma_fast_days"])
MA_MID_DAYS = int(TECHNICAL_STAGE_RULES["ma_mid_days"])
MA_SLOW_DAYS = int(TECHNICAL_STAGE_RULES["ma_slow_days"])
RANGE_LOOKBACK = int(TECHNICAL_STAGE_RULES["range_lookback"])
BREAKOUT_RECENT_DAYS = int(TECHNICAL_STAGE_RULES["breakout_recent_days"])
DRAWDOWN_MAX_STAGE2 = float(TECHNICAL_STAGE_RULES["drawdown_max_stage2"])
DRAWDOWN_LOOKBACK_DAYS = int(TECHNICAL_STAGE_RULES["drawdown_lookback_days"])
STAGE1_POS_MAX = float(TECHNICAL_STAGE_RULES["stage1_pos_max"])
STAGE1_SLOPE_MAX_ABS = float(TECHNICAL_STAGE_RULES["stage1_slope_max_abs"])
STAGE1_VOL_RATIO_MAX = float(TECHNICAL_STAGE_RULES["stage1_vol_ratio_max"])
STAGE15_VOL_RATIO_MIN = float(TECHNICAL_STAGE_RULES["stage15_vol_ratio_min"])
STAGE15_RECENT_BELOW_MIN_COUNT = int(TECHNICAL_STAGE_RULES["stage15_recent_below_min_count"])
VOLUME_MA_DAYS = int(TECHNICAL_STAGE_RULES["volume_ma_days"])
SLOPE_LOOKBACK_DAYS = int(TECHNICAL_STAGE_RULES["slope_lookback_days"])
STAGE3_PRICE_ABOVE_MA_MID_MULTIPLE = float(TECHNICAL_STAGE_RULES["stage3_price_above_ma_mid_multiple"])
STAGE3_SLOPE_MIN = float(TECHNICAL_STAGE_RULES["stage3_slope_min"])
STAGE4_SLOPE_MAX = float(TECHNICAL_STAGE_RULES["stage4_slope_max"])


def classify_technical_stage(
    closes: np.ndarray,
    volumes: np.ndarray,
) -> np.ndarray:
    """对单股 K 线计算每个日期的 technical_stage 标签。

    返回字符串 numpy array, 元素为: '1','1.5','2','3','4','unknown'
    """
    n = len(closes)
    out = np.full(n, "unknown", dtype="<U8")
    if n < MA_SLOW_DAYS:
        return out

    ma_fast = sma(closes, MA_FAST_DAYS)   # 10 周
    ma_mid = sma(closes, MA_MID_DAYS)     # 30 周
    ma_slow = sma(closes, MA_SLOW_DAYS)   # 50 周

    # 用 MA_MID 斜率近似走平判定
    slope_mid = np.full(n, np.nan)
    for i in range(MA_MID_DAYS + SLOPE_LOOKBACK_DAYS, n):
        slope_mid[i] = (ma_mid[i] - ma_mid[i - SLOPE_LOOKBACK_DAYS]) / max(ma_mid[i - SLOPE_LOOKBACK_DAYS], 1e-9)

    # 60 周高低区间位置
    range_pos = np.full(n, np.nan)
    for i in range(RANGE_LOOKBACK, n):
        window = closes[i - RANGE_LOOKBACK:i]
        lo, hi = window.min(), window.max()
        if hi > lo:
            range_pos[i] = (closes[i] - lo) / (hi - lo)

    # 量比 (20 日均量)
    vol_ma20 = sma(volumes, VOLUME_MA_DAYS)
    vol_ratio = np.where((vol_ma20 > 0) & ~np.isnan(vol_ma20), volumes / vol_ma20, 1.0)

    # 60 日回撤
    drawdown_60d = np.full(n, 0.0)
    for i in range(DRAWDOWN_LOOKBACK_DAYS, n):
        window = closes[i - DRAWDOWN_LOOKBACK_DAYS:i + 1]
        peak = window.max()
        drawdown_60d[i] = (closes[i] - peak) / peak

    for i in range(MA_SLOW_DAYS, n):
        c = closes[i]
        mf, mm, ms = ma_fast[i], ma_mid[i], ma_slow[i]
        if np.isnan(mf) or np.isnan(mm) or np.isnan(ms):
            continue
        slope = slope_mid[i] if not np.isnan(slope_mid[i]) else 0.0
        pos = range_pos[i] if not np.isnan(range_pos[i]) else 0.5

        # Stage 4 下跌趋势: MA10 < MA30 < MA50, 价 < MA30, 斜率明显向下
        if mf < mm < ms and c < mm and slope < STAGE4_SLOPE_MAX:
            out[i] = "4"
            continue
        # Stage 1 底部基础: 60 周低位 + MA30 走平 + 量能枯竭
        if pos < STAGE1_POS_MAX and abs(slope) < STAGE1_SLOPE_MAX_ABS and vol_ratio[i] < STAGE1_VOL_RATIO_MAX:
            out[i] = "1"
            continue
        # Stage 1.5 突破中: 突破 MA30 + 量比 > 1.5 + 最近 10 日内 (从下方上穿)
        below_ma30_recent = False
        if i >= BREAKOUT_RECENT_DAYS:
            recent_below = np.sum(closes[i - BREAKOUT_RECENT_DAYS:i] < ma_mid[i - BREAKOUT_RECENT_DAYS:i])
            below_ma30_recent = recent_below >= STAGE15_RECENT_BELOW_MIN_COUNT
        if c > mm and below_ma30_recent and vol_ratio[i] > STAGE15_VOL_RATIO_MIN:
            out[i] = "1.5"
            continue
        # Stage 2 上升趋势: MA10>MA30>MA50, 价>MA30, 回撤<15%
        if mf > mm > ms and c > mm and drawdown_60d[i] > -DRAWDOWN_MAX_STAGE2:
            out[i] = "2"
            continue
        # Stage 3 顶部分布: 距 MA30 偏离大 + MA10 开始下穿 MA30
        if c > mm * STAGE3_PRICE_ABOVE_MA_MID_MULTIPLE or (mf < mm and slope > STAGE3_SLOPE_MIN):
            out[i] = "3"
            continue
        # 其他默认 unknown (兜底)

    return out
