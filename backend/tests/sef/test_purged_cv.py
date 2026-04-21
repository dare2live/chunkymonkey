"""SEF Purged K-Fold 单元测试：purge + embargo 行为正确."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from services.sef.purged_cv import PurgedKFold, purged_walk_forward_splits


def _mk_samples(n: int, label_span_days: int = 30):
    """n 条样本，按月排列，每条 label 跨 30 天."""
    base = datetime(2024, 1, 1)
    rows = []
    for i in range(n):
        start = base + timedelta(days=i * 7)
        end = start + timedelta(days=label_span_days)
        rows.append([start, end])
    return rows


def test_purged_kfold_basic_shape():
    samples = _mk_samples(50)
    cv = PurgedKFold(n_splits=5, embargo_days=5)
    splits = list(cv.split(samples))
    assert len(splits) == 5
    for tr, te in splits:
        assert len(te) > 0
        assert len(tr) + len(te) <= 50


def test_purged_kfold_no_overlap():
    """训练集不能与测试集 label 时间重叠."""
    samples = _mk_samples(40)
    cv = PurgedKFold(n_splits=4, embargo_days=3)
    for tr, te in cv.split(samples):
        test_start = min(samples[i][0] for i in te)
        test_end = max(samples[i][1] for i in te)
        for i in tr:
            s, e = samples[i][0], samples[i][1]
            overlap = (e >= test_start) and (s <= test_end)
            assert not overlap, f"train sample overlaps test window: [{s}, {e}]"


def test_purged_kfold_embargo_applied():
    samples = _mk_samples(40, label_span_days=5)
    cv = PurgedKFold(n_splits=4, embargo_days=10)
    for tr, te in cv.split(samples):
        test_end = max(samples[i][1] for i in te)
        embargo_end = test_end + timedelta(days=10)
        # 训练集里不能有 "test_end < start ≤ embargo_end" 的样本
        for i in tr:
            s = samples[i][0]
            assert not (test_end < s <= embargo_end), f"embargo violated: start={s}, test_end={test_end}"


def test_purged_kfold_str_dates():
    rows = [["2024-01-01", "2024-01-15"], ["2024-02-01", "2024-02-20"], ["2024-03-01", "2024-03-20"],
            ["2024-04-01", "2024-04-20"], ["2024-05-01", "2024-05-20"]]
    cv = PurgedKFold(n_splits=5, embargo_days=0)
    splits = list(cv.split(rows))
    assert len(splits) == 5


def test_purged_kfold_invalid_params():
    with pytest.raises(ValueError):
        PurgedKFold(n_splits=1)
    with pytest.raises(ValueError):
        PurgedKFold(embargo_days=-1)


def test_walk_forward_monotonic():
    samples = _mk_samples(300, label_span_days=10)
    splits = purged_walk_forward_splits(samples, train_window_months=6, test_window_months=1, embargo_days=5)
    prev_test_end = None
    for tr, te in splits:
        tr_starts = [samples[i][0] for i in tr]
        te_starts = [samples[i][0] for i in te]
        # 训练集全部在测试集之前
        if tr_starts and te_starts:
            assert max(tr_starts) < min(te_starts)
        # 测试窗口单调递增
        te_max = max([samples[i][1] for i in te])
        if prev_test_end is not None:
            assert te_max >= prev_test_end
        prev_test_end = te_max
