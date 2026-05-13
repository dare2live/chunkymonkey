"""Phase γ D1 — stage_days 计算单测。

覆盖 run-length 算法 + 边界 (空/单元素/全相同/全不同/末尾切换)。
"""
from __future__ import annotations

import numpy as np
import pytest

from services.picture.stage_days import compute_stage_days, latest_stage_days


class TestComputeStageDays:
    def test_empty_input_returns_empty(self):
        out = compute_stage_days(np.array([], dtype=object))
        assert len(out) == 0
        assert out.dtype == np.int64

    def test_single_element_returns_one(self):
        out = compute_stage_days(np.array(["2"]))
        assert list(out) == [1]

    def test_all_same_stage_increments(self):
        out = compute_stage_days(np.array(["2", "2", "2", "2"]))
        assert list(out) == [1, 2, 3, 4]

    def test_all_different_stages_each_is_one(self):
        out = compute_stage_days(np.array(["1", "2", "3", "4"]))
        assert list(out) == [1, 1, 1, 1]

    def test_change_in_middle(self):
        # Stan Weinstein: 阶段 1 → 阶段 2
        out = compute_stage_days(np.array(["1", "1", "1", "2", "2"]))
        assert list(out) == [1, 2, 3, 1, 2]

    def test_change_on_last_day(self):
        # 边界: 最后一天 stage 变化
        out = compute_stage_days(np.array(["2", "2", "2", "3"]))
        assert list(out) == [1, 2, 3, 1]

    def test_multiple_transitions(self):
        # 1 → 1.5 → 2 → 2 → 3 (常见 4 阶段轮回)
        out = compute_stage_days(np.array(["1", "1.5", "2", "2", "3"]))
        assert list(out) == [1, 1, 1, 2, 1]

    def test_dtype_is_int64(self):
        out = compute_stage_days(np.array(["2", "2", "2"]))
        assert out.dtype == np.int64


class TestLatestStageDays:
    def test_empty_returns_zero(self):
        assert latest_stage_days(np.array([])) == 0

    def test_last_value_only(self):
        # 3 天处于 stage 2
        assert latest_stage_days(np.array(["1", "1", "2", "2", "2"])) == 3

    def test_just_switched(self):
        # 刚切换到 3, 才 1 天
        assert latest_stage_days(np.array(["2", "2", "2", "3"])) == 1

    def test_returns_int_not_numpy(self):
        # API 契约: 返回 Python int (而非 np.int64) 便于 JSON 序列化
        result = latest_stage_days(np.array(["2", "2"]))
        assert isinstance(result, int)
        assert result == 2


class TestRealisticWeinsteinSeries:
    """Stan Weinstein 4 阶段 + 1.5 桥接的真实场景。"""

    def test_full_weinstein_cycle(self):
        # 底部 → 早期反弹 → 上升 → 顶部 → 下跌
        stages = np.array(
            ["1"] * 30      # 30 日震荡筑底
            + ["1.5"] * 5    # 5 日反弹起势
            + ["2"] * 100    # 100 日主升
            + ["3"] * 20     # 20 日顶部分配
            + ["4"] * 40     # 40 日下跌
        )
        days = compute_stage_days(stages)
        # 验证段尾计数
        assert days[29] == 30           # 阶段 1 最后一天
        assert days[30] == 1            # 切换到 1.5 第一天
        assert days[34] == 5            # 阶段 1.5 最后一天
        assert days[35] == 1            # 阶段 2 第一天
        assert days[134] == 100         # 阶段 2 最后一天
        assert days[-1] == 40           # 当前阶段 4 已 40 天
        assert latest_stage_days(stages) == 40
