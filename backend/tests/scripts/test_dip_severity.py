"""
行数缺口严重程度判定（_dip_severity.dip_signal_level）的纯函数测试。
不使用任何 fixture、不连接数据库、不读配置文件。
"""
from __future__ import annotations

import pytest

from scripts._dip_severity import dip_signal_level


@pytest.mark.parametrize(
    "day_rows,neighbor_median,series_cv,expected",
    [
        # 规则 3: ratio >= 0.5 时返回 "none"
        (100, 100, 0.05, "none"),
        # 规则 3: ratio 恰好等于 0.5 的边界情况
        (50, 100, 0.05, "none"),
        # 规则 4a: ratio < 0.5 且 cv < 0.25 → 稳定域，返回 "high"
        (40, 100, 0.05, "high"),
        # 规则 4a: 极端缺口（ratio ≈ 0），cv 稳定 → "high"
        (1, 5000, 0.13, "high"),
        # 规则 4a: day_rows 为 0，cv 稳定 → "high"
        (0, 100, 0.05, "high"),
        # 规则 4a: ratio < 0.5，cv 刚好低于阈值 → "high"
        (49, 100, 0.05, "high"),
        # 规则 4b: ratio < 0.5 且 cv >= 0.25 → 事件类域，返回 "low"
        (40, 100, 0.45, "low"),
        # 规则 1: neighbor_median 为 0 → 无法判定，返回 "none"
        (100, 0, 0.05, "none"),
    ],
    ids=[
        "rule3_ratio_at_0.5",
        "rule3_ratio_at_0.5_boundary",
        "rule4a_stable_domain",
        "rule4a_extreme_dip_stable",
        "rule4a_zero_rows_stable",
        "rule4a_ratio_just_below_0.5",
        "rule4b_event_domain",
        "rule1_zero_neighbor_median",
    ],
)
def test_dip_signal_level(day_rows: int, neighbor_median: float, series_cv: float, expected: str) -> None:
    """
    参数化测试 dip_signal_level 函数。
    每个用例对应规则文档中的一条规则。
    """
    result = dip_signal_level(day_rows, neighbor_median, series_cv)
    assert result == expected, f"day_rows={day_rows}, neighbor_median={neighbor_median}, series_cv={series_cv}"
