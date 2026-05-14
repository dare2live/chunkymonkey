"""Rank Information Coefficient — 月度横截面 IC 计算.

PLAN_V3 v3.2 P0b Go metric: validation stitched OOS RankIC ≥ 0.03.

RankIC 定义 (Lopez de Prado, "Advances in ML"):
    给定某 signal_date t 的横截面 (所有 stock 的 score + 实际 fwd_return),
    RankIC_t = Spearman(score, fwd_return)

Stitched OOS RankIC = 全部 OOS signal_date 的 RankIC 简单平均
    (业界惯例; 也有用 IC IR = mean(RankIC) / std(RankIC) 衡量稳定性).

PIT 保证: score 必须是 train 集 fit 出的 model 在 test 集 predict, test_date > train_end.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("ml_ranking.rank_ic")


@dataclass(frozen=True)
class RankICResult:
    """Stitched OOS RankIC 统计."""
    mean_rank_ic: float       # 横截面 IC 简单平均
    ic_ir: float              # IC IR = mean / std (稳定性指标)
    n_dates: int              # 有效 signal_date 数 (≥2 stocks 且 fwd_return 都有值)
    n_dates_skipped: int      # 跳过 (单股 / 全 NaN) 的 signal_date 数
    per_date_ic: list[float]  # 每个 signal_date 的 RankIC, 长度 = n_dates


def compute_cross_section_ic(
    scores: list[float],
    fwd_returns: list[float],
) -> float | None:
    """单个 signal_date 横截面 RankIC.

    Args:
        scores: model 预测 score (len=n stocks at this date).
        fwd_returns: 对应实际 fwd cost-after return.

    Returns:
        Spearman RankIC ∈ [-1, 1]; None 当 valid pairs < 2 或全相同.
    """
    if len(scores) != len(fwd_returns):
        raise ValueError(f"length mismatch: scores {len(scores)} vs fwd_returns {len(fwd_returns)}")

    arr_s = np.asarray(scores, dtype=float)
    arr_r = np.asarray(fwd_returns, dtype=float)
    mask = np.isfinite(arr_s) & np.isfinite(arr_r)
    if mask.sum() < 2:
        return None

    arr_s, arr_r = arr_s[mask], arr_r[mask]
    # Spearman = Pearson of ranks; ranks 用 argsort + argsort.
    rank_s = arr_s.argsort().argsort().astype(float)
    rank_r = arr_r.argsort().argsort().astype(float)
    if rank_s.std() == 0 or rank_r.std() == 0:
        return None
    # Pearson correlation of ranks
    return float(np.corrcoef(rank_s, rank_r)[0, 1])


def compute_rank_ic(
    rows: list[dict],
    score_field: str = "score",
    label_field: str = "fwd_cost_after_10d",
    date_field: str = "signal_date",
) -> RankICResult:
    """Stitched OOS RankIC across signal_dates.

    Args:
        rows: list of dict with `score`, `fwd_cost_after_*`, `signal_date`.
        score_field: model 预测 score 字段名.
        label_field: 实际 fwd return 字段名 (default 10d cost-after).
        date_field: signal_date 字段名.

    Returns:
        RankICResult — mean + ic_ir + n_dates 等汇总.
    """
    by_date: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        s = r.get(score_field)
        y = r.get(label_field)
        d = r.get(date_field)
        if s is None or y is None or d is None:
            continue
        by_date.setdefault(str(d), []).append((float(s), float(y)))

    per_date_ic: list[float] = []
    n_skipped = 0
    for d in sorted(by_date):
        pairs = by_date[d]
        if len(pairs) < 2:
            n_skipped += 1
            continue
        scores = [p[0] for p in pairs]
        rets = [p[1] for p in pairs]
        ic = compute_cross_section_ic(scores, rets)
        if ic is None:
            n_skipped += 1
            continue
        per_date_ic.append(ic)

    if not per_date_ic:
        return RankICResult(
            mean_rank_ic=float("nan"),
            ic_ir=float("nan"),
            n_dates=0,
            n_dates_skipped=n_skipped,
            per_date_ic=[],
        )
    arr = np.asarray(per_date_ic)
    mean_ic = float(arr.mean())
    std_ic = float(arr.std(ddof=1)) if len(arr) > 1 else float("nan")
    ic_ir = mean_ic / std_ic if std_ic and std_ic > 0 else float("nan")
    return RankICResult(
        mean_rank_ic=mean_ic,
        ic_ir=ic_ir,
        n_dates=len(per_date_ic),
        n_dates_skipped=n_skipped,
        per_date_ic=per_date_ic,
    )
