"""Purged K-Fold + Embargo (Lopez de Prado 2018, Ch.7).

金融时间序列的交叉验证。两个关键修正:
1. Purging: 训练集删除 label 会延伸到测试集的样本
2. Embargo : train/test 之间留 N 天 buffer，防止因价格自相关导致信息泄漏

用法:
    from backend.services.sef.purged_cv import PurgedKFold
    cv = PurgedKFold(n_splits=5, embargo_days=5)
    for train_idx, test_idx in cv.split(
        sample_times=chain_df[['entry_date', 'exit_date']],
    ):
        ...

sample_times 必须是两列 DataFrame/ndarray：
  col 0 = 样本观察开始时刻（entry_date）
  col 1 = 样本 label 结束时刻（exit_date / eval_date）
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator, Sequence

import numpy as np


def _to_dt(x) -> datetime:
    if isinstance(x, datetime):
        return x
    if isinstance(x, str):
        return datetime.strptime(x[:10], "%Y-%m-%d")
    raise TypeError(f"unsupported date type: {type(x)}")


def _get(sample_times, i: int, col: int):
    """Work with list/ndarray/DataFrame (2D indexable)."""
    try:
        return sample_times.iloc[i, col]  # pandas DataFrame
    except AttributeError:
        pass
    return sample_times[i][col]


def _len(sample_times) -> int:
    try:
        return len(sample_times)
    except TypeError:
        return sample_times.shape[0]


class PurgedKFold:
    """Purged K-Fold cross validator with embargo.

    Parameters
    ----------
    n_splits : int
        Number of folds (>=2).
    embargo_days : int
        After each test fold, skip this many days before next train sample.
    """

    def __init__(self, n_splits: int = 5, embargo_days: int = 5):
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        if embargo_days < 0:
            raise ValueError("embargo_days must be >= 0")
        self.n_splits = n_splits
        self.embargo_days = embargo_days

    def split(self, sample_times) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """Yield (train_idx, test_idx) with purge + embargo.

        sample_times: indexable of (start_time, end_time) pairs, ordered by start_time asc.
        """
        n = _len(sample_times)
        if n < self.n_splits:
            raise ValueError(f"need >= {self.n_splits} samples, got {n}")

        indices = np.arange(n)
        starts = np.array([_to_dt(_get(sample_times, i, 0)) for i in indices])
        ends = np.array([_to_dt(_get(sample_times, i, 1)) for i in indices])

        fold_size = n // self.n_splits
        for k in range(self.n_splits):
            t0 = k * fold_size
            t1 = (k + 1) * fold_size if k < self.n_splits - 1 else n
            test_idx = indices[t0:t1]

            test_start = starts[test_idx].min()
            test_end = ends[test_idx].max()
            embargo = timedelta(days=self.embargo_days)

            train_mask = np.ones(n, dtype=bool)
            train_mask[test_idx] = False

            for i in indices:
                if not train_mask[i]:
                    continue
                # Purge: overlap between sample [starts[i], ends[i]] and test window
                if ends[i] >= test_start and starts[i] <= test_end:
                    train_mask[i] = False
                    continue
                # Embargo: sample starts after test ends but within embargo window
                if starts[i] > test_end and starts[i] <= test_end + embargo:
                    train_mask[i] = False

            train_idx = indices[train_mask]
            yield train_idx, test_idx


def purged_walk_forward_splits(
    sample_times,
    *,
    train_window_months: int = 24,
    test_window_months: int = 3,
    embargo_days: int = 5,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Rolling walk-forward with purge + embargo.

    Generates non-overlapping test windows with train windows ending `embargo_days`
    before each test window starts. Useful for model re-training schedule.
    """
    n = _len(sample_times)
    if n == 0:
        return []
    starts = np.array([_to_dt(_get(sample_times, i, 0)) for i in range(n)])
    ends = np.array([_to_dt(_get(sample_times, i, 1)) for i in range(n)])
    order = np.argsort(starts)
    starts = starts[order]
    ends = ends[order]

    res: list[tuple[np.ndarray, np.ndarray]] = []
    t0 = starts[0]
    while True:
        train_end = t0 + timedelta(days=train_window_months * 30)
        embargo_end = train_end + timedelta(days=embargo_days)
        test_end = embargo_end + timedelta(days=test_window_months * 30)
        if test_end > starts[-1]:
            break
        train_mask = (starts >= t0) & (ends < train_end)
        test_mask = (starts >= embargo_end) & (starts < test_end)
        if train_mask.any() and test_mask.any():
            res.append((order[train_mask], order[test_mask]))
        t0 = embargo_end
    return res
