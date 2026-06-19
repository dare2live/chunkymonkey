"""主升浪候选检测共享原语 (pivot 底 / 前瞻涨幅 / 长底 / forward 完整) — 单一真相源。

owner=backend/scripts/build_rally_ground_truth.py (语义来源) + analysis/data_validation_backtest_plan_20260619.md。
缘起 (A0 地基止血 #d): 负样本生成需与 GT 正样本**同一候选检测口径** (否则正负不可比)。把 pivot/gain/base/
  forward 原语抽到此处, 负样本 generator 与 (待重构) GT builder 共用, 防双真相源漂移 (mio: 能删必删/单算点)。
PIT 边界: 候选定义 (pivot ±win 确认) 含 forward (这是 LABEL 侧, 非特征); 训练特征只用 <=i 信息 (features.py)。

常量镜像 build_rally_ground_truth (用户图样型口径); TODO 统一: lock 释放后重构 GT builder 调本模块 + 重跑验 9070。
"""
from __future__ import annotations

from bisect import bisect_right

LOWWIN = 20          # rule-compliance: ok evidence=波段底确认窗(前后20日最低), 镜像 build_rally_ground_truth
MAXFWD = 250         # rule-compliance: ok evidence=底→顶前瞻上限(~1年), 镜像 build_rally_ground_truth
GAIN = 0.60          # rule-compliance: ok evidence=用户口述底→顶>60%(MASTER§5), 镜像 build_rally_ground_truth
BASEMIN = 40         # rule-compliance: ok evidence=长底>=40日盘整, 镜像 build_rally_ground_truth
BASE_LOOKBACK = 120  # rule-compliance: ok evidence=长底回看窗(底前120日), 镜像 build_rally_ground_truth


def is_pivot_low(lows: list, i: int, win: int = LOWWIN) -> bool:
    """lows[i] 是否 [i-win, i+win] 区间最低 (波段底)。±win 确认含 forward = 候选/label 侧, 非特征。"""
    if i < 0 or i >= len(lows) or lows[i] in (None, 0) or lows[i] < 0:
        return False
    seg = [x for x in lows[max(i - win, 0): min(i + win + 1, len(lows))] if x is not None]
    return bool(seg) and lows[i] == min(seg)


def forward_max_gain(highs: list, lows: list, i: int, maxfwd: int = MAXFWD) -> float | None:
    """(底后 maxfwd 日内最高 high)/lows[i] - 1.0。无前瞻 bar 或底价非正 -> None (不可判 = 不当负样本)。"""
    if lows[i] in (None, 0):
        return None
    fwd = [h for h in highs[i + 1: i + 1 + maxfwd] if h is not None]
    if not fwd:
        return None
    return max(fwd) / lows[i] - 1.0


def base_days_count(closes: list, i: int, ref_low: float, lookback: int = BASE_LOOKBACK) -> int:
    """底前 lookback 窗内贴底 (ref_low*0.85~1.25) 的收盘日数 = 长底盘整度 (PIT: slice [i-lookback:i] 不含 i)。"""
    win = closes[max(i - lookback, 0): i]
    return sum(1 for c in win if c is not None and ref_low * 0.85 <= c <= ref_low * 1.25)


def forward_complete(bottom_date: str, trading_days: list[str], last_data_date: str,
                     fwd_window_len: int = MAXFWD) -> bool:
    """bottom_date + fwd_window_len 交易日是否仍 <= 数据边缘 (forward 窗完整观测; False=右删失)。

    pre-calendar (trading_days 起点前) 的 bottom: forward 窗早已完整 -> True。
    """
    if not trading_days or bottom_date < trading_days[0]:
        return True
    pos = bisect_right(trading_days, bottom_date)        # 第一个 > bottom 的交易日下标
    end_idx = pos + int(fwd_window_len) - 1               # fwd_window_len-th 交易日 (bottom 后第1日=pos)
    return end_idx < len(trading_days) and trading_days[end_idx] <= last_data_date
