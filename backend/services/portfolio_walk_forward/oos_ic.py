"""Walk-forward OOS RankIC 核心 (L0 裸K线基准 Tier-1, owner=analysis/l0_bare_kline_baseline_spec_20260614.md)。

地基-reset 删了 walk_forward 主 runner, 本模块重建其 leakage-critical 核心: 纯函数 (无 DB 耦合,
合成数据可测) 计算 expanding_monthly 窗口 + PIT 前向收益标注 + 截面 RankIC。两层引擎 (Tier-1 RankIC
快筛 / Tier-2 backtest 终验) 共享本核心的窗口 + 标注原语。

PIT 纪律 (死亡条款泄漏死):
  - feature[t] 必须只用 bars[:t+1] (调用方职责; 本核心假设传入 feature 已 PIT-clean)。
  - label = forward return 天然向前 (是预测目标, 非泄漏); 泄漏风险在**寻参时 train 行的 label 探入
    test 窗** -> embargo (>= forward horizon) 在 train/test 边界切掉重叠行 (spec §4.1)。
  - 选参只看 OOS test 行 RankIC, 绝不看 train (selector 只读 oos_*, 防 stage_opt MAX(oos) 用未来反例)。

RankIC = 标准因子 IC: 每个交易日截面 spearman(feature, forward_return) = 日度 IC; OOS 窗内日度 IC
均值 = oos_rank_ic; IC_IR = mean/std。spearman via numpy (rank 上 pearson), 不依赖 scipy。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PanelRow:
    """单 (日期, 股票) 观测。feature 已 PIT-clean (只用 <=date 信息); fwd_ret 由 forward_returns 算。"""
    date: str          # 'YYYY-MM-DD' 或 'YYYYMMDD' (字典序 == 时间序)
    code: str
    feature: float | None
    fwd_ret: float | None


def forward_returns(dates: list[str], closes: list[float], horizon: int) -> list[float | None]:
    """单股时间序列前向收益: ret[i] = close[i+horizon]/close[i]-1; 末 horizon 行 = None (无未来)。

    PIT: label 用 future close 是合法 (预测目标); 不回看 (ret[i] 绝不含 close[<i])。
    dates 必须已按时间升序 (调用方保证, 同股 K线天然有序)。
    """
    if horizon < 1:
        raise ValueError(f"horizon 必须 >=1, got {horizon}")
    n = len(closes)
    if len(dates) != n:
        raise ValueError("dates/closes 长度不一致")
    out: list[float | None] = []
    for i in range(n):
        j = i + horizon
        if j >= n or closes[i] in (None, 0) or closes[j] is None:
            out.append(None)
        else:
            out.append(closes[j] / closes[i] - 1.0)
    return out


def _spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    """rank 上 pearson。样本 <3 或任一恒定 -> None (区分度不足, 不报假 0)。"""
    if x.size < 3:
        return None
    rx = _rankdata(x)
    ry = _rankdata(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def _rankdata(a: np.ndarray) -> np.ndarray:
    """平均秩 (ties 取均值), 同 scipy.stats.rankdata 默认。"""
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(a.size, dtype=float)
    ranks[order] = np.arange(1, a.size + 1, dtype=float)
    # ties -> 平均秩
    _, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(counts.size)
    np.add.at(sums, inv, ranks)
    return (sums / counts)[inv]


def cross_sectional_ic(features: list[float | None], labels: list[float | None]) -> float | None:
    """单日截面 RankIC: spearman(feature, label), 丢 None 对。样本不足 -> None。"""
    pairs = [(f, l) for f, l in zip(features, labels) if f is not None and l is not None]
    if len(pairs) < 3:
        return None
    fx = np.array([p[0] for p in pairs], dtype=float)
    lx = np.array([p[1] for p in pairs], dtype=float)
    return _spearman(fx, lx)


def _month(date: str) -> str:
    """'2024-03-05'/'20240305' -> '202403' 月键。"""
    d = date.replace("-", "")
    return d[:6]


def expanding_monthly_windows(
    months: list[str], *, min_train_months: int, forward_months: int, min_total_months: int,
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """R1 expanding_monthly: 每窗 train = 之前所有月, test = 接下来 forward_months 个月。

    返回 [(train_months, test_months), ...]。total < min_total_months -> 空 (调用方退 holdout)。
    """
    uniq = sorted(set(months))
    if len(uniq) < min_total_months:
        return []
    windows: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    start = min_train_months
    while start < len(uniq):
        train = tuple(uniq[:start])
        test = tuple(uniq[start:start + forward_months])
        if len(test) < forward_months:  # 不完整末窗丢弃 (统计一致性: 全窗等大)
            break
        windows.append((train, test))
        start += forward_months
    return windows


def oos_rank_ic(
    panel: list[PanelRow], *, min_train_months: int = 6, forward_months: int = 1,
    min_total_months: int = 12, embargo_days: int = 0,
) -> dict:
    """walk-forward OOS RankIC 聚合。

    每窗在 test 月的每个交易日算截面 IC -> 全窗 OOS 日度 IC 序列 -> 均值=oos_rank_ic, mean/std=ic_ir。
    只用 test (OOS) 行, 绝不用 train (防 in-sample fit 入选)。
    embargo_days>0: 丢每个 test 窗末 embargo_days 个交易日 (其 horizon 前向 label 跨入下一窗 = 跨窗
    label 重叠; 切掉使窗间 label-disjoint, 防泄露固化非死闸 — 寻参时 train tail 同理被 purge)。
    ic_ir 用无偏 std (ddof=1, 金融 Sharpe 类标准; 有偏 ddof=0 高估)。
    返回 {oos_rank_ic, ic_ir, n_windows, n_days, per_window_ic}; 无足够窗 -> oos_rank_ic=None (标 unknown)。
    """
    months = sorted({_month(r.date) for r in panel})
    windows = expanding_monthly_windows(
        months, min_train_months=min_train_months, forward_months=forward_months,
        min_total_months=min_total_months,
    )
    if not windows:
        return {"oos_rank_ic": None, "ic_ir": None, "n_windows": 0, "n_days": 0,
                "per_window_ic": [], "reason": "insufficient_months"}

    by_date: dict[str, list[PanelRow]] = {}
    for r in panel:
        by_date.setdefault(r.date, []).append(r)

    daily_ics: list[float] = []
    per_window: list[float | None] = []
    for _train, test in windows:
        test_set = set(test)
        test_dates = sorted(d for d in by_date if _month(d) in test_set)
        if embargo_days > 0:  # 切窗末 embargo_days 天 (其 label 跨入下一窗)
            test_dates = test_dates[:-embargo_days] if len(test_dates) > embargo_days else []
        win_ics: list[float] = []
        for date in test_dates:
            rows = by_date[date]
            ic = cross_sectional_ic([r.feature for r in rows], [r.fwd_ret for r in rows])
            if ic is not None:
                win_ics.append(ic)
        per_window.append(float(np.mean(win_ics)) if win_ics else None)
        daily_ics.extend(win_ics)

    if not daily_ics:
        return {"oos_rank_ic": None, "ic_ir": None, "n_windows": len(windows), "n_days": 0,
                "per_window_ic": per_window, "reason": "no_valid_ic_days"}
    arr = np.array(daily_ics, dtype=float)
    std = float(arr.std(ddof=1)) if arr.size > 1 else 0.0  # 无偏 (金融标准)
    ic_ir = float(arr.mean() / std) if std > 0 else None
    return {
        "oos_rank_ic": float(arr.mean()), "ic_ir": ic_ir, "n_windows": len(windows),
        "n_days": len(daily_ics), "per_window_ic": per_window,
    }
