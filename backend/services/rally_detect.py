"""主升浪候选检测共享原语 (pivot 底 / 前瞻涨幅 / 长底 / forward 完整) — 单一计算点。

owner=analysis/d1_gt_v2_design_20260702.md §2 + d1_gt_archaeology_20260702.md §3.3 (修正#8)。
缘起: 正样本 (services/rally_gt.detect_episodes) 与 hard-negative 必须共用**同一候选检测口径**,
  否则正负不可比 (v1.5 教训: GT builder 内联 pivot 检测 / 负样本走本模块 = 双真相源漂移风险)。
  v2 起两侧全部经本模块, 定义只在这里改。
阈值零 hardcode (修正#10): 本模块不带任何默认常数, 全部参数由调用方从
  backend/config/rally_gt.yaml 读出后显式传入 (判断死红线: 规则在 yaml, 不在代码)。
PIT 边界: 候选定义 (pivot ±win 确认 / forward 峰) 含 forward — 这是 LABEL 侧, 非特征;
  训练特征只许用 <= i 信息 (base_days_count 的 slice [i-lookback:i] 不含 i, 纯底前)。
语义来源: v1.5 归档代码 (rally_detect.py @ 390c8c3a + build_rally_ground_truth.py @ e909e548~1),
  定义参数已双证据验证 (考古 §4.1), 规则本体照搬。
"""
from __future__ import annotations

from bisect import bisect_right

import numpy as np


def is_pivot_low(lows, i: int, win: int) -> bool:
    """lows[i] 是否 [i-win, i+win] 区间最低 (波段底)。±win 确认含 forward = 候选/label 侧, 非特征。

    平价 (==) 语义与 v1.5 一致: 窗口内并列最低也算 pivot (同股去重由调用方 covered/间隔控制)。
    numpy 数组走 nanmin 快路径 (None 已转 nan); list 输入过滤 None。
    """
    if i < 0 or i >= len(lows):
        return False
    v = lows[i]
    if v is None or np.isnan(v) or v <= 0:
        return False
    seg = lows[max(i - win, 0): min(i + win + 1, len(lows))]
    if isinstance(seg, np.ndarray):
        return bool(v == np.nanmin(seg))
    vals = [x for x in seg if x is not None]
    return bool(vals) and v == min(vals)


def forward_peak(highs, lows, i: int, maxfwd: int) -> tuple[float, int] | None:
    """底后 maxfwd 根内 (gain, peak_offset): gain=max(high)/lows[i]-1, offset=首个 argmax+1。

    正/负样本共用的前瞻峰定义 (单一计算点)。无前瞻 bar 或底价非正 -> None (不可判)。
    """
    if lows[i] is None or not lows[i] > 0:
        return None
    fwd = highs[i + 1: i + 1 + maxfwd]
    if isinstance(fwd, np.ndarray):
        if not len(fwd):
            return None
        po = int(np.argmax(fwd)) + 1
        pk = float(fwd[po - 1])
    else:
        vals = [(h, k) for k, h in enumerate(fwd) if h is not None]
        if not vals:
            return None
        pk = max(v for v, _ in vals)
        po = next(k for v, k in vals if v == pk) + 1
    return pk / float(lows[i]) - 1.0, po


def forward_max_gain(highs, lows, i: int, maxfwd: int) -> float | None:
    """(底后 maxfwd 根内最高 high)/lows[i] - 1.0; 委托 forward_peak (单一计算点)。"""
    peak = forward_peak(highs, lows, i, maxfwd)
    return None if peak is None else peak[0]


def base_days_count(closes, i: int, ref_low: float, lookback: int,
                    band_low: float, band_high: float) -> int:
    """底前 lookback 窗内贴底 (ref_low*[band_low, band_high]) 的收盘日数 = 长底盘整度。

    PIT: slice [i-lookback:i] 不含 i, 纯底前 — 是 GT 唯一可做训练 X 的自带特征。
    """
    win = closes[max(i - lookback, 0): i]
    lo, hi = ref_low * band_low, ref_low * band_high
    if isinstance(win, np.ndarray):
        return int(np.sum((win >= lo) & (win <= hi)))
    return sum(1 for c in win if c is not None and lo <= c <= hi)


def forward_complete(bottom_date: str, trading_days: list[str], last_data_date: str,
                     fwd_window_len: int) -> bool:
    """bottom_date + fwd_window_len 交易日是否仍 <= 数据边缘 (forward 窗完整观测; False=右删失)。

    v2 双用途 (修正#1/#8 对称): 负样本 fwd_complete 判定 + 正样本右删失 embargo
    (bottom+maxfwd 交易日 > data_end 的 episode 剔出 train)。
    pre-calendar (trading_days 起点前) 的 bottom: forward 窗早已完整 -> True。
    """
    if not trading_days or bottom_date < trading_days[0]:
        return True
    pos = bisect_right(trading_days, bottom_date)        # 第一个 > bottom 的交易日下标
    end_idx = pos + int(fwd_window_len) - 1              # fwd_window_len-th 交易日 (bottom 后第1日=pos)
    return end_idx < len(trading_days) and trading_days[end_idx] <= last_data_date
