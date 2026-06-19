"""主升浪阶段切分单测 (2026-06-20, C#48 step2).

证 segment_episode: progress 首次跨阈划连续段; pullback 日不回退阶段 (contiguous time-segment)。
默认阈 launch_end=0.30 / main_end=0.85 (rally_stage.yaml)。
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.build_rally_stage import LAUNCH_END, MAIN_END, segment_episode  # noqa: E402


def test_thresholds_loaded():
    assert LAUNCH_END == 0.30 and MAIN_END == 0.85   # rally_stage.yaml pre-reg


def test_segment_monotonic_rise():
    # bottom=10(idx0) peak=20(idx6); progress 0/.1/.3/.5/.7/.9/1.0
    closes = [10.0, 11.0, 13.0, 15.0, 17.0, 19.0, 20.0]
    dates = [f"2024-01-0{i+1}" for i in range(7)]
    seg = segment_episode(dates, closes, 0, 6)
    stages = [s for _, s, _, _ in seg]
    # i30=idx2(prog0.3), i85=idx5(prog0.9): 起涨[0,1] 主升[2,3,4] 顶部[5,6]
    assert stages == ["起涨", "起涨", "主升", "主升", "主升", "顶部", "顶部"]
    assert seg[0][3] == 0 and seg[6][3] == 6        # days_from_bottom
    assert abs(seg[6][2] - 1.0) < 1e-9              # peak progress=1.0


def test_segment_pullback_no_revert():
    # idx1 冲到 prog0.3 (i30=1), idx2 回撤到 prog0.1 — 应仍 主升 (连续段不回退)
    closes = [10.0, 13.0, 11.0, 15.0, 17.0, 19.0, 20.0]
    dates = [f"2024-02-0{i+1}" for i in range(7)]
    seg = segment_episode(dates, closes, 0, 6)
    stages = [s for _, s, _, _ in seg]
    assert stages[0] == "起涨"
    assert stages[1] == "主升"        # 首次跨0.30
    assert stages[2] == "主升"        # pullback 但已过 i30, 不回退起涨 (核心: 连续时间段非逐日progress)


def test_segment_no_gain_skipped():
    assert segment_episode(["a", "b"], [10.0, 10.0], 0, 1) == []   # peak<=bottom
